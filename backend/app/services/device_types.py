"""Registry mapping our device_type key to a Netmiko driver + the default
command(s) that capture a device's running configuration.

Netmiko (https://github.com/ktbyers/netmiko) already speaks the CLI dialect
of dozens of vendors over SSH/Telnet, so we lean on its driver names rather
than reimplementing per-vendor logic. Devices with no dedicated driver (most
PDUs, some console servers) fall back to "generic_termserver" - a bare
driver with no vendor-specific prompt handling - and the user is expected to
supply the exact show command via Device.custom_commands.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class DeviceTypeSpec:
    label: str
    category: str
    netmiko_driver: str
    default_commands: tuple[str, ...]
    secret_supported: bool = True  # whether an "enable secret" concept applies


DEVICE_TYPE_REGISTRY: dict[str, DeviceTypeSpec] = {
    # --- Switches / routers ---
    "cisco_ios": DeviceTypeSpec(
        "Cisco IOS Switch/Router", "switch", "cisco_ios", ("show tech-support",)
    ),
    "cisco_xe": DeviceTypeSpec(
        "Cisco IOS-XE Switch/Router", "switch", "cisco_xe", ("show running-config",)
    ),
    "cisco_nxos": DeviceTypeSpec(
        "Cisco Nexus (NX-OS)", "switch", "cisco_nxos", ("show running-config",)
    ),
    "arista_eos": DeviceTypeSpec(
        "Arista EOS Switch", "switch", "arista_eos", ("show running-config",)
    ),
    "juniper_junos": DeviceTypeSpec(
        "Juniper Junos", "switch", "juniper_junos", ("show configuration | display set",)
    ),
    "hp_procurve": DeviceTypeSpec(
        "HP/Aruba ProCurve Switch", "switch", "hp_procurve", ("show running-config",)
    ),
    # --- Wireless LAN controllers ---
    "cisco_wlc": DeviceTypeSpec(
        "Cisco AireOS WLC", "wlc", "cisco_wlc", ("show run-config",), secret_supported=False
    ),
    "cisco_wlc_9800": DeviceTypeSpec(
        "Cisco Catalyst 9800 WLC (IOS-XE)", "wlc", "cisco_xe", ("show running-config",)
    ),
    # --- Firewalls ---
    "cisco_asa": DeviceTypeSpec(
        "Cisco ASA Firewall", "firewall", "cisco_asa", ("show running-config",)
    ),
    "paloalto_panos": DeviceTypeSpec(
        "Palo Alto PAN-OS Firewall", "firewall", "paloalto_panos", ("show config running",), secret_supported=False
    ),
    "fortinet": DeviceTypeSpec(
        "Fortinet FortiGate", "firewall", "fortinet", ("show full-configuration",), secret_supported=False
    ),
    "juniper_srx": DeviceTypeSpec(
        "Juniper SRX Firewall", "firewall", "juniper_junos", ("show configuration | display set",)
    ),
    # --- Power distribution units ---
    "pdu_generic": DeviceTypeSpec(
        "Generic PDU (SSH CLI)",
        "pdu",
        "generic_termserver",
        (),  # no universal command - vendor CLIs vary too much; require custom_commands
        secret_supported=False,
    ),
    "apc_pdu": DeviceTypeSpec(
        "APC Switched PDU", "pdu", "generic_termserver", ("about",), secret_supported=False
    ),
    # --- Console / terminal servers ---
    "console_server": DeviceTypeSpec(
        "Generic Console/Terminal Server",
        "console_server",
        "generic_termserver",
        (),  # vendor-specific - require custom_commands
        secret_supported=False,
    ),
    "opengear": DeviceTypeSpec(
        "Opengear Console Server", "console_server", "generic_termserver", ("show configuration",), secret_supported=False
    ),
    # --- Generic Linux host (jump boxes, appliances) ---
    "linux": DeviceTypeSpec(
        "Generic Linux Host", "server", "linux", ("cat /etc/network/interfaces",), secret_supported=False
    ),
}


# Common "show" (read-only) commands offered as checkboxes on the Commands
# page, grouped by category rather than exact device type since most vendors
# within a category share enough CLI syntax for these to be useful starting
# points - the user can always add anything else as free text. Cisco IOS
# defaults to "show tech-support" (it bundles most of these plus platform
# diagnostics), but every category's default is still listed here too so a
# user can add it back alongside, or instead of, the default.
CATEGORY_SUGGESTED_COMMANDS: dict[str, tuple[str, ...]] = {
    "switch": (
        "show tech-support",
        "show running-config",
        "show version",
        "show inventory",
        "show interfaces status",
        "show ip interface brief",
        "show vlan brief",
        "show mac address-table",
        "show cdp neighbors detail",
        "show spanning-tree",
        "show ip route",
    ),
    "wlc": (
        "show run-config",
        "show tech-support",
        "show ap summary",
        "show wlan summary",
        "show client summary",
    ),
    "firewall": (
        "show running-config",
        "show tech-support",
        "show version",
        "show interface",
        "show route",
        "show nat",
    ),
    "pdu": (
        "about",
        "show status",
        "show inlet",
        "show outlets",
    ),
    "console_server": (
        "show configuration",
        "show version",
        "show ports",
    ),
    "server": (
        "cat /etc/network/interfaces",
        "uname -a",
        "df -h",
    ),
}


def get_suggested_commands(category: str) -> tuple[str, ...]:
    return CATEGORY_SUGGESTED_COMMANDS.get(category, ())


def get_device_type_spec(device_type: str) -> DeviceTypeSpec:
    try:
        return DEVICE_TYPE_REGISTRY[device_type]
    except KeyError as exc:
        raise ValueError(f"Unknown device_type '{device_type}'") from exc


def parse_command_list(raw: str) -> list[str]:
    """Splits a comma-separated command string (as stored in
    Device.custom_commands, or supplied per-run via a job's
    commands_by_device_type) into individual commands."""
    return [c.strip() for c in raw.split(",") if c.strip()]


def resolve_commands(device_type: str, custom_commands: str | None) -> list[str]:
    """custom_commands, when set on the Device, is a comma-separated override
    of the registry's default_commands."""
    if custom_commands:
        return parse_command_list(custom_commands)
    spec = get_device_type_spec(device_type)
    if not spec.default_commands:
        raise ValueError(
            f"Device type '{device_type}' has no default command - set custom_commands on the device"
        )
    return list(spec.default_commands)
