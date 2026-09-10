from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient

from app import tasks as tasks_module
from app.models.job import CollectionJob, CollectionJobItem, JobStatus
from app.models.organization import Organization
from app.models.schedule import Schedule, ScheduleFrequency
from app.models.snapshot import ConfigSnapshot

pytestmark = pytest.mark.asyncio


async def _register(client: AsyncClient, email: str, org_name: str = "TaskSchedOrg") -> tuple[str, str, str]:
    resp = await client.post(
        "/api/auth/register",
        json={"org_name": org_name, "email": email, "password": "password123"},
    )
    assert resp.status_code == 201, resp.text
    token = resp.json()["access_token"]
    me = await client.get("/api/auth/me", headers=_auth(token))
    return token, me.json()["org_id"], me.json()["id"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _make_schedule(org_id, user_id, *, next_run_at, enabled=True, device_ids=None) -> str:
    db = tasks_module.SyncSessionLocal()
    try:
        schedule = Schedule(
            org_id=org_id,
            created_by_id=user_id,
            name="test schedule",
            enabled=enabled,
            device_ids=",".join(device_ids) if device_ids else None,
            frequency=ScheduleFrequency.EVERY_N_HOURS,
            interval_hours=6,
            next_run_at=next_run_at,
        )
        db.add(schedule)
        db.commit()
        return str(schedule.id)
    finally:
        db.close()


def _get_schedule(schedule_id: str) -> Schedule:
    db = tasks_module.SyncSessionLocal()
    try:
        schedule = db.get(Schedule, schedule_id)
        # SQLite round-trips DateTime(timezone=True) columns as naive UTC -
        # normalize back to tz-aware so these can be compared against
        # tz-aware datetimes built with datetime.now(timezone.utc), same as
        # the rest of the app implicitly treats every stored datetime.
        if schedule.next_run_at.tzinfo is None:
            schedule.next_run_at = schedule.next_run_at.replace(tzinfo=timezone.utc)
        if schedule.last_run_at is not None and schedule.last_run_at.tzinfo is None:
            schedule.last_run_at = schedule.last_run_at.replace(tzinfo=timezone.utc)
        return schedule
    finally:
        db.close()


async def test_run_due_schedules_creates_job_and_advances_next_run(client: AsyncClient, unique_email):
    token, org_id, user_id = await _register(client, unique_email)
    await client.post(
        "/api/credentials", headers=_auth(token), json={"name": "lab", "username": "admin", "password": "cisco123"}
    )
    device = await client.post(
        "/api/devices", headers=_auth(token), json={"name": "task-sw1", "host": "192.0.2.20", "device_type": "cisco_ios"}
    )
    device_id = device.json()["id"]

    past = datetime.now(timezone.utc) - timedelta(minutes=1)
    schedule_id = _make_schedule(org_id, user_id, next_run_at=past, device_ids=[device_id])

    tasks_module.run_due_schedules()

    updated = _get_schedule(schedule_id)
    assert updated.last_job_id is not None
    assert updated.last_run_at is not None
    assert updated.next_run_at > past + timedelta(hours=5)  # advanced by ~6 hours from "now", not from `past`

    job_resp = await client.get(f"/api/jobs/{updated.last_job_id}", headers=_auth(token))
    assert job_resp.status_code == 200, job_resp.text
    assert job_resp.json()["item_count"] == 1


async def test_run_due_schedules_skips_disabled_schedule(client: AsyncClient, unique_email):
    token, org_id, user_id = await _register(client, unique_email)
    past = datetime.now(timezone.utc) - timedelta(minutes=1)
    schedule_id = _make_schedule(org_id, user_id, next_run_at=past, enabled=False)

    tasks_module.run_due_schedules()

    updated = _get_schedule(schedule_id)
    assert updated.last_job_id is None
    assert updated.next_run_at == past  # untouched - a disabled schedule doesn't even advance


async def test_run_due_schedules_skips_schedule_not_yet_due(client: AsyncClient, unique_email):
    token, org_id, user_id = await _register(client, unique_email)
    future = datetime.now(timezone.utc) + timedelta(hours=1)
    schedule_id = _make_schedule(org_id, user_id, next_run_at=future)

    tasks_module.run_due_schedules()

    updated = _get_schedule(schedule_id)
    assert updated.last_job_id is None
    assert updated.next_run_at == future


async def test_run_due_schedules_with_no_matching_devices_advances_without_a_job(client: AsyncClient, unique_email):
    token, org_id, user_id = await _register(client, unique_email)
    past = datetime.now(timezone.utc) - timedelta(minutes=1)
    # No devices in this org at all, and device_ids=None means "all devices".
    schedule_id = _make_schedule(org_id, user_id, next_run_at=past)

    tasks_module.run_due_schedules()

    updated = _get_schedule(schedule_id)
    assert updated.last_job_id is None
    assert updated.last_run_at is None
    assert updated.next_run_at > past  # still advanced, so it doesn't spin forever


async def test_run_due_schedules_all_devices_when_device_ids_not_set(client: AsyncClient, unique_email):
    token, org_id, user_id = await _register(client, unique_email)
    await client.post(
        "/api/credentials", headers=_auth(token), json={"name": "lab", "username": "admin", "password": "cisco123"}
    )
    await client.post(
        "/api/devices", headers=_auth(token), json={"name": "task-sw2", "host": "192.0.2.21", "device_type": "cisco_ios"}
    )
    await client.post(
        "/api/devices", headers=_auth(token), json={"name": "task-sw3", "host": "192.0.2.22", "device_type": "cisco_ios"}
    )
    past = datetime.now(timezone.utc) - timedelta(minutes=1)
    schedule_id = _make_schedule(org_id, user_id, next_run_at=past)  # device_ids=None -> all devices

    tasks_module.run_due_schedules()

    updated = _get_schedule(schedule_id)
    job_resp = await client.get(f"/api/jobs/{updated.last_job_id}", headers=_auth(token))
    assert job_resp.json()["item_count"] == 2


def _seed_snapshot_at(org_id, user_id, device_id: str, collected_at) -> str:
    db = tasks_module.SyncSessionLocal()
    try:
        job = CollectionJob(org_id=org_id, created_by_id=user_id, status=JobStatus.COMPLETED)
        db.add(job)
        db.flush()
        item = CollectionJobItem(job_id=job.id, device_id=device_id, status=JobStatus.COMPLETED)
        db.add(item)
        db.flush()
        snapshot = ConfigSnapshot(
            device_id=device_id, job_item_id=item.id, content="old config\n", collected_at=collected_at
        )
        db.add(snapshot)
        db.commit()
        return str(snapshot.id)
    finally:
        db.close()


def _set_retention(org_id, days: int | None) -> None:
    db = tasks_module.SyncSessionLocal()
    try:
        org = db.get(Organization, org_id)
        org.snapshot_retention_days = days
        db.commit()
    finally:
        db.close()


async def test_purge_expired_snapshots_deletes_past_retention_window(client: AsyncClient, unique_email):
    token, org_id, user_id = await _register(client, unique_email)
    device = await client.post(
        "/api/devices", headers=_auth(token), json={"name": "purge-sw1", "host": "192.0.2.30", "device_type": "cisco_ios"}
    )
    device_id = device.json()["id"]
    _set_retention(org_id, 30)

    old_snapshot_id = _seed_snapshot_at(
        org_id, user_id, device_id, datetime.now(timezone.utc) - timedelta(days=31)
    )
    recent_snapshot_id = _seed_snapshot_at(
        org_id, user_id, device_id, datetime.now(timezone.utc) - timedelta(days=1)
    )

    tasks_module.purge_expired_snapshots()

    old_resp = await client.get(f"/api/snapshots/{old_snapshot_id}", headers=_auth(token))
    assert old_resp.status_code == 404

    recent_resp = await client.get(f"/api/snapshots/{recent_snapshot_id}", headers=_auth(token))
    assert recent_resp.status_code == 200


async def test_purge_expired_snapshots_skips_orgs_with_no_retention_set(client: AsyncClient, unique_email):
    token, org_id, user_id = await _register(client, unique_email)
    device = await client.post(
        "/api/devices", headers=_auth(token), json={"name": "purge-sw2", "host": "192.0.2.31", "device_type": "cisco_ios"}
    )
    device_id = device.json()["id"]
    # Retention left at its default (None = keep forever).

    old_snapshot_id = _seed_snapshot_at(
        org_id, user_id, device_id, datetime.now(timezone.utc) - timedelta(days=3650)
    )

    tasks_module.purge_expired_snapshots()

    resp = await client.get(f"/api/snapshots/{old_snapshot_id}", headers=_auth(token))
    assert resp.status_code == 200
