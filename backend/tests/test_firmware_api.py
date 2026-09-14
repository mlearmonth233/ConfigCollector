import hashlib
import os

import pytest
from httpx import AsyncClient

from app.services import firmware_storage

pytestmark = pytest.mark.asyncio


async def _register(client: AsyncClient, email: str, org_name: str = "FirmwareOrg") -> str:
    resp = await client.post(
        "/api/auth/register",
        json={"org_name": org_name, "email": email, "password": "password123"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["access_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _upload(client: AsyncClient, token: str, content: bytes, filename: str = "cat9k.SPA.bin", description=None):
    data = {"description": description} if description is not None else {}
    return await client.post(
        "/api/firmware",
        headers=_auth(token),
        files={"file": (filename, content, "application/octet-stream")},
        data=data,
    )


async def test_upload_computes_size_and_md5(client: AsyncClient, unique_email):
    token = await _register(client, unique_email)
    content = os.urandom(1024 * 64 + 17)

    resp = await _upload(client, token, content, description="test image")
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["filename"] == "cat9k.SPA.bin"
    assert body["description"] == "test image"
    assert body["size_bytes"] == len(content)
    assert body["md5"] == hashlib.md5(content).hexdigest()


async def test_upload_rejects_empty_file(client: AsyncClient, unique_email):
    token = await _register(client, unique_email)
    resp = await _upload(client, token, b"")
    assert resp.status_code == 400, resp.text


async def test_list_returns_every_uploaded_image(client: AsyncClient, unique_email):
    # Ordering itself (newest first) isn't asserted here - both uploads can
    # land in the same SQLite created_at tick, so exact order between them
    # isn't guaranteed enough to test precisely.
    token = await _register(client, unique_email)
    await _upload(client, token, b"first-content", filename="first.bin")
    await _upload(client, token, b"second-content", filename="second.bin")

    resp = await client.get("/api/firmware", headers=_auth(token))
    assert resp.status_code == 200
    names = {i["filename"] for i in resp.json()}
    assert names == {"first.bin", "second.bin"}


async def test_update_description(client: AsyncClient, unique_email):
    token = await _register(client, unique_email)
    upload = await _upload(client, token, b"content", description="old")
    image_id = upload.json()["id"]

    resp = await client.patch(f"/api/firmware/{image_id}", headers=_auth(token), json={"description": "new"})
    assert resp.status_code == 200
    assert resp.json()["description"] == "new"


async def test_delete_removes_row_and_file_from_disk(client: AsyncClient, unique_email):
    token = await _register(client, unique_email)
    upload = await _upload(client, token, b"delete-me-content")
    image_id = upload.json()["id"]

    me = await client.get("/api/auth/me", headers=_auth(token))
    org_id = me.json()["org_id"]
    org_dir = os.path.join(firmware_storage.settings.firmware_storage_path, org_id)
    assert len(os.listdir(org_dir)) == 1

    resp = await client.delete(f"/api/firmware/{image_id}", headers=_auth(token))
    assert resp.status_code == 204, resp.text

    listed = await client.get("/api/firmware", headers=_auth(token))
    assert listed.json() == []
    assert len(os.listdir(org_dir)) == 0


async def test_cross_org_access_is_404(client: AsyncClient, unique_email):
    token_a = await _register(client, unique_email, org_name="OrgA")
    upload = await _upload(client, token_a, b"org-a-content")
    image_id = upload.json()["id"]

    token_b = await _register(client, f"other-{unique_email}", org_name="OrgB")
    resp = await client.get("/api/firmware", headers=_auth(token_b))
    assert resp.json() == []

    delete_resp = await client.delete(f"/api/firmware/{image_id}", headers=_auth(token_b))
    assert delete_resp.status_code == 404
