"""Bulk hostname/IP checker: ping-alive status, forward DNS resolution, and
reverse DNS lookup, for a free-form list of hostnames and/or IP addresses -
not tied to this app's Device records at all, unlike
api/devices.py's /reachability endpoint (which is scoped to an org's saved
devices). A standalone diagnostic tool: paste in a batch of names/addresses
from anywhere and get back what DNS and ICMP each one reports.
"""

import asyncio
import ipaddress
import re
from dataclasses import dataclass

from app.services.reachability import ping_host, resolve_forward, resolve_reverse

# A giant pasted list would otherwise fan out into that many concurrent
# ping subprocesses + DNS lookups at once - plenty for a legitimate bulk
# check, but bounded so a single request can't turn into an unbounded
# resource/abuse vector.
MAX_BULK_ENTRIES = 500


@dataclass
class DnsCheckResult:
    input: str
    input_type: str  # "ip" or "hostname"
    resolved_ip: str | None  # the input itself, for an IP; forward-resolved, for a hostname
    reverse_hostname: str | None  # PTR lookup of resolved_ip, if any
    dns_ok: bool
    ping_ok: bool
    error: str | None


def parse_bulk_hosts(raw: str) -> list[str]:
    """Splits free-form bulk input - one per line, comma-separated, or a mix
    of both (matching the same forgiving convention as the Devices page's
    bulk-add-from-hostnames) - into a deduplicated, order-preserving list."""
    parts = re.split(r"[,\s]+", raw.strip())
    seen: set[str] = set()
    result: list[str] = []
    for part in parts:
        part = part.strip()
        if part and part not in seen:
            seen.add(part)
            result.append(part)
    return result


def _is_ip_address(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return False


async def _check_one(entry: str) -> DnsCheckResult:
    if _is_ip_address(entry):
        ping_ok, reverse_hostname = await asyncio.gather(ping_host(entry), resolve_reverse(entry))
        return DnsCheckResult(
            input=entry,
            input_type="ip",
            resolved_ip=entry,
            reverse_hostname=reverse_hostname,
            dns_ok=reverse_hostname is not None,
            ping_ok=ping_ok,
            error=None,
        )

    dns_ok, resolved_ip = await resolve_forward(entry)
    if not dns_ok or resolved_ip is None:
        # Both this and the OS's own ping command resolve via the same
        # system resolver, so a hostname that fails here would (barring a
        # transient blip) fail there too - not worth the extra ping timeout.
        return DnsCheckResult(
            input=entry,
            input_type="hostname",
            resolved_ip=None,
            reverse_hostname=None,
            dns_ok=False,
            ping_ok=False,
            error="Could not resolve this hostname",
        )

    ping_ok, reverse_hostname = await asyncio.gather(ping_host(entry), resolve_reverse(resolved_ip))
    return DnsCheckResult(
        input=entry,
        input_type="hostname",
        resolved_ip=resolved_ip,
        reverse_hostname=reverse_hostname,
        dns_ok=True,
        ping_ok=ping_ok,
        error=None,
    )


async def check_bulk(entries: list[str]) -> list[DnsCheckResult]:
    return list(await asyncio.gather(*(_check_one(entry) for entry in entries)))
