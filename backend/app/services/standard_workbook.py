"""Turns a configuration-standard workbook into golden templates.

The workbook is the one engineers already maintain: one sheet per
controller build (e.g. "Flex vWLC 9800 IOS-XE", "WLC 9800 IOS-XE", "WLC
AireOS"), column A the commands in build order, column B the variables
(<<hostname>>, <<radius key>>...), column C notes. It is written for a
person building a controller by hand, so besides configuration it holds
headings, explanations, pasted show output, worked examples, exec-only
steps (crypto key generate, transfer, save config, "y" answers) and
procedural steps that never appear in a running configuration ("shutdown"
before editing a WLAN, "config wlan disable 1" before changing it).

This module reads each sheet and keeps only what a running configuration
can be checked against, in services/config_template.py's syntax:

  - <<variable>>, <VARIABLE>, XXXXXXX, x.x.x.x, 239.X.X.X -> {{variable}},
    a wildcard (secrets are stored encrypted on the device anyway, so the
    value can never be compared - only that the line is there);
  - IOS-XE nesting rebuilt from the build order where the sheet's own
    indentation is missing ("shutdown" under "wlan ..." typed flush left);
  - lines the notes call conditional ("if needed", "only for sites where
    it is used", "do not configure if ...") and lines in a regional block
    (NALA / EU / Asia Pacific / China...) become optional: warnings, not
    failures;
  - "config snmp community delete public" becomes "absent: config snmp
    community create public".

Every line it drops or downgrades is listed in the import report with the
reason, so the result can be reviewed - and the template stays editable.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from io import BytesIO

from openpyxl import load_workbook

MAX_COLUMNS = 3  # A commands, B variables, C notes; anything to the right is stray
MAX_REPORTED = 400

_PLACEHOLDERS = (
    re.compile(r"<<\s*([^<>]*?)\s*>>"),
    re.compile(r"<\s*([A-Za-z][A-Za-z0-9 _./-]*?)\s*>"),
)
# Literal stand-ins the sheets use where a value goes.
_FILLERS = re.compile(
    r"(?<![\w.])(?:X{4,}|x{4,}|(?:[XxYy]{1,4}\.){3}[XxYy]{1,4}|\d{1,3}\.[Xx]\.[Xx]\.[Xx]|[xX]{4}\.[xX]{4}\.[xX]{4})(?![\w.])"
)
# "password 0 <<pw>>" is stored as "password 7 <cipher>": the encryption
# type digit is not part of the standard.
_ENCRYPTION_TYPE = re.compile(r"\b(password|secret|key|server-key|ascii|hex)\s+[0-9]\s+(?=\{\{)", re.IGNORECASE)

_OPTIONAL_HINTS = re.compile(
    r"if needed|if required|if used|if applicable|if present|only for sites|where it is used|where used|"
    r"optional|do not configure if|should only be|only be configured|depends on site|vary by site|"
    r"site requirement|only if|when required|if necessary|not mandatory",
    re.IGNORECASE,
)
_REGION_HEADING = re.compile(
    r"^(?:NALA|NA|North America|LATAM|EU|Europe|EMEA|AP|APAC|Asia(?: Pacific)?|China|India|Japan|Singapore|Frankfurt|Shanghai|"
    r"Americas|Region\b)",
    re.IGNORECASE,
)

_CONDITIONAL_HEADING = re.compile(r"do not configure|if using|in a mix|\bANCHORS? FOR\b|^(?:Frankfurt|Singapore|Shanghai|Hong Kong)\b", re.IGNORECASE)

# IOS-XE commands that only run at the exec prompt / once at build time:
# none of them is in a running configuration.
_XE_EXEC = re.compile(
    r"^(?:show|copy|write|wr|reload|clear|debug|undebug|test|request|install|ping|traceroute|more|dir|delete|erase|verify|"
    r"end|exit|conf|configure|enable$|crypto key generate|crypto pki (?:enroll|import|authenticate)|wireless config vwlc-ssc|"
    r"license smart (?:register|trust|deregister|factory|save|sync)|ap name |y$|yes$|n$|no$)",
    re.IGNORECASE,
)
_AIREOS_EXEC = re.compile(r"^(?:show|transfer|save|reset|debug|license|imm|ping|y$|yes$|n$|no$|clear|test)\b", re.IGNORECASE)

# Top-level IOS-XE configuration keywords. A flush-left line whose first
# word is not one of these, typed while a block is open, belongs to that
# block ("shutdown", "radio policy dot11 24ghz", "description ...").
_XE_TOP = {
    "aaa", "access-list", "alias", "ap", "archive", "banner", "boot", "boot-end-marker", "boot-start-marker", "call-home",
    "cdp", "class-map", "clock", "control-plane", "crypto", "cts", "device", "device-tracking", "diagnostic", "dot1x",
    "enable", "errdisable", "et-analytics", "event", "flow", "hostname", "interface", "ip", "ipv6", "key", "license",
    "line", "lldp", "logging", "login", "mac", "memory", "multilink", "netconf-yang", "ntp", "parameter-map", "password",
    "platform", "policy-map", "port-channel", "radius", "radius-server", "redundancy", "restconf", "router", "service",
    "service-template", "snmp", "snmp-server", "spanning-tree", "table-map", "tacacs", "tacacs-server", "telemetry",
    "track", "transceiver", "username", "version", "vlan", "vrf", "vtp", "wireless", "wlan", "file", "privilege",
    "power", "stack-power", "system", "subscriber", "monitor", "mpls", "nat", "object-group", "template",
}
_XE_BLOCK_START = re.compile(
    r"^(?:interface |vlan \d+\s*$|wlan \S+ (?:\d+|\{\{\w+\}\}) |wireless profile |wireless tag |wireless aaa policy |wireless cts-sxp profile |"
    r"wireless media-stream |line |class-map |policy-map |parameter-map |aaa server |aaa group server |radius server |"
    r"tacacs server |vrf definition |redundancy$|archive$|service-template |ap profile |ap dot11 \S+ rf-profile |"
    r"crypto pki |ip access-list |ipv6 access-list |flow |router |key chain |control-plane|device-tracking policy |"
    r"call-home$|spanning-tree mst configuration|table-map |ip dhcp pool |snmp-server view |license smart$|"
    r"wireless rf-network |wireless fabric |wireless mobility group$|ap tag-source-priority)",
    re.IGNORECASE,
)


@dataclass
class SkippedLine:
    row: int
    text: str
    reason: str


@dataclass
class ImportedSheet:
    name: str
    platform: str  # "9800" | "aireos"
    kind: str  # "flex" | "local"
    template: str
    rules: int = 0
    optional: int = 0
    skipped: list[SkippedLine] = field(default_factory=list)
    skipped_count: int = 0


@dataclass
class ImportedWorkbook:
    filename: str
    sha256: str
    sheets: list[ImportedSheet]


def _cells(row: tuple) -> tuple[str, str]:
    """(command cell, notes) - notes are columns B and C joined."""
    values = ["" if v is None else str(v) for v in row[:MAX_COLUMNS]]
    values += [""] * (MAX_COLUMNS - len(values))
    return values[0], " ".join(v.strip() for v in values[1:] if v and v.strip())


def _platform(sheet_name: str, lines: list[str]) -> str:
    name = sheet_name.lower()
    if "aireos" in name or "5520" in name or "3504" in name or "8540" in name:
        return "aireos"
    if "9800" in name or "ios-xe" in name or "iosxe" in name or "catalyst" in name:
        return "9800"
    commands = [line.strip() for line in lines if line.strip()]
    config_lines = sum(1 for line in commands if line.lower().startswith("config "))
    return "aireos" if commands and config_lines / len(commands) > 0.5 else "9800"


def _kind(sheet_name: str) -> str:
    name = sheet_name.lower()
    return "flex" if "flex" in name or "vwlc" in name or "-cl" in name else "local"


def _placeholders(text: str) -> str:
    def name_of(m: re.Match) -> str:
        raw = re.sub(r"[^A-Za-z0-9]+", "_", m.group(1)).strip("_").lower()
        return "{{" + (raw or "value") + "}}"

    for pattern in _PLACEHOLDERS:
        text = pattern.sub(name_of, text)
    text = _FILLERS.sub("{{value}}", text)
    return _ENCRYPTION_TYPE.sub(lambda m: f"{m.group(1)} {{{{enc}}}} ", text)


def _is_prose(stripped: str) -> bool:
    if not stripped:
        return False
    first = stripped[0]
    if first.isupper() or first in "*<\"'(•-–" or stripped.startswith("<<"):
        return True
    words = stripped.split()
    # A sentence typed in lower case: long, and ends like one.
    return len(words) > 10 and stripped.endswith((".", ":", "!"))


def parse_sheet(name: str, rows: list[tuple[int, str, str]]) -> ImportedSheet:
    """`rows` is [(row number, command cell, notes)] for one sheet."""
    raw_lines = [cmd for _, cmd, _ in rows]
    platform = _platform(name, raw_lines)
    sheet = ImportedSheet(name=name, platform=platform, kind=_kind(name), template="")
    out: list[tuple[int, str, bool, int]] = []  # (depth, text, optional, row)
    skipped: list[SkippedLine] = []

    in_example = False
    regional = False  # inside a regional block (optional)
    regional_lines = 0  # commands seen since the regional heading
    prev_row = 0
    block_open = False  # XE: last top-level line opens a block
    have_top = False
    stack: list[tuple[int, int]] = []  # XE: (indent, depth) of open indented parents

    def skip(row: int, text: str, reason: str) -> None:
        skipped.append(SkippedLine(row, text.strip()[:200], reason))

    for row, cell, notes in rows:
        gap = row - prev_row > 1
        prev_row = row
        if gap:
            # A blank row ends an example, and a regional / conditional
            # block once that block has had a command (a blank row between
            # the heading and its block does not end it).
            in_example = False
            if regional_lines:
                regional = False
        for raw in cell.replace("\r", "").split("\n"):
            line = raw.replace("\t", " ").rstrip()
            stripped = line.strip()
            if not stripped:
                in_example = False
                continue
            if stripped.lower() in ("commands", "command"):
                continue
            if stripped.startswith(("!", "#")):
                if "example" in stripped.lower():
                    in_example = True
                    skip(row, stripped, "example block")
                continue
            if in_example:
                skip(row, stripped, "example block")
                continue
            if _is_prose(stripped):
                # A regional alternative (NALA / EU / APAC...) or a
                # conditional note ("DO NOT CONFIGURE ... IF USING ...")
                # makes the block under it optional.
                regional = bool(_REGION_HEADING.match(stripped) or _CONDITIONAL_HEADING.search(stripped)) or (
                    regional and stripped.strip("<> ").upper() == "OR"
                )
                regional_lines = 0
                if "example" in stripped.lower():
                    in_example = True
                skip(row, stripped, "heading or note")
                continue
            exec_re = _AIREOS_EXEC if platform == "aireos" else _XE_EXEC
            if exec_re.match(stripped):
                skip(row, stripped, "exec command, not part of the running configuration")
                continue
            optional = regional or bool(notes and _OPTIONAL_HINTS.search(notes))
            regional_lines += 1
            text = _placeholders(" ".join(stripped.split()))

            if platform == "aireos":
                low = text.lower()
                if re.match(r"^config wlan disable \d+$", low) or re.match(r"^config 802\.11[abgx]+ (?:disable|enable) network$", low):
                    skip(row, stripped, "procedural step (disable before change)")
                    continue
                m = re.match(r"^config snmp (community|v3user) delete (\S+)$", text, re.IGNORECASE)
                if m:
                    text = f"absent: config snmp {m.group(1)} create {m.group(2)} {{{{*}}}}"
                elif low.startswith("config ") is False:
                    skip(row, stripped, "not an AireOS config command")
                    continue
                out.append((0, text, optional, row))
                continue

            # IOS-XE: work out the nesting. Explicit indentation is
            # trusted; a flush-left line that is not a top-level keyword,
            # typed while a block is open, is that block's child.
            indent = len(line) - len(line.lstrip(" "))
            words = text.split()
            first = words[0].lower()
            top_level_word = first in _XE_TOP or (first == "no" and len(words) > 1 and words[1].lower() in _XE_TOP)
            if first == "wlan" and re.match(r"^wlan \S+ policy \S+", text, re.IGNORECASE):
                top_level_word = False  # "wlan X policy Y" inside a policy tag
            if indent > 0 and have_top:
                while stack and stack[-1][0] >= indent:
                    stack.pop()
                depth = stack[-1][1] + 1 if stack else 1
                stack.append((indent, depth))
            elif indent == 0 and block_open and not top_level_word:
                depth = 1
                stack = []
            else:
                depth = 0
                stack = []
                have_top = True
                block_open = bool(_XE_BLOCK_START.match(text))
            out.append((depth, text, optional, row))

    if platform != "aireos":
        out = _drop_procedural_shutdowns(out, skipped)

    lines: list[str] = []
    for depth, text, optional in _flatten(_merge(_tree(out))):
        prefix = "optional: " if optional and not text.startswith("absent:") else ""
        lines.append(" " * depth + prefix + text)
        sheet.rules += 1
        sheet.optional += 1 if optional else 0

    header = [
        f"# Imported from sheet '{name}' ({'Catalyst 9800' if platform == '9800' else 'AireOS'}, {sheet.kind}).",
        "# Edit freely; re-importing the workbook replaces this template.",
    ]
    sheet.template = "\n".join(header + lines) + "\n"
    sheet.skipped_count = len(skipped)
    sheet.skipped = skipped[:MAX_REPORTED]
    return sheet


@dataclass
class _Line:
    text: str
    optional: bool
    children: list["_Line"] = field(default_factory=list)


def _tree(out: list[tuple[int, str, bool, int]]) -> list[_Line]:
    root = _Line("", False)
    stack: list[tuple[int, _Line]] = [(-1, root)]
    for depth, text, optional, _row in out:
        node = _Line(text, optional)
        while stack and stack[-1][0] >= depth:
            stack.pop()
        stack[-1][1].children.append(node)
        stack.append((depth, node))
    return root.children


def _merge(nodes: list[_Line]) -> list[_Line]:
    """A block the sheet configures in two places ("parameter-map type
    webauth global" early on, then again for webauth-http-enable) is one
    block in a running configuration: merge repeats, drop duplicate lines."""
    merged: dict[str, _Line] = {}
    order: list[_Line] = []
    for n in nodes:
        key = n.text.lower()
        if key in merged:
            first = merged[key]
            first.optional = first.optional and n.optional
            first.children.extend(n.children)
            continue
        merged[key] = n
        order.append(n)
    for n in order:
        n.children = _merge(n.children)
    return order


def _flatten(nodes: list[_Line], depth: int = 0):
    for n in nodes:
        yield depth, n.text, n.optional
        yield from _flatten(n.children, depth + 1)


def _drop_procedural_shutdowns(out: list[tuple[int, str, bool, int]], skipped: list[SkippedLine]) -> list[tuple[int, str, bool, int]]:
    """In a build guide, "shutdown" under a WLAN/profile is the step before
    editing it and "no shutdown" the step after: only the final state is in
    the running configuration."""
    result = list(out)
    drop: set[int] = set()
    for i, (depth, text, _opt, row) in enumerate(result):
        if depth == 0 or text.lower() != "shutdown":
            continue
        for j in range(i + 1, len(result)):
            d2, t2, _o2, _r2 = result[j]
            if d2 < depth:
                break
            if d2 == depth and t2.lower() == "no shutdown":
                drop.add(i)
                skipped.append(SkippedLine(row, text, "procedural step (shutdown before change, no shutdown after)"))
                break
    return [entry for i, entry in enumerate(result) if i not in drop]


def import_workbook(content: bytes, filename: str = "standard.xlsx") -> ImportedWorkbook:
    """Every sheet of the workbook that holds controller commands."""
    wb = load_workbook(BytesIO(content), read_only=True, data_only=True)
    sheets: list[ImportedSheet] = []
    for ws in wb.worksheets:
        rows: list[tuple[int, str, str]] = []
        for number, row in enumerate(ws.iter_rows(max_col=MAX_COLUMNS, values_only=True), start=1):
            cmd, notes = _cells(row)
            if cmd.strip():
                rows.append((number, cmd, notes))
        if not rows:
            continue
        sheet = parse_sheet(ws.title, rows)
        if sheet.rules:
            sheets.append(sheet)
    wb.close()
    return ImportedWorkbook(filename=filename, sha256=hashlib.sha256(content).hexdigest(), sheets=sheets)
