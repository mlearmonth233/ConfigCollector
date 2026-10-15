"""Wireless Audit: checks every Cisco wireless controller (Catalyst 9800
and AireOS 5520/3504/8540) against the organization's standard, from the
latest collected snapshot of each.

The controllers are not logged in to here: a normal collection job does
that (services/collector.py, with the command sets in device_types.py)
and stores everything it ran. The audit reads those snapshots, the same
way the inventory does, in three steps so DNS can be looked up between
them without this module doing any I/O:

    facts = gather(inputs)                   # parse every controller's snapshot
    names, ips = dns_targets(facts, std)     # what DNS needs to answer
    report = run_audit(facts, std, dns, now) # every check, as plain dicts

Each check reports how many things it evaluated and a finding per
problem (fail / warn / info). A controller's score is the share of its
checks that passed, a warning counting half.
"""

from __future__ import annotations

import ipaddress
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.schemas.wireless_audit import WirelessStandard
from app.services import inventory as inv
from app.services import client_events as ce
from app.services import r2o as r2o_engine
from app.services import wlc_parsers as wp
from app.services.config_template import check_template, parse_config

PLATFORMS: dict[str, str] = {"cisco_wlc_9800": "9800", "cisco_wlc": "aireos"}

# Canonical commands the audit reads, per controller type - what the
# Coverage tab offers to add to a command list that lacks them.
AUDIT_COMMANDS: dict[str, tuple[str, ...]] = {
    "cisco_wlc_9800": (
        "show version",
        "show running-config",
        "show ap summary",
        "show ap config general",
        "show ap tag summary",
        "show ap ethernet statistics",
        "show ap dot11 24ghz summary",
        "show ap dot11 5ghz summary",
        "show wlan summary",
        "show wireless client summary",
        "show wireless device-tracking database ip",
        "show wireless client mac-address {client} detail",
        "show redundancy",
        "show ntp associations",
        "show processes cpu sorted",
        "show logging",
        "show wireless loadbalance tag affinity",
        "show processes cpu platform sorted",
        "show wireless stats client delete reasons",
    ),
    "cisco_wlc": (
        "show sysinfo",
        "show run-config commands",
        "show wlan apgroups",
        "show ap summary",
        "show ap stats ethernet summary",
        "show advanced 802.11a summary",
        "show advanced 802.11b summary",
        "show wlan summary",
        "show client summary",
        "show client detail {client}",
        "show redundancy summary",
        "show network summary",
        "show time",
        "show cpu",
        "show msglog",
        "show traplog",
    ),
}

_CLIENT_DETAIL = ("show wireless client mac-address {client} detail", "show client detail {client}")
_LEGACY_PROTOCOL = re.compile(r"^(?:802\.)?11(?:a|b|g|bg)\b(?!\w)", re.IGNORECASE)
_DEFAULT_TAG = re.compile(r"^default-(?:policy|site|rf)-tag$", re.IGNORECASE)
_APIPA = ipaddress.ip_network("169.254.0.0/16")


# --- facts ----------------------------------------------------------------------------------


@dataclass
class ControllerFacts:
    input: inv.SnapshotInput
    platform: str
    model: str | None = None
    software: str | None = None
    aps: list[dict] = field(default_factory=list)
    clients: list[dict] = field(default_factory=list)
    radios: list[dict] = field(default_factory=list)
    wlans: list[dict] = field(default_factory=list)
    config: str = ""  # what the golden template is compared with
    config_text: str = ""  # every config-ish section, for "is X configured" searches
    network_summary: dict[str, str] = field(default_factory=dict)
    redundancy: dict | None = None
    ntp_synced: bool | None = None
    cpu: int | None = None
    syslog: list[dict] | None = None
    traplog: list[dict] | None = None
    missing_commands: list[str] = field(default_factory=list)
    sections_present: set[str] = field(default_factory=set)
    ap_groups: list[dict] | None = None  # AireOS
    tags: dict[str, dict[str, dict]] | None = None  # 9800, from the config
    wncd_affinity: list[dict] | None = None
    wncd_cpu: dict[int, int] | None = None
    client_history: dict[str, ce.ClientHistory] = field(default_factory=dict)
    delete_reasons: list[dict] = field(default_factory=list)


def _section(content: str, *canonicals: str) -> str | None:
    for canonical in canonicals:
        found = inv.find_section(content, canonical)
        if found is not None:
            return found
    return None


def _exact_section(content: str, canonical: str) -> str | None:
    """Like inventory.find_section, without its aliases ("show ap summary"
    would otherwise also accept the "show ap config general" section)."""
    want = inv._words(canonical)
    for typed, output in inv.sections(content):
        words = inv._words(typed)
        if typed and len(words) == len(want) and all(c.startswith(t) for t, c in zip(words, want)):
            return output
    return None


def _client_health(content: str) -> dict[str, dict]:
    result: dict[str, dict] = {}
    for typed, output in inv.sections(content):
        if not typed or not any(inv.command_matches(typed, c) for c in _CLIENT_DETAIL):
            continue
        m = inv._MAC_ANY.search(typed)
        mac = inv.normalize_mac(m.group(0)) if m else None
        if mac:
            result[mac] = wp.parse_client_health(output)
    return result


def gather(inputs: list[inv.SnapshotInput], *, ignore_log_patterns: list[str] | None = None) -> list[ControllerFacts]:
    """Parses every controller's latest snapshot. `inputs` is every device
    (switch ARP tables fill in client IPs the controller did not report)."""
    built = inv.build_inventory(inputs)
    device_facts = {d.device_id: d for d in built.devices}
    ignore = []
    for pattern in ignore_log_patterns or []:
        try:
            ignore.append(re.compile(pattern, re.IGNORECASE))
        except re.error:
            continue
    facts: list[ControllerFacts] = []
    for item in inputs:
        platform = PLATFORMS.get(item.device_type)
        if platform is None:
            continue
        f = ControllerFacts(input=item, platform=platform)
        facts.append(f)
        dev = device_facts.get(item.device_id)
        if dev:
            f.model = dev.model
            f.software = dev.software
        content = item.content
        if not content:
            continue
        wanted = AUDIT_COMMANDS[item.device_type]
        present, missing = inv.present_commands(content, wanted)
        f.missing_commands = missing
        f.sections_present = set(present)

        version = _section(content, "show sysinfo" if platform == "aireos" else "show version")
        f.software = wp.parse_software_version(version, platform) or f.software

        # Access points: the inventory's list, enriched.
        summary = _exact_section(content, "show ap summary") or ""
        extra = wp.parse_ap_summary_extra(summary)
        general = wp.parse_ap_config_general_extra(_section(content, "show ap config general") or "")
        tags = wp.parse_ap_tags(_section(content, "show ap tag summary") or "")
        names = {ap.name for ap in built.access_points if ap.controller_id == item.device_id}
        ethernet_out = _section(content, "show ap ethernet statistics", "show ap stats ethernet summary") or ""
        ethernet = wp.parse_ap_ethernet(ethernet_out, names)
        for ap in built.access_points:
            if ap.controller_id != item.device_id:
                continue
            row = {
                "name": ap.name,
                "model": ap.model,
                "mac": ap.mac,
                "ip": ap.ip,
                "serial": ap.serial,
                "software": ap.software,
                **{"country": None, "state": None, "clients": None, "ap_group": None},
                **extra.get(ap.name, {}),
                "tags": tags.get(ap.name),
                "general": general.get(ap.name),
                "ethernet": ethernet.get(ap.name),
            }
            f.aps.append(row)

        # Radios.
        for canonical, band in (
            ("show ap dot11 24ghz summary", "2.4"),
            ("show ap dot11 5ghz summary", "5"),
            ("show ap dot11 6ghz summary", "6"),
            ("show advanced 802.11b summary", "2.4"),
            ("show advanced 802.11a summary", "5"),
        ):
            out = inv.find_section(content, canonical)
            if out:
                f.radios.extend(wp.parse_radio_summary(out, band))

        # Clients: the inventory's rows plus RF health from the detail.
        health = _client_health(content)
        for c in built.wireless_clients:
            if c.controller_id != item.device_id:
                continue
            row = {
                "mac": c.mac,
                "manufacturer": c.manufacturer,
                "ip": c.ip,
                "ap_name": c.ap_name,
                "wlan_id": c.wlan_id,
                "ssid": c.ssid,
                "state": c.state,
                "protocol": c.protocol,
                "auth": c.auth,
                **{"rssi": None, "snr": None, "policy_state": None, "username": None, "vlan": None},
                **health.get(c.mac, {}),
            }
            f.clients.append(row)

        # Configuration.
        if platform == "aireos":
            commands_cfg = inv.find_section(content, "show run-config commands")
            run_cfg = inv.find_section(content, "show run-config")
            f.config = commands_cfg or run_cfg or ""
            f.network_summary = wp.parse_dotted(inv.find_section(content, "show network summary") or "")
            parts = [commands_cfg, run_cfg, inv.find_section(content, "show time"), inv.find_section(content, "show network summary")]
        else:
            f.config = inv.find_section(content, "show running-config") or ""
            parts = [f.config]
        f.config_text = "\n".join(p for p in parts if p)
        wlan_summary = inv.find_section(content, "show wlan summary")
        if wlan_summary:
            f.wlans = wp.parse_wlans(wlan_summary, f.config_text, platform)

        redundancy = _section(content, "show redundancy summary", "show redundancy")
        f.redundancy = wp.parse_redundancy(redundancy) if redundancy else None
        ntp = _section(content, "show ntp associations", "show time")
        f.ntp_synced = wp.parse_ntp_sync(ntp) if ntp else None
        cpu = _section(content, "show processes cpu sorted", "show processes cpu", "show cpu")
        f.cpu = wp.parse_cpu(cpu) if cpu else None
        syslog = _section(content, "show logging", "show msglog")
        f.syslog = wp.parse_syslog(syslog, ignore) if syslog is not None else None
        traplog = inv.find_section(content, "show traplog")
        f.traplog = wp.parse_traplog(traplog, ignore) if traplog is not None else None

        # Client history from the logs and per-client commands; the
        # controller's own APs and radios are not clients.
        ap_macs = {ap["mac"] for ap in f.aps if ap["mac"]} | {r["mac"] for r in f.radios if r["mac"]}
        f.client_history = ce.histories(content, ap_macs)
        reasons = inv.find_section(content, "show wireless stats client delete reasons")
        f.delete_reasons = ce.parse_delete_reasons(reasons) if reasons else []

        # AP groups / tags / WNCD.
        if platform == "aireos":
            apgroups = inv.find_section(content, "show wlan apgroups")
            if apgroups is not None:
                f.ap_groups = wp.parse_ap_groups(apgroups)
                group_of = {ap["name"]: g["name"] for g in f.ap_groups for ap in g["aps"]}
                for ap in f.aps:
                    ap["ap_group"] = group_of.get(ap["name"])
        else:
            if f.config:
                f.tags = wp.parse_tag_config(f.config)
            affinity = inv.find_section(content, "show wireless loadbalance tag affinity")
            f.wncd_affinity = wp.parse_wncd_affinity(affinity) if affinity is not None else None
            wncd_cpu = inv.find_section(content, "show processes cpu platform sorted")
            f.wncd_cpu = wp.parse_wncd_cpu(wncd_cpu) if wncd_cpu is not None else None
    return facts


# --- DNS ------------------------------------------------------------------------------------


@dataclass
class DnsResults:
    # name -> resolved addresses ([] = no record)
    forward: dict[str, list[str]] = field(default_factory=dict)
    # ip -> PTR name (None = no PTR record)
    reverse: dict[str, str | None] = field(default_factory=dict)


def ap_fqdn(name: str, domain: str) -> str:
    domain = domain.strip().strip(".")
    if "." in name or not domain:
        return name
    return f"{name}.{domain}"


def _is_ip(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return False


def dns_targets(facts: list[ControllerFacts], standard: WirelessStandard) -> tuple[set[str], set[str]]:
    names: set[str] = set()
    ips: set[str] = set()
    for f in facts:
        host = f.input.host.strip()
        (ips if _is_ip(host) else names).add(host)
        if not standard.check_dns:
            continue
        for ap in f.aps:
            if ap["name"]:
                names.add(ap_fqdn(ap["name"], standard.dns_domain))
            if ap["ip"] and standard.check_reverse_dns:
                ips.add(ap["ip"])
    return names, ips


def _same_host(a: str | None, b: str | None) -> bool:
    if not a or not b:
        return False
    a, b = a.lower().rstrip("."), b.lower().rstrip(".")
    return a == b or a.split(".")[0] == b.split(".")[0]


# --- running the checks ---------------------------------------------------------------------


@dataclass
class _Check:
    check_id: str
    title: str
    category: str
    evaluated: int = 0
    severities: list[str] = field(default_factory=list)
    note: str | None = None
    skipped: bool = False

    def status(self) -> str:
        if self.skipped and not self.severities:
            return "skipped"
        if "fail" in self.severities:
            return "fail"
        if "warn" in self.severities:
            return "warn"
        if "info" in self.severities:
            return "info"
        return "pass"


class _Auditor:
    """Collects checks and findings for one controller."""

    def __init__(self, f: ControllerFacts, findings: list[dict]):
        self.f = f
        self.findings = findings
        self.checks: dict[str, _Check] = {}
        self.profile_name: str | None = None
        self.r2o_name: str | None = None
        self.r2o_counts: dict[str, int] = {}

    def check(self, check_id: str, title: str, category: str) -> _Check:
        c = self.checks.get(check_id)
        if c is None:
            c = self.checks[check_id] = _Check(check_id, title, category)
        return c

    def skip(self, check_id: str, title: str, category: str, note: str) -> None:
        c = self.check(check_id, title, category)
        c.skipped = True
        c.note = note

    def find(self, c: _Check, severity: str, subject: str | None, message: str, expected: str | None = None, actual: str | None = None) -> None:
        c.severities.append(severity)
        self.findings.append(
            {
                "check_id": c.check_id,
                "category": c.category,
                "severity": severity,
                "controller_id": self.f.input.device_id,
                "controller_name": self.f.input.name,
                "subject": subject,
                "message": message,
                "expected": expected,
                "actual": actual,
            }
        )


def _prefix_ok(value: str | None, prefixes: list[str]) -> bool:
    if not value:
        return False
    v = value.lower()
    return any(v.startswith(p.strip().lower()) for p in prefixes if p.strip())


def _mentions(text: str, token: str, keywords: tuple[str, ...]) -> bool:
    """Whether a config line mentioning one of `keywords` names `token` as
    a whole word (an IP address or hostname)."""
    pattern = re.compile(rf"(?<![\w.-]){re.escape(token)}(?![\w-]|\.\w)", re.IGNORECASE)
    for line in text.splitlines():
        low = line.lower()
        if any(k in low for k in keywords) and pattern.search(line):
            return True
    return False


def _band(protocol: str | None) -> str | None:
    p = (protocol or "").lower()
    if not p:
        return None
    if "2.4" in p or re.search(r"11(?:b|g|bg)\b", p) or "(2" in p:
        return "2.4"
    if "(6" in p or "6 ghz" in p or "6ghz" in p:
        return "6"
    return "5"


def run_audit(
    facts: list[ControllerFacts],
    standard: WirelessStandard,
    dns: DnsResults | None,
    *,
    now: datetime | None = None,
) -> dict:
    now = now or datetime.now(timezone.utc)
    findings: list[dict] = []
    controllers: list[dict] = []
    ap_rows: list[dict] = []
    client_rows: list[dict] = []
    client_health: list[dict] = []
    log_rows: list[dict] = []
    group_rows: list[dict] = []
    wncd_rows: list[dict] = []
    r2o_rows: list[dict] = []
    client_event_rows: list[dict] = []

    name_re = None
    name_re_error = None
    if standard.ap_name_pattern.strip():
        try:
            name_re = re.compile(standard.ap_name_pattern.strip())
        except re.error as exc:
            name_re_error = f"The AP naming pattern is not a valid regular expression: {exc}"
    subnets = []
    for cidr in standard.ap_subnets:
        try:
            subnets.append(ipaddress.ip_network(cidr.strip(), strict=False))
        except ValueError:
            continue

    # Org-wide duplicates.
    names_seen: dict[str, list[str]] = defaultdict(list)
    ips_seen: dict[str, list[str]] = defaultdict(list)
    for f in facts:
        for ap in f.aps:
            names_seen[ap["name"].lower()].append(f.input.name)
            if ap["ip"]:
                ips_seen[ap["ip"]].append(ap["name"])

    for f in facts:
        a = _Auditor(f, findings)
        item = f.input
        if not item.content:
            c = a.check("data.snapshot", "Configuration collected", "Controller")
            c.evaluated = 1
            a.find(c, "fail", None, "This controller has never been collected, so nothing can be audited. Run a collection first.")
            controllers.append(_controller_out(f, a))
            continue
        _check_controller(a, standard, dns, now)
        _check_security(a, standard)
        _check_template(a, standard)
        _check_wlans(a, standard)
        per_ap = _check_aps(a, standard, dns, name_re, name_re_error, subnets, names_seen, ips_seen)
        _check_radios(a, standard)
        clients_out, health = _check_clients(a, standard)
        client_event_rows.extend(_check_client_events(a, standard, clients_out, health))
        _check_logs(a, log_rows)
        group_rows.extend(_check_groups(a, standard, wncd_rows))
        r2o_rows.extend(_run_r2o(a, standard, findings))
        ap_issue_counts = Counter(fd["subject"] for fd in findings if fd["controller_id"] == item.device_id and fd["category"] in ("Access points", "AP naming & DNS", "Radios"))
        for row in per_ap:
            row["issues"] = ap_issue_counts.get(row["name"], 0)
        ap_rows.extend(per_ap)
        client_rows.extend(clients_out)
        client_health.append(health)
        controllers.append(_controller_out(f, a))

    severity_counts = Counter(fd["severity"] for fd in findings)
    return {
        "generated_at": now,
        "dns_checked": dns is not None,
        "summary": {
            "controllers": len(facts),
            "controllers_collected": sum(1 for f in facts if f.input.content),
            "access_points": len(ap_rows),
            "aps_with_issues": sum(1 for r in ap_rows if r["issues"]),
            "clients": sum(1 for r in client_rows if r.get("connected", True)),
            "clients_poor": sum(1 for r in client_rows if r["health"] == "poor"),
            "fail": severity_counts.get("fail", 0),
            "warn": severity_counts.get("warn", 0),
            "info": severity_counts.get("info", 0),
        },
        "controllers": controllers,
        "findings": sorted(findings, key=lambda fd: ({"fail": 0, "warn": 1, "info": 2}[fd["severity"]], fd["controller_name"], fd["category"], fd["subject"] or "")),
        "access_points": ap_rows,
        "clients": client_rows,
        "client_health": client_health,
        "logs": log_rows,
        "groups": group_rows,
        "wncd": wncd_rows,
        "r2o": r2o_rows,
        "client_events": client_event_rows,
    }


def _controller_out(f: ControllerFacts, a: _Auditor) -> dict:
    checks = [
        {"check_id": c.check_id, "title": c.title, "category": c.category, "status": c.status(), "evaluated": c.evaluated, "issues": len(c.severities), "note": c.note}
        for c in a.checks.values()
    ]
    scored = [c["status"] for c in checks if c["status"] in ("pass", "warn", "fail")]
    score = round(100 * (scored.count("pass") + 0.5 * scored.count("warn")) / len(scored)) if scored else None
    counts = Counter(c["status"] for c in checks)
    return {
        "device_id": f.input.device_id,
        "name": f.input.name,
        "host": f.input.host,
        "platform": f.platform,
        "site": f.input.site,
        "model": f.model,
        "software": f.software,
        "collected_at": f.input.collected_at,
        "snapshot_id": f.input.snapshot_id,
        "ap_count": len(f.aps),
        "client_count": len(f.clients),
        "score": score if f.input.content else None,
        "counts": dict(counts),
        "checks": checks,
        "missing_commands": f.missing_commands,
        "template_profile": a.profile_name,
        "r2o_checklist": a.r2o_name,
        "r2o_counts": a.r2o_counts,
    }


# --- controller -----------------------------------------------------------------------------


def _check_controller(a: _Auditor, s: WirelessStandard, dns: DnsResults | None, now: datetime) -> None:
    f = a.f
    c = a.check("data.snapshot", "Configuration collected recently", "Controller")
    c.evaluated = 1
    if f.input.collected_at:
        collected = f.input.collected_at if f.input.collected_at.tzinfo else f.input.collected_at.replace(tzinfo=timezone.utc)
        age_h = (now - collected).total_seconds() / 3600
        if age_h > s.max_snapshot_age_hours:
            a.find(c, "warn", None, f"The latest snapshot is {age_h:.0f} hours old; the audit reflects the controller as it was then.", f"<= {s.max_snapshot_age_hours} h", f"{age_h:.0f} h")

    c = a.check("data.commands", "Audit commands collected", "Controller")
    c.evaluated = len(AUDIT_COMMANDS[f.input.device_type])
    if f.missing_commands:
        a.find(c, "info", None, "Some checks have no data because these commands are not in the collection: " + ", ".join(f.missing_commands) + ". Add them from the Coverage tab, then collect again.")

    approved = s.approved_9800_versions if f.platform == "9800" else s.approved_aireos_versions
    if approved:
        c = a.check("ctl.software", "Software version approved", "Controller")
        c.evaluated = 1
        if not f.software:
            a.find(c, "warn", None, "The software version could not be read from the snapshot.", ", ".join(approved))
        elif not _prefix_ok(f.software, approved):
            a.find(c, "fail", None, f"Running {f.software}, which is not an approved release.", ", ".join(approved), f.software)

    if s.require_ha:
        c = a.check("ctl.ha", "High availability (SSO hot standby)", "Controller")
        c.evaluated = 1
        if f.redundancy is None:
            a.skip("ctl.ha", c.title, c.category, "No redundancy output collected")
        else:
            peer = (f.redundancy.get("peer_state") or "").upper()
            if "STANDBY HOT" not in peer and "STANDBY-HOT" not in peer:
                a.find(c, "fail", None, "No hot-standby peer: the controller is not running as an SSO pair.", "Peer state STANDBY HOT", f.redundancy.get("peer_state") or "not reported")

    for check_id, title, servers, keywords in (
        ("ctl.ntp", "NTP servers configured", s.ntp_servers, ("ntp",)),
        ("ctl.syslog", "Syslog servers configured", s.syslog_servers, ("logging", "syslog")),
        ("ctl.dns_servers", "DNS servers configured", s.dns_servers, ("name-server", "dns", "domain")),
    ):
        if not servers:
            continue
        c = a.check(check_id, title, "Controller")
        if not f.config_text:
            a.skip(check_id, title, "Controller", "No configuration collected")
            continue
        for server in servers:
            server = server.strip()
            if not server:
                continue
            c.evaluated += 1
            if not _mentions(f.config_text, server, keywords):
                a.find(c, "fail", server, f"{server} is not configured.", server, None)

    c = a.check("ctl.ntp_sync", "Clock synchronised to NTP", "Controller")
    if f.ntp_synced is None:
        a.skip("ctl.ntp_sync", c.title, c.category, "No NTP status collected")
    else:
        c.evaluated = 1
        if not f.ntp_synced:
            a.find(c, "fail", None, "The controller is not synchronised to any NTP server. Log timestamps, certificates and AP joins depend on it.")

    c = a.check("ctl.cpu", "CPU load", "Controller")
    if f.cpu is None:
        a.skip("ctl.cpu", c.title, c.category, "No CPU output collected")
    else:
        c.evaluated = 1
        if f.cpu > s.max_cpu_percent:
            a.find(c, "warn", None, f"CPU at {f.cpu}%.", f"<= {s.max_cpu_percent}%", f"{f.cpu}%")

    if dns is not None:
        c = a.check("ctl.dns", "Controller address agrees with DNS", "Controller")
        c.evaluated = 1
        host = f.input.host.strip()
        if _is_ip(host):
            ptr = dns.reverse.get(host)
            if ptr is None:
                a.find(c, "warn", host, f"{host} has no PTR record.", f.input.name)
            elif not _same_host(ptr, f.input.name):
                a.find(c, "warn", host, f"PTR for {host} is {ptr}, but the device is named {f.input.name}.", f.input.name, ptr)
        elif not dns.forward.get(host):
            a.find(c, "fail", host, f"{host} does not resolve in DNS.")


# --- security ------------------------------------------------------------------------------


def _check_security(a: _Auditor, s: WirelessStandard) -> None:
    if not s.security_checks:
        return
    f = a.f
    if not f.config_text:
        a.skip("sec.mgmt", "Management-plane hardening", "Security", "No configuration collected")
        return
    if f.platform == "9800":
        tree = parse_config(f.config)
        top = [n.text.lower() for n in tree]

        c = a.check("sec.telnet", "Telnet disabled on VTY lines", "Security")
        for node in tree:
            if node.text.lower().startswith("line vty"):
                c.evaluated += 1
                for child in node.children:
                    words = child.text.lower().split()
                    if words[:2] == ["transport", "input"] and ("telnet" in words or "all" in words):
                        a.find(c, "fail", node.text, f"'{child.text}' allows telnet.", "transport input ssh", child.text)

        c = a.check("sec.http", "Plain-text HTTP server disabled", "Security")
        c.evaluated = 1
        if "ip http server" in top:
            a.find(c, "warn", None, "'ip http server' is enabled. Use 'ip http secure-server' only, unless web authentication needs HTTP redirection.", "no ip http server", "ip http server")

        c = a.check("sec.enable", "Enable secret (not enable password)", "Security")
        c.evaluated = 1
        if any(t.startswith("enable password") for t in top):
            a.find(c, "fail", None, "'enable password' is configured; use 'enable secret'.")

        c = a.check("sec.pwenc", "Service password-encryption", "Security")
        c.evaluated = 1
        if "service password-encryption" not in top:
            a.find(c, "warn", None, "'service password-encryption' is not configured.")

        c = a.check("sec.aaa", "AAA new-model", "Security")
        c.evaluated = 1
        if "aaa new-model" not in top:
            a.find(c, "warn", None, "'aaa new-model' is not configured, so admin logins are not using AAA (TACACS+/RADIUS).")
    else:
        ns = f.network_summary
        if ns:
            c = a.check("sec.telnet", "Telnet disabled", "Security")
            c.evaluated = 1
            if ns.get("telnet", "").lower().startswith("enable"):
                a.find(c, "fail", None, "Telnet is enabled on the controller.", "Disable", ns.get("telnet"))
            c = a.check("sec.http", "Plain-text HTTP web mode disabled", "Security")
            c.evaluated = 1
            if ns.get("web mode", "").lower().startswith("enable"):
                a.find(c, "warn", None, "HTTP web mode is enabled; use secure web mode (HTTPS) only.", "Disable", ns.get("web mode"))
        else:
            a.skip("sec.telnet", "Telnet disabled", "Security", "'show network summary' not collected")

    c = a.check("sec.snmp", "No default SNMP communities", "Security")
    c.evaluated = 1
    for line in f.config_text.splitlines():
        low = line.lower()
        if "snmp" in low and "community" in low and re.search(r"\b(public|private)\b", low) and not low.strip().startswith(("no ", "config snmp community delete")):
            a.find(c, "fail", None, "A default SNMP community (public/private) is configured.", None, line.strip())
            break


# --- golden template ------------------------------------------------------------------------


def is_flex(f: ControllerFacts) -> bool:
    """A 9800 built as FlexConnect: a virtual C9800-CL, or one whose site
    tags are all remote ("no local-site")."""
    if f.platform != "9800":
        return False
    if f.model and "9800-CL" in f.model.upper():
        return True
    sites = [t for name, t in (f.tags or {}).get("site", {}).items() if name != "default-site-tag"]
    return bool(sites) and all(not t["local_site"] for t in sites)


def select_profile(f: ControllerFacts, s: WirelessStandard):
    """The template profile a controller is checked against (or None),
    see WirelessStandard.template_profiles."""
    assigned = s.profile_assignments.get(f.input.name) or s.profile_assignments.get(f.input.device_id)
    if assigned:
        if assigned.lower() == "none":
            return None
        for p in s.template_profiles:
            if p.name == assigned:
                return p
    candidates = [p for p in s.template_profiles if p.platform == f.platform]
    if len(candidates) <= 1:
        return candidates[0] if candidates else None
    kind = "flex" if is_flex(f) else "local"
    return next((p for p in candidates if p.kind == kind), candidates[0])


def _check_template(a: _Auditor, s: WirelessStandard) -> None:
    f = a.f
    profile = select_profile(f, s)
    if profile is not None:
        template = profile.template
        title = f"Configuration standard: {profile.name}"
        a.profile_name = profile.name
    else:
        assigned = (s.profile_assignments.get(f.input.name) or "").lower() == "none"
        template = "" if assigned else (s.template_9800 if f.platform == "9800" else s.template_aireos)
        title = "Golden configuration template"
    if not template.strip():
        a.skip("cfg.template", title, "Configuration", "No configuration standard for this platform: import the standard workbook")
        return
    if not f.config:
        a.skip("cfg.template", title, "Configuration", "No running configuration collected")
        return
    variables = {"device_name": f.input.name, "device_host": f.input.host, **s.template_variables}
    result = check_template(template, f.config, variables)
    c = a.check("cfg.template", title, "Configuration")
    c.evaluated = result.rules_checked
    for issue in result.issues:
        where = " > ".join(issue.path) if issue.path else None
        if issue.kind == "missing":
            msg = f"Missing: {issue.rule}" + (f" (in '{where}')" if where else "")
        elif issue.kind == "forbidden":
            msg = f"Must not be configured: {issue.found}" + (f" (in '{where}')" if where else "")
        else:
            msg = f"Template line ignored, it is not valid: {issue.rule}"
        a.find(c, issue.severity, where, msg, issue.rule, issue.found)


# --- WLANs ----------------------------------------------------------------------------------


def _check_wlans(a: _Auditor, s: WirelessStandard) -> None:
    f = a.f
    if not f.wlans:
        a.skip("wlan.security", "WLAN security", "WLANs", "'show wlan summary' not collected or no WLANs")
        return
    by_ssid = {w["ssid"].lower(): w for w in f.wlans}
    if s.required_ssids:
        c = a.check("wlan.required", "Required SSIDs present and enabled", "WLANs")
        for ssid in s.required_ssids:
            if not ssid.strip():
                continue
            c.evaluated += 1
            w = by_ssid.get(ssid.strip().lower())
            if w is None:
                a.find(c, "fail", ssid, f"SSID '{ssid}' is not configured.")
            elif not w["enabled"]:
                a.find(c, "fail", ssid, f"SSID '{ssid}' is configured but disabled.")
        if s.flag_unexpected_ssids:
            c = a.check("wlan.unexpected", "No unexpected SSIDs", "WLANs")
            wanted = {x.strip().lower() for x in s.required_ssids}
            for w in f.wlans:
                c.evaluated += 1
                if w["ssid"].lower() not in wanted and w["enabled"]:
                    a.find(c, "warn", w["ssid"], f"SSID '{w['ssid']}' (WLAN {w['id']}) is not in the standard.")

    allowed_open = {x.strip().lower() for x in s.open_ssids_allowed}
    c = a.check("wlan.security", "WLAN security (no open or TKIP/WPA1 WLANs)", "WLANs")
    for w in f.wlans:
        c.evaluated += 1
        label = f"{w['ssid']} (WLAN {w['id']})"
        if w["open"] and w["ssid"].lower() not in allowed_open:
            how = "open with web authentication" if w.get("webauth") else "open (no layer-2 security)"
            a.find(c, "warn" if w.get("webauth") else "fail", w["ssid"], f"{label} is {how}.")
        if w["tkip"] or w["wpa1"]:
            a.find(c, "fail", w["ssid"], f"{label} still offers WPA1/TKIP, which is deprecated and slows every client on the WLAN.")
        if not w["enabled"]:
            a.find(c, "info", w["ssid"], f"{label} is disabled.")


# --- access points ---------------------------------------------------------------------------


def _check_aps(
    a: _Auditor,
    s: WirelessStandard,
    dns: DnsResults | None,
    name_re: re.Pattern | None,
    name_re_error: str | None,
    subnets: list,
    names_seen: dict[str, list[str]],
    ips_seen: dict[str, list[str]],
) -> list[dict]:
    f = a.f
    rows: list[dict] = []
    if not f.aps:
        a.skip("ap.present", "Access points", "Access points", "No access points in 'show ap summary'")
        return rows

    cat_name = "AP naming & DNS"
    naming = a.check("ap.naming", "AP names follow the naming standard", cat_name)
    if name_re_error:
        naming.note = name_re_error
    elif name_re is None:
        naming.note = "No AP naming pattern set in the standard: only factory-default names and letter case are checked"
    dup_name = a.check("ap.dup_name", "AP names unique", cat_name)
    fwd = a.check("ap.dns_forward", "AP name resolves to the AP's IP (forward DNS)", cat_name)
    rev = a.check("ap.dns_reverse", "AP IP resolves back to its name (PTR)", cat_name)
    if dns is None or not s.check_dns:
        fwd.skipped = rev.skipped = True
        fwd.note = rev.note = "DNS lookups were not run"
    elif not s.check_reverse_dns:
        rev.skipped = True
        rev.note = "Reverse DNS check is off in the standard"

    cat = "Access points"
    subnet_c = a.check("ap.subnet", "AP addresses in the AP management subnets", cat)
    if not subnets:
        subnet_c.skipped = True
        subnet_c.note = "No AP subnets set in the standard"
    dup_ip = a.check("ap.dup_ip", "AP IP addresses unique", cat)
    model_c = a.check("ap.model", "AP models approved", cat)
    if not s.approved_ap_models:
        model_c.skipped = True
        model_c.note = "No approved AP models set in the standard"
    sw_c = a.check("ap.software", "AP software consistent", cat)
    state_c = a.check("ap.state", "APs registered", cat)
    country_c = a.check("ap.country", "AP country code", cat)
    if not s.country_codes:
        country_c.skipped = True
        country_c.note = "No country codes set in the standard"
    uplink_c = a.check("ap.uplink", "AP uplink speed and duplex", cat)
    tags_c = a.check("ap.tags", "AP tags assigned (not default)", cat) if f.platform == "9800" else None
    primary_c = a.check("ap.primary", "AP primary controller primed", cat) if f.platform == "9800" and s.require_primary_controller else None
    load_c = a.check("ap.load", "Clients per AP", cat)

    software_counts = Counter(ap["software"] for ap in f.aps if ap["software"])
    common_sw = software_counts.most_common(1)[0][0] if software_counts else None
    clients_per_ap = Counter(cl["ap_name"] for cl in f.clients if cl["ap_name"])
    radios_by_ap: dict[str, list[dict]] = defaultdict(list)
    for r in f.radios:
        radios_by_ap[r["name"]].append(r)

    for ap in f.aps:
        name = ap["name"]
        row = {
            "controller_id": f.input.device_id,
            "controller_name": f.input.name,
            "name": name,
            "model": ap["model"],
            "mac": ap["mac"],
            "ip": ap["ip"],
            "serial": ap["serial"],
            "software": ap["software"],
            "state": ap["state"],
            "country": ap["country"],
            "clients": ap["clients"] if ap["clients"] is not None else (clients_per_ap.get(name) if f.clients else None),
            "ap_group": ap.get("ap_group"),
            "site_tag": (ap["tags"] or {}).get("site_tag"),
            "policy_tag": (ap["tags"] or {}).get("policy_tag"),
            "rf_tag": (ap["tags"] or {}).get("rf_tag"),
            "primary_controller": (ap["general"] or {}).get("primary"),
            "uplink": None,
            "radios": " · ".join(
                f"{r['band']}G" + (f" ch{r['channel']}" if r["channel"] else "") + f" {r['oper'] if r['admin'] == 'Enabled' else 'disabled'}"
                for r in sorted(radios_by_ap.get(name, []), key=lambda r: r["slot"])
            )
            or None,
            "fqdn": None,
            "dns_ips": [],
            "ptr": None,
            "name_ok": None,
            "subnet_ok": None,
            "dns_ok": None,
            "ptr_ok": None,
        }

        # Naming.
        naming.evaluated += 1
        ok = True
        if name_re is not None and not name_re.fullmatch(name):
            ok = False
            a.find(naming, "fail", name, f"'{name}' does not match the naming standard.", s.ap_name_pattern, name)
        if s.ap_name_case == "upper" and name != name.upper():
            ok = False
            a.find(naming, "warn", name, f"'{name}' is not upper case.", name.upper(), name)
        elif s.ap_name_case == "lower" and name != name.lower():
            ok = False
            a.find(naming, "warn", name, f"'{name}' is not lower case.", name.lower(), name)
        if re.match(r"^(AP)?[0-9A-Fa-f]{4}[.:-]?[0-9A-Fa-f]{4}[.:-]?[0-9A-Fa-f]{4}$", name):
            ok = False
            a.find(naming, "fail", name, f"'{name}' still has its factory default (MAC-based) name.")
        row["name_ok"] = ok
        dup_name.evaluated += 1
        if len(names_seen.get(name.lower(), [])) > 1:
            a.find(dup_name, "warn", name, f"'{name}' is listed on more than one controller: {', '.join(sorted(set(names_seen[name.lower()])))}.")

        # Addressing.
        ip = ap["ip"]
        if ip:
            try:
                addr = ipaddress.ip_address(ip)
            except ValueError:
                addr = None
            if subnets and addr is not None:
                subnet_c.evaluated += 1
                row["subnet_ok"] = any(addr in net for net in subnets)
                if not row["subnet_ok"]:
                    a.find(subnet_c, "fail", name, f"{ip} is outside the AP management subnets.", ", ".join(str(n) for n in subnets), ip)
            dup_ip.evaluated += 1
            if len(ips_seen.get(ip, [])) > 1:
                a.find(dup_ip, "fail", name, f"{ip} is also used by {', '.join(n for n in ips_seen[ip] if n != name)}.")

        # DNS.
        if dns is not None and s.check_dns:
            fqdn = ap_fqdn(name, s.dns_domain)
            row["fqdn"] = fqdn
            resolved = dns.forward.get(fqdn, [])
            row["dns_ips"] = resolved
            fwd.evaluated += 1
            if not resolved:
                row["dns_ok"] = False
                a.find(fwd, "fail", name, f"{fqdn} has no DNS record.", ip, None)
            elif ip and ip not in resolved:
                row["dns_ok"] = False
                a.find(fwd, "fail", name, f"{fqdn} resolves to {', '.join(resolved)} but the AP's address is {ip}.", ip, ", ".join(resolved))
            else:
                row["dns_ok"] = True
            if s.check_reverse_dns and ip:
                rev.evaluated += 1
                ptr = dns.reverse.get(ip)
                row["ptr"] = ptr
                if ptr is None:
                    row["ptr_ok"] = False
                    a.find(rev, "warn", name, f"{ip} has no PTR record.", fqdn, None)
                elif not _same_host(ptr, name):
                    row["ptr_ok"] = False
                    a.find(rev, "fail", name, f"PTR for {ip} is {ptr}, not {fqdn}.", fqdn, ptr)
                else:
                    row["ptr_ok"] = True

        # Model / software / state / country.
        if s.approved_ap_models:
            model_c.evaluated += 1
            if not _prefix_ok(ap["model"], s.approved_ap_models):
                a.find(model_c, "warn", name, f"Model {ap['model'] or 'unknown'} is not on the approved list.", ", ".join(s.approved_ap_models), ap["model"])
        if ap["software"] and common_sw:
            sw_c.evaluated += 1
            if ap["software"] != common_sw:
                a.find(sw_c, "warn", name, f"Running {ap['software']} while most APs run {common_sw}: likely a stuck or pending image download.", common_sw, ap["software"])
        if ap["state"]:
            state_c.evaluated += 1
            if ap["state"].lower() != "registered":
                a.find(state_c, "fail", name, f"AP state is '{ap['state']}'.", "Registered", ap["state"])
        if s.country_codes and ap["country"]:
            country_c.evaluated += 1
            if ap["country"].upper() not in {c.strip().upper() for c in s.country_codes}:
                a.find(country_c, "fail", name, f"Country code {ap['country']}.", ", ".join(s.country_codes), ap["country"])

        # Uplink.
        eth = ap["ethernet"]
        if eth:
            uplink_c.evaluated += 1
            row["uplink"] = " ".join(x for x in ((f"{eth['speed_mbps']} Mbps" if eth["speed_mbps"] else None), eth["duplex"], (None if eth["up"] is not False else "down")) if x) or None
            if eth["duplex"] == "Half" and s.require_full_duplex:
                a.find(uplink_c, "fail", name, "Uplink is half duplex: check the switch port and cable.", "Full", "Half")
            if eth["speed_mbps"] is not None and eth["speed_mbps"] < s.min_ap_uplink_mbps:
                a.find(uplink_c, "warn", name, f"Uplink negotiated {eth['speed_mbps']} Mbps.", f">= {s.min_ap_uplink_mbps} Mbps", f"{eth['speed_mbps']} Mbps")

        # 9800 tags and HA priming.
        if tags_c is not None and ap["tags"]:
            tags_c.evaluated += 1
            t = ap["tags"]
            if t["misconfigured"]:
                a.find(tags_c, "fail", name, "The controller flags this AP's tags as misconfigured.")
            if s.require_non_default_tags:
                defaults = [v for v in (t["site_tag"], t["policy_tag"], t["rf_tag"]) if v and _DEFAULT_TAG.match(v)]
                if defaults:
                    a.find(tags_c, "warn", name, f"On default tag(s): {', '.join(defaults)}.")
        if primary_c is not None and ap["general"] is not None:
            primary_c.evaluated += 1
            if not ap["general"].get("primary"):
                a.find(primary_c, "warn", name, "No primary controller configured: the AP will join whichever controller answers discovery first.")

        if row["clients"] is not None:
            load_c.evaluated += 1
            if row["clients"] > s.max_clients_per_ap:
                a.find(load_c, "warn", name, f"{row['clients']} clients on one AP.", f"<= {s.max_clients_per_ap}", str(row["clients"]))
        rows.append(row)

    for chk, why in ((uplink_c, "No AP Ethernet statistics collected"), (state_c, "AP state not in 'show ap summary' (AireOS does not show it)")):
        if chk.evaluated == 0 and not chk.severities:
            chk.skipped = True
            chk.note = why
    if tags_c is not None and tags_c.evaluated == 0:
        tags_c.skipped, tags_c.note = True, "'show ap tag summary' not collected"
    if primary_c is not None and primary_c.evaluated == 0:
        primary_c.skipped, primary_c.note = True, "Primary controller not in 'show ap config general' output"
    if load_c.evaluated == 0:
        load_c.skipped, load_c.note = True, "No client counts collected"
    return rows


# --- radios ----------------------------------------------------------------------------------


def _check_radios(a: _Auditor, s: WirelessStandard) -> None:
    f = a.f
    if not f.radios:
        a.skip("radio.state", "Radios up", "Radios", "No radio summaries collected")
        return
    c = a.check("radio.state", "Radios up", "Radios")
    ch = a.check("radio.channel24", "2.4 GHz channels in the channel plan", "Radios")
    allowed = set(s.allowed_24ghz_channels)
    for r in f.radios:
        c.evaluated += 1
        label = f"{r['band']} GHz radio (slot {r['slot']})"
        if r["admin"] == "Disabled":
            if s.flag_disabled_radios:
                a.find(c, "warn", r["name"], f"{label} is administratively disabled.")
        elif r["oper"].lower() not in ("up", "up*"):
            a.find(c, "fail", r["name"], f"{label} is enabled but operationally {r['oper']}.", "Up", r["oper"])
        if r["band"] == "2.4" and r["channel"] and allowed and r["admin"] == "Enabled":
            ch.evaluated += 1
            if r["channel"] not in allowed:
                a.find(ch, "warn", r["name"], f"2.4 GHz radio on channel {r['channel']}: overlapping channels cause co-channel interference.", ", ".join(str(x) for x in sorted(allowed)), str(r["channel"]))
    if ch.evaluated == 0 and not ch.severities:
        ch.skipped, ch.note = True, "No 2.4 GHz radios with a channel"


# --- clients ---------------------------------------------------------------------------------


def _check_clients(a: _Auditor, s: WirelessStandard) -> tuple[list[dict], dict]:
    f = a.f
    rows: list[dict] = []
    health = {
        "controller_id": f.input.device_id,
        "controller_name": f.input.name,
        "total": len(f.clients),
        "with_rf_detail": 0,
        "good": 0,
        "fair": 0,
        "poor": 0,
        "avg_rssi": None,
        "avg_snr": None,
        "band_24": 0,
        "band_5": 0,
        "band_6": 0,
        "not_run": 0,
        "no_ip": 0,
        "legacy": 0,
    }
    if not f.clients:
        a.skip("client.rf", "Client signal (RSSI / SNR)", "Clients", "No wireless clients in the snapshot")
        health["delete_reasons"] = f.delete_reasons
        return rows, health
    rf = a.check("client.rf", "Client signal (RSSI / SNR)", "Clients")
    st = a.check("client.state", "Clients fully connected (RUN state)", "Clients")
    ipc = a.check("client.ip", "Clients have a valid IP address", "Clients")
    leg = a.check("client.legacy", "No legacy 802.11a/b/g clients", "Clients") if s.flag_legacy_clients else None
    rssis: list[int] = []
    snrs: list[int] = []
    for cl in f.clients:
        issues: list[str] = []
        subject = cl["mac"]
        band = _band(cl["protocol"])
        if band == "2.4":
            health["band_24"] += 1
        elif band == "6":
            health["band_6"] += 1
        elif band == "5":
            health["band_5"] += 1

        quality = "unknown"
        if cl["rssi"] is not None or cl["snr"] is not None:
            health["with_rf_detail"] += 1
            rf.evaluated += 1
            quality = "good"
            if cl["rssi"] is not None:
                rssis.append(cl["rssi"])
                if cl["rssi"] < s.client_rssi_fail:
                    quality = "poor"
                    issues.append(f"RSSI {cl['rssi']} dBm")
                elif cl["rssi"] < s.client_rssi_warn:
                    quality = "fair"
                    issues.append(f"RSSI {cl['rssi']} dBm")
            if cl["snr"] is not None:
                snrs.append(cl["snr"])
                if cl["snr"] < s.client_snr_fail:
                    quality = "poor"
                    issues.append(f"SNR {cl['snr']} dB")
                elif cl["snr"] < s.client_snr_warn:
                    quality = "fair" if quality == "good" else quality
                    issues.append(f"SNR {cl['snr']} dB")
            if quality != "good":
                where = f" on {cl['ap_name']}" if cl["ap_name"] else ""
                a.find(rf, "warn" if quality == "fair" else "fail", subject, f"Weak signal{where}: {', '.join(issues)}.", f"RSSI >= {s.client_rssi_warn} dBm, SNR >= {s.client_snr_warn} dB", ", ".join(issues))
        if quality in ("good", "fair", "poor"):
            health[quality] += 1

        state = cl["policy_state"] or cl["state"]
        if state:
            st.evaluated += 1
            ok_state = state.strip().lower() in ("run", "associated") and (cl["policy_state"] in (None, "RUN"))
            if not ok_state:
                health["not_run"] += 1
                hint = _state_hint(state)
                issues.append(f"state {state}")
                a.find(st, "warn", subject, f"Client is in '{state}'{hint}.", "Run", state)
        ipc.evaluated += 1
        if not cl["ip"]:
            health["no_ip"] += 1
            issues.append("no IP")
            a.find(ipc, "warn", subject, "No IP address learned for this client.")
        else:
            try:
                if ipaddress.ip_address(cl["ip"]) in _APIPA:
                    health["no_ip"] += 1
                    issues.append("self-assigned IP")
                    a.find(ipc, "fail", subject, f"Self-assigned address {cl['ip']}: the client got no DHCP lease.", None, cl["ip"])
            except ValueError:
                pass
        if leg is not None and cl["protocol"]:
            leg.evaluated += 1
            if _LEGACY_PROTOCOL.match(cl["protocol"].strip()):
                health["legacy"] += 1
                issues.append("legacy 802.11")
                a.find(leg, "info", subject, f"Connected as {cl['protocol']}: legacy clients use more airtime and slow everyone on the radio.")
        rows.append(
            {
                "controller_id": f.input.device_id,
                "controller_name": f.input.name,
                "mac": cl["mac"],
                "manufacturer": cl["manufacturer"],
                "ip": cl["ip"],
                "username": cl["username"],
                "ap_name": cl["ap_name"],
                "ssid": cl["ssid"],
                "state": state,
                "protocol": cl["protocol"],
                "rssi": cl["rssi"],
                "snr": cl["snr"],
                "health": quality,
                "issues": issues,
            }
        )
    if rf.evaluated == 0:
        rf.skipped, rf.note = True, "Per-client detail (RSSI/SNR) not collected"
    health["delete_reasons"] = f.delete_reasons
    health["avg_rssi"] = round(sum(rssis) / len(rssis), 1) if rssis else None
    health["avg_snr"] = round(sum(snrs) / len(snrs), 1) if snrs else None
    return rows, health


def _state_hint(state: str) -> str:
    s = state.lower()
    if "ip" in s or "dhcp" in s:
        return " - waiting for an IP address; check DHCP and the client VLAN"
    if "auth" in s or "8021x" in s or "l2" in s:
        return " - stuck in authentication; check RADIUS/ISE and the key"
    if "web" in s:
        return " - waiting on web authentication (guest portal)"
    return ""


# --- logs ------------------------------------------------------------------------------------


def _check_logs(a: _Auditor, log_rows: list[dict]) -> None:
    f = a.f
    if f.syslog is None and f.traplog is None:
        a.skip("log.syslog", "Controller log", "Logs", "No log output collected ('show logging' / 'show msglog')")
        return
    if f.syslog is not None:
        c = a.check("log.syslog", "No critical or error messages in the log", "Logs")
        c.evaluated = sum(g["count"] for g in f.syslog)
        for g in f.syslog:
            if g["severity"] <= 3 or g["notable"]:
                severity = "fail" if g["severity"] <= 2 else "warn"
                a.find(c, severity, g["key"], f"{g['key']} logged {g['count']} time(s). Latest: {g['example']}")
            if g["severity"] <= 5 or g["notable"]:
                log_rows.append({"controller_id": f.input.device_id, "controller_name": f.input.name, "source": "syslog", "severity": g["severity"], **{k: g[k] for k in ("key", "count", "example", "notable")}})
    if f.traplog is not None:
        c = a.check("log.traplog", "Trap log", "Logs")
        c.evaluated = sum(g["count"] for g in f.traplog)
        for i, g in enumerate(f.traplog):
            if g["notable"]:
                a.find(c, "warn", g["key"], f"'{g['key']}' trapped {g['count']} time(s). Latest: {g['example']}")
            if g["notable"] or i < 15:
                log_rows.append({"controller_id": f.input.device_id, "controller_name": f.input.name, "source": "traplog", "severity": None, **{k: g[k] for k in ("key", "count", "example", "notable")}})


# --- AP groups / tags / WNCD -----------------------------------------------------------------


def _generation(model: str | None, legacy: list[str]) -> str | None:
    if not model:
        return None
    m = model.upper()
    if any(m.startswith(p.strip().upper()) for p in legacy if p.strip()):
        return "802.11ac"
    if m.startswith(("C91", "CW91", "C92", "CW92")):
        return "802.11ax"
    return None


def _models_summary(models: list[str | None]) -> str | None:
    counts = Counter(m for m in models if m)
    return ", ".join(f"{m} ×{n}" for m, n in counts.most_common()) or None


def _check_groups(a: _Auditor, s: WirelessStandard, wncd_rows: list[dict]) -> list[dict]:
    f = a.f
    rows: list[dict] = []
    pattern = None
    if s.ap_group_name_pattern.strip():
        try:
            pattern = re.compile(s.ap_group_name_pattern.strip())
        except re.error:
            pattern = None
    cat = "AP groups & tags"
    naming = a.check("grp.naming", "AP group / site tag names follow the standard", cat)
    if pattern is None:
        naming.skipped, naming.note = True, "No AP group naming pattern set in the standard"
    default_c = a.check("grp.default", "No APs in the default AP group / site tag", cat)
    mixed_c = a.check("grp.mixed", "802.11ac and 802.11ax APs in separate groups", cat) if s.flag_mixed_ap_generations else None
    empty_c = a.check("grp.unused", "Groups and tags in use", cat)

    def group_checks(name: str, aps: list[dict], is_default: bool, subject_kind: str) -> None:
        if pattern is not None and not is_default:
            naming.evaluated += 1
            if not pattern.fullmatch(name):
                a.find(naming, "fail", name, f"{subject_kind} '{name}' does not match the naming standard.", s.ap_group_name_pattern, name)
        if is_default and aps and s.flag_default_ap_group:
            a.find(default_c, "warn", name, f"{len(aps)} AP(s) in {name}: they get the default WLANs and RF settings, not the site's.", None, ", ".join(ap["name"] for ap in aps[:10]) + (" ..." if len(aps) > 10 else ""))
        default_c.evaluated += 1 if is_default else 0
        if mixed_c is not None and aps:
            mixed_c.evaluated += 1
            gens = {g for g in (_generation(ap.get("model"), s.legacy_ap_models) for ap in aps) if g}
            if len(gens) > 1:
                legacy = [ap["name"] for ap in aps if _generation(ap.get("model"), s.legacy_ap_models) == "802.11ac"]
                a.find(mixed_c, "warn", name, f"{subject_kind} '{name}' mixes 802.11ac Wave 2 and 802.11ax APs ({len(legacy)} legacy): 802.11ax has to stay disabled on its WLANs. Move them to their own group and RF profile.")
        if not aps and not is_default:
            empty_c.evaluated += 1
            a.find(empty_c, "info", name, f"{subject_kind} '{name}' has no APs.")

    if f.platform == "aireos":
        if f.ap_groups is None:
            a.skip("grp.naming", naming.title, cat, "'show wlan apgroups' not collected")
            return rows
        models = {ap["name"]: ap["model"] for ap in f.aps}
        for g in f.ap_groups:
            aps = [{"name": ap["name"], "model": ap["model"] or models.get(ap["name"])} for ap in g["aps"]]
            is_default = g["name"].lower() == "default-group"
            group_checks(g["name"], aps, is_default, "AP group")
            rows.append(
                {
                    "controller_id": f.input.device_id, "controller_name": f.input.name, "kind": "AP group", "name": g["name"],
                    "description": g["description"], "ap_count": len(aps), "models": _models_summary([ap["model"] for ap in aps]),
                    "wlans": ", ".join(g["wlans"]) or None,
                    "profiles": ", ".join(x for x in (f"2.4 GHz {g['rf_24']}" if g["rf_24"] else None, f"5 GHz {g['rf_5']}" if g["rf_5"] else None) if x) or None,
                    "wncd": None,
                }
            )
    else:
        tag_aps: dict[tuple[str, str], list[dict]] = defaultdict(list)
        for ap in f.aps:
            for kind in ("site", "policy", "rf"):
                t = (ap["tags"] or {}).get(f"{kind}_tag")
                if t:
                    tag_aps[(kind, t)].append({"name": ap["name"], "model": ap["model"]})
        defined = f.tags or {"site": {}, "policy": {}, "rf": {}}
        wncd_of = {r["tag"]: r["instance"] for r in (f.wncd_affinity or [])}
        names: dict[str, set[str]] = {k: set(v) for k, v in defined.items()}
        for (kind, t) in tag_aps:
            names[kind].add(t)
        if not any(names.values()):
            a.skip("grp.naming", naming.title, cat, "No tags in the configuration or 'show ap tag summary'")
            return rows
        flex_c = a.check("grp.flex_size", "FlexConnect site tags within the AP limit", cat)
        for kind, label in (("site", "Site tag"), ("policy", "Policy tag"), ("rf", "RF tag")):
            for t in sorted(names[kind], key=str.lower):
                aps = tag_aps.get((kind, t), [])
                conf = defined.get(kind, {}).get(t, {})
                is_default = t.lower() == f"default-{kind}-tag"
                if kind == "site":
                    group_checks(t, aps, is_default, label)
                    if conf and not conf.get("local_site", True):
                        flex_c.evaluated += 1
                        if len(aps) > s.max_aps_per_flex_site_tag:
                            a.find(flex_c, "warn", t, f"FlexConnect site tag '{t}' has {len(aps)} APs; keep a flex site tag under {s.max_aps_per_flex_site_tag} so its WNCD process and PMK propagation keep up.", f"<= {s.max_aps_per_flex_site_tag}", str(len(aps)))
                elif is_default and aps and s.flag_default_ap_group:
                    default_c.evaluated += 1
                    a.find(default_c, "warn", t, f"{len(aps)} AP(s) on {t}.")
                profiles = None
                if kind == "site":
                    profiles = ", ".join(x for x in (f"AP profile {conf.get('ap_profile')}" if conf.get("ap_profile") else None, f"flex {conf.get('flex_profile')}" if conf.get("flex_profile") else None, None if conf.get("local_site", True) else "remote (no local-site)") if x) or None
                elif kind == "rf":
                    profiles = ", ".join(f"{band} {name}" for band, name in conf.get("rf", {}).items()) or None
                rows.append(
                    {
                        "controller_id": f.input.device_id, "controller_name": f.input.name, "kind": label, "name": t,
                        "description": conf.get("description"), "ap_count": len(aps), "models": _models_summary([ap["model"] for ap in aps]),
                        "wlans": ", ".join(f"{w} → {p}" for w, p in conf.get("wlans", {}).items()) or None if kind == "policy" else None,
                        "profiles": profiles, "wncd": wncd_of.get(t) if kind == "site" else None,
                    }
                )
        if flex_c.evaluated == 0:
            flex_c.skipped, flex_c.note = True, "No FlexConnect site tags"
        _check_wncd(a, s, wncd_rows)
    for chk in (default_c, empty_c):
        if chk.evaluated == 0 and not chk.severities:
            chk.skipped, chk.note = True, "Nothing to evaluate"
    issue_counts = Counter(fd["subject"] for fd in a.findings if fd["controller_id"] == f.input.device_id and fd["category"] == cat)
    for r in rows:
        r["issues"] = issue_counts.get(r["name"], 0)
    return rows


def _check_wncd(a: _Auditor, s: WirelessStandard, wncd_rows: list[dict]) -> None:
    """9800 WNCD: each site tag is pinned to one WNCD process; APs should
    be spread across all of them and none should run hot."""
    f = a.f
    cat = "WNCD"
    if f.wncd_affinity is None and f.wncd_cpu is None:
        a.skip("wncd.balance", "APs balanced across WNCD instances", cat, "WNCD commands not collected")
        return
    instances: dict[int, dict] = {}
    for i, cpu in (f.wncd_cpu or {}).items():
        instances.setdefault(i, {"tags": [], "aps": 0, "cpu": None})["cpu"] = cpu
    for r in f.wncd_affinity or []:
        inst = instances.setdefault(r["instance"], {"tags": [], "aps": 0, "cpu": None})
        inst["tags"].append(r["tag"])
        inst["aps"] += r["aps"]
    for i in sorted(instances):
        inst = instances[i]
        wncd_rows.append({"controller_id": f.input.device_id, "controller_name": f.input.name, "instance": i, "site_tags": inst["tags"], "aps": inst["aps"], "cpu_5min": inst["cpu"]})

    bal = a.check("wncd.balance", "APs balanced across WNCD instances", cat)
    if f.wncd_affinity is not None and instances:
        counts = [inst["aps"] for inst in instances.values()]
        total = sum(counts)
        bal.evaluated = len(instances)
        if total >= 10 * len(instances) and len(instances) > 1:
            mean = total / len(instances)
            busiest = max(instances, key=lambda i: instances[i]["aps"])
            idle = [i for i, inst in instances.items() if inst["aps"] == 0]
            if instances[busiest]["aps"] > s.wncd_imbalance_ratio * mean:
                a.find(bal, "warn", f"wncd_{busiest}", f"WNCD {busiest} carries {instances[busiest]['aps']} of {total} APs (average {mean:.0f}). Split large site tags so APs spread across all {len(instances)} instances.", f"<= {s.wncd_imbalance_ratio:g}× average", str(instances[busiest]["aps"]))
            if idle:
                a.find(bal, "info", None, f"WNCD instance(s) {', '.join(str(i) for i in sorted(idle))} carry no APs: there are fewer busy site tags than WNCD processes.")
    elif f.wncd_affinity is None:
        bal.skipped, bal.note = True, "'show wireless loadbalance tag affinity' not collected"
    cpu = a.check("wncd.cpu", "WNCD CPU", cat)
    if f.wncd_cpu:
        for i, value in sorted(f.wncd_cpu.items()):
            cpu.evaluated += 1
            if value > s.max_wncd_cpu_percent:
                a.find(cpu, "warn", f"wncd_{i}", f"WNCD {i} at {value}% CPU (5 min).", f"<= {s.max_wncd_cpu_percent}%", f"{value}%")
    else:
        cpu.skipped, cpu.note = True, "WNCD CPU not collected"


# --- Return to Operations --------------------------------------------------------------------


def select_checklist(f: ControllerFacts, s: WirelessStandard):
    assigned = s.r2o_assignments.get(f.input.name) or s.r2o_assignments.get(f.input.device_id)
    if assigned:
        if assigned.lower() == "none":
            return None
        for c in s.r2o_checklists:
            if c.name == assigned:
                return c
    candidates = [c for c in s.r2o_checklists if c.platform == f.platform]
    if len(candidates) <= 1:
        return candidates[0] if candidates else None
    kind = "flex" if is_flex(f) else "local"
    return next((c for c in candidates if c.kind == kind), candidates[0])


def _run_r2o(a: _Auditor, s: WirelessStandard, findings: list[dict]) -> list[dict]:
    f = a.f
    checklist = select_checklist(f, s)
    if checklist is None or not f.input.content:
        return []
    a.r2o_name = checklist.name
    by_check: dict[str, list[dict]] = defaultdict(list)
    for fd in findings:
        if fd["controller_id"] == f.input.device_id:
            by_check[fd["check_id"]].append(fd)
    profile = select_profile(f, s)
    template = profile.template if profile else (s.template_9800 if f.platform == "9800" else s.template_aireos)
    ap_names = [ap["name"] for ap in f.aps]

    def fix_lines(item) -> list[str]:
        hints = r2o_engine.fix_hints(item, f.platform)
        lines = r2o_engine.standard_lines(item, template, f.platform)
        out = list(hints)
        if lines:
            out.append(f"Per the configuration standard{' (' + profile.name + ')' if profile else ''}:")
            out += [re.sub(r"^(\s*)(?:optional|absent):\s*", r"\1", line) for line in lines]
        return out

    rows = []
    for model in checklist.items:
        if not model.enabled:
            continue
        item = r2o_engine.R2OItem(
            item_id=model.item_id, row=model.row, description=model.description, commands=model.commands,
            expected=model.expected, criteria=model.criteria, stage=model.stage, level=model.level, comments=model.comments,
        )
        result = r2o_engine.evaluate(item, f.input.content, f.input.name, ap_names, by_check, fix_lines)
        a.r2o_counts[result.status] = a.r2o_counts.get(result.status, 0) + 1
        rows.append(
            {
                "controller_id": f.input.device_id, "controller_name": f.input.name, "checklist": checklist.name,
                "item_id": item.item_id, "row": item.row, "description": item.description, "commands": result.commands_run or item.commands,
                "stage": item.stage, "level": item.level, "status": result.status, "details": result.details, "output": result.output, "fix": result.fix,
            }
        )
    return rows


def r2o_commands(s: WirelessStandard, platform: str) -> list[str]:
    """Every command the org's R2O checklists for a platform run, with
    their {site}/{ap} placeholders, in checklist order."""
    commands: list[str] = []
    for c in s.r2o_checklists:
        if c.platform != platform:
            continue
        for item in c.items:
            for cmd in item.commands:
                if cmd not in commands:
                    commands.append(cmd)
    return commands


# --- client events (logs, roams, drops) ------------------------------------------------------------

MAX_EVENTS_PER_CLIENT = 200


def history_out(f: ControllerFacts, h: ce.ClientHistory, connected: bool, issues: list[str]) -> dict:
    return {
        "controller_id": f.input.device_id,
        "controller_name": f.input.name,
        "mac": h.mac,
        "connected": connected,
        "roams": h.roam_count,
        "drops": h.count("drop"),
        "auth_failures": h.count("auth_failure"),
        "dhcp_failures": h.count("dhcp_failure"),
        "exclusions": h.count("exclusion"),
        "roam_history": h.roams,
        "counters": h.counters,
        "issues": issues,
        "events": [
            {"kind": e.kind, "time": e.time, "source": e.source, "reason": e.reason, "text": e.text}
            for e in h.events[-MAX_EVENTS_PER_CLIENT:]
        ],
    }


def _check_client_events(a: _Auditor, s: WirelessStandard, client_rows: list[dict], health: dict) -> list[dict]:
    """Drops, roams, authentication / DHCP failures and exclusions per
    client, from the logs and per-client commands. Clients only seen in the
    logs (gone by the time of the collection) are reported too."""
    f = a.f
    if not f.client_history and not f.delete_reasons:
        a.skip("client.events", "Client drops, roams and failures", "Clients", "No client messages in the collected logs")
        return []
    c = a.check("client.events", "Client drops, roams and failures", "Clients")
    out: list[dict] = []
    by_mac = {row["mac"]: row for row in client_rows}
    for mac, h in sorted(f.client_history.items()):
        c.evaluated += 1
        found = ce.issues_for(h, roam_warn=s.client_roam_warn, retry_warn_percent=s.client_retry_warn_percent)
        row = by_mac.get(mac)
        connected = row is not None
        for severity, message in found:
            a.find(c, severity, mac, message + ("" if connected else " (client no longer associated)"))
        messages = [m for _, m in found]
        if row is not None:
            row["roams"], row["drops"], row["events"] = h.roam_count, h.count("drop"), len(h.events)
            row["issues"] = row["issues"] + messages
        elif found:
            client_rows.append(
                {
                    "controller_id": f.input.device_id, "controller_name": f.input.name, "mac": mac, "manufacturer": None, "ip": None,
                    "username": None, "ap_name": None, "ssid": None, "state": "not associated", "protocol": None, "rssi": None, "snr": None,
                    "health": "unknown", "issues": messages, "roams": h.roam_count, "drops": h.count("drop"), "events": len(h.events), "connected": False,
                }
            )
        if h.events or h.roams or h.counters:
            out.append(history_out(f, h, connected, messages))
    if f.delete_reasons:
        top = ", ".join(f"{r['reason']} ×{r['count']}" for r in f.delete_reasons[:5])
        a.find(c, "info", None, f"Client delete reasons since the counters were cleared: {top}")
    return out


# --- commands added to a controller's collection ----------------------------------------------------

_FULL_COLLECTION = re.compile(r"^(?:sh\w*\s+run\w*(?:-config)?|show running-config|show run-config)\b", re.IGNORECASE)


def augment_commands(commands: list[str], device_type: str, standard: WirelessStandard | None) -> list[str]:
    """A full collection of a controller (one that includes its running
    configuration) also runs what the audit reads and every command of the
    org's R2O checklists for that platform, so neither needs adding to the
    command list by hand. Anything else (a client investigation, a one-off
    command) runs exactly as asked."""
    platform = PLATFORMS.get(device_type)
    if platform is None or not any(_FULL_COLLECTION.match(c.strip()) for c in commands):
        return commands
    out = list(commands)
    for canonical in AUDIT_COMMANDS[device_type]:
        if not any(inv.command_matches(c, canonical) for c in out):
            out.append(canonical)
    if standard is not None:
        have = {" ".join(c.lower().split()) for c in out}
        for cmd in r2o_commands(standard, platform):
            key = " ".join(cmd.lower().split())
            if key not in have:
                have.add(key)
                out.append(cmd)
    return out
