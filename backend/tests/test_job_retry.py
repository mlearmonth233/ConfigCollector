import pytest
from httpx import AsyncClient

from app import tasks as tasks_module
from app.services.collector import AuthenticationError

pytestmark = pytest.mark.asyncio


async def _register(client: AsyncClient, email: str, org_name: str = "RetryOrg") -> str:
    resp = await client.post(
        "/api/auth/register",
        json={"org_name": org_name, "email": email, "password": "password123"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["access_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def test_retry_keeps_the_same_job_rather_than_creating_a_new_one(
    client: AsyncClient, unique_email, monkeypatch
):
    token = await _register(client, unique_email)
    await client.post(
        "/api/credentials", headers=_auth(token), json={"name": "lab", "username": "admin", "password": "cisco123"}
    )
    device = await client.post(
        "/api/devices",
        headers=_auth(token),
        json={"name": "sw1", "host": "192.0.2.40", "device_type": "cisco_ios"},
    )
    device_id = device.json()["id"]

    def _fake_fail(device, credential, otp, commands_override, on_authenticated, on_output, should_cancel):
        raise AuthenticationError("simulated unreachable device")

    monkeypatch.setattr(tasks_module, "_attempt_collection", _fake_fail)

    job = await client.post("/api/jobs", headers=_auth(token), json={"device_ids": [device_id]})
    assert job.status_code == 201, job.text
    job_id = job.json()["id"]

    detail = (await client.get(f"/api/jobs/{job_id}", headers=_auth(token))).json()
    assert detail["status"] == "failed"
    item_id = detail["items"][0]["id"]

    def _fake_succeed(device, credential, otp, commands_override, on_authenticated, on_output, should_cancel):
        on_authenticated()
        return "hostname sw1\n"

    monkeypatch.setattr(tasks_module, "_attempt_collection", _fake_succeed)

    retry = await client.post(f"/api/jobs/{job_id}/items/{item_id}/retry", headers=_auth(token), json={})
    assert retry.status_code == 200, retry.text
    retry_body = retry.json()

    # Same job, not a new one.
    assert retry_body["id"] == job_id
    assert retry_body["status"] == "completed"
    assert len(retry_body["items"]) == 1
    assert retry_body["items"][0]["id"] == item_id
    assert retry_body["items"][0]["status"] == "completed"
    assert retry_body["items"][0]["error_message"] is None
    assert "===== Retrying =====" in retry_body["items"][0]["live_output"]

    # No second job was created anywhere in the org.
    jobs = await client.get("/api/jobs", headers=_auth(token))
    assert len(jobs.json()) == 1


async def test_retry_requires_otp_for_passcode_credential(client: AsyncClient, unique_email, monkeypatch):
    token = await _register(client, unique_email)
    cred = await client.post(
        "/api/credentials",
        headers=_auth(token),
        json={"name": "mfa-cred", "username": "admin", "password": "cisco123", "mfa_mode": "passcode"},
    )
    cred_id = cred.json()["id"]
    device = await client.post(
        "/api/devices",
        headers=_auth(token),
        json={"name": "sw1", "host": "192.0.2.41", "device_type": "cisco_ios", "credential_id": cred_id},
    )
    device_id = device.json()["id"]

    def _fake_fail(device, credential, otp, commands_override, on_authenticated, on_output, should_cancel):
        raise AuthenticationError("simulated unreachable device")

    monkeypatch.setattr(tasks_module, "_attempt_collection", _fake_fail)

    job = await client.post(
        "/api/jobs", headers=_auth(token), json={"device_ids": [device_id], "credential_otps": {cred_id: "123456"}}
    )
    assert job.status_code == 201, job.text
    job_id = job.json()["id"]
    item_id = (await client.get(f"/api/jobs/{job_id}", headers=_auth(token))).json()["items"][0]["id"]

    # No OTP on retry - rejected before any device is contacted.
    resp = await client.post(f"/api/jobs/{job_id}/items/{item_id}/retry", headers=_auth(token), json={})
    assert resp.status_code == 400
    assert "mfa-cred" in resp.json()["detail"]

    resp = await client.post(
        f"/api/jobs/{job_id}/items/{item_id}/retry",
        headers=_auth(token),
        json={"credential_otp": "654321"},
    )
    assert resp.status_code == 200, resp.text


async def test_retry_rejects_item_that_is_still_active(client: AsyncClient, unique_email, monkeypatch):
    token = await _register(client, unique_email)
    await client.post(
        "/api/credentials", headers=_auth(token), json={"name": "lab", "username": "admin", "password": "cisco123"}
    )
    device = await client.post(
        "/api/devices",
        headers=_auth(token),
        json={"name": "sw1", "host": "192.0.2.42", "device_type": "cisco_ios"},
    )
    device_id = device.json()["id"]

    # Never completes authenticating - item stays PENDING/AUTHENTICATING
    # forever from this test's point of view (task never actually runs
    # since we monkeypatch dispatch away entirely).
    monkeypatch.setattr(tasks_module.collect_device_task, "apply_async", lambda *a, **k: None)

    job = await client.post("/api/jobs", headers=_auth(token), json={"device_ids": [device_id]})
    assert job.status_code == 201, job.text
    job_id = job.json()["id"]
    item_id = (await client.get(f"/api/jobs/{job_id}", headers=_auth(token))).json()["items"][0]["id"]

    resp = await client.post(f"/api/jobs/{job_id}/items/{item_id}/retry", headers=_auth(token), json={})
    assert resp.status_code == 400
    assert "still running" in resp.json()["detail"]


async def test_retry_unknown_item_404s(client: AsyncClient, unique_email):
    token = await _register(client, unique_email)
    await client.post(
        "/api/credentials", headers=_auth(token), json={"name": "lab", "username": "admin", "password": "cisco123"}
    )
    device = await client.post(
        "/api/devices",
        headers=_auth(token),
        json={"name": "sw1", "host": "192.0.2.43", "device_type": "cisco_ios"},
    )
    job = await client.post("/api/jobs", headers=_auth(token), json={"device_ids": [device.json()["id"]]})
    job_id = job.json()["id"]

    resp = await client.post(
        f"/api/jobs/{job_id}/items/00000000-0000-0000-0000-000000000000/retry", headers=_auth(token), json={}
    )
    assert resp.status_code == 404
