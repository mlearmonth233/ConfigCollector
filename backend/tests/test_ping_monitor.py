"""Continuous ping monitor: threshold-based up/down, alerts on transitions,
history and the Monitor page's overview API."""

from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient

from app.models.ping_monitor import PingStatus
from app.services import ping_monitor
from app.services.ping_monitor import _RTT_RE, apply_result

pytestmark = pytest.mark.asyncio


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _register(client: AsyncClient, email: str) -> str:
    resp = await client.post("/api/auth/register", json={"email": email, "password": "secret123", "org_name": "Ping Org"})
    assert resp.status_code == 201, resp.text
    return resp.json()["access_token"]


async def _member(client: AsyncClient, admin_token: str, email: str) -> str:
    resp = await client.post("/api/users", headers=_auth(admin_token), json={"email": email, "password": "secret123", "role": "member"})
    assert resp.status_code == 201, resp.text
    return (await client.post("/api/auth/login", json={"email": email, "password": "secret123"})).json()["access_token"]


class FakeNetwork:
    """host -> rtt (ms) or None for no reply; swapped in for the real ping."""

    def __init__(self, **hosts):
        self.hosts = dict(hosts)
        self.calls = 0

    async def ping(self, host, timeout_ms=1500):
        self.calls += 1
        return self.hosts.get(host)


def test_rtt_regex_handles_linux_mac_and_windows_output():
    assert _RTT_RE.search("64 bytes from 10.0.0.1: icmp_seq=1 ttl=64 time=0.412 ms").group(1) == "0.412"
    assert _RTT_RE.search("Reply from 10.0.0.1: bytes=32 time=3ms TTL=64").group(1) == "3"
    assert _RTT_RE.search("Reply from 10.0.0.1: bytes=32 time<1ms TTL=64").group(1) == "1"


def test_apply_result_threshold_and_transitions():
    now = datetime.now(timezone.utc)
    s = PingStatus(state="unknown", consecutive_failures=0)
    # First reply: unknown -> up, but not an alertable event (no prior known state).
    assert apply_result(s, True, 2.5, 3, now) is None
    assert s.state == "up" and s.last_rtt_ms == 2.5 and s.last_change_at == now
    # Two misses: still up, failures counting.
    assert apply_result(s, False, None, 3, now) is None
    assert apply_result(s, False, None, 3, now) is None
    assert s.state == "up" and s.consecutive_failures == 2 and s.last_rtt_ms is None
    # Third miss crosses the threshold: up -> down is an event.
    later = now + timedelta(minutes=3)
    assert apply_result(s, False, None, 3, later) == "down"
    assert s.state == "down" and s.last_change_at == later
    # Staying down is not a new event; a reply is the recovery event.
    assert apply_result(s, False, None, 3, later) is None
    assert apply_result(s, True, 1.0, 3, later) == "up"
    assert s.consecutive_failures == 0
    # A device that is unreachable from the start becomes down silently.
    fresh = PingStatus(state="unknown", consecutive_failures=0)
    assert apply_result(fresh, False, None, 1, now) is None
    assert fresh.state == "down"


async def test_cycle_tracks_state_records_history_and_alerts(client: AsyncClient, unique_email, monkeypatch):
    token = await _register(client, unique_email)
    me = (await client.get("/api/auth/me", headers=_auth(token))).json()
    for name, host in (("core", "192.0.2.1"), ("edge", "192.0.2.2"), ("printer-sw", "192.0.2.3")):
        await client.post("/api/devices", headers=_auth(token), json={"name": name, "host": host, "device_type": "cisco_ios"})
    net = FakeNetwork(**{"192.0.2.1": 1.2, "192.0.2.2": 8.8, "192.0.2.3": None})
    monkeypatch.setattr(ping_monitor, "ping_rtt", net.ping)

    # Lower the threshold so the test needs fewer cycles.
    saved = await client.put("/api/ping/settings", headers=_auth(token), json={"enabled": True, "interval_seconds": 30, "failure_threshold": 2, "timeout_ms": 500, "alert_on_down": True, "alert_on_up": True, "history_days": 7})
    assert saved.status_code == 200, saved.text
    assert saved.json()["failure_threshold"] == 2 and saved.json()["email_configured"] is False

    # Cycle 1: baseline. Two answer, one has never answered -> unknown until the threshold.
    first = ping_monitor.run_ping_cycle(me["org_id"])
    assert first["devices"] == 3 and first["up"] == 2 and first["down"] == 0 and first["alerts"] == 0
    assert net.calls == 3

    overview = (await client.get("/api/ping/overview", headers=_auth(token))).json()
    assert overview["summary"] == {**overview["summary"], "total": 3, "up": 2, "down": 0, "unknown": 1}
    assert overview["summary"]["avg_rtt_ms"] == 5.0
    by_name = {d["name"]: d for d in overview["devices"]}
    assert by_name["core"]["state"] == "up" and by_name["core"]["last_rtt_ms"] == 1.2 and by_name["core"]["uptime_24h_pct"] == 100.0
    assert by_name["printer-sw"]["state"] == "unknown" and by_name["printer-sw"]["consecutive_failures"] == 1
    assert len(by_name["core"]["recent"]) == 1 and by_name["core"]["recent"][0]["ok"] is True
    assert overview["settings"]["last_result"].startswith("Pinged 3 device(s): 2 up, 0 down")

    # Cycle 2: printer-sw hits the threshold but was never up -> down, no alert.
    second = ping_monitor.run_ping_cycle(me["org_id"])
    assert second["down"] == 1 and second["alerts"] == 0

    # Cycles 3-4: the edge switch stops answering -> alert on the second miss.
    net.hosts["192.0.2.2"] = None
    assert ping_monitor.run_ping_cycle(me["org_id"])["alerts"] == 0
    fourth = ping_monitor.run_ping_cycle(me["org_id"])
    assert fourth["down"] == 2 and fourth["alerts"] == 1
    assert "not delivered (no channels configured)" in fourth["summary"]

    alerts = (await client.get("/api/alerts", headers=_auth(token))).json()
    assert len(alerts) == 1
    assert alerts[0]["kind"] == "ping_down" and alerts[0]["kind_label"] == "Ping: device unreachable"
    assert alerts[0]["device_name"] == "edge" and "after 2 consecutive checks" in alerts[0]["detail"]
    assert alerts[0]["emailed"] is False and "No delivery channel" in alerts[0]["email_error"]

    # Cycle 5: it comes back -> recovery alert; uptime reflects the misses.
    net.hosts["192.0.2.2"] = 4.0
    fifth = ping_monitor.run_ping_cycle(me["org_id"])
    assert fifth["up"] == 2 and fifth["alerts"] == 1
    alerts = (await client.get("/api/alerts", headers=_auth(token))).json()
    assert {a["kind"] for a in alerts} == {"ping_down", "ping_up"}
    overview = (await client.get("/api/ping/overview", headers=_auth(token))).json()
    edge = next(d for d in overview["devices"] if d["name"] == "edge")
    assert edge["state"] == "up" and edge["checks_24h"] == 5 and edge["uptime_24h_pct"] == 60.0
    assert [s["ok"] for s in edge["recent"]] == [True, True, False, False, True]

    history = (await client.get(f"/api/ping/history/{edge['device_id']}", headers=_auth(token), params={"hours": 1})).json()
    assert len(history["samples"]) == 5 and history["samples"][-1]["rtt_ms"] == 4.0


async def test_run_now_endpoint_and_disabled_monitor(client: AsyncClient, unique_email, monkeypatch):
    token = await _register(client, unique_email)
    me = (await client.get("/api/auth/me", headers=_auth(token))).json()
    await client.post("/api/devices", headers=_auth(token), json={"name": "sw1", "host": "192.0.2.10", "device_type": "cisco_ios"})
    net = FakeNetwork(**{"192.0.2.10": 0.8})
    monkeypatch.setattr(ping_monitor, "ping_rtt", net.ping)

    paused = await client.put("/api/ping/settings", headers=_auth(token), json={"enabled": False, "interval_seconds": 60, "failure_threshold": 3, "timeout_ms": 1500, "alert_on_down": True, "alert_on_up": True, "history_days": 7})
    assert paused.status_code == 200
    # Beat skips a paused org...
    assert ping_monitor.run_ping_cycle(me["org_id"]) == {"skipped": True}
    assert me["org_id"] not in {str(o) for o in ping_monitor.due_org_ids()}
    # ...but "Check now" still works and returns the refreshed overview.
    ran = await client.post("/api/ping/run-now", headers=_auth(token))
    assert ran.status_code == 200, ran.text
    assert ran.json()["summary"]["up"] == 1 and ran.json()["devices"][0]["last_rtt_ms"] == 0.8
    assert net.calls == 1


async def test_settings_are_admin_only_and_validated(client: AsyncClient, unique_email):
    admin = await _register(client, unique_email)
    member = await _member(client, admin, f"m-{unique_email}")
    body = {"enabled": True, "interval_seconds": 60, "failure_threshold": 3, "timeout_ms": 1500, "alert_on_down": True, "alert_on_up": False, "history_days": 7}
    assert (await client.put("/api/ping/settings", headers=_auth(member), json=body)).status_code == 403
    assert (await client.get("/api/ping/overview", headers=_auth(member))).status_code == 200
    too_fast = await client.put("/api/ping/settings", headers=_auth(admin), json={**body, "interval_seconds": 5})
    assert too_fast.status_code == 422
    ok = await client.put("/api/ping/settings", headers=_auth(admin), json=body)
    assert ok.status_code == 200 and ok.json()["alert_on_up"] is False


async def test_new_org_is_monitored_by_default_and_flagged_stale_until_beat_runs(client: AsyncClient, unique_email):
    token = await _register(client, unique_email)
    me = (await client.get("/api/auth/me", headers=_auth(token))).json()
    await client.post("/api/devices", headers=_auth(token), json={"name": "sw1", "host": "192.0.2.20", "device_type": "cisco_ios"})
    overview = (await client.get("/api/ping/overview", headers=_auth(token))).json()
    assert overview["settings"]["enabled"] is True and overview["settings"]["interval_seconds"] == 60
    assert overview["summary"]["stale"] is True and overview["summary"]["seconds_since_last_run"] is None
    # due_org_ids picks the org up (its config row exists now) so beat will run it.
    assert me["org_id"] in {str(o) for o in ping_monitor.due_org_ids()}


async def test_deleting_a_device_removes_its_monitor_rows(client: AsyncClient, unique_email, monkeypatch):
    token = await _register(client, unique_email)
    me = (await client.get("/api/auth/me", headers=_auth(token))).json()
    dev = (await client.post("/api/devices", headers=_auth(token), json={"name": "gone", "host": "192.0.2.30", "device_type": "cisco_ios"})).json()
    monkeypatch.setattr(ping_monitor, "ping_rtt", FakeNetwork(**{"192.0.2.30": 1.0}).ping)
    ping_monitor.run_ping_cycle(me["org_id"], force=True)
    assert len((await client.get("/api/ping/overview", headers=_auth(token))).json()["devices"]) == 1
    assert (await client.delete(f"/api/devices/{dev['id']}", headers=_auth(token))).status_code == 204
    overview = (await client.get("/api/ping/overview", headers=_auth(token))).json()
    assert overview["devices"] == [] and overview["summary"]["total"] == 0
    assert (await client.get(f"/api/ping/history/{dev['id']}", headers=_auth(token))).status_code == 404
