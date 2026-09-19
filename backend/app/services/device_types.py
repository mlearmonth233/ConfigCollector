"""Registry mapping our device_type key to a Netmiko driver + the default
command(s) that capture a device's running configuration.

Netmiko (https://github.com/ktbyers/netmiko) already speaks the CLI dialect
of dozens of vendors over SSH/Telnet, so we lean on its driver names rather
than reimplementing per-vendor logic. Devices with no dedicated driver (most
PDUs, some console servers) fall back to "generic_termserver" - a bare
driver with no vendor-specific prompt handling - and the user is expected to
supply the exact show command via Device.custom_commands.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class DeviceTypeSpec:
    label: str
    category: str
    netmiko_driver: str
    default_commands: tuple[str, ...]
    secret_supported: bool = True  # whether an "enable secret" concept applies
    # How command output is read. None = decide from the driver/category
    # (see uses_timing_read); a custom type sets this explicitly since the
    # app can't know how a driver it has never met behaves.
    timing_read: bool | None = None
    # True for an org-defined type (see build_catalog), False for a
    # built-in one from DEVICE_TYPE_REGISTRY.
    custom: bool = False

    @property
    def uses_timing_read(self) -> bool:
        """Whether commands run with Netmiko's timing-based read
        (send_command_timing) instead of the prompt-pattern read. See the
        long comment in collector.collect_device_config for why the
        generic driver and WLCs need it."""
        if self.timing_read is not None:
            return self.timing_read
        return self.netmiko_driver == "generic_termserver" or self.category == "wlc"

    @classmethod
    def from_custom(cls, row) -> "DeviceTypeSpec":
        """A spec built from an org's CustomDeviceType row (duck-typed so
        this module needs no import of the model layer)."""
        return cls(
            label=row.label,
            category=row.category,
            netmiko_driver=row.netmiko_driver,
            default_commands=tuple(parse_command_list(row.default_commands or "")),
            secret_supported=bool(row.secret_supported),
            timing_read=bool(row.timing_read),
            custom=True,
        )


# A full audit-style command set for Cisco IOS/NX-OS switches - interfaces,
# neighbors, VLANs/port-channels, routing (static/EIGRP/OSPF/BGP/multicast),
# QoS, and hardware/environmentals. Some commands (e.g. "show vpc brief")
# are NX-OS-specific and will just error harmlessly on plain IOS, and vice
# versa - this is a deliberately broad "gather everything" set.
_CISCO_IOS_COMMANDS: tuple[str, ...] = (
    "term len 0",
    "sh clock",
    "show ver",
    "show run",
    "show cdp neighbor",
    "show cdp neighbor detail",
    "show vlan",
    "show vpc brief",
    "show port-channel sum",
    "show etherch sum",
    "show int trunk",
    "show int status",
    "show int brief",
    "show ip int brief",
    "show int",
    "show mac address",
    "show mac-address",
    "show ip arp",
    "show ip route summ",
    "show ip route 0.0.0.0",
    "show ip eigrp neigh",
    "show ip eigrp int",
    "show ip ospf neigh",
    "show ip ospf int",
    "show ip bgp summ",
    "show ip bgp neighbor",
    "show ip bgp neighbor detail",
    "show ip bgp 0.0.0.0/0",
    "show ip mroute",
    "show ip pim int brief",
    "show ip pim int",
    "show class-map",
    "show policy-map",
    "show policy-map interface",
    "show inventory",
    "show module",
    "show power",
    "show environment all",
    "sh ip igmp snooping mrouter",
    "show version | i uptime",
    "sho power inline",
    "sh environment temperature status",
    "sh lldp nei",
    "sh lldp nei detail",
    "sh ip igmp snooping groups",
    "sh ip igmp snooping querier",
    "sh ptp parent",
    "sh ptp clock",
    "sh ptp port",
)

# AireOS WLC (cisco_wlc driver). Deliberately doesn't include a
# paging-disable command: Netmiko's own driver already disables paging
# automatically during connection setup (cisco_wlc runs "config paging
# disable" itself in session_preparation()) - sending it again here as a
# first "real" command doesn't just duplicate that, it actively confused
# Netmiko's prompt detection for the command right after it (the device's
# second response to the same command isn't shaped like its first, so the
# auto-detected prompt for the next send_command() call keyed off it
# instead of the actual CLI prompt), breaking that command with "Pattern
# not detected" errors back when this used pattern-based reads - see
# collector.py's use_timing_read.
_CISCO_AIREOS_WLC_COMMANDS: tuple[str, ...] = (
    "show ap summary",
    "show ap stats ethernet summary",
    "show cdp neighbors detail",
    "show lldp neighbors",
    "show advanced 802.11a summary",
    "show advanced 802.11b summary",
    "show wlan apgroups",
    "show sysinfo",
    "show inventory",
    "show redundancy summary",
    "show interface detailed management",
    "show run-config",
    "show wlan summary",
    "show interface summary",
    # Wireless clients: every client with its AP, WLAN, protocol and auth
    # state, then the same list keyed by IP address (8.x).
    "show client summary",
    "show client summary ip",
)

# Catalyst 9800 WLC (cisco_xe driver, IOS-XE). Distinct AP-inventory-style
# commands from AireOS above - the "term len 0" here is redundant with the
# cisco_xe driver's own paging disable during session_preparation(), but
# unlike AireOS this doesn't risk the prompt-detection issue described
# above since WLCs already use timing-based reads (collector.py's
# use_timing_read), not pattern-based ones.
_CISCO_WLC_9800_COMMANDS: tuple[str, ...] = (
    "term len 0",
    "sh ap config general | i MAC Address|IP Address|AP Model|IOS Version|AP Serial Number|Cisco AP Name",
    "show inventory",
    "show vers",
    "sh cdp nei",
    "sh cdp nei detail",
    "show ap summary",
    "show ap ethernet statistics",
    "show wlan summary",
    "show interface summary",
    "show running-config",
    # Wireless clients. IOS-XE wireless syntax ("show wireless ..."): the
    # AireOS "show client summary" does not exist on a 9800. The summary
    # lists every client with its AP, WLAN, state, protocol and auth
    # method; the device-tracking database gives each client's IP address.
    "show wireless client summary",
    "show wireless device-tracking database ip",
)

# Nexus (NX-OS). Paging is disabled by the driver. The running config plus
# what the Inventory page reads: identity, components, neighbours, MAC and
# ARP tables, and the interface/VLAN state useful in a backup.
_CISCO_NXOS_COMMANDS: tuple[str, ...] = (
    "show version",
    "show inventory",
    "show cdp neighbors detail",
    "show lldp neighbors detail",
    "show interface status",
    "show vlan brief",
    "show port-channel summary",
    "show vpc",
    "show mac address-table",
    "show ip arp",
    "show ip interface brief vrf all",
    "show running-config",
)

# VDOM-enabled FortiGates need "config vdom" / "edit root" before the
# per-vdom "get"/"show" commands actually reflect that vdom's data rather
# than the global admin domain - the leading "end" is defensive, making
# sure the session starts from the top-level prompt regardless of context.
_FORTINET_COMMANDS: tuple[str, ...] = (
    "end",
    "config vdom",
    "edit root",
    "get system arp",
    "get system status",
    "show full-configuration",
)

# APC's NMC CLI: most of these are section names (typing one alone prints
# that section's config), not "show" commands.
_APC_PDU_COMMANDS: tuple[str, ...] = (
    "about",
    "user",
    "web",
    "console",
    "ftp",
    "ntp",
    "snmp",
    "snmpv3",
    "system",
    "tcpip",
    "date",
    "olStatus all",
)

# Versa Networks SD-WAN routers (VOS - Versa Operating System), typically
# deployed on Dell-branded appliances. Netmiko has no dedicated driver for
# these, and Versa's Junos-inspired CLI hasn't been verified against this
# app's pattern-based reads, so "generic_termserver" is used the same way
# as apc_pdu above - no vendor-specific paging-disable command is sent
# (unlike Cisco's "term len 0"), since no Versa equivalent could be
# confirmed and guessing wrong risks producing more harm (a hung read
# waiting on a paginated prompt) than leaving it out. Confirm/adjust these
# against a real device before relying on them.
_VERSA_COMMANDS: tuple[str, ...] = (
    "show configuration",
    "show configuration | details | display set",
    "show system status",
    "show system detail",
    "show interfaces detail",
)


# Trimmed down to exactly what this org actually runs: Cisco switches, both
# WLC generations, FortiGate firewalls, APC PDUs, and Versa SD-WAN routers.
# Previously also had cisco_xe, hp_procurve, juniper_junos, arista_eos,
# linux, pdu_generic, console_server, opengear, paloalto_panos, cisco_asa,
# and juniper_srx - removed since nothing in this org's environment used
# them, simplifying the Commands page and the "Add device" type dropdown
# down to real choices. Add a type back here (mapping to a Netmiko driver
# name) if a device from one of those vendors shows up.
DEVICE_TYPE_REGISTRY: dict[str, DeviceTypeSpec] = {
    # --- Switches / routers ---
    "cisco_ios": DeviceTypeSpec("Cisco IOS Switch/Router", "switch", "cisco_ios", _CISCO_IOS_COMMANDS),
    "cisco_nxos": DeviceTypeSpec(
        "Cisco Nexus (NX-OS)", "switch", "cisco_nxos", _CISCO_NXOS_COMMANDS
    ),
    "versa": DeviceTypeSpec(
        "Versa SD-WAN Router (VOS)", "router", "generic_termserver", _VERSA_COMMANDS, secret_supported=False
    ),
    # --- Wireless LAN controllers ---
    "cisco_wlc": DeviceTypeSpec(
        "Cisco AireOS WLC", "wlc", "cisco_wlc", _CISCO_AIREOS_WLC_COMMANDS, secret_supported=False
    ),
    "cisco_wlc_9800": DeviceTypeSpec(
        "Cisco Catalyst 9800 WLC (IOS-XE)", "wlc", "cisco_xe", _CISCO_WLC_9800_COMMANDS
    ),
    # --- Firewalls ---
    "fortinet": DeviceTypeSpec(
        "Fortinet FortiGate", "firewall", "fortinet", _FORTINET_COMMANDS, secret_supported=False
    ),
    # --- Power distribution units ---
    "apc_pdu": DeviceTypeSpec(
        "APC Switched PDU", "pdu", "generic_termserver", _APC_PDU_COMMANDS, secret_supported=False
    ),
}


Catalog = Mapping[str, DeviceTypeSpec]


def build_catalog(custom_types: Iterable = ()) -> dict[str, DeviceTypeSpec]:
    """Every device type an org can use: the built-in registry plus the
    org's own CustomDeviceType rows (which may not shadow a built-in key -
    the API enforces that). Callers that resolve a device's type (job
    creation, the worker, the device-types listing) build one of these per
    request/task rather than reaching for DEVICE_TYPE_REGISTRY directly, so
    a custom type behaves exactly like a built-in one everywhere."""
    catalog = dict(DEVICE_TYPE_REGISTRY)
    for row in custom_types:
        if row.key not in DEVICE_TYPE_REGISTRY:
            catalog[row.key] = DeviceTypeSpec.from_custom(row)
    return catalog


def get_device_type_spec(device_type: str, catalog: Catalog | None = None) -> DeviceTypeSpec:
    try:
        return (catalog if catalog is not None else DEVICE_TYPE_REGISTRY)[device_type]
    except KeyError as exc:
        raise ValueError(f"Unknown device_type '{device_type}'") from exc


def list_netmiko_drivers() -> list[str]:
    """The SSH platform names Netmiko can drive - what a custom device type
    picks from. Telnet/serial variants are left out (the app only speaks
    SSH), as are Netmiko's internal aliases."""
    from netmiko.ssh_dispatcher import CLASS_MAPPER_BASE

    return sorted(k for k in CLASS_MAPPER_BASE if not k.endswith(("_telnet", "_serial")))


def parse_command_list(raw: str) -> list[str]:
    """Splits a comma-separated command string (as stored in
    Device.custom_commands, or supplied per-run via a job's
    commands_by_device_type) into individual commands."""
    return [c.strip() for c in raw.split(",") if c.strip()]


def resolve_commands(device_type: str, custom_commands: str | None, catalog: Catalog | None = None) -> list[str]:
    """custom_commands, when set on the Device, is a comma-separated override
    of the type's default_commands (built-in registry or, via `catalog`, an
    org's custom type)."""
    if custom_commands:
        return parse_command_list(custom_commands)
    spec = get_device_type_spec(device_type, catalog)
    if not spec.default_commands:
        raise ValueError(
            f"Device type '{device_type}' has no default command - set custom_commands on the device"
        )
    return list(spec.default_commands)
