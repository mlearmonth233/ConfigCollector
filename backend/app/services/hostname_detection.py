"""Best-effort classification of a device from its name, based on a site
naming convention: short role codes and an IT/OT zone marker embedded as
substrings (e.g. "GBGYSP01SWA001" = ... "P0" (IT) ... "SWA" (access
switch) ...). Always just a starting guess for the "Add device"
form/CSV import to pre-fill - every value it suggests stays fully
user-editable, and an explicitly supplied value always wins over a
detected one.
"""

from dataclasses import dataclass

from app.models.device import NetworkZone

DEVICE_ROLES: dict[str, str] = {
    "access_switch": "Access switch",
    "server_switch": "Server switch",
    "core_switch": "Core switch",
    "distribution_switch": "Distribution switch",
    "wlc": "Wireless LAN controller",
    "pdu": "Power distribution unit",
    "console_server": "Console server",
}

# These tokens don't overlap with one another, so match order doesn't
# matter for correctness - listed in the order the convention was given.
_ROLE_PATTERNS: tuple[tuple[str, str], ...] = (
    ("SWA", "access_switch"),
    ("SWS", "server_switch"),
    ("SWC", "core_switch"),
    ("SWD", "distribution_switch"),
    ("WLC", "wlc"),
    ("PDU", "pdu"),
    ("CON", "console_server"),
)

_ZONE_PATTERNS: tuple[tuple[str, NetworkZone], ...] = (
    ("P0", NetworkZone.IT),
    ("O0", NetworkZone.OT),
)

# A role maps straight to a Netmiko device_type only when the role alone
# determines it. "wlc" deliberately has no entry: the naming convention
# can't distinguish an older AireOS controller from a Catalyst 9800, so
# that choice is always left to the user rather than guessed.
_ROLE_TO_DEVICE_TYPE: dict[str, str] = {
    "access_switch": "cisco_ios",
    "server_switch": "cisco_ios",
    "core_switch": "cisco_ios",
    "distribution_switch": "cisco_ios",
    "pdu": "pdu_generic",
    "console_server": "console_server",
}


def detect_device_role(name: str) -> str | None:
    upper = name.upper()
    for pattern, role in _ROLE_PATTERNS:
        if pattern in upper:
            return role
    return None


def detect_network_zone(name: str) -> NetworkZone | None:
    upper = name.upper()
    for pattern, zone in _ZONE_PATTERNS:
        if pattern in upper:
            return zone
    return None


def device_type_for_role(role: str | None) -> str | None:
    if role is None:
        return None
    return _ROLE_TO_DEVICE_TYPE.get(role)


@dataclass(frozen=True)
class Detection:
    device_role: str | None
    device_role_label: str | None
    network_zone: NetworkZone | None
    suggested_device_type: str | None


def detect(name: str) -> Detection:
    role = detect_device_role(name)
    return Detection(
        device_role=role,
        device_role_label=DEVICE_ROLES.get(role) if role else None,
        network_zone=detect_network_zone(name),
        suggested_device_type=device_type_for_role(role),
    )
