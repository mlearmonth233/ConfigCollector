import pytest
from httpx import AsyncClient

from app import tasks as tasks_module

pytestmark = pytest.mark.asyncio


async def _register(client: AsyncClient, email: str, org_name: str = "QueueOrg") -> str:
    resp = await client.post(
        "/api/auth/register",
        json={"org_name": org_name, "email": email, "password": "password123"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["access_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def test_job_devices_processed_strictly_one_at_a_time(client: AsyncClient, unique_email):
    token = await _register(client, unique_email)
    for name in ["sw1", "sw2", "sw3"]:
        resp = await client.post(
            "/api/devices",
            headers=_auth(token),
            # No credential assigned - each item fails instantly with no
            # network call, making the ordering assertion below reliable.
            json={"name": name, "host": "192.0.2.1", "device_type": "cisco_ios"},
        )
        assert resp.status_code == 201, resp.text

    devices = await client.get("/api/devices", headers=_auth(token))
    device_ids = [d["id"] for d in devices.json()]

    job = await client.post("/api/jobs", headers=_auth(token), json={"device_ids": device_ids})
    assert job.status_code == 201, job.text

    detail = await client.get(f"/api/jobs/{job.json()['id']}", headers=_auth(token))
    items = detail.json()["items"]
    assert len(items) == 3
    assert all(i["status"] == "failed" for i in items)
    assert all(i["error_message"] == "Device has no credential assigned" for i in items)

    # None of these devices ever authenticates (no credential assigned), so
    # the next device is only ever dispatched via the end-of-task fallback
    # path (see collect_device_task) - same as fully sequential. Each
    # item's finished_at must be no later than the next item's started_at.
    for earlier, later in zip(items, items[1:]):
        assert earlier["finished_at"] <= later["started_at"], items


async def test_next_device_starts_authenticating_as_soon_as_previous_authenticates(
    client: AsyncClient, unique_email, monkeypatch
):
    token = await _register(client, unique_email)
    cred = await client.post(
        "/api/credentials",
        headers=_auth(token),
        json={"name": "lab", "username": "admin", "password": "cisco123"},
    )
    cred_id = cred.json()["id"]

    created = []
    for name, host in [("sw1", "10.0.0.1"), ("sw2", "10.0.0.2")]:
        device = await client.post(
            "/api/devices",
            headers=_auth(token),
            json={"name": name, "host": host, "device_type": "cisco_ios", "credential_id": cred_id},
        )
        assert device.status_code == 201, device.text
        created.append(device.json())

    events: list[str] = []

    def _fake_attempt(device, credential, otp, commands_override, on_authenticated, on_output):
        events.append(f"{device.host}-authenticating")
        on_authenticated()
        events.append(f"{device.host}-running")
        return f"hostname {device.host}\n"

    monkeypatch.setattr(tasks_module, "_attempt_collection", _fake_attempt)

    job = await client.post(
        "/api/jobs", headers=_auth(token), json={"device_ids": [d["id"] for d in created]}
    )
    assert job.status_code == 201, job.text

    detail = await client.get(f"/api/jobs/{job.json()['id']}", headers=_auth(token))
    items = detail.json()["items"]
    assert all(i["status"] == "completed" for i in items)

    # dispatch_next() is called from inside sw1's on_authenticated callback,
    # so (under the synchronous eager-mode dispatch used in tests) sw2's
    # entire task runs to completion nested inside that call, before sw1's
    # own "running" line below it executes - proving sw2 started
    # authenticating the moment sw1 finished authenticating, not after
    # sw1's task fully completed.
    assert events == [
        "10.0.0.1-authenticating",
        "10.0.0.2-authenticating",
        "10.0.0.2-running",
        "10.0.0.1-running",
    ]


async def test_job_continues_after_unexpected_exception_mid_chain(
    client: AsyncClient, unique_email, monkeypatch
):
    token = await _register(client, unique_email)
    cred = await client.post(
        "/api/credentials",
        headers=_auth(token),
        json={"name": "lab", "username": "admin", "password": "cisco123"},
    )
    cred_id = cred.json()["id"]

    created = []
    for name, host in [("crash-me", "203.0.113.1"), ("sw2", "192.0.2.1")]:
        device = await client.post(
            "/api/devices",
            headers=_auth(token),
            json={"name": name, "host": host, "device_type": "cisco_ios", "credential_id": cred_id},
        )
        assert device.status_code == 201, device.text
        created.append(device.json())

    original = tasks_module._attempt_collection

    def _boom_for_first_device(device, credential, otp, commands_override, on_authenticated, on_output):
        if device.host == "203.0.113.1":
            raise RuntimeError("simulated unexpected crash")
        return original(device, credential, otp, commands_override, on_authenticated, on_output)

    monkeypatch.setattr(tasks_module, "_attempt_collection", _boom_for_first_device)

    job = await client.post(
        "/api/jobs", headers=_auth(token), json={"device_ids": [d["id"] for d in created]}
    )
    assert job.status_code == 201, job.text

    detail = await client.get(f"/api/jobs/{job.json()['id']}", headers=_auth(token))
    items = detail.json()["items"]
    assert len(items) == 2
    # Both items reached a terminal state - the crash on the first device
    # didn't stop the queue from ever attempting the second one.
    assert all(i["status"] == "failed" for i in items)
    assert all(i["finished_at"] is not None for i in items)

    crashed = next(i for i in items if i["device_id"] == created[0]["id"])
    assert "Unexpected error during collection" in crashed["error_message"]
    assert "simulated unexpected crash" in crashed["error_message"]
