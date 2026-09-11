import io
import re
import zipfile
from datetime import datetime, timezone

import pytest
from httpx import AsyncClient

from app import tasks as tasks_module
from app.database import async_session_factory
from app.models.job import CollectionJob, CollectionJobItem, JobStatus
from app.models.snapshot import ConfigSnapshot
from app.services.collector import AuthenticationError

pytestmark = pytest.mark.asyncio

_ZIP_FILENAME_PATTERN = re.compile(r'filename="(\d{8})\.zip"')


async def _register(client: AsyncClient, email: str, org_name: str = "DownloadOrg") -> str:
    resp = await client.post(
        "/api/auth/register",
        json={"org_name": org_name, "email": email, "password": "password123"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["access_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _seed_job_with_snapshots(
    token: str, client: AsyncClient, device_names: list[str], device_type: str = "cisco_ios"
) -> str:
    """Creates one device per name (all of the same device_type, unless a
    per-device list is passed to _seed_job_with_mixed_snapshots below) and a
    completed job item + snapshot for each, directly via the DB - no real
    device is reachable in tests, and this feature's behavior doesn't
    depend on how the snapshots were collected."""
    return await _seed_job_with_mixed_snapshots(
        token, client, [(name, device_type) for name in device_names]
    )


async def _seed_job_with_mixed_snapshots(
    token: str, client: AsyncClient, devices: list[tuple[str, str]]
) -> str:
    me = await client.get("/api/auth/me", headers=_auth(token))
    org_id = me.json()["org_id"]
    user_id = me.json()["id"]

    device_ids = []
    for name, device_type in devices:
        device = await client.post(
            "/api/devices",
            headers=_auth(token),
            json={"name": name, "host": "192.0.2.50", "device_type": device_type},
        )
        assert device.status_code == 201, device.text
        device_ids.append(device.json()["id"])

    async with async_session_factory() as db:
        job = CollectionJob(
            org_id=org_id,
            created_by_id=user_id,
            status=JobStatus.COMPLETED,
            finished_at=datetime.now(timezone.utc),
        )
        db.add(job)
        await db.flush()
        for device_id, (name, _device_type) in zip(device_ids, devices):
            item = CollectionJobItem(job_id=job.id, device_id=device_id, status=JobStatus.COMPLETED)
            db.add(item)
            await db.flush()
            db.add(ConfigSnapshot(device_id=device_id, job_item_id=item.id, content=f"hostname {name}\n"))
        await db.commit()
        job_id = str(job.id)
    return job_id


async def test_download_job_configs_zip_contains_all_snapshots(client: AsyncClient, unique_email):
    token = await _register(client, unique_email)
    job_id = await _seed_job_with_snapshots(token, client, ["core-sw1", "core-sw2"])

    resp = await client.get(f"/api/jobs/{job_id}/download", headers=_auth(token))
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"] == "application/zip"
    # Default zip filename is the collection date (yyyymmdd), not the job id.
    assert _ZIP_FILENAME_PATTERN.search(resp.headers["content-disposition"])

    zf = zipfile.ZipFile(io.BytesIO(resp.content))
    # Non-PDU devices (switches, WLCs, firewalls) land in "Switches/".
    assert sorted(zf.namelist()) == ["Switches/core-sw1.txt", "Switches/core-sw2.txt"]
    assert zf.read("Switches/core-sw1.txt").decode() == "hostname core-sw1\n"
    assert zf.read("Switches/core-sw2.txt").decode() == "hostname core-sw2\n"


async def test_download_job_configs_splits_pdus_and_switches_into_folders(client: AsyncClient, unique_email):
    token = await _register(client, unique_email)
    job_id = await _seed_job_with_mixed_snapshots(
        token,
        client,
        [("core-sw1", "cisco_ios"), ("pdu1", "apc_pdu"), ("wlc1", "cisco_wlc_9800")],
    )

    resp = await client.get(f"/api/jobs/{job_id}/download", headers=_auth(token))
    assert resp.status_code == 200, resp.text
    zf = zipfile.ZipFile(io.BytesIO(resp.content))
    assert sorted(zf.namelist()) == [
        "PDUs/pdu1.txt",
        "Switches/core-sw1.txt",
        "Switches/wlc1.txt",
    ]


async def test_download_job_configs_respects_ext_and_timestamp_params(client: AsyncClient, unique_email):
    token = await _register(client, unique_email)
    job_id = await _seed_job_with_snapshots(token, client, ["core-sw1"])

    resp = await client.get(
        f"/api/jobs/{job_id}/download",
        headers=_auth(token),
        params={"ext": "log", "include_timestamp": "true"},
    )
    assert resp.status_code == 200
    zf = zipfile.ZipFile(io.BytesIO(resp.content))
    names = zf.namelist()
    assert len(names) == 1
    assert names[0].startswith("Switches/core-sw1_")
    assert names[0].endswith(".log")


async def test_download_job_configs_dedupes_colliding_filenames(client: AsyncClient, unique_email):
    token = await _register(client, unique_email)
    # Two devices sharing the same name would otherwise both map to
    # "Switches/sw1.txt" and silently clobber one another inside the zip.
    job_id = await _seed_job_with_snapshots(token, client, ["sw1", "sw1"])

    resp = await client.get(f"/api/jobs/{job_id}/download", headers=_auth(token))
    assert resp.status_code == 200
    zf = zipfile.ZipFile(io.BytesIO(resp.content))
    assert sorted(zf.namelist()) == ["Switches/sw1.txt", "Switches/sw1_1.txt"]


async def test_download_job_configs_400_when_nothing_collected_yet(
    client: AsyncClient, unique_email, monkeypatch
):
    token = await _register(client, unique_email)
    await client.post(
        "/api/credentials", headers=_auth(token), json={"name": "lab", "username": "admin", "password": "cisco123"}
    )
    device = await client.post(
        "/api/devices",
        headers=_auth(token),
        json={"name": "sw1", "host": "192.0.2.1", "device_type": "cisco_ios"},
    )
    device_id = device.json()["id"]

    # Fails instantly with no real network call - this test only cares that
    # nothing was collected, not why.
    def _fake_attempt(device, credential, otp, commands_override, on_authenticated, on_output, should_cancel):
        raise AuthenticationError(f"simulated unreachable device {device.host}")

    monkeypatch.setattr(tasks_module, "_attempt_collection", _fake_attempt)

    job = await client.post("/api/jobs", headers=_auth(token), json={"device_ids": [device_id]})
    job_id = job.json()["id"]

    resp = await client.get(f"/api/jobs/{job_id}/download", headers=_auth(token))
    assert resp.status_code == 400
    assert "No collected configs" in resp.json()["detail"]


async def test_download_job_configs_404_for_other_org(client: AsyncClient, unique_email):
    token_a = await _register(client, unique_email)
    job_id = await _seed_job_with_snapshots(token_a, client, ["sw1"])

    other_email = f"other-{unique_email}"
    token_b = await _register(client, other_email, org_name="OtherDownloadOrg")

    resp = await client.get(f"/api/jobs/{job_id}/download", headers=_auth(token_b))
    assert resp.status_code == 404
