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


@pytest.mark.asyncio
async def test_deleting_device_orphans_job_history_instead_of_failing(client: AsyncClient, unique_email):
    token = await _register(client, unique_email)
    device = await client.post(
        "/api/devices",
        headers=_auth(token),
        json={"name": "core-sw2", "host": "192.0.2.98", "device_type": "cisco_ios"},
    )
    device_id = device.json()["id"]
    me = await client.get("/api/auth/me", headers=_auth(token))
    org_id = me.json()["org_id"]
    user_id = me.json()["id"]

    async with async_session_factory() as db:
        job = CollectionJob(org_id=org_id, created_by_id=user_id, status=JobStatus.COMPLETED)
        db.add(job)
        await db.flush()
        item = CollectionJobItem(job_id=job.id, device_id=device_id, status=JobStatus.COMPLETED)
        db.add(item)
        await db.flush()
        snapshot = ConfigSnapshot(device_id=device_id, job_item_id=item.id, content="hostname core-sw2\n")
        db.add(snapshot)
        await db.commit()
        job_id, item_id, snapshot_id = str(job.id), str(item.id), str(snapshot.id)

    delete_resp = await client.delete(f"/api/devices/{device_id}", headers=_auth(token))
    assert delete_resp.status_code == 204, delete_resp.text

    # The job/item/snapshot are all still there - only the device is gone.
    job_detail = await client.get(f"/api/jobs/{job_id}", headers=_auth(token))
    assert job_detail.status_code == 200, job_detail.text
    item_out = next(i for i in job_detail.json()["items"] if i["id"] == item_id)
    assert item_out["device_id"] is None
    assert item_out["status"] == "completed"

    # A snapshot's ownership no longer depends on its device existing.
    snap_resp = await client.get(f"/api/snapshots/{snapshot_id}", headers=_auth(token))
    assert snap_resp.status_code == 200, snap_resp.text
    assert snap_resp.json()["device_id"] is None

    download_resp = await client.get(f"/api/snapshots/{snapshot_id}/download", headers=_auth(token))
    assert download_resp.status_code == 200, download_resp.text
    assert 'filename="deleted-device.txt"' in download_resp.headers["content-disposition"]


async def _seed_snapshot(org_id, user_id, device_id: str, content: str) -> str:
    async with async_session_factory() as db:
        job = CollectionJob(org_id=org_id, created_by_id=user_id, status=JobStatus.COMPLETED)
        db.add(job)
        await db.flush()
        item = CollectionJobItem(job_id=job.id, device_id=device_id, status=JobStatus.COMPLETED)
        db.add(item)
        await db.flush()
        snapshot = ConfigSnapshot(device_id=device_id, job_item_id=item.id, content=content)
        db.add(snapshot)
        await db.commit()
        return str(snapshot.id)


@pytest.mark.asyncio
async def test_diff_snapshots_shows_added_and_removed_lines(client: AsyncClient, unique_email):
    token = await _register(client, unique_email)
    device = await client.post(
        "/api/devices",
        headers=_auth(token),
        json={"name": "diff-sw1", "host": "192.0.2.90", "device_type": "cisco_ios"},
    )
    device_id = device.json()["id"]
    me = await client.get("/api/auth/me", headers=_auth(token))
    org_id, user_id = me.json()["org_id"], me.json()["id"]

    old_id = await _seed_snapshot(org_id, user_id, device_id, "hostname sw1\nline vty 0 4\n")
    new_id = await _seed_snapshot(org_id, user_id, device_id, "hostname sw1\nntp server 10.0.0.1\n")

    resp = await client.get(
        "/api/snapshots/diff", headers=_auth(token), params={"from_id": old_id, "to_id": new_id}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["from_id"] == old_id
    assert body["to_id"] == new_id
    diff_text = "\n".join(body["diff"])
    assert "-line vty 0 4" in diff_text
    assert "+ntp server 10.0.0.1" in diff_text
    assert "hostname sw1" not in "\n".join(
        line for line in body["diff"] if line.startswith("+") or line.startswith("-")
    )


@pytest.mark.asyncio
async def test_diff_identical_snapshots_is_empty(client: AsyncClient, unique_email):
    token = await _register(client, unique_email)
    device = await client.post(
        "/api/devices",
        headers=_auth(token),
        json={"name": "diff-sw2", "host": "192.0.2.91", "device_type": "cisco_ios"},
    )
    device_id = device.json()["id"]
    me = await client.get("/api/auth/me", headers=_auth(token))
    org_id, user_id = me.json()["org_id"], me.json()["id"]

    id_a = await _seed_snapshot(org_id, user_id, device_id, "hostname sw2\n")
    id_b = await _seed_snapshot(org_id, user_id, device_id, "hostname sw2\n")

    resp = await client.get(
        "/api/snapshots/diff", headers=_auth(token), params={"from_id": id_a, "to_id": id_b}
    )
    assert resp.status_code == 200
    assert resp.json()["diff"] == []


@pytest.mark.asyncio
async def test_diff_404s_for_a_snapshot_from_another_org(client: AsyncClient, unique_email):
    token_a = await _register(client, unique_email)
    device = await client.post(
        "/api/devices",
        headers=_auth(token_a),
        json={"name": "diff-sw3", "host": "192.0.2.92", "device_type": "cisco_ios"},
    )
    device_id = device.json()["id"]
    me = await client.get("/api/auth/me", headers=_auth(token_a))
    org_id, user_id = me.json()["org_id"], me.json()["id"]
    snapshot_id = await _seed_snapshot(org_id, user_id, device_id, "hostname sw3\n")

    other_email = f"other-{unique_email}"
    token_b = await _register(client, other_email)
    resp = await client.get(
        "/api/snapshots/diff", headers=_auth(token_b), params={"from_id": snapshot_id, "to_id": snapshot_id}
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_cannot_delete_device_with_a_collection_job_in_progress(client: AsyncClient, unique_email):
    token = await _register(client, unique_email)
    device = await client.post(
        "/api/devices",
        headers=_auth(token),
        json={"name": "core-sw3", "host": "192.0.2.97", "device_type": "cisco_ios"},
    )
    device_id = device.json()["id"]
    me = await client.get("/api/auth/me", headers=_auth(token))
    org_id, user_id = me.json()["org_id"], me.json()["id"]

    async with async_session_factory() as db:
        job = CollectionJob(org_id=org_id, created_by_id=user_id, status=JobStatus.RUNNING)
        db.add(job)
        await db.flush()
        db.add(CollectionJobItem(job_id=job.id, device_id=device_id, status=JobStatus.AUTHENTICATING))
        await db.commit()

    resp = await client.delete(f"/api/devices/{device_id}", headers=_auth(token))
    assert resp.status_code == 400
    assert "in progress" in resp.json()["detail"]

    # Untouched - still there to retry/cancel/finish.
    still_there = await client.get("/api/devices", headers=_auth(token))
    assert any(d["id"] == device_id for d in still_there.json())
