from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


async def _register(client: AsyncClient, email: str, org_name: str = "SchedOrg") -> str:
    resp = await client.post(
        "/api/auth/register",
        json={"org_name": org_name, "email": email, "password": "password123"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["access_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _parse_utc(iso_string: str) -> datetime:
    # SQLite round-trips DateTime(timezone=True) columns as naive UTC (no
    # offset in the JSON) - the app treats every stored datetime as UTC
    # implicitly (same as everywhere else timestamps are compared in this
    # codebase), so tests normalize it back to tz-aware here rather than
    # comparing naive-vs-aware.
    parsed = datetime.fromisoformat(iso_string)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


async def _add_device(client: AsyncClient, token: str, name: str, host: str) -> str:
    resp = await client.post(
        "/api/devices", headers=_auth(token), json={"name": name, "host": host, "device_type": "cisco_ios"}
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def test_create_every_n_hours_schedule_computes_next_run_at(client: AsyncClient, unique_email):
    token = await _register(client, unique_email)
    before = datetime.now(timezone.utc)

    resp = await client.post(
        "/api/schedules",
        headers=_auth(token),
        json={"name": "Nightly", "frequency": "every_n_hours", "interval_hours": 6},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["enabled"] is True
    assert body["device_ids"] is None
    next_run_at = _parse_utc(body["next_run_at"])
    assert before + timedelta(hours=6) - timedelta(seconds=5) <= next_run_at <= before + timedelta(hours=6, seconds=5)


async def test_create_daily_schedule_computes_next_run_at(client: AsyncClient, unique_email):
    token = await _register(client, unique_email)
    resp = await client.post(
        "/api/schedules",
        headers=_auth(token),
        json={"name": "Daily backup", "frequency": "daily", "run_at_hour": 3, "run_at_minute": 30},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    next_run_at = _parse_utc(body["next_run_at"])
    assert next_run_at.hour == 3
    assert next_run_at.minute == 30
    assert next_run_at > datetime.now(timezone.utc)


async def test_create_daily_schedule_without_time_is_rejected(client: AsyncClient, unique_email):
    token = await _register(client, unique_email)
    resp = await client.post(
        "/api/schedules", headers=_auth(token), json={"name": "Bad", "frequency": "daily"}
    )
    assert resp.status_code == 422


async def test_create_every_n_hours_schedule_without_interval_is_rejected(client: AsyncClient, unique_email):
    token = await _register(client, unique_email)
    resp = await client.post(
        "/api/schedules", headers=_auth(token), json={"name": "Bad", "frequency": "every_n_hours"}
    )
    assert resp.status_code == 422


async def test_create_schedule_rejects_unknown_device_id(client: AsyncClient, unique_email):
    token = await _register(client, unique_email)
    resp = await client.post(
        "/api/schedules",
        headers=_auth(token),
        json={
            "name": "Bad devices",
            "frequency": "every_n_hours",
            "interval_hours": 1,
            "device_ids": ["00000000-0000-0000-0000-000000000000"],
        },
    )
    assert resp.status_code == 400


async def test_create_schedule_with_specific_devices_round_trips(client: AsyncClient, unique_email):
    token = await _register(client, unique_email)
    device_id = await _add_device(client, token, "sched-sw1", "192.0.2.10")

    resp = await client.post(
        "/api/schedules",
        headers=_auth(token),
        json={
            "name": "Specific devices",
            "frequency": "every_n_hours",
            "interval_hours": 2,
            "device_ids": [device_id],
        },
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["device_ids"] == [device_id]


async def test_list_schedules_scoped_to_org(client: AsyncClient, unique_email):
    token_a = await _register(client, unique_email)
    await client.post(
        "/api/schedules",
        headers=_auth(token_a),
        json={"name": "OrgA schedule", "frequency": "every_n_hours", "interval_hours": 4},
    )

    other_email = f"other-{unique_email}"
    token_b = await _register(client, other_email, org_name="OtherSchedOrg")
    resp = await client.get("/api/schedules", headers=_auth(token_b))
    assert resp.status_code == 200
    assert resp.json() == []


async def test_update_schedule_toggle_enabled_and_rename(client: AsyncClient, unique_email):
    token = await _register(client, unique_email)
    create_resp = await client.post(
        "/api/schedules",
        headers=_auth(token),
        json={"name": "Original", "frequency": "every_n_hours", "interval_hours": 12},
    )
    schedule_id = create_resp.json()["id"]

    resp = await client.patch(
        f"/api/schedules/{schedule_id}",
        headers=_auth(token),
        json={"name": "Renamed", "enabled": False},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["name"] == "Renamed"
    assert body["enabled"] is False
    # Unrelated fields (frequency/timing) shouldn't have moved.
    assert body["interval_hours"] == 12


async def test_update_schedule_frequency_recomputes_next_run_at(client: AsyncClient, unique_email):
    token = await _register(client, unique_email)
    create_resp = await client.post(
        "/api/schedules",
        headers=_auth(token),
        json={"name": "Switching", "frequency": "every_n_hours", "interval_hours": 1},
    )
    schedule_id = create_resp.json()["id"]
    original_next_run = create_resp.json()["next_run_at"]

    resp = await client.patch(
        f"/api/schedules/{schedule_id}",
        headers=_auth(token),
        json={"frequency": "every_n_hours", "interval_hours": 48},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["next_run_at"] != original_next_run
    assert resp.json()["interval_hours"] == 48


async def test_delete_schedule(client: AsyncClient, unique_email):
    token = await _register(client, unique_email)
    create_resp = await client.post(
        "/api/schedules",
        headers=_auth(token),
        json={"name": "To delete", "frequency": "every_n_hours", "interval_hours": 1},
    )
    schedule_id = create_resp.json()["id"]

    resp = await client.delete(f"/api/schedules/{schedule_id}", headers=_auth(token))
    assert resp.status_code == 204

    list_resp = await client.get("/api/schedules", headers=_auth(token))
    assert list_resp.json() == []


async def test_schedule_not_found_for_other_org(client: AsyncClient, unique_email):
    token_a = await _register(client, unique_email)
    create_resp = await client.post(
        "/api/schedules",
        headers=_auth(token_a),
        json={"name": "OrgA only", "frequency": "every_n_hours", "interval_hours": 1},
    )
    schedule_id = create_resp.json()["id"]

    other_email = f"other-{unique_email}"
    token_b = await _register(client, other_email, org_name="OtherSchedOrg2")
    resp = await client.get(f"/api/schedules", headers=_auth(token_b))
    assert resp.status_code == 200 and resp.json() == []

    patch_resp = await client.patch(
        f"/api/schedules/{schedule_id}", headers=_auth(token_b), json={"enabled": False}
    )
    assert patch_resp.status_code == 404

    delete_resp = await client.delete(f"/api/schedules/{schedule_id}", headers=_auth(token_b))
    assert delete_resp.status_code == 404


async def test_run_schedule_now_creates_a_job(client: AsyncClient, unique_email):
    token = await _register(client, unique_email)
    await client.post(
        "/api/credentials", headers=_auth(token), json={"name": "lab", "username": "admin", "password": "cisco123"}
    )
    device_id = await _add_device(client, token, "sched-sw2", "192.0.2.11")

    create_resp = await client.post(
        "/api/schedules",
        headers=_auth(token),
        json={
            "name": "Run me",
            "frequency": "every_n_hours",
            "interval_hours": 1,
            "device_ids": [device_id],
        },
    )
    schedule_id = create_resp.json()["id"]

    run_resp = await client.post(f"/api/schedules/{schedule_id}/run-now", headers=_auth(token))
    assert run_resp.status_code == 201, run_resp.text
    job = run_resp.json()
    assert job["item_count"] == 1

    get_resp = await client.get(f"/api/schedules", headers=_auth(token))
    updated = next(s for s in get_resp.json() if s["id"] == schedule_id)
    assert updated["last_job_id"] == job["id"]
    assert updated["last_run_at"] is not None


async def test_run_schedule_now_404_for_other_org(client: AsyncClient, unique_email):
    token_a = await _register(client, unique_email)
    create_resp = await client.post(
        "/api/schedules",
        headers=_auth(token_a),
        json={"name": "OrgA", "frequency": "every_n_hours", "interval_hours": 1},
    )
    schedule_id = create_resp.json()["id"]

    other_email = f"other-{unique_email}"
    token_b = await _register(client, other_email, org_name="OtherSchedOrg3")
    resp = await client.post(f"/api/schedules/{schedule_id}/run-now", headers=_auth(token_b))
    assert resp.status_code == 404


async def test_non_admin_cannot_manage_schedules(client: AsyncClient, unique_email):
    token = await _register(client, unique_email)
    member_email = f"member-{unique_email}"
    create_user_resp = await client.post(
        "/api/users", headers=_auth(token), json={"email": member_email, "password": "password123"}
    )
    assert create_user_resp.status_code == 201, create_user_resp.text

    member_login = await client.post(
        "/api/auth/login", json={"email": member_email, "password": "password123"}
    )
    member_token = member_login.json()["access_token"]

    resp = await client.post(
        "/api/schedules",
        headers=_auth(member_token),
        json={"name": "Nope", "frequency": "every_n_hours", "interval_hours": 1},
    )
    assert resp.status_code == 403
