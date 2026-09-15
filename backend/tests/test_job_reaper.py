"""Stuck-job cleanup: jobs whose worker died are marked interrupted on
start-up, when stale, or by the user's Force stop."""

from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.db_sync import SyncSessionLocal
from app.models.job import CollectionJob, CollectionJobItem, JobStatus
from app.services import job_reaper

pytestmark = pytest.mark.asyncio


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _register(client: AsyncClient, email: str) -> str:
    resp = await client.post("/api/auth/register", json={"email": email, "password": "secret123", "org_name": "Reaper Org"})
    assert resp.status_code == 201, resp.text
    return resp.json()["access_token"]


async def _stuck_job(client: AsyncClient, token: str, names: tuple[str, ...] = ("sw1", "sw2", "sw3")) -> str:
    """A job that looks exactly like one whose worker died: device 1 done,
    device 2 mid-commands, device 3 still queued, job RUNNING. Built by
    running a real (eager) job and then rewinding the rows, since a test
    can't kill the in-process worker halfway."""
    await client.post("/api/credentials", headers=_auth(token), json={"name": "cred", "username": "u", "password": "p"})
    ids = []
    for i, name in enumerate(names):
        r = await client.post("/api/devices", headers=_auth(token), json={"name": name, "host": f"192.0.2.{i + 1}", "device_type": "cisco_ios"})
        ids.append(r.json()["id"])
    job = await client.post("/api/jobs", headers=_auth(token), json={"device_ids": ids})
    assert job.status_code == 201, job.text
    job_id = job.json()["id"]

    db = SyncSessionLocal()
    try:
        row = db.get(CollectionJob, job_id)
        items = sorted(db.execute(select(CollectionJobItem).where(CollectionJobItem.job_id == row.id)).scalars().all(), key=lambda i: i.created_at)
        now = datetime.now(timezone.utc)
        row.status = JobStatus.RUNNING
        row.finished_at = None
        row.cancel_requested = False
        items[0].status, items[0].error_message, items[0].finished_at = JobStatus.COMPLETED, None, now
        items[1].status, items[1].error_message, items[1].finished_at = JobStatus.RUNNING, None, None
        items[1].live_output = "Connecting...\nAuthenticated.\n$ show running-config\n"
        items[2].status, items[2].error_message, items[2].finished_at, items[2].started_at = JobStatus.PENDING, None, None, None
        db.commit()
    finally:
        db.close()
    return job_id


def _statuses(job_id: str) -> tuple[str, list[tuple[str, str | None]]]:
    db = SyncSessionLocal()
    try:
        job = db.get(CollectionJob, job_id)
        items = sorted(db.execute(select(CollectionJobItem).where(CollectionJobItem.job_id == job.id)).scalars().all(), key=lambda i: i.created_at)
        return job.status.value, [(i.status.value, i.error_message) for i in items]
    finally:
        db.close()


async def test_stuck_job_blocks_device_delete_with_a_hint(client: AsyncClient, unique_email, monkeypatch):
    monkeypatch.setattr("app.tasks._attempt_collection", lambda *a, **k: "hostname sw\n")
    token = await _register(client, unique_email)
    job_id = await _stuck_job(client, token)
    job = (await client.get(f"/api/jobs/{job_id}", headers=_auth(token))).json()
    running = next(i for i in job["items"] if i["status"] == "running")
    resp = await client.delete(f"/api/devices/{running['device_id']}", headers=_auth(token))
    assert resp.status_code == 400
    assert "Force stop" in resp.json()["detail"]


async def test_startup_reap_marks_leftover_jobs_interrupted(client: AsyncClient, unique_email, monkeypatch):
    monkeypatch.setattr("app.tasks._attempt_collection", lambda *a, **k: "hostname sw\n")
    token = await _register(client, unique_email)
    job_id = await _stuck_job(client, token)

    summary = job_reaper.reap_orphaned_jobs("the worker was restarted")
    assert summary.get("collection", 0) >= 1

    status, items = _statuses(job_id)
    assert status == "cancelled"  # a queued device was skipped, so cancelled wins over failed
    assert items[0] == ("completed", None)
    assert items[1][0] == "failed" and "Interrupted: the worker was restarted" in items[1][1]
    assert items[2][0] == "cancelled" and "Skipped" in items[2][1]

    # The device is deletable again and the job list shows it finished.
    job = (await client.get(f"/api/jobs/{job_id}", headers=_auth(token))).json()
    assert job["status"] == "cancelled" and job["finished_at"] is not None
    interrupted = next(i for i in job["items"] if i["status"] == "failed")
    assert "[Interrupted" in interrupted["live_output"]
    assert (await client.delete(f"/api/devices/{interrupted['device_id']}", headers=_auth(token))).status_code == 204

    # Nothing left to reap on a second pass.
    assert job_reaper.reap_orphaned_jobs() == {}


async def test_stale_sweep_only_touches_jobs_without_recent_activity(client: AsyncClient, unique_email, monkeypatch):
    monkeypatch.setattr("app.tasks._attempt_collection", lambda *a, **k: "hostname sw\n")
    token = await _register(client, unique_email)
    job_id = await _stuck_job(client, token)

    # Rows were just written, so a 45-minute threshold leaves the job alone...
    assert job_reaper.reap_stale_jobs(timedelta(minutes=45)) == {}
    assert _statuses(job_id)[0] == "running"

    # ...until every timestamp on it is older than the threshold.
    db = SyncSessionLocal()
    try:
        old = datetime.now(timezone.utc) - timedelta(hours=2)
        job = db.get(CollectionJob, job_id)
        job.created_at = job.started_at = job.updated_at = old
        for item in db.execute(select(CollectionJobItem).where(CollectionJobItem.job_id == job.id)).scalars():
            item.created_at = item.updated_at = old
            item.started_at = old if item.started_at else None
            item.finished_at = old if item.finished_at else None
        db.commit()
        # SQLAlchemy's onupdate just re-stamped updated_at to now; force it back.
        for row in [job, *db.execute(select(CollectionJobItem).where(CollectionJobItem.job_id == job.id)).scalars()]:
            row.updated_at = old
        db.commit()
    finally:
        db.close()

    summary = job_reaper.reap_stale_jobs(timedelta(minutes=45))
    assert summary == {"collection": 1}
    status, items = _statuses(job_id)
    assert status == "cancelled"
    assert items[1][0] == "failed" and "no progress for 45 minutes" in items[1][1]


async def test_force_stop_endpoint_ends_a_stuck_job_now(client: AsyncClient, unique_email, monkeypatch):
    monkeypatch.setattr("app.tasks._attempt_collection", lambda *a, **k: "hostname sw\n")
    token = await _register(client, unique_email)
    job_id = await _stuck_job(client, token)

    cancelled = await client.post(f"/api/jobs/{job_id}/cancel", headers=_auth(token))
    assert cancelled.status_code == 200
    body = cancelled.json()
    assert body["cancel_requested"] is True
    assert body["status"] == "running"  # the in-flight device can't be interrupted by a dead worker

    stopped = await client.post(f"/api/jobs/{job_id}/force-stop", headers=_auth(token))
    assert stopped.status_code == 200, stopped.text
    body = stopped.json()
    assert body["status"] == "cancelled" and body["finished_at"] is not None
    by_status = {i["status"] for i in body["items"]}
    assert by_status == {"completed", "cancelled"}
    # Cancel already marked the queued device cancelled; Force stop dealt
    # with the one the dead worker had in flight.
    forced = [i for i in body["items"] if i["error_message"] and "Force-stopped by" in i["error_message"]]
    assert len(forced) == 1 and forced[0]["device_name"] == "sw2" and unique_email in forced[0]["error_message"]
    assert "[Force-stopped by" in forced[0]["live_output"]

    # Already finished -> 400, and the device is free to delete.
    assert (await client.post(f"/api/jobs/{job_id}/force-stop", headers=_auth(token))).status_code == 400
    device_id = next(i["device_id"] for i in body["items"] if i["status"] == "cancelled" and i["device_id"])
    assert (await client.delete(f"/api/devices/{device_id}", headers=_auth(token))).status_code == 204


async def test_force_stop_requires_the_job_to_belong_to_the_caller(client: AsyncClient, unique_email, monkeypatch):
    monkeypatch.setattr("app.tasks._attempt_collection", lambda *a, **k: "hostname sw\n")
    token = await _register(client, unique_email)
    other = await _register(client, f"other-{unique_email}")
    job_id = await _stuck_job(client, token)
    assert (await client.post(f"/api/jobs/{job_id}/force-stop", headers=_auth(other))).status_code == 404
    assert _statuses(job_id)[0] == "running"


def test_interrupt_job_leaves_finished_items_alone_and_finalizes():
    class Item:
        def __init__(self, status):
            self.status = status
            self.error_message = None
            self.live_output = ""
            self.started_at = None
            self.finished_at = None

    class Job:
        status = JobStatus.RUNNING
        started_at = None
        finished_at = None

    job = Job()
    items = [Item(JobStatus.COMPLETED), Item(JobStatus.FAILED), Item(JobStatus.AUTHENTICATING)]
    changed = job_reaper.interrupt_job(job, items, reason="test")
    assert changed == 1
    assert [i.status for i in items] == [JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.FAILED]
    assert items[2].error_message == "Interrupted: test" and items[2].finished_at is not None
    assert job.status == JobStatus.FAILED and job.finished_at is not None
