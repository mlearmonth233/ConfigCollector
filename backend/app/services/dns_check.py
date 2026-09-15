"""Bulk ad-hoc ping/DNS/reverse-DNS check for a pasted list of hostnames or
IPs - the "DNS Check" page. Unlike devices.py's reachability check, targets
here aren't existing Device records - just whatever the user pasted in - so
results are returned directly to the request rather than persisted anywhere.

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


async def _no_forward_lookup() -> tuple[bool, list[str]]:
    # The target is already an IP - there's nothing to resolve.
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

    ping_ok, (forward_ok, forward_ips) = await asyncio.gather(
        _ping(target),
        _no_forward_lookup() if is_ip else _forward_lookup(target),
    )

    reverse_ip = target if is_ip else (forward_ips[0] if forward_ips else None)
    if reverse_ip is not None:
        reverse_ok, reverse_hostname = await _reverse_lookup(reverse_ip)
    else:
        reverse_ok, reverse_hostname = False, None

    return DnsCheckResult(
        target=target,
        ping_ok=ping_ok,
        forward_ok=forward_ok,
        forward_ips=forward_ips,
        reverse_ok=reverse_ok,
        reverse_hostname=reverse_hostname,
    )


async def run_dns_checks(targets: list[str]) -> list[DnsCheckResult]:
    semaphore = asyncio.Semaphore(CONCURRENCY)

    async def _bounded(target: str) -> DnsCheckResult:
        async with semaphore:
            return await run_dns_check(target)

    return list(await asyncio.gather(*(_bounded(t) for t in targets)))
