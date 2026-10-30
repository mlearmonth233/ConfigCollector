"""Bulk ad-hoc ping/DNS/reverse-DNS check for a pasted list of hostnames or
IPs, or for every usable address in a subnet (a sweep) - the "DNS Check"
page. Unlike devices.py's reachability check, targets here aren't existing
Device records - just whatever the user pasted in or the subnet expands to.

Every check is best-effort: a failed ping, forward lookup, or reverse
lookup is a normal, reportable outcome (see reachability.py's own docstring
for why none of these alone prove anything is actually down), never a
crashed request.
"""

import asyncio
import ipaddress
import socket

from app.services.reachability import _ping

# How many checks run at once - stops a large batch from launching that
# many ping subprocesses simultaneously. Public because tasks.py's
# run_dns_check_job_task reuses it as its progress-reporting chunk size too
# (one constant, so the two can't drift apart), and app/api/dns_check.py
# imports MAX_TARGETS as a sanity-check cap on job size.
CONCURRENCY = 25
MAX_TARGETS = 10000
# A subnet sweep is mostly addresses nobody answers on, so each check waits
# out the full ping timeout; running more at once keeps a /24 to seconds.
SWEEP_CONCURRENCY = 64


class SubnetError(ValueError):
    """The text is not a subnet Packrat can sweep. The message says why."""


def parse_subnet(text: str) -> ipaddress.IPv4Network | ipaddress.IPv6Network:
    """A subnet as a network engineer types it: "10.1.1.0/24",
    "10.1.1.0 255.255.255.0", "10.1.1.0/255.255.255.0", or an interface
    address such as "10.1.1.1/24" (the host bits are dropped)."""
    raw = " ".join((text or "").split())
    if not raw:
        raise SubnetError("Enter a subnet, for example 10.1.1.0/24")
    if " " in raw:
        address, _, mask = raw.partition(" ")
        if "/" in address or " " in mask:
            raise SubnetError(f"{text.strip()!r} is not a subnet: use 10.1.1.0/24 or 10.1.1.0 255.255.255.0")
        raw = f"{address}/{mask}"
    if "/" not in raw:
        raise SubnetError(f"{raw} has no mask: add one, for example {raw}/24")
    try:
        return ipaddress.ip_network(raw, strict=False)
    except ValueError as exc:
        raise SubnetError(f"{raw} is not a valid subnet ({exc})") from exc


def usable_hosts(network: ipaddress.IPv4Network | ipaddress.IPv6Network) -> list[str]:
    """Every address a host can use: an IPv4 subnet without its network and
    broadcast addresses (a /31 or /32 keeps them, as RFC 3021 allows), an
    IPv6 one without its subnet-router anycast address. Refuses a subnet
    with more than MAX_TARGETS of them before listing any."""
    total = network.num_addresses
    if network.version == 4 and network.prefixlen < 31:
        total -= 2
    elif network.version == 6 and network.prefixlen < 127:
        total -= 1
    if total > MAX_TARGETS:
        smallest = 32 - (MAX_TARGETS + 2).bit_length() + 1 if network.version == 4 else None
        hint = f" The largest IPv4 subnet one sweep covers is a /{smallest}; split bigger ones." if smallest else ""
        raise SubnetError(f"{network} has {total:,} usable addresses, more than the {MAX_TARGETS:,} one sweep can check.{hint}")
    return [str(host) for host in network.hosts()]


def _is_ip_address(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return False


async def _forward_lookup(hostname: str) -> tuple[bool, list[str]]:
    """The "DNS test": resolves a hostname to its IP address(es)."""
    try:
        loop = asyncio.get_running_loop()
        infos = await loop.getaddrinfo(hostname, None)
        # De-duplicate while preserving order - getaddrinfo commonly returns
        # the same address once per socket type (SOCK_STREAM, SOCK_DGRAM).
        seen: dict[str, None] = {}
        for info in infos:
            seen.setdefault(info[4][0], None)
        return True, list(seen.keys())
    except OSError:
        return False, []


async def _reverse_lookup(ip: str) -> tuple[bool, str | None]:
    """The "reverse lookup" check: looks up the PTR record for `ip`.
    NI_NAMEREQD makes a missing PTR record raise instead of silently
    falling back to returning the IP itself as the "hostname"."""
    try:
        loop = asyncio.get_running_loop()
        hostname, _port = await loop.getnameinfo((ip, 0), socket.NI_NAMEREQD)
        return True, hostname
    except (OSError, socket.gaierror):
        return False, None


class DnsCheckResult:
    def __init__(
        self,
        target: str,
        ping_ok: bool,
        forward_ok: bool,
        forward_ips: list[str],
        reverse_ok: bool,
        reverse_hostname: str | None,
    ) -> None:
        self.target = target
        self.ping_ok = ping_ok
        self.forward_ok = forward_ok
        self.forward_ips = forward_ips
        self.reverse_ok = reverse_ok
        self.reverse_hostname = reverse_hostname


async def run_dns_check(target: str) -> DnsCheckResult:
    """Runs all three checks for one target - a hostname or a bare IP,
    either is fine. Ping always runs against the target exactly as given
    (letting the OS ping command do its own resolution, so the result
    matches what a user's own `ping <target>` would show). Forward
    resolution only makes sense for a hostname. Reverse lookup runs against
    whichever IP is relevant: the target itself if it's already an IP, or
    its first resolved address if it's a hostname that resolved."""
    is_ip = _is_ip_address(target)

    if is_ip:
        # Nothing to resolve first, so the ping and the PTR lookup run side
        # by side: a sweep of silent addresses waits for one, not both.
        forward_ok, forward_ips = False, []
        ping_ok, (reverse_ok, reverse_hostname) = await asyncio.gather(_ping(target), _reverse_lookup(target))
    else:
        ping_ok, (forward_ok, forward_ips) = await asyncio.gather(_ping(target), _forward_lookup(target))
        if forward_ips:
            reverse_ok, reverse_hostname = await _reverse_lookup(forward_ips[0])
        else:
            reverse_ok, reverse_hostname = False, None

    if is_ip and reverse_ok and reverse_hostname:
        # Forward-confirm the PTR record: does the name it gives point back
        # at this address? A PTR left behind after a host moved or was
        # retired shows up as a name that resolves elsewhere, or not at all.
        forward_ok, forward_ips = await _forward_lookup(reverse_hostname)

    return DnsCheckResult(
        target=target,
        ping_ok=ping_ok,
        forward_ok=forward_ok,
        forward_ips=forward_ips,
        reverse_ok=reverse_ok,
        reverse_hostname=reverse_hostname,
    )


def dns_matches(target: str, reverse_ok: bool | None, forward_ok: bool | None, forward_ips: list[str]) -> bool | None:
    """For an IP target: True when its PTR name resolves back to it, False
    when the name resolves elsewhere or not at all, None when there is no
    PTR record to check (or the target is a hostname)."""
    if not _is_ip_address(target) or not reverse_ok:
        return None
    if not forward_ok:
        return False
    wanted = ipaddress.ip_address(target)
    return any(_is_ip_address(ip) and ipaddress.ip_address(ip) == wanted for ip in forward_ips)


async def run_dns_checks(targets: list[str], concurrency: int = CONCURRENCY) -> list[DnsCheckResult]:
    semaphore = asyncio.Semaphore(concurrency)

    async def _bounded(target: str) -> DnsCheckResult:
        async with semaphore:
            return await run_dns_check(target)

    return list(await asyncio.gather(*(_bounded(t) for t in targets)))
