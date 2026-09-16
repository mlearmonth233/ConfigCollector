"""Collection jobs carry a human-readable name: what the user typed, else a
timestamped default; scheduled jobs are named after their schedule in
the schedule's own timezone."""

from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient

from app import tasks as tasks_module
from app.db_sync import SyncSessionLocal
from app.models.schedule import Schedule, ScheduleFrequency

pytestmark = pytest.mark.asyncio


async def _register(client: AsyncClient, email: str) -> tuple[str, str, str]:
    resp = await client.post("/api/auth/register", json={"org_name": "NamesOrg", "email": email, "password": "password123"})
    assert resp.status_code == 201, resp.text
    token = resp.json()["access_token"]
    me = (await client.get("/api/auth/me", headers=_auth(token))).json()
    return token, me["org_id"], me["id"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _device(client: AsyncClient, token: str) -> str:
    await client.post("/api/credentials", headers=_auth(token), json={"name": "lab", "username": "admin", "password": "cisco123"})
    device = await client.post("/api/devices", headers=_auth(token), json={"name": "sw1", "host": "192.0.2.1", "device_type": "cisco_ios"})
    assert device.status_code == 201, device.text
    return device.json()["id"]


async def test_manual_job_gets_the_typed_name_or_a_timestamped_default(client: AsyncClient, unique_email, monkeypatch):
    token, _, _ = await _register(client, unique_email)
    device_id = await _device(client, token)
    monkeypatch.setattr(tasks_module, "_attempt_collection", lambda *a, **k: "hostname sw1\n")

    named = await client.post("/api/jobs", headers=_auth(token), json={"device_ids": [device_id], "name": "  Pre-change backup, Site B  "})
    assert named.status_code == 201, named.text
    assert named.json()["name"] == "Pre-change backup, Site B"

    unnamed = await client.post("/api/jobs", headers=_auth(token), json={"device_ids": [device_id]})
    name = unnamed.json()["name"]
    assert name.startswith("Collection 20") and name.endswith(" UTC")
    datetime.strptime(name, "Collection %Y-%m-%d %H:%M UTC")  # parses

    listed = (await client.get("/api/jobs", headers=_auth(token))).json()
    assert {j["name"] for j in listed} >= {"Pre-change backup, Site B", name}
    too_long = await client.post("/api/jobs", headers=_auth(token), json={"device_ids": [device_id], "name": "x" * 201})
    assert too_long.status_code == 422


async def test_scheduled_job_is_named_after_the_schedule_in_its_timezone(client: AsyncClient, unique_email, monkeypatch):
    token, org_id, user_id = await _register(client, unique_email)
    await _device(client, token)
    monkeypatch.setattr(tasks_module, "_attempt_collection", lambda *a, **k: "hostname sw1\n")

    db = SyncSessionLocal()
    try:
        schedule = Schedule(
            org_id=org_id,
            created_by_id=user_id,
            name="Nightly backup",
            enabled=True,
            device_ids=None,
            frequency=ScheduleFrequency.DAILY,
            run_at_hour=2,
            run_at_minute=0,
            timezone="America/Denver",
            next_run_at=datetime.now(timezone.utc) - timedelta(minutes=1),
        )
        db.add(schedule)
        db.commit()
        schedule_id = schedule.id
    finally:
        db.close()

    tasks_module.run_due_schedules()

    db = SyncSessionLocal()
    try:
        schedule = db.get(Schedule, schedule_id)
        job_id = schedule.last_job_id
    finally:
        db.close()
    job = (await client.get(f"/api/jobs/{job_id}", headers=_auth(token))).json()
    assert job["name"].startswith("Nightly backup 20") and not job["name"].endswith("UTC")
    # The timestamp is Denver's clock, not UTC's.
    from zoneinfo import ZoneInfo  # noqa: PLC0415

    denver_now = datetime.now(ZoneInfo("America/Denver")).strftime("%Y-%m-%d %H:")
    assert job["name"].startswith(f"Nightly backup {denver_now}")
