"""Parsers for the wireless-controller outputs the Wireless Audit reads on
top of what the inventory already parses (services/inventory.py): AP
state and country, AP tags, radio state, AP uplink speed, per-client RF
health, WLAN security, the controller's own NTP / redundancy / CPU state,
the message logs, and AireOS "key.......value" summaries.

Same rules as the inventory parsers: best-effort screen-scraping of Cisco
9800 (IOS-XE) and AireOS (5520/3504/8540) output, tolerant of column
drift between releases, never raising on odd input - a field that cannot
be found is simply None.
"""

from __future__ import annotations

import re
from collections import Counter

from app.services.inventory import _IP, _MAC, normalize_mac

# --- access points -------------------------------------------------------------------------

# 'show ap summary'. 9800 ends a row with the AP state ("Registered"),
# AireOS with the client count and DSE location. The token before the IP
# is the country code on both.
_AP_ROW = re.compile(
    rf"^(?P<name>\S+)\s+\d+\s+(?P<model>\S+)\s+(?P<mac>{_MAC})\s+.*?(?P<country>[A-Z]{{2,3}}(?:-[A-Z0-9]+)?)?\s+(?P<ip>{_IP})\b(?P<tail>.*)$",
    re.MULTILINE,
)


def parse_ap_summary_extra(output: str) -> dict[str, dict]:
    """AP name -> {'country', 'state', 'clients'} from 'show ap summary'."""
    result: dict[str, dict] = {}
    for m in _AP_ROW.finditer(output):
        tail = m.group("tail").strip()
        state = None
        clients = None
        first = tail.split()[0] if tail.split() else ""
        if first.isdigit():
            clients = int(first)  # AireOS: Clients column
        elif tail:
            state = re.split(r"\s{2,}", tail)[0].strip() or None  # 9800: State column
        result[m.group("name")] = {"country": m.group("country"), "state": state, "clients": clients}
    return result


# 9800 'show ap tag summary':
# AP Name  AP Mac  Site Tag Name  Policy Tag Name  RF Tag Name  Misconfigured  Tag Source
_TAG_ROW = re.compile(
    rf"^(?P<name>\S+)\s+(?P<mac>{_MAC})\s+(?P<site>\S+)\s+(?P<policy>\S+)\s+(?P<rf>\S+)\s+(?P<misconfigured>Yes|No)\b\s*(?P<source>\S+)?",
    re.MULTILINE | re.IGNORECASE,
)


def parse_ap_tags(output: str) -> dict[str, dict]:
    return {
        m.group("name"): {
            "site_tag": m.group("site"),
            "policy_tag": m.group("policy"),
            "rf_tag": m.group("rf"),
            "misconfigured": m.group("misconfigured").lower() == "yes",
            "source": m.group("source"),
        }
        for m in _TAG_ROW.finditer(output)
    }


# Radio summaries: 9800 'show ap dot11 5ghz|24ghz|6ghz summary', AireOS
# 'show advanced 802.11a|802.11b summary'. Both start
# AP Name, MAC, Slot, Admin State, Oper State, then channel / power in an
# order that differs by platform and release.
_RADIO_ROW = re.compile(
    rf"^(?P<name>\S+)\s+(?P<mac>{_MAC})\s+(?P<slot>\d+)\s+(?P<admin>Enabled|Disabled)\s+(?P<oper>\S+)\s*(?P<rest>.*)$",
    re.MULTILINE | re.IGNORECASE,
)
_CHANNEL = re.compile(r"\(?\*?(?P<ch>\d{1,3})(?:,\d{1,3})*\)?\*?")
_TXPOWER = re.compile(r"\*?(?P<level>\d)/(?P<levels>\d)\s*(?:\((?P<dbm>-?\d+)\s*dBm\))?")


def parse_radio_summary(output: str, band: str) -> list[dict]:
    rows = []
    for m in _RADIO_ROW.finditer(output):
        rest = m.group("rest")
        power = _TXPOWER.search(rest)
        # Channel: the first parenthesised / starred number that is not the
        # power level ("*1/8 (17 dBm)") or the channel width ("20").
        stripped = _TXPOWER.sub(" ", rest)
        channel = None
        for token in stripped.split():
            if "(" in token or "*" in token:
                cm = _CHANNEL.fullmatch(token)
                if cm:
                    channel = int(cm.group("ch"))
                    break
        if channel is None:
            # AireOS prints a bare channel number when it is static.
            for token in stripped.split():
                if token.isdigit() and int(token) not in (20, 40, 80, 160, 320):
                    channel = int(token)
                    break
        rows.append(
            {
                "name": m.group("name"),
                "mac": normalize_mac(m.group("mac")),
                "slot": m.group("slot"),
                "band": band,
                "admin": m.group("admin").capitalize(),
                "oper": m.group("oper").capitalize(),
                "channel": channel,
                "power_level": f"{power.group('level')}/{power.group('levels')}" if power else None,
                "power_dbm": int(power.group("dbm")) if power and power.group("dbm") else None,
            }
        )
    return rows


_SPEED = re.compile(r"(?P<n>\d+(?:\.\d+)?)\s*(?P<unit>Mbps|Gbps|M|G)\b", re.IGNORECASE)
_DUPLEX = re.compile(r"\b(?P<d>Full|Half)\b", re.IGNORECASE)
_LINK = re.compile(r"\b(?P<s>Up|Down)\b", re.IGNORECASE)


def parse_ap_ethernet(output: str, ap_names: set[str]) -> dict[str, dict]:
    """AP name -> {'speed_mbps', 'duplex', 'up'} for its fastest port that
    is up, from 9800 'show ap ethernet statistics' or AireOS 'show ap stats
    ethernet summary'. Only rows starting with a known AP name are read,
    which keeps the column layout out of it."""
    result: dict[str, dict] = {}
    for line in output.splitlines():
        words = line.split()
        if not words or words[0] not in ap_names:
            continue
        speed_m = _SPEED.search(line)
        speed = None
        if speed_m:
            speed = float(speed_m.group("n")) * (1000 if speed_m.group("unit").lower().startswith("g") else 1)
        link = _LINK.search(line)
        up = None if link is None else link.group("s").lower() == "up"
        duplex = _DUPLEX.search(line)
        row = {"speed_mbps": int(speed) if speed is not None else None, "duplex": duplex.group("d").capitalize() if duplex else None, "up": up}
        best = result.get(words[0])
        if best is None or (row["up"] and (not best["up"] or (row["speed_mbps"] or 0) > (best["speed_mbps"] or 0))):
            result[words[0]] = row
    return result


def parse_ap_config_general_extra(output: str) -> dict[str, dict]:
    """AP name -> primary/secondary controller and AP mode from 'show ap
    config general' (when the filter lets those lines through)."""
    starts = [m.start() for m in re.finditer(r"^\s*Cisco AP Name\s*[.:]+\s*", output, re.MULTILINE)]
    result: dict[str, dict] = {}
    for i, start in enumerate(starts):
        block = output[start : starts[i + 1] if i + 1 < len(starts) else len(output)]
        name = _value(block, r"Cisco AP Name")
        if not name:
            continue
        result[name.split()[0]] = {
            "primary": _value(block, r"Primary Cisco (?:Switch|Controller) Name"),
            "secondary": _value(block, r"Secondary Cisco (?:Switch|Controller) Name"),
            "mode": _value(block, r"AP Mode"),
        }
    return result


def _value(block: str, label: str) -> str | None:
    m = re.search(rf"^\s*{label}\s*[.:]+\s*(?P<v>\S.*?)\s*$", block, re.MULTILINE | re.IGNORECASE)
    if not m:
        return None
    value = m.group("v").strip()
    return None if value.lower() in ("not configured", "n/a", "none", "") else value


# --- clients -------------------------------------------------------------------------------

_RSSI = re.compile(r"^\s*(?:Radio Signal Strength Indicator|RSSI)\s*[.:]*\s*(?P<v>-\d+)\s*dBm", re.IGNORECASE | re.MULTILINE)
_SNR = re.compile(r"^\s*(?:Signal to Noise Ratio|SNR)\s*[.:]*\s*(?P<v>-?\d+)\s*dB\b", re.IGNORECASE | re.MULTILINE)
_PM_STATE = re.compile(r"^\s*Policy Manager State\s*[.:]*\s*(?P<v>\S+)", re.IGNORECASE | re.MULTILINE)
_USERNAME = re.compile(r"^\s*(?:Client )?Username\s*[.:]*\s*(?P<v>\S.*?)\s*$", re.IGNORECASE | re.MULTILINE)
_VLAN = re.compile(r"^\s*(?:VLAN|Vlan Id)\s*[.:]+\s*(?P<v>\d+)\s*$", re.IGNORECASE | re.MULTILINE)


def parse_client_health(output: str) -> dict:
    """RSSI, SNR, policy-manager state, username and VLAN from either
    controller's per-client detail."""

    def num(pattern: re.Pattern) -> int | None:
        m = pattern.search(output)
        return int(m.group("v")) if m else None

    pm = _PM_STATE.search(output)
    user = _USERNAME.search(output)
    vlan = _VLAN.search(output)
    username = user.group("v") if user else None
    return {
        "rssi": num(_RSSI),
        "snr": num(_SNR),
        "policy_state": pm.group("v").upper() if pm else None,
        "username": None if not username or username.upper() == "N/A" else username,
        "vlan": vlan.group("v") if vlan else None,
    }


# --- WLANs ---------------------------------------------------------------------------------

_WLAN_9800 = re.compile(r"^(?P<id>\d+)\s+(?P<profile>\S+)\s+(?P<ssid>.+?)\s{2,}(?P<status>UP|DOWN)\b\s*(?P<security>.*)$", re.MULTILINE)
_WLAN_AIREOS = re.compile(r"^(?P<id>\d+)\s+(?P<profile>.+?)\s+/\s+(?P<ssid>.+?)\s{2,}(?P<status>Enabled|Disabled)\b\s*(?P<rest>.*)$", re.MULTILINE)


def parse_wlans(summary: str, config: str | None, platform: str) -> list[dict]:
    """[{'id', 'profile', 'ssid', 'enabled', 'security', 'open', 'tkip',
    'wpa1'}] from 'show wlan summary' plus, for the security flags, the
    running config (9800 "wlan" blocks, AireOS "config wlan security ...")."""
    wlans: list[dict] = []
    if platform == "aireos":
        for m in _WLAN_AIREOS.finditer(summary):
            wlans.append({"id": m.group("id"), "profile": m.group("profile").strip(), "ssid": m.group("ssid").strip(), "enabled": m.group("status") == "Enabled", "security": None})
    else:
        for m in _WLAN_9800.finditer(summary):
            wlans.append({"id": m.group("id"), "profile": m.group("profile"), "ssid": m.group("ssid").strip(), "enabled": m.group("status") == "UP", "security": m.group("security").strip() or None})
    flags = _wlan_security_flags(config or "", platform)
    for w in wlans:
        f = flags.get(w["id"], {})
        sec = (w["security"] or "").lower()
        w["open"] = f.get("open", "[open]" in sec or sec == "open")
        w["webauth"] = f.get("webauth", "web" in sec)
        w["tkip"] = f.get("tkip", "tkip" in sec)
        w["wpa1"] = f.get("wpa1", "[wpa]" in sec and "wpa2" not in sec)
    return wlans


_XE_WLAN = re.compile(r"^wlan\s+\S+\s+(?P<id>\d+)\s+\S", re.IGNORECASE)
_AIREOS_WLAN_CMD = re.compile(r"^config wlan (?P<what>.+?)\s+(?P<id>\d+)\s*$", re.IGNORECASE)


def _wlan_security_flags(config: str, platform: str) -> dict[str, dict]:
    flags: dict[str, dict] = {}
    if platform == "aireos":
        for raw in config.splitlines():
            m = _AIREOS_WLAN_CMD.match(raw.strip())
            if not m:
                continue
            what = m.group("what").lower()
            f = flags.setdefault(m.group("id"), {})
            if what == "security wpa disable":
                f["open"] = True
            elif what == "security wpa enable":
                f["open"] = False
            elif what == "security wpa wpa1 enable":
                f["wpa1"] = True
            elif "ciphers tkip enable" in what:
                f["tkip"] = True
            elif what in ("security web-auth enable", "security web-passthrough enable"):
                f["webauth"] = True
        return flags
    current: str | None = None
    for raw in config.splitlines():
        header = _XE_WLAN.match(raw)
        if header:
            current = header.group("id")
            flags[current] = {"open": False, "tkip": False, "wpa1": False, "webauth": False}
            continue
        if current is None:
            continue
        if raw and not raw[0].isspace():
            current = None
            continue
        words = raw.strip().lower().split()
        f = flags[current]
        if words[:3] == ["no", "security", "wpa"] and len(words) == 3:
            f["open"] = True
        elif words[:3] == ["security", "wpa", "wpa1"]:
            f["wpa1"] = True
        elif "tkip" in words and words[:2] == ["security", "wpa"] and words[0] != "no":
            f["tkip"] = True
        elif words[:2] == ["security", "web-auth"]:
            f["webauth"] = True
    return flags


# --- controller state ----------------------------------------------------------------------

_DOTTED = re.compile(r"^\s*(?P<k>[A-Za-z0-9][^.\n]*?)\s*\.{2,}\s*(?P<v>.*?)\s*$", re.MULTILINE)


def parse_dotted(output: str) -> dict[str, str]:
    """AireOS 'Key Name........ value' lines -> {'key name': 'value'}."""
    return {m.group("k").strip().lower(): m.group("v").strip() for m in _DOTTED.finditer(output)}


def parse_redundancy(output: str) -> dict:
    """{'mode', 'peer_state', 'local_state'} from 9800 'show redundancy'
    or AireOS 'show redundancy summary'."""

    def pick(*labels: str) -> str | None:
        for label in labels:
            m = re.search(rf"^\s*{label}\s*[=:.]+\s*(?P<v>\S.*?)\s*$", output, re.MULTILINE | re.IGNORECASE)
            if m:
                return m.group("v")
        return None

    peer = pick(r"Peer State", r"Peer Software state")
    if peer is None:
        # 9800: "Peer Processor Information :" then its own "Current
        # Software state = STANDBY HOT".
        m = re.search(r"Peer Processor Information.*?Current Software state\s*=\s*(?P<v>\S.*?)\s*$", output, re.MULTILINE | re.IGNORECASE | re.DOTALL)
        peer = m.group("v") if m else None
    return {
        "mode": pick(r"Redundancy Mode \(Operational\)", r"Operating Redundancy Mode", r"Redundancy Mode"),
        "local_state": pick(r"Current Software state", r"Local State"),
        "peer_state": peer,
    }


def parse_ntp_sync(output: str) -> bool | None:
    """True/False whether NTP is synchronised, from 9800 'show ntp
    associations' (a '*' marks the sys.peer) or AireOS 'show time' ("In
    Sync"). None when the output says nothing either way."""
    if not output.strip():
        return None
    if re.search(r"\bIn Sync\b", output, re.IGNORECASE):
        return True
    if re.search(r"^\s*\*~?\s*\d", output, re.MULTILINE):
        return True
    if re.search(r"Not Synch|unsynchronized|Not in sync|no association", output, re.IGNORECASE):
        return False
    if re.search(rf"^\s*[~+#x.-]?\s*{_IP}", output, re.MULTILINE) or re.search(r"NTP Server", output, re.IGNORECASE):
        return False  # servers listed, none selected
    return None


def parse_cpu(output: str) -> int | None:
    """Recent CPU % from 9800 'show processes cpu sorted' ("five minutes:
    4%") or AireOS 'show cpu' ("Current CPU(s) load: 2%")."""
    for pattern in (r"five minutes:\s*(\d+)%", r"one minute:\s*(\d+)%", r"CPU\(s\) load:\s*(\d+)%", r"load:\s*(\d+)%"):
        m = re.search(pattern, output, re.IGNORECASE)
        if m:
            return int(m.group(1))
    return None


def parse_software_version(content_version: str | None, platform: str) -> str | None:
    if not content_version:
        return None
    if platform == "aireos":
        return parse_dotted(content_version).get("product version")
    m = re.search(r"Cisco IOS XE Software, Version\s+(\S+)", content_version) or re.search(r"Version\s+(\d+\.\d+\.\w+)", content_version)
    return m.group(1).rstrip(",") if m else None


# --- logs ----------------------------------------------------------------------------------

_SYSLOG = re.compile(r"%(?P<facility>[A-Z0-9_]+)-(?P<sev>[0-7])-(?P<mnemonic>[A-Z0-9_]+)\s*:?\s*(?P<text>.*)$")
_NOTABLE = re.compile(r"DISJOIN|DISCONNECT|DISASSOC|RADAR|DFS|CRASH|TRACEBACK|COREDUMP|REBOOT|RELOAD|DOWN|FAIL", re.IGNORECASE)
_VOLATILE = re.compile(rf"{_MAC}|{_IP}|\b\d+\b|\b[0-9a-f]{{2}}(?::[0-9a-f]{{2}}){{2,}}\b", re.IGNORECASE)
_TRAP_LINE = re.compile(r"^\s*\d+\s+\w{3}\s+\w{3}\s+\d+\s+[\d:]+\s+\d{4}\s+(?P<text>.+?)\s*$", re.MULTILINE)


def parse_syslog(output: str, ignore: list[re.Pattern] | None = None) -> list[dict]:
    """Messages grouped by %FACILITY-SEV-MNEMONIC: [{'key', 'severity',
    'count', 'example', 'notable'}], most severe and most frequent first.
    Reads 9800 'show logging' and AireOS 'show msglog'."""
    groups: dict[str, dict] = {}
    for line in output.splitlines():
        m = _SYSLOG.search(line)
        if not m:
            continue
        if ignore and any(p.search(line) for p in ignore):
            continue
        key = f"%{m.group('facility')}-{m.group('sev')}-{m.group('mnemonic')}"
        g = groups.setdefault(key, {"key": key, "severity": int(m.group("sev")), "count": 0, "example": line.strip()[:300], "notable": bool(_NOTABLE.search(m.group("mnemonic")))})
        g["count"] += 1
        g["example"] = line.strip()[:300]  # keep the latest
    return sorted(groups.values(), key=lambda g: (g["severity"], -g["count"]))


def parse_traplog(output: str, ignore: list[re.Pattern] | None = None) -> list[dict]:
    """AireOS 'show traplog' grouped by message shape (MACs, IPs and numbers
    blanked out): [{'key', 'count', 'example', 'notable'}]."""
    counts: Counter[str] = Counter()
    examples: dict[str, str] = {}
    for m in _TRAP_LINE.finditer(output):
        text = m.group("text")
        if ignore and any(p.search(text) for p in ignore):
            continue
        shape = re.split(r"[.:]", _VOLATILE.sub("#", text), maxsplit=1)[0].strip()[:120]
        counts[shape] += 1
        examples.setdefault(shape, text[:300])
    return [{"key": k, "count": n, "example": examples[k], "notable": bool(_NOTABLE.search(k))} for k, n in counts.most_common()]


# --- AP groups (AireOS) / tags (9800) / WNCD -------------------------------------------------

_APG_ROW_WLAN = re.compile(r"^\s*(?P<id>\d+)\s+(?P<interface>\S+)\s+(?:Enabled|Disabled)\b", re.MULTILINE | re.IGNORECASE)
_APG_ROW_AP = re.compile(rf"^(?P<name>\S+)\s+\d+\s+(?P<model>\S+)\s+(?P<mac>{_MAC})\b", re.MULTILINE)


def parse_ap_groups(output: str) -> list[dict]:
    """AireOS 'show wlan apgroups': [{'name', 'description', 'rf_24',
    'rf_5', 'wlans': [ids], 'aps': [{'name', 'model'}]}]."""
    starts = [m for m in re.finditer(r"^\s*(?:Site|AP Group) Name\s*\.{2,}\s*(?P<name>\S.*?)\s*$", output, re.MULTILINE | re.IGNORECASE)]
    groups = []
    for i, m in enumerate(starts):
        block = output[m.end() : starts[i + 1].start() if i + 1 < len(starts) else len(output)]
        d = parse_dotted(block)

        def rf(band: str) -> str | None:
            found = re.search(rf"^\s*{re.escape(band)}\s*band\s*\.{{2,}}\s*(?P<v>\S.*?)\s*$", block, re.MULTILINE | re.IGNORECASE)
            value = found.group("v") if found else None
            return None if not value or value.lower() in ("<none>", "none") else value

        groups.append(
            {
                "name": m.group("name"),
                "description": None if d.get("site description", "<none>").lower() in ("<none>", "") else d.get("site description"),
                "rf_24": rf("2.4 ghz"),
                "rf_5": rf("5 ghz"),
                "wlans": [w.group("id") for w in _APG_ROW_WLAN.finditer(block)],
                "aps": [{"name": a.group("name"), "model": a.group("model")} for a in _APG_ROW_AP.finditer(block)],
            }
        )
    return groups


_XE_TAG = re.compile(r"^wireless tag (?P<kind>site|policy|rf) (?P<name>\S+)\s*$", re.IGNORECASE)


def parse_tag_config(config: str) -> dict[str, dict[str, dict]]:
    """9800 running config -> {'site': {name: {...}}, 'policy': {...}, 'rf': {...}}.
    Site tags carry ap_profile / flex_profile / local_site, policy tags
    their WLAN -> policy-profile map, RF tags their per-band RF profiles."""
    tags: dict[str, dict[str, dict]] = {"site": {}, "policy": {}, "rf": {}}
    current: dict | None = None
    for raw in config.splitlines():
        m = _XE_TAG.match(raw.rstrip())
        if m:
            kind = m.group("kind").lower()
            current = {"description": None, "wlans": {}, "ap_profile": None, "flex_profile": None, "local_site": True, "rf": {}}
            tags[kind][m.group("name")] = current
            continue
        if current is None:
            continue
        if raw and not raw[0].isspace():
            current = None
            continue
        words = raw.split()
        if not words:
            continue
        if words[0] == "description":
            current["description"] = raw.strip()[len("description") :].strip().strip('"')
        elif words[0] == "wlan" and len(words) >= 4 and words[2] == "policy":
            current["wlans"][words[1]] = words[3]
        elif words[0] == "ap-profile" and len(words) > 1:
            current["ap_profile"] = words[1]
        elif words[0] == "flex-profile" and len(words) > 1:
            current["flex_profile"] = words[1]
        elif words[:2] == ["no", "local-site"]:
            current["local_site"] = False
        elif words[0].endswith("-rf-policy") and len(words) > 1:
            current["rf"][words[0].replace("-rf-policy", "")] = words[1]
    return tags


_AFFINITY = re.compile(
    r"^(?P<tag>\S+)\s+(?P<type>SITE TAG|RF TAG|POLICY TAG)\s+(?P<aps>\d+)\s+(?:(?P<load>\d+(?:\.\d+)?)\s+)?(?P<instance>\d+)\s*$",
    re.MULTILINE | re.IGNORECASE,
)
_WNCD_CPU = re.compile(r"(?P<s5>\d+)%\s+(?P<m1>\d+)%\s+(?P<m5>\d+)%\s+\S+\s+\d+\s+wncd_(?P<instance>\d+)\b")


def parse_wncd_affinity(output: str) -> list[dict]:
    """9800 'show wireless loadbalance tag affinity': [{'tag', 'aps',
    'instance'}] - which WNCD process each site tag's APs live on."""
    return [
        {"tag": m.group("tag"), "aps": int(m.group("aps")), "instance": int(m.group("instance")), "load": float(m.group("load")) if m.group("load") else None}
        for m in _AFFINITY.finditer(output)
        if m.group("type").upper() == "SITE TAG"
    ]


def parse_wncd_cpu(output: str) -> dict[int, int]:
    """WNCD instance -> five-minute CPU % from 'show processes cpu platform
    sorted | i wncd'."""
    result: dict[int, int] = {}
    for m in _WNCD_CPU.finditer(output):
        result[int(m.group("instance"))] = int(m.group("m5"))
    return result
