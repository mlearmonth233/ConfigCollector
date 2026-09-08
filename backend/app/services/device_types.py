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
        "Cisco IOS Switch/Router", "switch", "cisco_ios", ("show running-config",)
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


def get_device_type_spec(device_type: str) -> DeviceTypeSpec:
    try:
        return DEVICE_TYPE_REGISTRY[device_type]
    except KeyError as exc:
        raise ValueError(f"Unknown device_type '{device_type}'") from exc


def resolve_commands(device_type: str, custom_commands: str | None) -> list[str]:
    """custom_commands, when set on the Device, is a comma-separated override
    of the registry's default_commands."""
    if custom_commands:
        return [c.strip() for c in custom_commands.split(",") if c.strip()]
    spec = get_device_type_spec(device_type)
    if not spec.default_commands:
        raise ValueError(
            f"Device type '{device_type}' has no default command - set custom_commands on the device"
        )
    return list(spec.default_commands)
