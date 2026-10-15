"""Wireless client history from a controller's logs: every message that
names a client MAC, classified (join, roam, drop, authentication or DHCP
failure, exclusion, timeout), plus what the per-client commands add -
roam (mobility) history and the client's own error counters.

Sources, all collected like any other command:

  9800    show logging                                   syslog buffer
          show wireless client mac-address <mac> detail  state, counters
          show wireless client mac-address <mac> mobility history
          show wireless stats client delete reasons      why clients were dropped
  AireOS  show msglog, show traplog                       message / trap log
          show client detail <mac>                        state, counters
          show client roam-history <mac>

Messages differ between releases, so classification is by keyword, never
by exact message text, and a MAC is recognised in any of its spellings.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

from app.services import inventory as inv

# Not followed by another ":xx" - so "MAC:a4:bb:..." is not read as "AC:a4:...".
_MAC_RE = re.compile(r"(?i)(?<![0-9a-f])(?:[0-9a-f]{4}\.[0-9a-f]{4}\.[0-9a-f]{4}|(?:[0-9a-f]{2}[:-]){5}[0-9a-f]{2})(?![0-9a-f]|[:.-][0-9a-f])")
_TIMESTAMP = re.compile(
    r"(?P<ts>(?:\*?[A-Z][a-z]{2}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:\s+[A-Z]{2,4})?)|(?:[A-Z][a-z]{2}\s+[A-Z][a-z]{2}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2}\s+\d{4})|(?:\d{4}/\d{2}/\d{2}\s+\d{2}:\d{2}:\d{2}(?:\.\d+)?)|(?:\d{1,2}/\d{1,2}/\d{4}\s+\d{2}:\d{2}:\d{2}))"
)
_REASON = re.compile(r"(?i)(?:delete\s+)?reason(?:\s+code)?\s*[:=]?\s*(?P<r>[^,;()\]]{1,80})")

# (kind, pattern) - first match wins, most specific first.
EVENT_KINDS: tuple[tuple[str, re.Pattern], ...] = (
    ("exclusion", re.compile(r"(?i)exclu|blocklist|black.?list|deny.?list")),
    ("auth_failure", re.compile(r"(?i)auth\w*[\s_-]*fail|fail\w*[\s_-]*auth|eap\w*.*(?:fail|timeout|retr|exceed)|max\s+\w*\s*retr|dot1x.*(?:fail|timeout)|802\.1x.*fail|mic[\s_-]*fail|4[\s-]?way|key\s*(?:exchange|handshake).*(?:fail|timeout)|psk.*mismatch|invalid\s+pmk|radius.*(?:reject|timeout)|access-reject")),
    ("dhcp_failure", re.compile(r"(?i)dhcp.*(?:fail|timeout|nak|no\s+(?:offer|response))|ip[\s_-]*learn.*(?:fail|timeout)|static\s+ip.*(?:not|denied)")),
    ("timeout", re.compile(r"(?i)idle[\s_-]*timeout|session[\s_-]*timeout|inactivity|keep\s*alive.*(?:timeout|expire)")),
    ("roam", re.compile(r"(?i)roam|mobility|handoff|hand-off|moved\s+(?:from|to)|reassoc|anchor|foreign|ft\s+(?:roam|over)")),
    ("drop", re.compile(r"(?i)de-?auth|dis-?assoc|delet|disconnect|removed|client\s+left|aged\s+out|timed\s*out|deleted|dropped")),
    ("join", re.compile(r"(?i)assoc|join|run[\s_-]*state|added|authenticated|connected|ip[\s_-]*learn")),
)

ISSUE_KINDS = ("drop", "auth_failure", "dhcp_failure", "exclusion", "timeout")


def mac_key(raw: str) -> str | None:
    return inv.normalize_mac(raw)


@dataclass
class ClientEvent:
    mac: str
    kind: str
    time: str | None
    source: str  # the command it came from
    reason: str | None
    text: str


def classify(line: str) -> tuple[str, str | None]:
    for kind, pattern in EVENT_KINDS:
        if pattern.search(line):
            reason = None
            m = _REASON.search(line)
            if m:
                reason = m.group("r").strip(" .:")
            return kind, reason
    return "other", None


_LOG_COMMANDS = ("show logging", "show msglog", "show traplog")


def log_events(content: str, ignore_macs: set[str] | None = None) -> dict[str, list[ClientEvent]]:
    """MAC -> events from every log section of a snapshot. MACs in
    `ignore_macs` (the controller's own APs and radios) are left out."""
    ignore = ignore_macs or set()
    events: dict[str, list[ClientEvent]] = {}
    for typed, output in inv.sections(content):
        if not typed or not any(inv.command_matches(typed, c) for c in _LOG_COMMANDS):
            continue
        for line in output.splitlines():
            macs = {mac_key(m.group(0)) for m in _MAC_RE.finditer(line)}
            macs = {m for m in macs if m and m not in ignore}
            if not macs:
                continue
            kind, reason = classify(line)
            ts = _TIMESTAMP.search(line)
            for mac in macs:
                events.setdefault(mac, []).append(ClientEvent(mac, kind, ts.group("ts").lstrip("*") if ts else None, typed, reason, line.strip()[:400]))
    return events


# --- per-client commands -------------------------------------------------------------------------

_MOBILITY_COMMANDS = ("show wireless client mac-address {client} mobility history", "show client roam-history {client}")
_DETAIL_COMMANDS = ("show wireless client mac-address {client} detail", "show client detail {client}")


def _client_sections(content: str, canonicals: tuple[str, ...]) -> dict[str, str]:
    out: dict[str, str] = {}
    for typed, output in inv.sections(content):
        if not typed or not any(inv.command_matches(typed, c) for c in canonicals):
            continue
        m = _MAC_RE.search(typed)
        mac = mac_key(m.group(0)) if m else None
        if mac:
            out[mac] = output
    return out


def parse_mobility_history(output: str) -> list[dict]:
    """[{'ap', 'time', 'type'}] oldest first, from the 9800 'mobility
    history' or AireOS 'roam-history' table."""
    rows = []
    started = False
    for line in output.splitlines():
        if re.match(r"^\s*-{5,}", line):
            started = True
            continue
        if not started or not line.strip():
            continue
        words = line.split()
        ap = next((w for w in words if re.match(r"^[A-Za-z][\w.-]{2,}$", w) and not re.match(r"^(?:Local|Anchor|Foreign|Export|UTC|GMT)$", w, re.I) and not _MAC_RE.fullmatch(w)), None)
        ts = re.search(r"\d{1,2}/\d{1,2}/\d{4}\s+\d{2}:\d{2}:\d{2}|\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}|[A-Z][a-z]{2}\s+[A-Z][a-z]{2}\s+\d+\s+\d{2}:\d{2}:\d{2}\s+\d{4}", line)
        roam_type = re.search(r"(?i)\b(802\.11r|11r|FT|OKC|CCKM|PMKID|full auth|fast|slow)\b", line)
        if ap:
            rows.append({"ap": ap, "time": ts.group(0) if ts else None, "type": roam_type.group(1) if roam_type else None})
    # The 9800 lists the newest first.
    if len(rows) > 1 and rows[0]["time"] and rows[-1]["time"] and rows[0]["time"] > rows[-1]["time"]:
        rows.reverse()
    return rows


_COUNTERS = {
    "retries": r"(?:Number of )?Data Retries|Data Retries|Number of Data Retries",
    "rts_retries": r"(?:Number of )?RTS Retries",
    "packets_sent": r"(?:Number of )?(?:Packets Sent|Tx Packets|Packets Transmitted)",
    "packets_received": r"(?:Number of )?(?:Packets Received|Rx Packets)",
    "decrypt_failed": r"(?:Number of )?Decrypt Failed Packets|Decrypt Failures",
    "mic_failed": r"(?:Number of )?Mic Failur\w* Packets|MIC Failures",
    "tx_dropped": r"(?:Number of )?Tx Total Dropped Packets|Tx Dropped",
    "excessive_retries": r"(?:Number of )?Tx Excessive Retries|Excessive Retries",
    "move_count": r"Mobility Move Count|Number of Roams|Roam Count",
}


def parse_detail_counters(output: str) -> dict[str, int]:
    counters: dict[str, int] = {}
    for key, label in _COUNTERS.items():
        m = re.search(rf"(?im)^\s*(?:{label})\s*[.:=]*\s*(?P<v>\d+)\b", output)
        if m:
            counters[key] = int(m.group("v"))
    return counters


def parse_delete_reasons(output: str) -> list[dict]:
    """9800 'show wireless stats client delete reasons': non-zero reasons,
    most frequent first."""
    rows = []
    for line in output.splitlines():
        m = re.match(r"^\s*(?P<reason>[A-Za-z][^:]{2,80}?)\s*:\s*(?P<n>\d+)\s*$", line)
        if m and int(m.group("n")) > 0 and not re.match(r"(?i)total", m.group("reason")):
            rows.append({"reason": m.group("reason").strip(), "count": int(m.group("n"))})
    return sorted(rows, key=lambda r: -r["count"])


# --- per client summary --------------------------------------------------------------------------


@dataclass
class ClientHistory:
    mac: str
    events: list[ClientEvent] = field(default_factory=list)
    roams: list[dict] = field(default_factory=list)  # mobility history rows
    counters: dict[str, int] = field(default_factory=dict)

    def count(self, kind: str) -> int:
        return sum(1 for e in self.events if e.kind == kind)

    @property
    def roam_count(self) -> int:
        from_history = max(len(self.roams) - 1, 0)
        return max(from_history, self.count("roam"), self.counters.get("move_count", 0))

    def ping_pong(self) -> list[str]:
        """AP pairs the client bounced between (A -> B -> A)."""
        aps = [r["ap"] for r in self.roams]
        pairs = Counter()
        for a, b, c in zip(aps, aps[1:], aps[2:]):
            if a == c and a != b:
                pairs[" ↔ ".join(sorted((a, b)))] += 1
        return [p for p, n in pairs.items() if n >= 1]


def histories(content: str, ignore_macs: set[str] | None = None) -> dict[str, ClientHistory]:
    """Everything one snapshot says about each client."""
    result: dict[str, ClientHistory] = {}
    for mac, events in log_events(content, ignore_macs).items():
        result.setdefault(mac, ClientHistory(mac)).events.extend(events)
    for mac, output in _client_sections(content, _MOBILITY_COMMANDS).items():
        result.setdefault(mac, ClientHistory(mac)).roams = parse_mobility_history(output)
    for mac, output in _client_sections(content, _DETAIL_COMMANDS).items():
        counters = parse_detail_counters(output)
        if counters:
            result.setdefault(mac, ClientHistory(mac)).counters.update(counters)
    return result


def issues_for(h: ClientHistory, *, roam_warn: int, retry_warn_percent: int) -> list[tuple[str, str]]:
    """[(severity, message)] for one client."""
    out: list[tuple[str, str]] = []
    drops = [e for e in h.events if e.kind == "drop"]
    if drops:
        reasons = Counter(e.reason for e in drops if e.reason)
        detail = "; ".join(f"{r} ×{n}" for r, n in reasons.most_common(3))
        out.append(("warn" if len(drops) < 3 else "fail", f"Dropped {len(drops)} time(s)" + (f": {detail}" if detail else "")))
    for kind, label in (("auth_failure", "authentication failure"), ("dhcp_failure", "DHCP / IP-learning failure"), ("exclusion", "exclusion / blocklist"), ("timeout", "timeout")):
        n = h.count(kind)
        if n:
            severity = "fail" if kind in ("auth_failure", "exclusion", "dhcp_failure") else "info"
            out.append((severity, f"{n} {label} event(s)"))
    roams = h.roam_count
    if roams >= roam_warn:
        out.append(("warn", f"Roamed {roams} times: check coverage overlap, TX power and the client's roaming threshold"))
    pp = h.ping_pong()
    if pp:
        out.append(("warn", f"Ping-pong roaming between {', '.join(pp)}"))
    sent = h.counters.get("packets_sent", 0)
    retries = h.counters.get("retries", 0)
    if sent >= 100 and retries * 100 / sent >= retry_warn_percent:
        out.append(("warn", f"{retries * 100 // sent}% data retries ({retries} of {sent} packets)"))
    for key, label in (("decrypt_failed", "decrypt failures"), ("mic_failed", "MIC failures")):
        if h.counters.get(key):
            out.append(("warn", f"{h.counters[key]} {label}"))
    return out
