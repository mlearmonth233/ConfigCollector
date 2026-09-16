"""SNMP monitoring: what the monitor polls each cycle, how one cycle's
snapshot is compared with the last, and how the resulting events are
turned into an email. Everything here is pure except take_snapshot (which
uses snmp_poll's network primitives) and send_email (SMTP), both of which
tests swap for fakes.

Events (see SnmpAlertKind):
- link_down / link_up: an interface's ifOperStatus changed. Only interfaces
  that are administratively up count - a port someone shut down on purpose
  isn't an outage - and only *changes* alert, so a port that's been down
  since before monitoring started doesn't page anyone every five minutes.
- ap_down / ap_up: an access point disappeared from the controller's AP
  table (AIRESPACE-WIRELESS-MIB bsnAPTable - AireOS and Catalyst 9800 both
  serve it) or is no longer "associated", or came back.
- device_down / device_up: the device stopped answering SNMP after having
  answered, or started answering again.
- syslog: a *new* entry appeared in the device's syslog history buffer at
  or worse than the configured severity level (Cisco 0=emergencies ...
  7=debugging), tracked by the buffer's monotonically increasing index.

The first snapshot of a device is a baseline: nothing is alerted for it.
"""

from __future__ import annotations

import json
import smtplib
import ssl
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from email.message import EmailMessage

from app.models.snmp_monitor import SnmpAlertKind
from app.services import snmp_poll
from app.services.snmp_poll import IF_COLUMNS, SYSLOG_COLUMNS, SYSTEM_OIDS, SnmpAuth, SnmpError

# AIRESPACE-WIRELESS-MIB::bsnAPTable - indexed by the AP's radio MAC.
AP_COLUMNS = {
    "name": "1.3.6.1.4.1.14179.2.2.1.1.3",  # bsnAPName
    "status": "1.3.6.1.4.1.14179.2.2.1.1.6",  # bsnAPOperationStatus: 1 associated, 2 disassociating, 3 downloading
}

SYSLOG_LEVEL_NAMES = ["emergencies", "alerts", "critical", "errors", "warnings", "notifications", "informational", "debugging"]


@dataclass
class Snapshot:
    reachable: bool = True
    error: str | None = None
    sys_name: str | None = None
    # ifIndex -> {"descr", "admin", "oper"} (statuses as the integer text the MIB returns)
    interfaces: dict[str, dict[str, str]] = field(default_factory=dict)
    # AP index (MAC as OID suffix) -> {"name", "status"}
    aps: dict[str, dict[str, str]] = field(default_factory=dict)
    ap_table_supported: bool = False
    # Highest clogHistoryIndex seen, and the entries newer than the previous
    # snapshot's index (filled by take_snapshot when a previous index is given).
    syslog_max_index: int = 0
    new_syslog: list[dict[str, str]] = field(default_factory=list)

    def to_json(self) -> str:
        data = asdict(self)
        data.pop("new_syslog", None)  # transient - only meaningful within one cycle
        return json.dumps(data)

    @classmethod
    def from_json(cls, text: str) -> "Snapshot":
        data = json.loads(text)
        data.pop("new_syslog", None)
        return cls(**data)


@dataclass(frozen=True)
class Event:
    kind: SnmpAlertKind
    subject: str
    detail: str


@dataclass(frozen=True)
class EventFilter:
    link_down: bool = True
    link_up: bool = False
    ap_down: bool = True
    ap_up: bool = False
    device_down: bool = True
    device_up: bool = True
    syslog_max_level: int | None = None  # None = ignore syslog


def _table(columns: dict[str, str], walked: dict[str, list[tuple[str, str]]]) -> dict[str, dict[str, str]]:
    rows: dict[str, dict[str, str]] = {}
    for column in columns:
        for index, text in walked.get(column, []):
            rows.setdefault(index, {})[column] = text
    return rows


async def take_snapshot(
    host: str,
    auth: SnmpAuth,
    *,
    previous_syslog_index: int = 0,
    watch_syslog: bool = False,
    getter: Callable[..., Awaitable] | None = None,
    walker: Callable[..., Awaitable] | None = None,
) -> Snapshot:
    """One device's current state. A device that doesn't answer the system
    GET is recorded as unreachable (with the classified error) rather than
    raising - that's an event, not a failure of the monitor."""
    get = getter or snmp_poll.snmp_get
    walk = walker or snmp_poll.snmp_walk
    snap = Snapshot()
    try:
        system = await get(host, auth, [SYSTEM_OIDS["sysName"]])
    except SnmpError as exc:
        return Snapshot(reachable=False, error=str(exc))
    snap.sys_name = system.get(SYSTEM_OIDS["sysName"])

    if_cols = {k: IF_COLUMNS[k] for k in ("ifDescr", "ifAdminStatus", "ifOperStatus")}
    walked = {}
    try:
        for column, oid in if_cols.items():
            walked[column] = await walk(host, auth, oid)
        snap.interfaces = {
            idx: {"descr": row.get("ifDescr", f"ifIndex {idx}"), "admin": row.get("ifAdminStatus", ""), "oper": row.get("ifOperStatus", "")}
            for idx, row in _table(if_cols, walked).items()
        }
    except SnmpError:
        pass  # the interface table is optional; reachability already established

    walked = {}
    try:
        for column, oid in AP_COLUMNS.items():
            walked[column] = await walk(host, auth, oid)
        aps = _table(AP_COLUMNS, walked)
        snap.ap_table_supported = bool(aps)
        snap.aps = {idx: {"name": row.get("name", idx), "status": row.get("status", "")} for idx, row in aps.items()}
    except SnmpError:
        pass

    if watch_syslog:
        try:
            walked = {}
            for column, oid in SYSLOG_COLUMNS.items():
                walked[column] = await walk(host, auth, oid)
            entries = _table(SYSLOG_COLUMNS, walked)
            for idx, row in entries.items():
                try:
                    index = int(idx)
                except ValueError:
                    continue
                snap.syslog_max_index = max(snap.syslog_max_index, index)
                if index > previous_syslog_index:
                    snap.new_syslog.append({"index": idx, **row})
        except SnmpError:
            pass
    return snap


def _oper_name(code: str) -> str:
    return snmp_poll.IF_STATUS.get(snmp_poll._int(code), code or "?")


def diff_snapshots(device_name: str, previous: Snapshot | None, current: Snapshot, filters: EventFilter) -> list[Event]:
    """The events between two consecutive snapshots of one device. A
    missing `previous` is the baseline case and yields nothing."""
    if previous is None:
        return []
    events: list[Event] = []

    if previous.reachable and not current.reachable:
        if filters.device_down:
            events.append(Event(SnmpAlertKind.DEVICE_DOWN, device_name, current.error or "No SNMP response"))
        return events  # nothing else can be compared against an unreachable device
    if not previous.reachable and current.reachable:
        if filters.device_up:
            events.append(Event(SnmpAlertKind.DEVICE_UP, device_name, "Answering SNMP again"))
        # Interfaces/APs may well have changed while it was down; without a
        # trustworthy "before", treat this as a fresh baseline for them.
        return events

    if filters.link_down or filters.link_up:
        for idx, before in previous.interfaces.items():
            after = current.interfaces.get(idx)
            if after is None or before.get("admin") != "1" or after.get("admin") != "1":
                continue  # removed, or administratively down on either side
            was_up, is_up = before.get("oper") == "1", after.get("oper") == "1"
            name = after.get("descr") or f"ifIndex {idx}"
            if was_up and not is_up and filters.link_down:
                events.append(Event(SnmpAlertKind.LINK_DOWN, name, f"{name} went {_oper_name(after.get('oper', ''))} (was up)"))
            elif not was_up and is_up and filters.link_up:
                events.append(Event(SnmpAlertKind.LINK_UP, name, f"{name} is up again (was {_oper_name(before.get('oper', ''))})"))

    if (filters.ap_down or filters.ap_up) and previous.ap_table_supported:
        for idx, before in previous.aps.items():
            after = current.aps.get(idx)
            name = before.get("name") or idx
            was_ok = before.get("status") == "1"
            is_ok = after is not None and after.get("status") == "1"
            if was_ok and not is_ok and filters.ap_down:
                detail = f"AP {name} is no longer joined to the controller" if after is None else f"AP {name} status changed to {after.get('status')} (1 = associated)"
                events.append(Event(SnmpAlertKind.AP_DOWN, name, detail))
            elif not was_ok and is_ok and filters.ap_up:
                events.append(Event(SnmpAlertKind.AP_UP, name, f"AP {name} joined the controller"))
        if filters.ap_up:
            for idx, after in current.aps.items():
                if idx not in previous.aps and after.get("status") == "1":
                    events.append(Event(SnmpAlertKind.AP_UP, after.get("name") or idx, f"New AP {after.get('name') or idx} joined the controller"))

    if filters.syslog_max_level is not None:
        for entry in current.new_syslog:
            sev_mib = snmp_poll._int(entry.get("severity"))
            if sev_mib is None:
                continue
            level = sev_mib - 1  # MIB is 1-based, Cisco CLI levels are 0-based
            if level <= filters.syslog_max_level:
                line = f"%{entry.get('facility', '?')}-{level}-{entry.get('msgName', '?')}: {entry.get('msgText', '')}"
                events.append(Event(SnmpAlertKind.SYSLOG, line[:255], line))
    return events


KIND_LABELS = {
    SnmpAlertKind.LINK_DOWN: "Link down",
    SnmpAlertKind.LINK_UP: "Link up",
    SnmpAlertKind.AP_DOWN: "Access point down",
    SnmpAlertKind.AP_UP: "Access point up",
    SnmpAlertKind.DEVICE_DOWN: "Device unreachable",
    SnmpAlertKind.DEVICE_UP: "Device reachable",
    SnmpAlertKind.SYSLOG: "Syslog",
    SnmpAlertKind.PING_DOWN: "Ping: device unreachable",
    SnmpAlertKind.PING_UP: "Ping: device reachable again",
    SnmpAlertKind.CONFIG_CHANGED: "Config changed",
}


def format_email(org_name: str, alerts: list[tuple[str, Event]]) -> tuple[str, str]:
    """(subject, plain-text body) for a batch of (device_name, event)."""
    counts: dict[SnmpAlertKind, int] = {}
    for _, ev in alerts:
        counts[ev.kind] = counts.get(ev.kind, 0) + 1
    summary = ", ".join(f"{n} {KIND_LABELS[k].lower()}" for k, n in counts.items())
    subject = f"[Packrat] {len(alerts)} alert{'s' if len(alerts) != 1 else ''}: {summary}"
    lines = [f"Packrat SNMP monitor for {org_name} detected {len(alerts)} event(s):", ""]
    by_device: dict[str, list[Event]] = {}
    for device, ev in alerts:
        by_device.setdefault(device, []).append(ev)
    for device, events in by_device.items():
        lines.append(device)
        for ev in events:
            lines.append(f"  - [{KIND_LABELS[ev.kind]}] {ev.detail}")
        lines.append("")
    lines.append("Open Packrat > SNMP > Alerts for the full history.")
    return subject, "\n".join(lines)


@dataclass(frozen=True)
class SmtpSettings:
    host: str
    port: int = 587
    username: str | None = None
    password: str | None = None
    starttls: bool = True
    ssl: bool = False
    sender: str | None = None


def send_email(settings: SmtpSettings, recipients: list[str], subject: str, body: str, timeout: float = 20.0) -> None:
    """Sends one plain-text message. Raises smtplib/OSError exceptions to
    the caller, whose job is to record them (never to crash a cycle)."""
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = settings.sender or settings.username or "packrat@localhost"
    msg["To"] = ", ".join(recipients)
    msg.set_content(body)
    if settings.ssl:
        server: smtplib.SMTP = smtplib.SMTP_SSL(settings.host, settings.port, timeout=timeout, context=ssl.create_default_context())
    else:
        server = smtplib.SMTP(settings.host, settings.port, timeout=timeout)
    try:
        server.ehlo()
        if settings.starttls and not settings.ssl:
            server.starttls(context=ssl.create_default_context())
            server.ehlo()
        if settings.username:
            server.login(settings.username, settings.password or "")
        server.send_message(msg)
    finally:
        try:
            server.quit()
        except Exception:  # noqa: BLE001
            pass


def parse_recipients(raw: str | None) -> list[str]:
    return [r.strip() for r in (raw or "").replace(";", ",").replace("\n", ",").split(",") if r.strip()]
