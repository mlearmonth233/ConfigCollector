"""Best-effort extraction of neighboring devices from a collected
snapshot's CDP/LLDP output, used to flag devices a switch/WLC can see on
the wire that aren't yet tracked in ConfigCollector (see
api/jobs.py's check_neighbor_gaps).

Screen-scraping CDP/LLDP CLI output is inherently approximate across
IOS/IOS-XE/AireOS versions - this only parses the "detail" command
variants ("show cdp neighbor detail", "sh lldp nei detail", ...), whose
one-neighbor-per-block layout is far more reliable to parse than the
column-aligned summary tables (which wrap onto a second line for any
sufficiently long device name, with no reliable way to tell that apart
from a second, oddly-named neighbor). A device type whose command list
only runs the summary form (no detail variant) just won't surface
anything here - a real but acceptable gap, not a wrong answer.
"""

import re
from dataclasses import dataclass

_COMMAND_BLOCK = re.compile(r"^! ---- (?P<command>.+?) ----$\n(?P<output>.*?)(?=^! ---- |\Z)", re.MULTILINE | re.DOTALL)

_CDP_DEVICE_ID = re.compile(r"^Device ID:\s*(\S+)", re.MULTILINE)
_LLDP_SYSTEM_NAME = re.compile(r"^System Name:\s*(\S+)", re.MULTILINE)
_CDP_IP_ADDRESS = re.compile(r"IP address:\s*([\d.]+)")
_LLDP_IP_ADDRESS = re.compile(r"^\s*IP:\s*([\d.]+)", re.MULTILINE)


@dataclass(frozen=True)
class DiscoveredNeighbor:
    name: str  # exactly as reported by the neighbor (e.g. may include a domain suffix)
    ip: str | None
    protocol: str  # "cdp" or "lldp"


def normalize_device_name(name: str) -> str:
    """For comparing a CDP/LLDP-reported name against Device.name: upper-
    cased (hostnames are conventionally case-insensitive - matches
    hostname_detection.py's own convention) and with any domain suffix
    dropped (CDP/LLDP commonly report a neighbor's FQDN, e.g.
    "SW02.corp.local", where this app's own inventory just uses "SW02")."""
    return name.strip().split(".")[0].upper()


def extract_neighbors(snapshot_content: str) -> list[DiscoveredNeighbor]:
    """Every neighbor reported by a "detail"-style CDP or LLDP command
    anywhere in this snapshot (a device type may run more than one, e.g.
    both CDP and LLDP) - duplicates (the same neighbor seen by both
    protocols) are left in; api/jobs.py's gap check dedupes across the
    whole job, not just within one snapshot."""
    neighbors: list[DiscoveredNeighbor] = []
    for command, output in _iter_neighbor_command_blocks(snapshot_content):
        if "cdp" in command:
            neighbors.extend(_parse_cdp_detail(output))
        else:
            neighbors.extend(_parse_lldp_detail(output))
    return neighbors


def has_neighbor_command(snapshot_content: str) -> bool:
    """True if this snapshot ran a "detail"-style CDP or LLDP command at
    all - used to distinguish "ran the command, found zero neighbors"
    (a real, meaningful result) from "never ran a command that could have
    found any" (nothing to conclude either way)."""
    return next(_iter_neighbor_command_blocks(snapshot_content), None) is not None


def _iter_neighbor_command_blocks(snapshot_content: str):
    for match in _COMMAND_BLOCK.finditer(snapshot_content):
        command = match.group("command").lower()
        if "detail" not in command:
            continue
        if "cdp" in command or "lldp" in command:
            yield command, match.group("output")


def _parse_cdp_detail(output: str) -> list[DiscoveredNeighbor]:
    matches = list(_CDP_DEVICE_ID.finditer(output))
    neighbors = []
    for i, m in enumerate(matches):
        block_end = matches[i + 1].start() if i + 1 < len(matches) else len(output)
        block = output[m.end():block_end]
        ip_match = _CDP_IP_ADDRESS.search(block)
        neighbors.append(DiscoveredNeighbor(name=m.group(1), ip=ip_match.group(1) if ip_match else None, protocol="cdp"))
    return neighbors


def _parse_lldp_detail(output: str) -> list[DiscoveredNeighbor]:
    matches = list(_LLDP_SYSTEM_NAME.finditer(output))
    neighbors = []
    for i, m in enumerate(matches):
        block_end = matches[i + 1].start() if i + 1 < len(matches) else len(output)
        block = output[m.end():block_end]
        ip_match = _LLDP_IP_ADDRESS.search(block)
        neighbors.append(DiscoveredNeighbor(name=m.group(1), ip=ip_match.group(1) if ip_match else None, protocol="lldp"))
    return neighbors
