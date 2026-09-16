"""Hardware inventory built from collected configuration snapshots.

Every collection stores the raw output of each command the device type
runs, separated by "! ---- <command> ----" markers (services/collector.py).
This module reads the *latest* snapshot of every device and screen-scrapes
the parts that describe hardware:

  show version           hostname, model, serial, software, uptime, base MAC
  show inventory         chassis, modules, power supplies, optics (PID/SN)
  show cdp/lldp ... detail   what is plugged into which port, and where
  show mac address-table which MAC addresses sit behind which access port
  show ip arp            MAC -> IP, so an endpoint gets an address too
  show ap summary / show ap config general   access points on a controller
  get system status/arp  FortiGate equivalents
  about                  APC PDU model, serial, MAC

Screen-scraping CLI output is inherently best-effort across platforms and
versions: every parser is tolerant (a field it cannot find is simply None)
and never raises on odd input. The commands themselves are abbreviated in
the wild ("sh ver", "show cdp nei det"), so section lookup matches each
typed word as a prefix of the canonical command's word.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime

from app.services.neighbor_discovery import normalize_device_name

# --- snapshot sections -----------------------------------------------------------------

_COMMAND_BLOCK = re.compile(r"^! ---- (?P<command>.+?) ----$\n(?P<output>.*?)(?=^! ---- |\Z)", re.MULTILINE | re.DOTALL)

# Canonical commands the inventory reads, per built-in device type. Used
# both to find the right section in a snapshot and to tell the user which
# commands their type is not collecting yet (api/inventory.py).
INVENTORY_COMMANDS: dict[str, tuple[str, ...]] = {
    "cisco_ios": (
        "show version",
        "show inventory",
        "show cdp neighbors detail",
        "show lldp neighbors detail",
        "show mac address-table",
        "show ip arp",
    ),
    "cisco_nxos": (
        "show version",
        "show inventory",
        "show cdp neighbors detail",
        "show lldp neighbors detail",
        "show mac address-table",
        "show ip arp",
    ),
    "cisco_wlc_9800": (
        "show version",
        "show inventory",
        "show cdp neighbors detail",
        "show ap summary",
    ),
    "cisco_wlc": (
        "show sysinfo",
        "show inventory",
        "show cdp neighbors detail",
        "show ap summary",
    ),
    "fortinet": (
        "get system status",
        "get system arp",
    ),
    "apc_pdu": ("about",),
}

# Alternative spellings that mean the same section.
_ALIASES: dict[str, tuple[str, ...]] = {
    "show mac address-table": ("show mac-address-table", "show mac address-table dynamic", "show mac address-table | exclude CPU"),
    "show ip arp": ("show arp",),
    "show version": ("show ver",),
    "show ap summary": ("show ap config general",),
}


def _words(command: str) -> list[str]:
    head = command.split("|", 1)[0]
    return [w for w in head.strip().lower().split() if w]


def command_matches(typed: str, canonical: str) -> bool:
    """'sh cdp nei det' matches 'show cdp neighbors detail': same number of
    words, each typed word a prefix of the canonical one. Anything after a
    pipe on the typed side is ignored ('show ap config general | i MAC')."""
    typed_words = _words(typed)
    for candidate in (canonical, *_ALIASES.get(canonical, ())):
        canon_words = _words(candidate)
        if len(typed_words) != len(canon_words):
            continue
        if all(c.startswith(t) for t, c in zip(typed_words, canon_words)):
            return True
    return False


def sections(snapshot: str) -> list[tuple[str, str]]:
    """[(command as typed, output)] in snapshot order. A snapshot with no
    markers (a custom type that ran one command) is one unnamed section."""
    found = [(m.group("command").strip(), m.group("output")) for m in _COMMAND_BLOCK.finditer(snapshot)]
    if not found and snapshot.strip():
        return [("", snapshot)]
    return found


def find_section(snapshot: str, canonical: str) -> str | None:
    """Output of the first section whose command means `canonical`."""
    for typed, output in sections(snapshot):
        if typed and command_matches(typed, canonical):
            return output
    return None


def present_commands(snapshot: str, wanted: tuple[str, ...]) -> tuple[list[str], list[str]]:
    present = [c for c in wanted if find_section(snapshot, c) is not None]
    return present, [c for c in wanted if c not in present]


# --- normalisation helpers ---------------------------------------------------------------

_MAC_ANY = re.compile(r"(?i)\b(?:[0-9a-f]{4}\.[0-9a-f]{4}\.[0-9a-f]{4}|(?:[0-9a-f]{2}[:-]){5}[0-9a-f]{2}|(?:[0-9a-f]{2} ){5}[0-9a-f]{2})\b")
_MAC_DOTTED = r"[0-9a-fA-F]{4}\.[0-9a-fA-F]{4}\.[0-9a-fA-F]{4}"
_MAC_COLON = r"(?:[0-9a-fA-F]{2}[:-]){5}[0-9a-fA-F]{2}"
_MAC = rf"(?:{_MAC_DOTTED}|{_MAC_COLON})"
_IP = r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}"


def normalize_mac(raw: str | None) -> str | None:
    """Any common MAC spelling -> 'aabb.ccdd.eeff'."""
    if not raw:
        return None
    digits = re.sub(r"[^0-9a-fA-F]", "", raw)
    if len(digits) != 12:
        return None
    digits = digits.lower()
    return f"{digits[0:4]}.{digits[4:8]}.{digits[8:12]}"


def _first(pattern: str, text: str, flags: int = re.MULTILINE) -> str | None:
    m = re.search(pattern, text, flags)
    return m.group(1).strip() if m else None


# --- data ---------------------------------------------------------------------------------


@dataclass
class DeviceFacts:
    device_id: str
    name: str
    host: str
    device_type: str
    site: str | None
    role: str | None
    hostname: str | None = None
    model: str | None = None
    serial: str | None = None
    software: str | None = None
    uptime: str | None = None
    base_mac: str | None = None
    collected_at: datetime | None = None
    snapshot_id: str | None = None
    commands_present: list[str] = field(default_factory=list)
    commands_missing: list[str] = field(default_factory=list)
    has_snapshot: bool = False


@dataclass
class HardwareItem:
    device_id: str
    device_name: str
    name: str
    description: str | None
    pid: str | None
    vid: str | None
    serial: str | None


@dataclass
class Neighbor:
    device_id: str
    device_name: str
    local_port: str | None
    name: str | None
    ip: str | None
    platform: str | None
    capabilities: str | None
    remote_port: str | None
    protocol: str  # cdp | lldp
    managed_device_id: str | None = None
    managed_device_name: str | None = None


@dataclass
class AccessPoint:
    controller_id: str
    controller_name: str
    name: str
    model: str | None
    mac: str | None
    ip: str | None
    serial: str | None
    software: str | None


@dataclass
class Endpoint:
    device_id: str
    device_name: str
    port: str
    vlan: str | None
    mac: str
    ip: str | None
    entry_type: str | None
    on_uplink: bool  # the port also has a CDP/LLDP neighbor, so this MAC is probably behind another switch


@dataclass
class UnmanagedDevice:
    """Something CDP/LLDP can see that is not in the Devices list."""

    name: str | None
    ip: str | None
    platform: str | None
    capabilities: str | None
    kind: str
    seen_from: list[str]  # "SW01 Gi1/0/24"
    protocols: list[str]


@dataclass
class Inventory:
    generated_at: datetime
    devices: list[DeviceFacts]
    hardware: list[HardwareItem]
    neighbors: list[Neighbor]
    access_points: list[AccessPoint]
    endpoints: list[Endpoint]
    unmanaged: list[UnmanagedDevice]
    models: dict[str, int]  # model -> count across devices, APs and unmanaged neighbours' platforms


# --- parsers: show version and friends ------------------------------------------------------


def parse_show_version(output: str) -> dict[str, str | None]:
    """IOS, IOS-XE and NX-OS. Returns hostname/model/serial/software/uptime/base_mac."""
    facts: dict[str, str | None] = {"hostname": None, "model": None, "serial": None, "software": None, "uptime": None, "base_mac": None}
    m = re.search(r"^\s*Kernel uptime is (.+)$", output, re.MULTILINE)  # NX-OS
    if m:
        facts["uptime"] = m.group(1).strip()
        facts["hostname"] = _first(r"^\s*Device name:\s*(\S+)", output)
    else:
        m = re.search(r"^(\S+) uptime is (.+)$", output, re.MULTILINE)  # IOS / IOS-XE
        if m:
            facts["hostname"], facts["uptime"] = m.group(1), m.group(2).strip()

    facts["software"] = (
        # Prefer the classic "Cisco IOS Software [...], Version 17.9.4a" line
        # over the IOS XE banner's zero-padded "17.09.04a".
        _first(r"^Cisco IOS Software.*?,\s*Version\s+([^,\s\[]+)", output)
        or _first(r"^Cisco IOS(?: XE)? Software,\s*Version\s+([^,\s\[]+)", output)
        or _first(r"^\s*NXOS: version (\S+)", output)
        or _first(r"^\s*system:\s+version (\S+)", output)
        or _first(r"^\s*Cisco Nexus Operating System.*?Version\s+(\S+)", output)
    )
    facts["model"] = (
        _first(r"^\s*Model [Nn]umber\s*:\s*(\S+)", output)
        or _first(r"^\s*cisco (Nexus\S*\s+\S+) Chassis", output, re.MULTILINE | re.IGNORECASE)
        or _first(r"^\s*cisco (\S+) \([^)]*\) processor", output, re.MULTILINE | re.IGNORECASE)
        or _first(r"^\s*cisco (\S+) .*?with \d+K", output, re.MULTILINE | re.IGNORECASE)
    )
    facts["serial"] = (
        _first(r"^\s*System [Ss]erial [Nn]umber\s*:\s*(\S+)", output)
        or _first(r"^\s*Processor [Bb]oard ID\s+(\S+)", output)
    )
    facts["base_mac"] = normalize_mac(_first(r"^\s*Base [Ee]thernet MAC Address\s*:\s*(\S+)", output))
    return facts


def parse_show_sysinfo(output: str) -> dict[str, str | None]:
    """AireOS controllers."""
    return {
        "hostname": _first(r"^\s*System Name\.*\s*(\S+)", output),
        "model": _first(r"^\s*Product Name\.*\s*(.+)$", output),
        "serial": None,  # comes from show inventory
        "software": _first(r"^\s*Product Version\.*\s*(\S+)", output),
        "uptime": _first(r"^\s*System Up Time\.*\s*(.+)$", output),
        "base_mac": normalize_mac(_first(r"^\s*Burned-in MAC Address\.*\s*(\S+)", output)),
    }


def parse_fortigate_status(output: str) -> dict[str, str | None]:
    version = _first(r"^\s*Version:\s*(.+)$", output)
    model = version.split()[0] if version else None
    return {
        "hostname": _first(r"^\s*Hostname:\s*(\S+)", output),
        "model": model,
        "serial": _first(r"^\s*Serial-Number:\s*(\S+)", output),
        "software": _first(r"^\s*Version:\s*\S+\s+(v\S+)", output) or version,
        "uptime": None,
        "base_mac": None,
    }


def parse_apc_about(output: str) -> dict[str, str | None]:
    return {
        "hostname": _first(r"^\s*Name\s*:\s*(.+)$", output),
        "model": _first(r"^\s*Model Number\s*:\s*(\S+)", output),
        "serial": _first(r"^\s*Serial Number\s*:\s*(\S+)", output),
        "software": _first(r"^\s*Application Module.*?Version\s*:\s*(\S+)", output, re.MULTILINE | re.DOTALL),
        "uptime": _first(r"^\s*Management Uptime\s*:\s*(.+)$", output),
        "base_mac": normalize_mac(_first(r"^\s*MAC Address\s*:\s*([0-9A-Fa-f: .-]+?)\s*$", output)),
    }


_INVENTORY_ITEM = re.compile(
    r'NAME:\s*"(?P<name>[^"]*)"\s*,\s*DESCR:\s*"(?P<descr>[^"]*)"\s*\n\s*PID:\s*(?P<pid>[^,\n]*?)\s*,\s*VID:\s*(?P<vid>[^,\n]*?)\s*,\s*SN:\s*(?P<sn>[^\s,]*)',
    re.MULTILINE,
)


def parse_show_inventory(output: str) -> list[dict[str, str | None]]:
    """IOS/IOS-XE/NX-OS/AireOS 'show inventory' (all use the NAME/DESCR/PID/VID/SN layout)."""
    items = []
    for m in _INVENTORY_ITEM.finditer(output):
        items.append(
            {
                "name": m.group("name").strip(),
                "description": m.group("descr").strip() or None,
                "pid": m.group("pid").strip() or None,
                "vid": m.group("vid").strip() or None,
                "serial": m.group("sn").strip() or None,
            }
        )
    return items


# --- parsers: neighbours --------------------------------------------------------------------


def parse_cdp_detail(output: str) -> list[dict[str, str | None]]:
    starts = [m.start() for m in re.finditer(r"^Device ID:", output, re.MULTILINE)]
    neighbors = []
    for i, start in enumerate(starts):
        block = output[start : starts[i + 1] if i + 1 < len(starts) else len(output)]
        platform_caps = re.search(r"Platform:\s*(.+?),\s*Capabilities:\s*(.+?)\s*$", block, re.MULTILINE)
        ports = re.search(r"Interface:\s*(\S+?),?\s+Port ID \(outgoing port\):\s*(\S+)", block)
        neighbors.append(
            {
                "name": _first(r"^Device ID:\s*(\S+)", block),
                "ip": _first(r"IP(?:v4)? [Aa]ddress:\s*(" + _IP + ")", block),
                "platform": platform_caps.group(1).strip() if platform_caps else None,
                "capabilities": platform_caps.group(2).strip() if platform_caps else None,
                "local_port": ports.group(1) if ports else None,
                "remote_port": ports.group(2) if ports else None,
            }
        )
    return neighbors


def parse_lldp_detail(output: str) -> list[dict[str, str | None]]:
    """IOS ('Local Intf:', 'Management Addresses:\\n IP:') and NX-OS
    ('Local Port id:', 'Management Address:')."""
    starts = [m.start() for m in re.finditer(r"^Chassis id:", output, re.MULTILINE)]
    neighbors = []
    for i, start in enumerate(starts):
        block = output[start : starts[i + 1] if i + 1 < len(starts) else len(output)]
        description = _first(r"^System Description:\s*\n?\s*(.+)$", block)
        name = _first(r"^System Name:\s*(\S+)", block)
        if name and name.lower() == "not advertised":
            name = None
        neighbors.append(
            {
                "name": name,
                "ip": _first(r"^\s*(?:Management Address(?:es)?:\s*\n?\s*(?:IP:\s*)?|IP:\s*)(" + _IP + ")", block),
                "platform": description,
                "capabilities": _first(r"^System Capabilities:\s*(.+)$", block),
                "local_port": _first(r"^Local (?:Intf|Port id):\s*(\S+)", block),
                "remote_port": _first(r"^Port id:\s*(\S+)", block),
                "chassis_id": _first(r"^Chassis id:\s*(\S+)", block),
            }
        )
    return neighbors


# --- parsers: tables ----------------------------------------------------------------------

_MAC_ROW = re.compile(
    r"^[\s*+GR]*(?P<vlan>\d+|All|-)\s+(?P<mac>" + _MAC + r")\s+(?P<type>\S+)\s+(?:.*?\s)?(?P<port>\S+)\s*$",
    re.MULTILINE | re.IGNORECASE,
)
_NOT_A_PORT = {"cpu", "sup-eth1", "sup-eth1(r)", "router", "switch", "drop", "vpc", "peer-link", "(r)", "self", "static"}


def parse_mac_table(output: str) -> list[dict[str, str | None]]:
    rows = []
    for m in _MAC_ROW.finditer(output):
        port = m.group("port")
        if port.lower() in _NOT_A_PORT or m.group("vlan").lower() == "all":
            continue
        mac = normalize_mac(m.group("mac"))
        if not mac or mac.startswith("0100.5e") or mac.startswith("0180.c2") or mac == "ffff.ffff.ffff":
            continue  # multicast / STP / broadcast entries are not hardware
        rows.append({"vlan": m.group("vlan"), "mac": mac, "type": m.group("type").lower(), "port": port})
    return rows


_ARP_ROW = re.compile(
    r"^\s*(?:Internet\s+)?(?P<ip>" + _IP + r")\s+(?P<age>\S+)\s+(?P<mac>" + _MAC + r")\s+(?:ARPA\s+)?(?P<intf>\S+)",
    re.MULTILINE | re.IGNORECASE,
)


def parse_arp(output: str) -> list[dict[str, str | None]]:
    """IOS, NX-OS and FortiGate ARP tables -> ip/mac/interface."""
    rows = []
    for m in _ARP_ROW.finditer(output):
        mac = normalize_mac(m.group("mac"))
        if mac:
            rows.append({"ip": m.group("ip"), "mac": mac, "interface": m.group("intf"), "age": m.group("age")})
    return rows


# --- parsers: access points -----------------------------------------------------------------

_AP_SUMMARY_ROW = re.compile(
    r"^(?P<name>\S+)\s+\d+\s+(?P<model>\S+)\s+(?P<mac>" + _MAC + r")\s+.*?(?P<ip>" + _IP + r")",
    re.MULTILINE | re.IGNORECASE,
)


def parse_ap_summary(output: str) -> list[dict[str, str | None]]:
    """'show ap summary' (AireOS and 9800): AP Name, Slots, AP Model, Ethernet MAC, Location, Country, IP Address, State."""
    return [
        {"name": m.group("name"), "model": m.group("model"), "mac": normalize_mac(m.group("mac")), "ip": m.group("ip"), "serial": None, "software": None}
        for m in _AP_SUMMARY_ROW.finditer(output)
    ]


def parse_ap_config_general(output: str) -> list[dict[str, str | None]]:
    """'show ap config general' (optionally filtered with '| i ...'): one
    block per AP starting at 'Cisco AP Name'."""
    starts = [m.start() for m in re.finditer(r"^\s*Cisco AP Name\s*:", output, re.MULTILINE)]
    aps = []
    for i, start in enumerate(starts):
        block = output[start : starts[i + 1] if i + 1 < len(starts) else len(output)]
        name = _first(r"^\s*Cisco AP Name\s*:\s*(\S+)", block)
        if not name:
            continue
        aps.append(
            {
                "name": name,
                "model": _first(r"^\s*AP Model\s*:\s*(\S+)", block),
                "mac": normalize_mac(_first(r"^\s*MAC Address\s*:\s*(\S+)", block)),
                "ip": _first(r"^\s*IP Address\s*:\s*(" + _IP + ")", block),
                "serial": _first(r"^\s*AP Serial Number\s*:\s*(\S+)", block),
                "software": _first(r"^\s*(?:AP )?IOS Version\s*:\s*(\S+)", block),
            }
        )
    return aps


# --- assembling the inventory -----------------------------------------------------------------


def guess_kind(platform: str | None, capabilities: str | None, name: str | None = None) -> str:
    text = f"{platform or ''} {capabilities or ''} {name or ''}".lower()
    if "phone" in text:
        return "IP phone"
    if any(k in text for k in ("air-", "c91", "c92", "c93", "ap ", "access point", "aironet", "trans-bridge")) and "switch" not in (platform or "").lower():
        if "trans-bridge" in text or "air" in text or "ap" in text:
            return "Access point"
    if "wlc" in text or "wireless lan controller" in text or "c9800" in text or "air-ct" in text:
        return "Wireless controller"
    if "fortigate" in text or "firewall" in text or "asa" in text or "palo" in text:
        return "Firewall"
    if "router" in text and "switch" not in text:
        return "Router"
    if "switch" in text or "bridge" in text or "ws-c" in text or "c9300" in text or "c9200" in text or "nexus" in text:
        return "Switch"
    if "host" in text or "station" in text or "server" in text or "vmware" in text or "linux" in text or "windows" in text:
        return "Host"
    if "camera" in text or "printer" in text:
        return "Device"
    return "Other"


_INFRASTRUCTURE_KINDS = {"Switch", "Router", "Wireless controller", "Firewall"}


@dataclass
class SnapshotInput:
    """What build_inventory needs per device (keeps it free of ORM types)."""

    device_id: str
    name: str
    host: str
    device_type: str
    site: str | None
    role: str | None
    content: str | None  # latest snapshot, None if never collected
    collected_at: datetime | None = None
    snapshot_id: str | None = None


def _identity_facts(device_type: str, content: str) -> dict[str, str | None]:
    if device_type == "fortinet":
        output = find_section(content, "get system status")
        return parse_fortigate_status(output) if output else {}
    if device_type == "apc_pdu":
        output = find_section(content, "about")
        return parse_apc_about(output) if output else {}
    if device_type == "cisco_wlc":
        output = find_section(content, "show sysinfo")
        return parse_show_sysinfo(output) if output else {}
    output = find_section(content, "show version")
    return parse_show_version(output) if output else {}


def build_inventory(inputs: list[SnapshotInput], *, now: datetime | None = None) -> Inventory:
    now = now or datetime.utcnow()
    devices: list[DeviceFacts] = []
    hardware: list[HardwareItem] = []
    neighbors: list[Neighbor] = []
    access_points: list[AccessPoint] = []
    mac_rows: list[tuple[SnapshotInput, dict]] = []
    arp_ip_by_mac: dict[str, str] = {}
    uplink_ports: dict[str, set[str]] = defaultdict(set)

    for item in inputs:
        wanted = INVENTORY_COMMANDS.get(item.device_type, ("show version",))
        facts = DeviceFacts(item.device_id, item.name, item.host, item.device_type, item.site, item.role)
        if item.content:
            facts.has_snapshot = True
            facts.collected_at = item.collected_at
            facts.snapshot_id = item.snapshot_id
            facts.commands_present, facts.commands_missing = present_commands(item.content, wanted)
            identity = _identity_facts(item.device_type, item.content)
            for key in ("hostname", "model", "serial", "software", "uptime", "base_mac"):
                setattr(facts, key, identity.get(key))

            inventory_output = find_section(item.content, "show inventory")
            if inventory_output:
                parsed = parse_show_inventory(inventory_output)
                for entry in parsed:
                    hardware.append(HardwareItem(item.device_id, item.name, entry["name"] or "", entry["description"], entry["pid"], entry["vid"], entry["serial"]))
                if parsed and not facts.serial:
                    facts.serial = parsed[0]["serial"]
                if parsed and not facts.model:
                    facts.model = parsed[0]["pid"]

            cdp = find_section(item.content, "show cdp neighbors detail")
            if cdp:
                for n in parse_cdp_detail(cdp):
                    neighbors.append(Neighbor(item.device_id, item.name, n["local_port"], n["name"], n["ip"], n["platform"], n["capabilities"], n["remote_port"], "cdp"))
            lldp = find_section(item.content, "show lldp neighbors detail")
            if lldp:
                for n in parse_lldp_detail(lldp):
                    neighbors.append(
                        Neighbor(item.device_id, item.name, n["local_port"], n["name"] or n.get("chassis_id"), n["ip"], n["platform"], n["capabilities"], n["remote_port"], "lldp")
                    )

            ap_output = find_section(item.content, "show ap summary")
            if ap_output:
                parsed_aps = parse_ap_config_general(ap_output) if "Cisco AP Name" in ap_output else parse_ap_summary(ap_output)
                for ap in parsed_aps:
                    access_points.append(AccessPoint(item.device_id, item.name, ap["name"] or "", ap["model"], ap["mac"], ap["ip"], ap["serial"], ap["software"]))

            mac_output = find_section(item.content, "show mac address-table")
            if mac_output:
                for row in parse_mac_table(mac_output):
                    mac_rows.append((item, row))
            arp_output = find_section(item.content, "show ip arp") or (find_section(item.content, "get system arp") if item.device_type == "fortinet" else None)
            if arp_output:
                for row in parse_arp(arp_output):
                    arp_ip_by_mac.setdefault(row["mac"], row["ip"])
        else:
            facts.commands_missing = list(wanted)
        devices.append(facts)

    # Which neighbours are devices we already manage? Match on name, the
    # hostname the device reported about itself, or its management IP.
    by_name: dict[str, DeviceFacts] = {}
    by_ip: dict[str, DeviceFacts] = {}
    for d in devices:
        by_name.setdefault(normalize_device_name(d.name), d)
        if d.hostname:
            by_name.setdefault(normalize_device_name(d.hostname), d)
        by_ip.setdefault(d.host, d)
    for n in neighbors:
        match = None
        if n.name:
            match = by_name.get(normalize_device_name(n.name))
        if match is None and n.ip:
            match = by_ip.get(n.ip)
        if match is not None:
            n.managed_device_id, n.managed_device_name = match.device_id, match.name
        # A port leading to another switch/router (managed or not) carries
        # that device's whole MAC table; a phone or a server on a port is an
        # endpoint in its own right, so those ports are not uplinks.
        if n.local_port and (match is not None or guess_kind(n.platform, n.capabilities, n.name) in _INFRASTRUCTURE_KINDS):
            uplink_ports[n.device_id].add(_short_port(n.local_port))

    endpoints: list[Endpoint] = []
    seen: set[tuple[str, str, str]] = set()
    for item, row in mac_rows:
        key = (item.device_id, row["mac"], row["port"])
        if key in seen:
            continue
        seen.add(key)
        endpoints.append(
            Endpoint(
                item.device_id,
                item.name,
                row["port"],
                row["vlan"],
                row["mac"],
                arp_ip_by_mac.get(row["mac"]),
                row["type"],
                _short_port(row["port"]) in uplink_ports.get(item.device_id, set()),
            )
        )

    unmanaged_map: dict[str, UnmanagedDevice] = {}
    for n in neighbors:
        if n.managed_device_id or (not n.name and not n.ip):
            continue
        key = normalize_device_name(n.name) if n.name else f"ip:{n.ip}"
        entry = unmanaged_map.get(key)
        if entry is None:
            entry = UnmanagedDevice(n.name, n.ip, n.platform, n.capabilities, guess_kind(n.platform, n.capabilities, n.name), [], [])
            unmanaged_map[key] = entry
        entry.ip = entry.ip or n.ip
        entry.platform = entry.platform or n.platform
        entry.capabilities = entry.capabilities or n.capabilities
        if entry.kind == "Other":
            entry.kind = guess_kind(entry.platform, entry.capabilities, entry.name)
        where = f"{n.device_name} {n.local_port or '?'}"
        if where not in entry.seen_from:
            entry.seen_from.append(where)
        if n.protocol not in entry.protocols:
            entry.protocols.append(n.protocol)
    unmanaged = sorted(unmanaged_map.values(), key=lambda u: (u.kind, (u.name or u.ip or "").lower()))

    models: dict[str, int] = defaultdict(int)
    for d in devices:
        if d.model:
            models[d.model] += 1
    for ap in access_points:
        if ap.model:
            models[ap.model] += 1
    for u in unmanaged:
        if u.platform:
            models[u.platform] += 1

    return Inventory(now, devices, hardware, neighbors, access_points, endpoints, unmanaged, dict(sorted(models.items(), key=lambda kv: (-kv[1], kv[0]))))


_PORT_ABBREV = (
    ("tengigabitethernet", "te"),
    ("twentyfivegige", "twe"),
    ("fortygigabitethernet", "fo"),
    ("hundredgige", "hu"),
    ("gigabitethernet", "gi"),
    ("fastethernet", "fa"),
    ("ethernet", "eth"),
    ("port-channel", "po"),
    ("mgmt", "mgmt"),
)


def _short_port(port: str) -> str:
    """'GigabitEthernet1/0/24' and 'Gi1/0/24' compare equal."""
    p = port.strip().lower()
    for long, short in _PORT_ABBREV:
        if p.startswith(long):
            return short + p[len(long) :]
    return p
