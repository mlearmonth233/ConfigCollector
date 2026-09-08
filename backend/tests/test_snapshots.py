from datetime import datetime, timezone

import pytest
from httpx import AsyncClient

from app.database import async_session_factory
from app.models.job import CollectionJob, CollectionJobItem, JobStatus
from app.models.snapshot import ConfigSnapshot
from app.services.filenames import build_snapshot_filename


def test_build_filename_defaults_to_hostname_txt_no_timestamp():
    collected_at = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    assert build_snapshot_filename("core-sw1", collected_at=collected_at, ext="txt", include_timestamp=False) == (
        "core-sw1.txt"
    )


def test_build_filename_with_timestamp_and_log_extension():
    collected_at = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    assert build_snapshot_filename("core-sw1", collected_at=collected_at, ext="log", include_timestamp=True) == (
        "core-sw1_20260102T030405.log"
    )


def test_build_filename_sanitizes_unsafe_characters():
    collected_at = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    assert build_snapshot_filename('core sw1/"weird"', collected_at=collected_at, ext="txt", include_timestamp=False) == (
        "core_sw1_weird.txt"
    )


def test_build_filename_falls_back_when_hostname_is_all_unsafe():
    collected_at = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    assert build_snapshot_filename("///", collected_at=collected_at, ext="txt", include_timestamp=False) == "device.txt"


async def _register(client: AsyncClient, email: str) -> str:
    resp = await client.post(
        "/api/auth/register",
        json={"org_name": "SnapOrg", "email": email, "password": "password123"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["access_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_download_snapshot_filename_options(client: AsyncClient, unique_email):
    token = await _register(client, unique_email)
    device = await client.post(
        "/api/devices",
        headers=_auth(token),
        json={"name": "core-sw1", "host": "192.0.2.99", "device_type": "cisco_ios"},
    )
    device_id = device.json()["id"]
    me = await client.get("/api/auth/me", headers=_auth(token))
    org_id = me.json()["org_id"]
    user_id = me.json()["id"]

    # Insert a snapshot directly - no real device is reachable in tests, and
    # this endpoint's filename behavior doesn't depend on how the snapshot
    # was collected.
    async with async_session_factory() as db:
        job = CollectionJob(org_id=org_id, created_by_id=user_id, status=JobStatus.COMPLETED)
        db.add(job)
        await db.flush()
        item = CollectionJobItem(job_id=job.id, device_id=device_id, status=JobStatus.COMPLETED)
        db.add(item)
        await db.flush()
        snapshot = ConfigSnapshot(device_id=device_id, job_item_id=item.id, content="hostname core-sw1\n")
        db.add(snapshot)
        await db.commit()
        snapshot_id = str(snapshot.id)

    default_resp = await client.get(f"/api/snapshots/{snapshot_id}/download", headers=_auth(token))
    assert default_resp.status_code == 200
    assert 'filename="core-sw1.txt"' in default_resp.headers["content-disposition"]

    log_resp = await client.get(
        f"/api/snapshots/{snapshot_id}/download",
        headers=_auth(token),
        params={"ext": "log", "include_timestamp": "true"},
    )
    assert log_resp.status_code == 200
    disposition = log_resp.headers["content-disposition"]
    assert 'filename="core-sw1_' in disposition
    assert disposition.endswith('.log"')

    bad_ext_resp = await client.get(
        f"/api/snapshots/{snapshot_id}/download", headers=_auth(token), params={"ext": "exe"}
    )
    assert bad_ext_resp.status_code == 422
