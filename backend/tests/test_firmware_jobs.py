import pytest
from httpx import AsyncClient

from app import tasks as tasks_module
from app.services.firmware_push import AuthenticationError, PushResult

pytestmark = pytest.mark.asyncio


async def _register(client: AsyncClient, email: str, org_name: str = "FirmwareJobOrg") -> str:
    resp = await client.post(
        "/api/auth/register",
        json={"org_name": org_name, "email": email, "password": "password123"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["access_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _upload(client: AsyncClient, token: str, content: bytes = b"firmware-bytes", filename: str = "img.bin"):
    resp = await client.post(
        "/api/firmware",
        headers=_auth(token),
        files={"file": (filename, content, "application/octet-stream")},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _add_device(client: AsyncClient, token: str, name: str, host: str, device_type: str = "cisco_ios", **extra):
    resp = await client.post(
        "/api/devices", headers=_auth(token), json={"name": name, "host": host, "device_type": device_type, **extra}
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def test_successful_push_records_checksum_and_completes(client: AsyncClient, unique_email, monkeypatch):
    token = await _register(client, unique_email)
    await client.post(
        "/api/credentials", headers=_auth(token), json={"name": "lab", "username": "admin", "password": "cisco123"}
    )
    image = await _upload(client, token)
    device = await _add_device(client, token, "sw1", "10.0.5.1")

    monkeypatch.setattr(
        tasks_module,
        "push_firmware_to_device",
        lambda **kwargs: PushResult(transcript="bytes copied in 1.0 secs\n", checksum_verified=True),
    )

    job = await client.post(
        "/api/firmware-jobs",
        headers=_auth(token),
        json={"firmware_image_id": image["id"], "device_ids": [device["id"]]},
    )
    assert job.status_code == 201, job.text
    body = job.json()
    assert body["status"] == "completed"
    assert body["items"][0]["status"] == "completed"
    assert body["items"][0]["checksum_verified"] is True
    assert body["target_filename"] == image["filename"]


async def test_push_failure_records_error_message(client: AsyncClient, unique_email, monkeypatch):
    token = await _register(client, unique_email)
    await client.post(
        "/api/credentials", headers=_auth(token), json={"name": "lab", "username": "admin", "password": "cisco123"}
    )
    image = await _upload(client, token)
    device = await _add_device(client, token, "sw1", "10.0.5.2")

    def _fake_push(**kwargs):
        raise AuthenticationError(f"simulated unreachable {kwargs['host']}")

    monkeypatch.setattr(tasks_module, "push_firmware_to_device", _fake_push)

    job = await client.post(
        "/api/firmware-jobs",
        headers=_auth(token),
        json={"firmware_image_id": image["id"], "device_ids": [device["id"]]},
    )
    assert job.status_code == 201, job.text
    body = job.json()
    assert body["status"] == "failed"
    assert "simulated unreachable" in body["items"][0]["error_message"]


async def test_unsupported_device_type_rejected_before_dispatch(client: AsyncClient, unique_email):
    token = await _register(client, unique_email)
    await client.post(
        "/api/credentials", headers=_auth(token), json={"name": "lab", "username": "admin", "password": "cisco123"}
    )
    image = await _upload(client, token)
    # apc_pdu has no flash-style filesystem registered for firmware push.
    device = await _add_device(client, token, "pdu1", "10.0.5.3", device_type="apc_pdu")

    resp = await client.post(
        "/api/firmware-jobs",
        headers=_auth(token),
        json={"firmware_image_id": image["id"], "device_ids": [device["id"]]},
    )
    assert resp.status_code == 400, resp.text
    assert "pdu1" in resp.json()["detail"]


async def test_missing_credential_rejected_before_dispatch(client: AsyncClient, unique_email):
    token = await _register(client, unique_email)
    image = await _upload(client, token)
    device = await _add_device(client, token, "sw1", "10.0.5.4")  # no credential created at all

    resp = await client.post(
        "/api/firmware-jobs",
        headers=_auth(token),
        json={"firmware_image_id": image["id"], "device_ids": [device["id"]]},
    )
    assert resp.status_code == 400, resp.text
    assert "sw1" in resp.json()["detail"]


async def test_invalid_target_filename_rejected(client: AsyncClient, unique_email):
    token = await _register(client, unique_email)
    image = await _upload(client, token)
    device = await _add_device(client, token, "sw1", "10.0.5.5")

    resp = await client.post(
        "/api/firmware-jobs",
        headers=_auth(token),
        json={
            "firmware_image_id": image["id"],
            "device_ids": [device["id"]],
            "target_filename": "../etc/passwd",
        },
    )
    assert resp.status_code == 422, resp.text


async def test_fallback_credential_used_on_primary_auth_failure(client: AsyncClient, unique_email, monkeypatch):
    token = await _register(client, unique_email)
    fallback = await client.post(
        "/api/credentials",
        headers=_auth(token),
        json={"name": "local-admin", "username": "admin", "password": "localpass"},
    )
    fallback_id = fallback.json()["id"]
    primary = await client.post(
        "/api/credentials",
        headers=_auth(token),
        json={
            "name": "tacacs-primary",
            "username": "netops",
            "password": "cisco123",
            "fallback_credential_id": fallback_id,
        },
    )
    primary_id = primary.json()["id"]
    image = await _upload(client, token)
    device = await _add_device(client, token, "sw1", "10.0.5.6", credential_id=primary_id)

    def _fake_push(**kwargs):
        if kwargs["username"] == "netops":
            raise AuthenticationError("simulated primary auth failure")
        return PushResult(transcript="bytes copied in 1.0 secs\n", checksum_verified=None)

    monkeypatch.setattr(tasks_module, "push_firmware_to_device", _fake_push)

    job = await client.post(
        "/api/firmware-jobs",
        headers=_auth(token),
        json={"firmware_image_id": image["id"], "device_ids": [device["id"]], "verify_checksum": False},
    )
    assert job.status_code == 201, job.text
    item = job.json()["items"][0]
    assert item["status"] == "completed"
    assert item["used_fallback_credential"] is True


async def test_cannot_delete_image_with_active_job(client: AsyncClient, unique_email):
    token = await _register(client, unique_email)
    image = await _upload(client, token)

    from app.database import async_session_factory
    from app.models.firmware import FirmwareJob, FirmwareJobItem, FirmwareJobStatus

    me = await client.get("/api/auth/me", headers=_auth(token))
    org_id, user_id = me.json()["org_id"], me.json()["id"]

    async with async_session_factory() as db:
        job = FirmwareJob(
            org_id=org_id,
            created_by_id=user_id,
            firmware_image_id=image["id"],
            firmware_filename=image["filename"],
            firmware_md5=image["md5"],
            firmware_size_bytes=image["size_bytes"],
            firmware_storage_path="unused",
            target_filename=image["filename"],
            status=FirmwareJobStatus.RUNNING,
        )
        db.add(job)
        await db.flush()
        db.add(FirmwareJobItem(job_id=job.id, status=FirmwareJobStatus.TRANSFERRING))
        await db.commit()

    resp = await client.delete(f"/api/firmware/{image['id']}", headers=_auth(token))
    assert resp.status_code == 400, resp.text
    assert "in progress" in resp.json()["detail"]
