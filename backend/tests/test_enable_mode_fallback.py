import pytest
from httpx import AsyncClient

from app import tasks as tasks_module
from app.services.collector import EnableModeError

pytestmark = pytest.mark.asyncio


async def _register(client: AsyncClient, email: str, org_name: str = "EnableFallbackOrg") -> str:
    resp = await client.post(
        "/api/auth/register",
        json={"org_name": org_name, "email": email, "password": "password123"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["access_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _setup_primary_and_fallback(client: AsyncClient, token: str) -> tuple[str, str]:
    fallback = await client.post(
        "/api/credentials",
        headers=_auth(token),
        json={"name": "local-admin", "username": "admin", "password": "localpass", "enable_secret": "goodsecret"},
    )
    fallback_id = fallback.json()["id"]
    primary = await client.post(
        "/api/credentials",
        headers=_auth(token),
        json={
            "name": "tacacs-primary",
            "username": "netops",
            "password": "cisco123",
            "enable_secret": "wrongsecret",
            "fallback_credential_id": fallback_id,
        },
    )
    return primary.json()["id"], fallback_id


async def test_enable_mode_failure_falls_back_to_other_credential(client: AsyncClient, unique_email, monkeypatch):
    token = await _register(client, unique_email)
    primary_id, _ = await _setup_primary_and_fallback(client, token)
    device = await client.post(
        "/api/devices",
        headers=_auth(token),
        json={"name": "wlc1", "host": "10.0.0.9", "device_type": "cisco_wlc_9800", "credential_id": primary_id},
    )
    device_id = device.json()["id"]

    def _fake_attempt(device, credential, otp, commands_override, on_authenticated, on_output):
        on_authenticated()
        if credential.name == "tacacs-primary":
            raise EnableModeError("Failed to enter enable mode: wrong secret")
        return f"hostname {device.host}\n"

    monkeypatch.setattr(tasks_module, "_attempt_collection", _fake_attempt)

    job = await client.post("/api/jobs", headers=_auth(token), json={"device_ids": [device_id]})
    assert job.status_code == 201, job.text

    detail = await client.get(f"/api/jobs/{job.json()['id']}", headers=_auth(token))
    item = detail.json()["items"][0]
    assert item["status"] == "completed"
    assert item["used_fallback_credential"] is True
    assert "Trying fallback credential 'local-admin'" in item["live_output"]


async def test_enable_mode_failure_on_both_credentials_reports_both(client: AsyncClient, unique_email, monkeypatch):
    token = await _register(client, unique_email)
    primary_id, _ = await _setup_primary_and_fallback(client, token)
    device = await client.post(
        "/api/devices",
        headers=_auth(token),
        json={"name": "wlc1", "host": "10.0.0.9", "device_type": "cisco_wlc_9800", "credential_id": primary_id},
    )
    device_id = device.json()["id"]

    def _fake_attempt(device, credential, otp, commands_override, on_authenticated, on_output):
        on_authenticated()
        raise EnableModeError(f"Failed to enter enable mode as {credential.name}")

    monkeypatch.setattr(tasks_module, "_attempt_collection", _fake_attempt)

    job = await client.post("/api/jobs", headers=_auth(token), json={"device_ids": [device_id]})
    assert job.status_code == 201, job.text

    detail = await client.get(f"/api/jobs/{job.json()['id']}", headers=_auth(token))
    item = detail.json()["items"][0]
    assert item["status"] == "failed"
    assert item["used_fallback_credential"] is False
    assert "tacacs-primary" in item["error_message"]
    assert "local-admin" in item["error_message"]
