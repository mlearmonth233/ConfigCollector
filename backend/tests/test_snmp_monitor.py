"""SNMP monitoring/alerting: snapshot diffing, email formatting, the config
API (SMTP password write-only, guard rails on enabling), and a full cycle
through run_snmp_monitor_cycle with faked SNMP and SMTP."""

import pytest
from httpx import AsyncClient

from app import tasks as tasks_module
from app.models.snmp_monitor import SnmpAlertKind
from app.services import snmp_monitor
from app.services.snmp_monitor import AP_COLUMNS, Event, EventFilter, Snapshot, diff_snapshots, format_email, take_snapshot
from app.services.snmp_poll import IF_COLUMNS, SYSLOG_COLUMNS, SYSTEM_OIDS, SnmpAuth, SnmpError
from app.models.snmp import SnmpVersion

pytestmark = pytest.mark.asyncio

ALL = EventFilter(link_down=True, link_up=True, ap_down=True, ap_up=True, device_down=True, device_up=True, syslog_max_level=3)


def _snap(interfaces=None, aps=None, reachable=True, ap_supported=None, new_syslog=None):
    return Snapshot(
        reachable=reachable,
        error=None if reachable else "No SNMP response (timed out).",
        interfaces=interfaces or {},
        aps=aps or {},
        ap_table_supported=bool(aps) if ap_supported is None else ap_supported,
        new_syslog=new_syslog or [],
    )


def _if(descr, admin="1", oper="1"):
    return {"descr": descr, "admin": admin, "oper": oper}


# --- diffing -----------------------------------------------------------------------------


def test_first_snapshot_is_a_baseline_with_no_events():
    assert diff_snapshots("sw1", None, _snap({"1": _if("Gi1/0/1", oper="2")}), ALL) == []


def test_link_down_and_up_are_detected_only_for_admin_up_ports():
    before = _snap({"1": _if("Gi1/0/1"), "2": _if("Gi1/0/2", admin="2", oper="2"), "3": _if("Gi1/0/3", oper="2")})
    after = _snap({"1": _if("Gi1/0/1", oper="2"), "2": _if("Gi1/0/2", admin="2", oper="2"), "3": _if("Gi1/0/3")})
    events = diff_snapshots("sw1", before, after, ALL)
    kinds = {(e.kind, e.subject) for e in events}
    assert (SnmpAlertKind.LINK_DOWN, "Gi1/0/1") in kinds
    assert (SnmpAlertKind.LINK_UP, "Gi1/0/3") in kinds
    assert not any(e.subject == "Gi1/0/2" for e in events)  # shut on purpose, never alerts
    # A port that stays down does not re-alert.
    assert diff_snapshots("sw1", after, after, ALL) == []


def test_link_events_respect_the_filter():
    before, after = _snap({"1": _if("Gi1/0/1")}), _snap({"1": _if("Gi1/0/1", oper="2")})
    assert diff_snapshots("sw1", before, after, EventFilter(link_down=False)) == []
    assert [e.kind for e in diff_snapshots("sw1", before, after, EventFilter(link_down=True))] == [SnmpAlertKind.LINK_DOWN]


def test_ap_down_when_it_leaves_the_table_or_stops_being_associated():
    before = _snap(aps={"a": {"name": "AP-LOBBY", "status": "1"}, "b": {"name": "AP-DOCK", "status": "1"}, "c": {"name": "AP-HR", "status": "1"}})
    after = _snap(aps={"a": {"name": "AP-LOBBY", "status": "1"}, "b": {"name": "AP-DOCK", "status": "3"}})
    events = diff_snapshots("wlc1", before, after, ALL)
    assert {e.subject for e in events if e.kind == SnmpAlertKind.AP_DOWN} == {"AP-DOCK", "AP-HR"}
    # ...and back up, including a brand new AP.
    recovered = _snap(aps={"a": {"name": "AP-LOBBY", "status": "1"}, "b": {"name": "AP-DOCK", "status": "1"}, "c": {"name": "AP-HR", "status": "1"}, "d": {"name": "AP-NEW", "status": "1"}})
    ups = {e.subject for e in diff_snapshots("wlc1", after, recovered, ALL) if e.kind == SnmpAlertKind.AP_UP}
    assert ups == {"AP-DOCK", "AP-HR", "AP-NEW"}


def test_no_ap_events_for_a_device_without_an_ap_table():
    before, after = _snap(aps={}, ap_supported=False), _snap(aps={}, ap_supported=False)
    assert diff_snapshots("sw1", before, after, ALL) == []


def test_device_down_then_up_and_nothing_else_while_down():
    up = _snap({"1": _if("Gi1/0/1")})
    down = _snap(reachable=False)
    down_events = diff_snapshots("sw1", up, down, ALL)
    assert [e.kind for e in down_events] == [SnmpAlertKind.DEVICE_DOWN]
    assert "No SNMP response" in down_events[0].detail
    assert diff_snapshots("sw1", down, down, ALL) == []  # still down: no repeat
    # Back up with a port now down: recovery alert only - the port change has no trustworthy "before".
    back = _snap({"1": _if("Gi1/0/1", oper="2")})
    assert [e.kind for e in diff_snapshots("sw1", down, back, ALL)] == [SnmpAlertKind.DEVICE_UP]


def test_syslog_entries_at_or_below_the_level_alert():
    before = _snap()
    after = _snap(new_syslog=[
        {"index": "41", "facility": "LINK", "severity": "4", "msgName": "UPDOWN", "msgText": "Interface Gi1/0/2, changed state to down"},  # level 3 (errors)
        {"index": "42", "facility": "SYS", "severity": "6", "msgName": "CONFIG_I", "msgText": "Configured from console"},  # level 5 (notifications)
    ])
    events = diff_snapshots("sw1", before, after, EventFilter(syslog_max_level=3))
    assert len(events) == 1 and events[0].kind == SnmpAlertKind.SYSLOG
    assert events[0].subject == "%LINK-3-UPDOWN: Interface Gi1/0/2, changed state to down"
    assert diff_snapshots("sw1", before, after, EventFilter(syslog_max_level=None)) == []
    assert len(diff_snapshots("sw1", before, after, EventFilter(syslog_max_level=5))) == 2


def test_format_email_groups_by_device_and_summarises_kinds():
    subject, body = format_email("Northwind", [
        ("HQ-CORE-SW01", Event(SnmpAlertKind.LINK_DOWN, "Gi1/0/1", "Gi1/0/1 went down (was up)")),
        ("HQ-CORE-SW01", Event(SnmpAlertKind.LINK_DOWN, "Gi1/0/2", "Gi1/0/2 went down (was up)")),
        ("HQ-WLC-9800", Event(SnmpAlertKind.AP_DOWN, "AP-LOBBY", "AP AP-LOBBY is no longer joined to the controller")),
    ])
    assert subject == "[Packrat] 3 alerts: 2 link down, 1 access point down"
    assert "HQ-CORE-SW01\n  - [Link down] Gi1/0/1 went down (was up)" in body
    assert "HQ-WLC-9800\n  - [Access point down]" in body


def test_snapshot_round_trips_through_json_without_transient_syslog():
    snap = _snap({"1": _if("Gi1/0/1")}, aps={"a": {"name": "AP", "status": "1"}}, new_syslog=[{"index": "1"}])
    snap.syslog_max_index = 41
    restored = Snapshot.from_json(snap.to_json())
    assert restored.interfaces == snap.interfaces and restored.aps == snap.aps and restored.syslog_max_index == 41
    assert restored.new_syslog == []


# --- take_snapshot over fakes -------------------------------------------------------------


async def test_take_snapshot_records_unreachable_instead_of_raising():
    async def getter(host, auth, oids):
        raise SnmpError("No SNMP response (timed out).")

    snap = await take_snapshot("192.0.2.1", SnmpAuth(SnmpVersion.V2C, community="x"), getter=getter, walker=None)
    assert snap.reachable is False and "timed out" in snap.error


async def test_take_snapshot_collects_interfaces_aps_and_new_syslog():
    tables = {
        IF_COLUMNS["ifDescr"]: [("1", "Gi1/0/1")], IF_COLUMNS["ifAdminStatus"]: [("1", "1")], IF_COLUMNS["ifOperStatus"]: [("1", "2")],
        AP_COLUMNS["name"]: [("0.1.2.3.4.5", "AP-LOBBY")], AP_COLUMNS["status"]: [("0.1.2.3.4.5", "1")],
        SYSLOG_COLUMNS["severity"]: [("40", "4"), ("41", "4")], SYSLOG_COLUMNS["msgText"]: [("40", "old"), ("41", "new")],
    }

    async def getter(host, auth, oids):
        return {SYSTEM_OIDS["sysName"]: "HQ-WLC"}

    async def walker(host, auth, base):
        return tables.get(base, [])

    snap = await take_snapshot("10.0.0.1", SnmpAuth(SnmpVersion.V2C, community="x"), previous_syslog_index=40, watch_syslog=True, getter=getter, walker=walker)
    assert snap.sys_name == "HQ-WLC"
    assert snap.interfaces["1"] == {"descr": "Gi1/0/1", "admin": "1", "oper": "2"}
    assert snap.aps["0.1.2.3.4.5"] == {"name": "AP-LOBBY", "status": "1"} and snap.ap_table_supported
    assert snap.syslog_max_index == 41
    assert [e["index"] for e in snap.new_syslog] == ["41"]


# --- API + full cycle ----------------------------------------------------------------------


async def _register(client: AsyncClient, email: str, org_name: str = "MonitorOrg") -> str:
    resp = await client.post("/api/auth/register", json={"org_name": org_name, "email": email, "password": "password123"})
    assert resp.status_code == 201, resp.text
    return resp.json()["access_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


CONFIG = {
    "enabled": True,
    "interval_minutes": 5,
    "alert_link_down": True,
    "alert_ap_down": True,
    "alert_device_down": True,
    "alert_syslog_max_level": 3,
    "recipients": ["noc@example.com", "oncall@example.com"],
    "smtp_host": "smtp.example.com",
    "smtp_port": 587,
    "smtp_username": "packrat",
    "smtp_password": "smtp-secret-123",
    "smtp_from": "packrat@example.com",
}


async def test_monitor_config_round_trips_with_write_only_password(client: AsyncClient, unique_email):
    token = await _register(client, unique_email)
    initial = await client.get("/api/snmp/monitor", headers=_auth(token))
    assert initial.status_code == 200 and initial.json()["enabled"] is False

    saved = await client.put("/api/snmp/monitor", headers=_auth(token), json=CONFIG)
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["recipients"] == ["noc@example.com", "oncall@example.com"]
    assert body["has_smtp_password"] is True and "smtp-secret-123" not in saved.text
    assert body["next_run_at"] is not None  # enabling schedules the first (baseline) cycle

    again = await client.put("/api/snmp/monitor", headers=_auth(token), json={**CONFIG, "smtp_password": ""})
    assert again.json()["has_smtp_password"] is True  # blank keeps it


@pytest.mark.parametrize(
    ("override", "fragment"),
    [({"recipients": []}, "recipient"), ({"smtp_host": None}, "SMTP server"), ({"recipients": ["not-an-email"]}, "email address")],
)
async def test_enabling_requires_recipients_and_smtp(client: AsyncClient, unique_email, override, fragment):
    token = await _register(client, unique_email)
    resp = await client.put("/api/snmp/monitor", headers=_auth(token), json={**CONFIG, **override})
    assert resp.status_code in (400, 422), resp.text
    assert fragment in resp.text


async def test_non_admin_cannot_change_monitor(client: AsyncClient, unique_email):
    token = await _register(client, unique_email)
    member_email = f"member-{unique_email}"
    await client.post("/api/users", headers=_auth(token), json={"email": member_email, "password": "password123"})
    member = (await client.post("/api/auth/login", json={"email": member_email, "password": "password123"})).json()["access_token"]
    assert (await client.put("/api/snmp/monitor", headers=_auth(member), json=CONFIG)).status_code == 403
    assert (await client.get("/api/snmp/monitor", headers=_auth(member))).status_code == 200


async def test_test_email_uses_saved_settings(client: AsyncClient, unique_email, monkeypatch):
    token = await _register(client, unique_email)
    early = await client.post("/api/snmp/monitor/test-email", headers=_auth(token))
    assert early.json()["ok"] is False and "Save recipients" in early.json()["message"]

    await client.put("/api/snmp/monitor", headers=_auth(token), json=CONFIG)
    sent = {}

    def fake_send(settings, recipients, subject, body, timeout=20.0):
        sent.update(host=settings.host, password=settings.password, recipients=recipients, subject=subject)

    monkeypatch.setattr(snmp_monitor, "send_email", fake_send)
    resp = await client.post("/api/snmp/monitor/test-email", headers=_auth(token))
    assert resp.json()["ok"] is True
    assert sent["host"] == "smtp.example.com" and sent["password"] == "smtp-secret-123"
    assert sent["recipients"] == ["noc@example.com", "oncall@example.com"]
    assert sent["subject"] == "[Packrat] Test alert"


async def test_full_cycle_baselines_then_alerts_and_emails(client: AsyncClient, unique_email, monkeypatch):
    token = await _register(client, unique_email)
    await client.post("/api/snmp/profiles", headers=_auth(token), json={"name": "ro", "version": "v2c", "community": "public"})
    sw = await client.post("/api/devices", headers=_auth(token), json={"name": "HQ-CORE-SW01", "host": "10.0.0.1", "device_type": "cisco_ios"})
    wlc = await client.post("/api/devices", headers=_auth(token), json={"name": "HQ-WLC", "host": "10.0.0.2", "device_type": "cisco_wlc_9800"})
    assert sw.status_code == 201 and wlc.status_code == 201
    await client.put("/api/snmp/monitor", headers=_auth(token), json=CONFIG)

    # Fake the network: state per host that the test flips between cycles.
    world = {
        "10.0.0.1": _snap({"1": _if("Gi1/0/1"), "2": _if("Gi1/0/2")}),
        "10.0.0.2": _snap(aps={"a": {"name": "AP-LOBBY", "status": "1"}, "b": {"name": "AP-DOCK", "status": "1"}}),
    }

    async def fake_take_snapshot(host, auth, **kwargs):
        snap = world[host]
        return Snapshot(**{**snap.__dict__})

    monkeypatch.setattr(snmp_monitor, "take_snapshot", fake_take_snapshot)
    emails = []
    monkeypatch.setattr(snmp_monitor, "send_email", lambda settings, recipients, subject, body, timeout=20.0: emails.append((recipients, subject, body)))

    # Cycle 1: baseline, no alerts, no email.
    first = await client.post("/api/snmp/monitor/run-now", headers=_auth(token))
    assert first.status_code == 200, first.text
    assert "2 device(s), 0 alert(s)" in first.json()["last_result"]
    assert (await client.get("/api/snmp/alerts", headers=_auth(token))).json() == []
    assert emails == []

    # Things go wrong.
    world["10.0.0.1"] = _snap({"1": _if("Gi1/0/1", oper="2"), "2": _if("Gi1/0/2")})
    world["10.0.0.2"] = _snap(aps={"a": {"name": "AP-LOBBY", "status": "1"}})
    second = await client.post("/api/snmp/monitor/run-now", headers=_auth(token))
    assert "2 alert(s)" in second.json()["last_result"] and "emailed 2 recipient(s)" in second.json()["last_result"]

    alerts = (await client.get("/api/snmp/alerts", headers=_auth(token))).json()
    assert {(a["kind"], a["subject"], a["device_name"]) for a in alerts} == {
        ("link_down", "Gi1/0/1", "HQ-CORE-SW01"),
        ("ap_down", "AP-DOCK", "HQ-WLC"),
    }
    assert all(a["emailed"] for a in alerts)
    assert len(emails) == 1
    recipients, subject, body = emails[0]
    assert recipients == ["noc@example.com", "oncall@example.com"]
    assert subject == "[Packrat] 2 alerts: 1 link down, 1 access point down"
    assert "Gi1/0/1 went down (was up)" in body and "AP-DOCK" in body

    # Cycle 3: nothing changed - nothing new; then the device stops answering.
    third = await client.post("/api/snmp/monitor/run-now", headers=_auth(token))
    assert "0 alert(s)" in third.json()["last_result"]
    world["10.0.0.1"] = _snap(reachable=False)
    fourth = await client.post("/api/snmp/monitor/run-now", headers=_auth(token))
    assert "1 alert(s)" in fourth.json()["last_result"]
    alerts_now = (await client.get("/api/snmp/alerts", headers=_auth(token))).json()
    assert len(alerts_now) == 3
    down = next(a for a in alerts_now if a["kind"] == "device_down")
    assert down["kind_label"] == "Device unreachable" and down["device_name"] == "HQ-CORE-SW01"

    cleared = await client.delete("/api/snmp/alerts", headers=_auth(token))
    assert cleared.json()["deleted"] == 3


async def test_cycle_records_email_failure_on_the_alerts(client: AsyncClient, unique_email, monkeypatch):
    token = await _register(client, unique_email)
    await client.post("/api/snmp/profiles", headers=_auth(token), json={"name": "ro", "version": "v2c", "community": "public"})
    await client.post("/api/devices", headers=_auth(token), json={"name": "sw1", "host": "10.0.0.1", "device_type": "cisco_ios"})
    await client.put("/api/snmp/monitor", headers=_auth(token), json=CONFIG)

    states = iter([_snap({"1": _if("Gi1/0/1")}), _snap({"1": _if("Gi1/0/1", oper="2")})])

    async def fake_take_snapshot(host, auth, **kwargs):
        return next(states)

    def failing_send(*args, **kwargs):
        raise OSError("Connection refused")

    monkeypatch.setattr(snmp_monitor, "take_snapshot", fake_take_snapshot)
    monkeypatch.setattr(snmp_monitor, "send_email", failing_send)
    await client.post("/api/snmp/monitor/run-now", headers=_auth(token))
    result = await client.post("/api/snmp/monitor/run-now", headers=_auth(token))
    assert "EMAIL FAILED: Connection refused" in result.json()["last_result"]
    alert = (await client.get("/api/snmp/alerts", headers=_auth(token))).json()[0]
    assert alert["emailed"] is False and "Connection refused" in alert["email_error"]


async def test_beat_task_runs_only_enabled_due_monitors(client: AsyncClient, unique_email, monkeypatch):
    token = await _register(client, unique_email)
    await client.put("/api/snmp/monitor", headers=_auth(token), json={**CONFIG, "enabled": False})
    ran = []
    monkeypatch.setattr(tasks_module, "run_snmp_monitor_cycle", lambda org_id, **kw: ran.append(str(org_id)))
    tasks_module.run_snmp_monitors()
    me = (await client.get("/api/auth/me", headers=_auth(token))).json()
    assert me["org_id"] not in ran

    await client.put("/api/snmp/monitor", headers=_auth(token), json=CONFIG)  # enabled, next_run_at = now
    tasks_module.run_snmp_monitors()
    assert me["org_id"] in ran
