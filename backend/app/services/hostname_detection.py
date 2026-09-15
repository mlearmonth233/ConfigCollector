"""Best-effort classification of a device from its name - role (access
switch, core switch, firewall...), IT/OT zone, and a suggested device
type - driven by *naming rules*.

Every organization names devices differently: one embeds "SWA"/"SWC" role
codes and a "P0"/"O0" zone marker ("GBGYSP01SWA001"), another spells it
out ("den-core-sw01", "plant2-ot-acc-03"). So the rules are data, not
code: an org defines its own (see models/hostname_rule.py and
api/hostname_rules.py), and until it does, BUILTIN_RULES - the original
convention - apply as a starting point it can copy and edit.

A rule is "when the hostname matches this pattern, it's this role and/or
this zone, and (optionally) this device type". Rules are checked in order;
the first match wins for each of role, zone and device type separately, so
a role rule and a zone rule can both fire on one name. Everything detected
is only ever a pre-fill for the Add-device form / bulk add - an explicitly
supplied value always wins.
"""

from __future__ import annotations

import enum
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from app.models.device import NetworkZone

# Roles this app knows how to label out of the box. An org's rules may name
# any other role key (a lowercase slug), with its own label - see
# roles_for_rules().
DEVICE_ROLES: dict[str, str] = {
    "access_switch": "Access switch",
    "server_switch": "Server switch",
    "core_switch": "Core switch",
    "distribution_switch": "Distribution switch",
    "wlc": "Wireless LAN controller",
    "pdu": "Power distribution unit",
    "console_server": "Console server",
    "firewall": "Firewall",
    "router": "Router",
}


class MatchMode(str, enum.Enum):
    CONTAINS = "contains"  # case-insensitive substring
    PREFIX = "prefix"
    SUFFIX = "suffix"
    REGEX = "regex"  # Python re, case-insensitive, search() anywhere in the name


@dataclass(frozen=True)
class Rule:
    pattern: str
    match_mode: MatchMode = MatchMode.CONTAINS
    device_role: str | None = None
    role_label: str | None = None
    network_zone: NetworkZone | None = None
    device_type: str | None = None

    def matches(self, name: str) -> bool:
        if not self.pattern:
            return False
        if self.match_mode == MatchMode.REGEX:
            try:
                return re.search(self.pattern, name, re.IGNORECASE) is not None
            except re.error:
                return False
        upper, pat = name.upper(), self.pattern.upper()
        if self.match_mode == MatchMode.PREFIX:
            return upper.startswith(pat)
        if self.match_mode == MatchMode.SUFFIX:
            return upper.endswith(pat)
        return pat in upper


def validate_rule(rule: Rule) -> None:
    """Raises ValueError with a user-facing message for a rule that can't
    work - shared by the API on save."""
    if not rule.pattern.strip():
        raise ValueError("A rule needs a pattern to match")
    if rule.match_mode == MatchMode.REGEX:
        try:
            re.compile(rule.pattern, re.IGNORECASE)
        except re.error as exc:
            raise ValueError(f"'{rule.pattern}' is not a valid regular expression: {exc}") from exc
    if rule.device_role is None and rule.network_zone is None and rule.device_type is None:
        raise ValueError(f"Rule '{rule.pattern}' sets nothing - give it a role, a zone or a device type")
    if rule.device_role is not None and not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", rule.device_role):
        raise ValueError(
            f"Role key '{rule.device_role}' must be lowercase letters, digits and underscores (e.g. wan_edge)"
        )


# The original convention this app shipped with - what an org gets until it
# defines its own rules, and the starting point "Start from the built-in
# rules" copies in. Two roles deliberately suggest no device type: "wlc"
# (the name can't tell an AireOS controller from a Catalyst 9800) and
# "console_server" (no console-server device type is registered).
BUILTIN_RULES: tuple[Rule, ...] = (
    Rule("SWA", device_role="access_switch", device_type="cisco_ios"),
    Rule("SWS", device_role="server_switch", device_type="cisco_ios"),
    Rule("SWC", device_role="core_switch", device_type="cisco_ios"),
    Rule("SWD", device_role="distribution_switch", device_type="cisco_ios"),
    Rule("WLC", device_role="wlc"),
    Rule("PDU", device_role="pdu", device_type="apc_pdu"),
    Rule("CON", device_role="console_server"),
    Rule("FWL", device_role="firewall", device_type="fortinet"),
    Rule("RTR", device_role="router", device_type="versa"),
    Rule("P0", network_zone=NetworkZone.IT),
    Rule("O0", network_zone=NetworkZone.OT),
)


def roles_for_rules(rules: Iterable[Rule] | None) -> dict[str, str]:
    """Built-in role labels plus any role an org's rules introduce (labelled
    by the rule, or prettified from the key)."""
    roles = dict(DEVICE_ROLES)
    for rule in rules or ():
        if rule.device_role and rule.device_role not in roles:
            roles[rule.device_role] = rule.role_label or rule.device_role.replace("_", " ").capitalize()
        elif rule.device_role and rule.role_label:
            roles[rule.device_role] = rule.role_label
    return roles


@dataclass(frozen=True)
class Detection:
    device_role: str | None
    device_role_label: str | None
    network_zone: NetworkZone | None
    suggested_device_type: str | None


def detect(name: str, rules: Iterable[Rule] | None = None) -> Detection:
    """Applies `rules` (or BUILTIN_RULES when None/empty) to a hostname.
    First match wins independently for role, zone and device type; a
    device type comes from the first matching rule that names one, so a
    zone-only rule never overrides a role rule's type suggestion."""
    active = tuple(rules) if rules else BUILTIN_RULES
    role = zone = device_type = None
    role_label = None
    labels = roles_for_rules(active)
    for rule in active:
        if not rule.matches(name):
            continue
        if role is None and rule.device_role:
            role = rule.device_role
            role_label = labels.get(role)
        if zone is None and rule.network_zone is not None:
            zone = rule.network_zone
        if device_type is None and rule.device_type:
            device_type = rule.device_type
    return Detection(device_role=role, device_role_label=role_label, network_zone=zone, suggested_device_type=device_type)


def device_type_for_role(role: str | None, rules: Iterable[Rule] | None = None) -> str | None:
    """The device type the rules associate with a role (the first rule for
    that role that names one) - used when a caller supplies a role
    explicitly but no type."""
    if role is None:
        return None
    for rule in (tuple(rules) if rules else BUILTIN_RULES):
        if rule.device_role == role and rule.device_type:
            return rule.device_type
    return None


# Thin wrappers kept for callers/tests that only ever used the built-ins.
def detect_device_role(name: str) -> str | None:
    return detect(name).device_role


def detect_network_zone(name: str) -> NetworkZone | None:
    return detect(name).network_zone


def rules_from_rows(rows: Iterable) -> tuple[Rule, ...]:
    """Rule objects from an org's HostnameRule rows (duck-typed - any object
    with the same attribute names), in their saved order."""
    return tuple(
        Rule(
            pattern=r.pattern,
            match_mode=MatchMode(r.match_mode) if not isinstance(r.match_mode, MatchMode) else r.match_mode,
            device_role=r.device_role,
            role_label=r.role_label,
            network_zone=r.network_zone,
            device_type=r.device_type,
        )
        for r in sorted(rows, key=lambda r: r.sort_order)
    )


def describe_mode(mode: MatchMode) -> str:
    return {
        MatchMode.CONTAINS: "contains",
        MatchMode.PREFIX: "starts with",
        MatchMode.SUFFIX: "ends with",
        MatchMode.REGEX: "matches regex",
    }[mode]


MODE_LABELS: Mapping[str, str] = {m.value: describe_mode(m) for m in MatchMode}
