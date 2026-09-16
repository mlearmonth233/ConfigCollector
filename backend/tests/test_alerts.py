"""The Alerts page: one set of delivery settings (email, Teams, Slack) shared
by every monitor, a test button, one alert history, and config-change
alerts raised by collection jobs."""

import httpx
import pytest
from httpx import AsyncClient

from app import tasks as tasks_module
from app.services import alerting, snmp_monitor

pytestmark = pytest.mark.asyncio


async def _register(client: AsyncClient, email: str, org_name: str = "AlertsOrg") -> str:
    resp = await client.post("/api/auth/register", json={"org_name": org_name, "email": email, "password": "password123"})
    assert resp.status_code == 201, resp.text
    return resp.json()["access_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


DELIVERY = {
    "recipients": ["noc@example.com", "oncall@example.com"],
    "smtp_host": "smtp.example.com",
    "smtp_port": 587,
    "smtp_username": "packrat",
    "smtp_password": "smtp-secret-123",
    "smtp_from": "packrat@example.com",
    "teams_webhook_url": "https://prod.westeurope.logic.azure.com:443/workflows/abc/triggers/manual/paths/invoke?sig=SECRET",
    "slack_webhook_url": "https://hooks.slack.com/services/T000/B000/SECRETTOKEN",
}


def _fake_webhooks(monkeypatch, handler):
    """Routes every httpx.Client the alerting module opens through `handler`."""
    real_client = httpx.Client
    monkeypatch.setattr(alerting.httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))


# --- settings --------------------------------------------------------------------------


async def test_settings_default_to_no_channels(client: AsyncClient, unique_email):
    token = await _register(client, unique_email)
    resp = await client.get("/api/alerts/settings", headers=_auth(token))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["channels"] == [] and body["recipients"] == [] and body["has_smtp_password"] is False
    assert body["teams_webhook_host"] is None and body["alert_config_change"] is True


async def test_settings_round_trip_hides_secrets_and_keeps_them_on_blank(client: AsyncClient, unique_email):
    token = await _register(client, unique_email)
    saved = await client.put("/api/alerts/settings", headers=_auth(token), json=DELIVERY)
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["channels"] == ["email", "teams", "slack"]
    assert body["has_smtp_password"] is True and "smtp-secret-123" not in saved.text
    # Webhook URLs carry a secret token: only the host comes back.
    assert body["teams_webhook_host"] == "prod.westeurope.logic.azure.com:443"
    assert body["slack_webhook_host"] == "hooks.slack.com"
    assert "SECRET" not in saved.text

    # Saving the form again with the write-only fields blank keeps them.
    again = await client.put(
        "/api/alerts/settings", headers=_auth(token), json={**DELIVERY, "smtp_password": "", "teams_webhook_url": "", "slack_webhook_url": None}
    )
    assert again.json()["has_smtp_password"] is True
    assert again.json()["channels"] == ["email", "teams", "slack"]

    # "-" clears a webhook, clear_smtp_password clears the password.
    cleared = await client.put(
        "/api/alerts/settings", headers=_auth(token), json={**DELIVERY, "smtp_password": "", "clear_smtp_password": True, "teams_webhook_url": "-"}
    )
    assert cleared.json()["has_smtp_password"] is False
    assert cleared.json()["teams_webhook_host"] is None and cleared.json()["channels"] == ["email", "slack"]


async def test_settings_validation(client: AsyncClient, unique_email):
    token = await _register(client, unique_email)
    bad_url = await client.put("/api/alerts/settings", headers=_auth(token), json={**DELIVERY, "teams_webhook_url": "http://insecure.example.com/hook"})
    assert bad_url.status_code == 422 and "https://" in bad_url.text
    bad_email = await client.put("/api/alerts/settings", headers=_auth(token), json={**DELIVERY, "recipients": ["not-an-email"]})
    assert bad_email.status_code == 422 and "email address" in bad_email.text
    # Email needs both a host and at least one recipient before it counts as a channel.
    partial = await client.put("/api/alerts/settings", headers=_auth(token), json={"recipients": ["noc@example.com"]})
    assert partial.status_code == 200 and partial.json()["channels"] == []


async def test_settings_are_admin_only_but_readable_by_members(client: AsyncClient, unique_email):
    token = await _register(client, unique_email)
    member_email = f"member-{unique_email}"
    await client.post("/api/users", headers=_auth(token), json={"email": member_email, "password": "password123"})
    member = (await client.post("/api/auth/login", json={"email": member_email, "password": "password123"})).json()["access_token"]
    assert (await client.put("/api/alerts/settings", headers=_auth(member), json=DELIVERY)).status_code == 403
    assert (await client.post("/api/alerts/test", headers=_auth(member))).status_code == 403
    assert (await client.delete("/api/alerts", headers=_auth(member))).status_code == 403
    assert (await client.get("/api/alerts/settings", headers=_auth(member))).status_code == 200
    assert (await client.get("/api/alerts", headers=_auth(member))).status_code == 200


# --- test button -----------------------------------------------------------------------


async def test_test_button_reports_per_channel_results(client: AsyncClient, unique_email, monkeypatch):
    token = await _register(client, unique_email)
    early = await client.post("/api/alerts/test", headers=_auth(token))
    assert early.json() == {"results": {}, "ok": False}

    await client.put("/api/alerts/settings", headers=_auth(token), json=DELIVERY)
    sent = {}
    monkeypatch.setattr(
        snmp_monitor,
        "send_email",
        lambda settings, recipients, subject, body, timeout=20.0: sent.update(host=settings.host, password=settings.password, recipients=recipients, subject=subject),
    )
    posted = []

    def handler(request: httpx.Request) -> httpx.Response:
        posted.append((request.url.host, request.read()))
        return httpx.Response(200, text="1")

    _fake_webhooks(monkeypatch, handler)
    resp = await client.post("/api/alerts/test", headers=_auth(token))
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"results": {"email": "ok", "teams": "ok", "slack": "ok"}, "ok": True}
    assert sent["host"] == "smtp.example.com" and sent["password"] == "smtp-secret-123"
    assert sent["recipients"] == ["noc@example.com", "oncall@example.com"] and sent["subject"] == "[Packrat] Test alert"
    hosts = [h for h, _ in posted]
    assert hosts == ["prod.westeurope.logic.azure.com", "hooks.slack.com"]
    teams_body, slack_body = posted[0][1], posted[1][1]
    assert b"AdaptiveCard" in teams_body and b"Test alert" in teams_body
    assert b'"text"' in slack_body and b"Test alert" in slack_body


async def test_test_button_surfaces_a_failing_channel(client: AsyncClient, unique_email, monkeypatch):
    token = await _register(client, unique_email)
    await client.put("/api/alerts/settings", headers=_auth(token), json={**DELIVERY, "smtp_host": None, "recipients": []})
    monkeypatch.setattr(snmp_monitor, "send_email", lambda *a, **k: (_ for _ in ()).throw(AssertionError("email must not be attempted")))

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "hooks.slack.com":
            return httpx.Response(403, text="invalid_token")
        return httpx.Response(202)

    _fake_webhooks(monkeypatch, handler)
    resp = await client.post("/api/alerts/test", headers=_auth(token))
    body = resp.json()
    assert body["ok"] is False and body["results"]["teams"] == "ok"
    assert "403" in body["results"]["slack"]


# --- config-change alerts from collection jobs -------------------------------------------


async def _org_with_device(client: AsyncClient, token: str) -> str:
    await client.post("/api/credentials", headers=_auth(token), json={"name": "lab", "username": "admin", "password": "cisco123"})
    device = await client.post("/api/devices", headers=_auth(token), json={"name": "EDGE-RTR01", "host": "192.0.2.10", "device_type": "cisco_ios"})
    assert device.status_code == 201, device.text
    return device.json()["id"]


async def _collect(client: AsyncClient, token: str, device_id: str) -> None:
    job = await client.post("/api/jobs", headers=_auth(token), json={"device_ids": [device_id]})
    assert job.status_code == 201, job.text
    detail = await client.get(f"/api/jobs/{job.json()['id']}", headers=_auth(token))
    assert detail.json()["status"] == "completed", detail.text


async def test_collection_raises_a_config_change_alert_and_delivers_it_once(client: AsyncClient, unique_email, monkeypatch):
    token = await _register(client, unique_email)
    device_id = await _org_with_device(client, token)
    await client.put("/api/alerts/settings", headers=_auth(token), json={**DELIVERY, "teams_webhook_url": None, "slack_webhook_url": None})
    emails = []
    monkeypatch.setattr(snmp_monitor, "send_email", lambda settings, recipients, subject, body, timeout=20.0: emails.append((subject, body)))

    world = {"config": "hostname EDGE-RTR01\ninterface Gi0/0\n ip address 192.0.2.10 255.255.255.0\n"}
    monkeypatch.setattr(tasks_module, "_attempt_collection", lambda *args, **kwargs: world["config"])

    await _collect(client, token, device_id)  # first snapshot: nothing to compare with
    await _collect(client, token, device_id)  # identical: no alert
    assert (await client.get("/api/alerts", headers=_auth(token))).json() == []
    assert emails == []

    world["config"] = "hostname EDGE-RTR01\ninterface Gi0/0\n ip address 192.0.2.11 255.255.255.0\n description uplink\n"
    await _collect(client, token, device_id)
    alerts = (await client.get("/api/alerts", headers=_auth(token))).json()
    assert len(alerts) == 1, alerts
    alert = alerts[0]
    assert alert["kind"] == "config_changed" and alert["kind_label"] == "Config changed"
    assert alert["device_name"] == "EDGE-RTR01" and alert["subject"] == "Config changed on EDGE-RTR01"
    assert "2 line(s) added, 1 removed" in alert["detail"]
    assert alert["emailed"] is True and alert["notified_via"] == "email" and alert["email_error"] is None
    assert len(emails) == 1
    subject, body = emails[0]
    assert subject == "[Packrat] 1 alert: 1 config changed" and "EDGE-RTR01" in body

    # The device history has all three snapshots: alerting never blocks collection.
    history = await client.get(f"/api/devices/{device_id}/snapshots", headers=_auth(token))
    assert history.status_code == 200 and len(history.json()) == 3


async def test_config_change_without_channels_is_recorded_but_not_delivered(client: AsyncClient, unique_email, monkeypatch):
    token = await _register(client, unique_email)
    device_id = await _org_with_device(client, token)
    world = {"config": "version 1\n"}
    monkeypatch.setattr(tasks_module, "_attempt_collection", lambda *args, **kwargs: world["config"])
    await _collect(client, token, device_id)
    world["config"] = "version 2\n"
    await _collect(client, token, device_id)
    alerts = (await client.get("/api/alerts", headers=_auth(token))).json()
    assert len(alerts) == 1 and alerts[0]["emailed"] is False
    assert "No delivery channel" in alerts[0]["email_error"]

    cleared = await client.delete("/api/alerts", headers=_auth(token))
    assert cleared.json()["deleted"] == 1
    assert (await client.get("/api/alerts", headers=_auth(token))).json() == []


async def test_config_change_alerts_can_be_switched_off(client: AsyncClient, unique_email, monkeypatch):
    token = await _register(client, unique_email)
    device_id = await _org_with_device(client, token)
    await client.put("/api/alerts/settings", headers=_auth(token), json={**DELIVERY, "alert_config_change": False})
    world = {"config": "version 1\n"}
    monkeypatch.setattr(tasks_module, "_attempt_collection", lambda *args, **kwargs: world["config"])
    await _collect(client, token, device_id)
    world["config"] = "version 2\n"
    await _collect(client, token, device_id)
    assert (await client.get("/api/alerts", headers=_auth(token))).json() == []
