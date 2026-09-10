import pytest
from httpx import AsyncClient

from app import tasks as tasks_module
from app.database import async_session_factory
from app.models.job import CollectionJob, CollectionJobItem, JobStatus
from app.models.snapshot import ConfigSnapshot
from app.services.collector import AuthenticationError

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


async def test_job_devices_processed_strictly_one_at_a_time(client: AsyncClient, unique_email, monkeypatch):
    token = await _register(client, unique_email)
    # A credential now covers every device in the org automatically (the
    # org's one default) - it just needs to exist for job creation's
    # precheck. The instant, deterministic, no-real-network-call failure
    # the ordering assertion below relies on instead comes from
    # monkeypatching _attempt_collection directly.
    await client.post(
        "/api/credentials", headers=_auth(token), json={"name": "lab", "username": "admin", "password": "cisco123"}
    )
    for name in ["sw1", "sw2", "sw3"]:
        resp = await client.post(
            "/api/devices",
            headers=_auth(token),
            json={"name": name, "host": "192.0.2.1", "device_type": "cisco_ios"},
        )
        assert resp.status_code == 201, resp.text

    def _fake_attempt(device, credential, otp, commands_override, on_authenticated, on_output, should_cancel):
        raise AuthenticationError(f"simulated unreachable device {device.host}")

    monkeypatch.setattr(tasks_module, "_attempt_collection", _fake_attempt)

    devices = await client.get("/api/devices", headers=_auth(token))
    device_ids = [d["id"] for d in devices.json()]

    job = await client.post("/api/jobs", headers=_auth(token), json={"device_ids": device_ids})
    assert job.status_code == 201, job.text

    detail = await client.get(f"/api/jobs/{job.json()['id']}", headers=_auth(token))
    items = detail.json()["items"]
    assert len(items) == 3
    assert all(i["status"] == "failed" for i in items)
    assert all("simulated unreachable device" in i["error_message"] for i in items)

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

    def _fake_attempt(device, credential, otp, commands_override, on_authenticated, on_output, should_cancel):
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

    def _boom_for_first_device(device, credential, otp, commands_override, on_authenticated, on_output, should_cancel):
        if device.host == "203.0.113.1":
            raise RuntimeError("simulated unexpected crash")
        return original(device, credential, otp, commands_override, on_authenticated, on_output, should_cancel)

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


async def test_cancel_endpoint_rejects_already_finished_job(client: AsyncClient, unique_email, monkeypatch):
    token = await _register(client, unique_email)
    await client.post(
        "/api/credentials", headers=_auth(token), json={"name": "lab", "username": "admin", "password": "cisco123"}
    )
    device = await client.post(
        "/api/devices",
        headers=_auth(token),
        json={"name": "sw1", "host": "192.0.2.1", "device_type": "cisco_ios"},
    )
    assert device.status_code == 201, device.text

    # Fails instantly with no real network call, so under eager dispatch
    # the job is already finished by the time the create call returns.
    def _fake_attempt(device, credential, otp, commands_override, on_authenticated, on_output, should_cancel):
        raise AuthenticationError(f"simulated unreachable device {device.host}")

    monkeypatch.setattr(tasks_module, "_attempt_collection", _fake_attempt)

    job = await client.post(
        "/api/jobs", headers=_auth(token), json={"device_ids": [device.json()["id"]]}
    )
    assert job.status_code == 201, job.text
    assert job.json()["status"] == "failed"

    cancel = await client.post(f"/api/jobs/{job.json()['id']}/cancel", headers=_auth(token))
    assert cancel.status_code == 400, cancel.text
    assert "already finished" in cancel.json()["detail"]


async def test_cancelling_mid_chain_skips_remaining_devices_without_resurrecting_them(
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
    for name, host in [("sw1", "10.0.1.1"), ("sw2", "10.0.1.2"), ("sw3", "10.0.1.3")]:
        device = await client.post(
            "/api/devices",
            headers=_auth(token),
            json={"name": name, "host": host, "device_type": "cisco_ios", "credential_id": cred_id},
        )
        assert device.status_code == 201, device.text
        created.append(device.json())

    def _fake_attempt(device, credential, otp, commands_override, on_authenticated, on_output, should_cancel):
        if device.host == "10.0.1.1":
            # Simulate a cancel request arriving (via jobs.py's cancel_job)
            # while sw2/sw3 are still queued (PENDING) behind sw1 - that
            # endpoint does exactly this update to their rows.
            db = tasks_module.SyncSessionLocal()
            try:
                items = (
                    db.query(tasks_module.CollectionJobItem)
                    .filter(
                        tasks_module.CollectionJobItem.device_id.in_(
                            [created[1]["id"], created[2]["id"]]
                        )
                    )
                    .all()
                )
                for item in items:
                    item.status = tasks_module.JobStatus.CANCELLED
                    item.finished_at = tasks_module.datetime.now(tasks_module.timezone.utc)
                db.commit()
            finally:
                db.close()
        on_authenticated()
        return f"hostname {device.host}\n"

    monkeypatch.setattr(tasks_module, "_attempt_collection", _fake_attempt)

    job = await client.post(
        "/api/jobs", headers=_auth(token), json={"device_ids": [d["id"] for d in created]}
    )
    assert job.status_code == 201, job.text

    detail = await client.get(f"/api/jobs/{job.json()['id']}", headers=_auth(token))
    body = detail.json()
    items = {i["device_id"]: i for i in body["items"]}
    assert items[created[0]["id"]]["status"] == "completed"
    assert items[created[1]["id"]]["status"] == "cancelled"
    assert items[created[2]["id"]]["status"] == "cancelled"
    assert items[created[1]["id"]]["finished_at"] is not None
    assert items[created[2]["id"]]["finished_at"] is not None

    # CANCELLED takes priority in the job's overall status, even though sw1
    # itself completed fine.
    assert body["status"] == "cancelled"


async def test_delete_job_removes_it_and_its_snapshot(client: AsyncClient, unique_email):
    token = await _register(client, unique_email)
    device = await client.post(
        "/api/devices",
        headers=_auth(token),
        json={"name": "sw1", "host": "192.0.2.1", "device_type": "cisco_ios"},
    )
    device_id = device.json()["id"]
    me = await client.get("/api/auth/me", headers=_auth(token))
    org_id, user_id = me.json()["org_id"], me.json()["id"]

    async with async_session_factory() as db:
        job = CollectionJob(org_id=org_id, created_by_id=user_id, status=JobStatus.COMPLETED)
        db.add(job)
        await db.flush()
        item = CollectionJobItem(job_id=job.id, device_id=device_id, status=JobStatus.COMPLETED)
        db.add(item)
        await db.flush()
        snapshot = ConfigSnapshot(device_id=device_id, job_item_id=item.id, content="hostname sw1\n")
        db.add(snapshot)
        await db.commit()
        job_id, snapshot_id = str(job.id), str(snapshot.id)

    delete_resp = await client.delete(f"/api/jobs/{job_id}", headers=_auth(token))
    assert delete_resp.status_code == 204, delete_resp.text

    assert (await client.get(f"/api/jobs/{job_id}", headers=_auth(token))).status_code == 404
    # The snapshot went with it - it has no reason to exist once its job does.
    assert (await client.get(f"/api/snapshots/{snapshot_id}", headers=_auth(token))).status_code == 404


async def test_cannot_delete_a_job_still_in_progress(client: AsyncClient, unique_email):
    token = await _register(client, unique_email)
    me = await client.get("/api/auth/me", headers=_auth(token))
    org_id, user_id = me.json()["org_id"], me.json()["id"]

    async with async_session_factory() as db:
        job = CollectionJob(org_id=org_id, created_by_id=user_id, status=JobStatus.RUNNING)
        db.add(job)
        await db.commit()
        job_id = str(job.id)

    resp = await client.delete(f"/api/jobs/{job_id}", headers=_auth(token))
    assert resp.status_code == 400
    assert "in progress" in resp.json()["detail"]

    # Untouched.
    assert (await client.get(f"/api/jobs/{job_id}", headers=_auth(token))).status_code == 200


async def test_clear_finished_jobs_leaves_running_ones_alone(client: AsyncClient, unique_email):
    token = await _register(client, unique_email)
    me = await client.get("/api/auth/me", headers=_auth(token))
    org_id, user_id = me.json()["org_id"], me.json()["id"]

    async with async_session_factory() as db:
        finished_ids = []
        for finished_status in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED):
            job = CollectionJob(org_id=org_id, created_by_id=user_id, status=finished_status)
            db.add(job)
            await db.flush()
            finished_ids.append(str(job.id))
        running_job = CollectionJob(org_id=org_id, created_by_id=user_id, status=JobStatus.RUNNING)
        db.add(running_job)
        await db.commit()
        running_id = str(running_job.id)

    resp = await client.delete("/api/jobs", headers=_auth(token))
    assert resp.status_code == 200, resp.text
    assert resp.json()["deleted"] == 3

    for job_id in finished_ids:
        assert (await client.get(f"/api/jobs/{job_id}", headers=_auth(token))).status_code == 404
    assert (await client.get(f"/api/jobs/{running_id}", headers=_auth(token))).status_code == 200

    # Calling it again with nothing left to clear is a no-op, not an error.
    resp2 = await client.delete("/api/jobs", headers=_auth(token))
    assert resp2.status_code == 200
    assert resp2.json()["deleted"] == 0


async def test_cancel_stops_a_device_partway_through_its_command_list(
    client: AsyncClient, unique_email, monkeypatch
):
    # Reproduces the real bug report: cancelling a job only ever skipped
    # devices that hadn't started yet - for a job where the device is
    # already authenticating/running by the time the user clicks cancel
    # (the common case once pipelining is involved), it looked like cancel
    # did nothing at all. This verifies the actual fix: a device mid-way
    # through its command list notices cancel_requested (set by the real
    # /cancel endpoint) and stops, via the same should_cancel plumbing
    # collector.py uses for real.
    token = await _register(client, unique_email)
    cred = await client.post(
        "/api/credentials",
        headers=_auth(token),
        json={"name": "lab", "username": "admin", "password": "cisco123"},
    )
    cred_id = cred.json()["id"]
    device = await client.post(
        "/api/devices",
        headers=_auth(token),
        json={"name": "sw1", "host": "10.0.9.1", "device_type": "cisco_ios", "credential_id": cred_id},
    )
    device_id = device.json()["id"]

    def _fake_attempt(device, credential, otp, commands_override, on_authenticated, on_output, should_cancel):
        on_authenticated()
        # Simulate a concurrent POST /api/jobs/{id}/cancel arriving right
        # after this device authenticates, exactly as it would set
        # CollectionJob.cancel_requested for real.
        db = tasks_module.SyncSessionLocal()
        try:
            item = (
                db.query(tasks_module.CollectionJobItem)
                .filter(tasks_module.CollectionJobItem.device_id == device.id)
                .one()
            )
            job = db.get(tasks_module.CollectionJob, item.job_id)
            job.cancel_requested = True
            db.commit()
        finally:
            db.close()
        if should_cancel():
            raise tasks_module.CollectionCancelled(f"cancelled for {device.host}")
        return f"hostname {device.host}\n"  # pragma: no cover - shouldn't be reached

    monkeypatch.setattr(tasks_module, "_attempt_collection", _fake_attempt)

    job = await client.post("/api/jobs", headers=_auth(token), json={"device_ids": [device_id]})
    assert job.status_code == 201, job.text

    detail = await client.get(f"/api/jobs/{job.json()['id']}", headers=_auth(token))
    body = detail.json()
    assert body["status"] == "cancelled"
    assert body["items"][0]["status"] == "cancelled"
