import pytest
from httpx import AsyncClient

from app.database import async_session_factory
from app.models.job import CollectionJob, CollectionJobItem, JobStatus

pytestmark = pytest.mark.asyncio


async def _register(client: AsyncClient, email: str, org_name: str = "ClearOrg") -> str:
    resp = await client.post(
        "/api/auth/register",
        json={"org_name": org_name, "email": email, "password": "password123"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["access_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def test_clear_all_devices_deletes_everything(client: AsyncClient, unique_email):
    token = await _register(client, unique_email)
    for i in range(3):
        resp = await client.post(
            "/api/devices",
            headers=_auth(token),
            json={"name": f"sw-{i}", "host": "192.0.2.1", "device_type": "cisco_ios"},
        )
        assert resp.status_code == 201, resp.text

    resp = await client.delete("/api/devices", headers=_auth(token))
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"deleted": 3, "skipped": 0}

    listing = await client.get("/api/devices", headers=_auth(token))
    assert listing.json() == []


async def test_clear_all_devices_empty_org_is_a_no_op(client: AsyncClient, unique_email):
    token = await _register(client, unique_email)
    resp = await client.delete("/api/devices", headers=_auth(token))
    assert resp.status_code == 200
    assert resp.json() == {"deleted": 0, "skipped": 0}


async def test_clear_all_devices_skips_device_with_job_in_progress(client: AsyncClient, unique_email):
    token = await _register(client, unique_email)
    me = await client.get("/api/auth/me", headers=_auth(token))
    org_id = me.json()["org_id"]
    user_id = me.json()["id"]

    busy = await client.post(
        "/api/devices", headers=_auth(token), json={"name": "busy-sw", "host": "192.0.2.1", "device_type": "cisco_ios"}
    )
    idle = await client.post(
        "/api/devices", headers=_auth(token), json={"name": "idle-sw", "host": "192.0.2.2", "device_type": "cisco_ios"}
    )
    busy_id = busy.json()["id"]
    idle_id = idle.json()["id"]

    async with async_session_factory() as db:
        job = CollectionJob(org_id=org_id, created_by_id=user_id, status=JobStatus.RUNNING)
        db.add(job)
        await db.flush()
        db.add(CollectionJobItem(job_id=job.id, device_id=busy_id, status=JobStatus.RUNNING))
        await db.commit()

    resp = await client.delete("/api/devices", headers=_auth(token))
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"deleted": 1, "skipped": 1}

    remaining = (await client.get("/api/devices", headers=_auth(token))).json()
    assert [d["id"] for d in remaining] == [busy_id]
    assert not any(d["id"] == idle_id for d in remaining)


async def test_clear_all_devices_scoped_to_caller_org(client: AsyncClient, unique_email):
    token_a = await _register(client, unique_email, org_name="OrgA")
    token_b = await _register(client, f"other-{unique_email}", org_name="OrgB")

    await client.post(
        "/api/devices", headers=_auth(token_a), json={"name": "a-sw", "host": "192.0.2.1", "device_type": "cisco_ios"}
    )

    resp = await client.delete("/api/devices", headers=_auth(token_b))
    assert resp.status_code == 200
    assert resp.json() == {"deleted": 0, "skipped": 0}

    still_there = await client.get("/api/devices", headers=_auth(token_a))
    assert len(still_there.json()) == 1
