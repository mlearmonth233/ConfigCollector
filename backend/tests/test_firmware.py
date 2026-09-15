import io

import pytest
from httpx import AsyncClient

from app.config import get_settings
from app.models.firmware import TransferProtocol
from app.services.firmware_push import DEFAULT_PUSH_COMMANDS, render_push_commands

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


@pytest.fixture(autouse=True)
def _non_privileged_transfer_ports(monkeypatch, tmp_path):
    # 69/21 need root - use plain high ports for tests, and an isolated
    # per-test storage directory so uploaded images don't pile up in the
    # repo's working directory across runs.
    settings = get_settings()
    monkeypatch.setattr(settings, "firmware_tftp_port", 16969, raising=False)
    monkeypatch.setattr(settings, "firmware_ftp_port", 12121, raising=False)
    monkeypatch.setattr(settings, "firmware_scp_port", 12222, raising=False)
    monkeypatch.setattr(settings, "firmware_storage_dir", str(tmp_path), raising=False)


async def test_render_push_commands_substitutes_legacy_placeholders():
    rendered = render_push_commands(
        "copy {protocol}://{host}:{port}/{filename} flash:, dir flash:",
        protocol=TransferProtocol.TFTP,
        host="10.0.0.5",
        port=6969,
        filename="ios.bin",
    )
    assert rendered == ["copy tftp://10.0.0.5:6969/ios.bin flash:", "dir flash:"]


async def test_render_push_commands_url_placeholder_omits_standard_port():
    rendered = render_push_commands(
        DEFAULT_PUSH_COMMANDS["cisco_ios"], protocol=TransferProtocol.TFTP, host="10.0.0.5", port=69, filename="ios.bin"
    )
    assert rendered == ["copy tftp://10.0.0.5/ios.bin flash:"]


async def test_render_push_commands_rejects_unknown_placeholder():
    with pytest.raises(ValueError, match="reload_after"):
        render_push_commands(
            "copy {url} flash:, {reload_after}", protocol=TransferProtocol.TFTP, host="h", port=69, filename="f"
        )


async def test_push_defaults_endpoint(client: AsyncClient, unique_email):
    token = await _register(client, unique_email)
    resp = await client.get("/api/firmware/push-defaults", headers=_auth(token))
    assert resp.status_code == 200
    body = resp.json()
    assert body["commands_by_device_type"]["cisco_ios"] == "copy {url} flash:"
    assert "{url}" in body["placeholders"]
    # Only a *copy* - none of the defaults may install or reload anything.
    for template in body["commands_by_device_type"].values():
        assert template.startswith("copy ")
        for forbidden in ("install", "reload", "boot system", "activate"):
            assert forbidden not in template


async def test_network_interfaces_endpoint(client: AsyncClient, unique_email):
    token = await _register(client, unique_email)
    resp = await client.get("/api/firmware/network-interfaces", headers=_auth(token))
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


async def test_upload_list_delete_firmware_image(client: AsyncClient, unique_email):
    token = await _register(client, unique_email)

    upload = await client.post(
        "/api/firmware/images",
        headers=_auth(token),
        params={"label": "17.9.5"},
        files={"file": ("ios.bin", io.BytesIO(b"fake-firmware-bytes"), "application/octet-stream")},
    )
    assert upload.status_code == 201, upload.text
    body = upload.json()
    assert body["original_filename"] == "ios.bin"
    assert body["label"] == "17.9.5"
    assert body["size_bytes"] == len(b"fake-firmware-bytes")
    image_id = body["id"]

    listing = await client.get("/api/firmware/images", headers=_auth(token))
    assert listing.status_code == 200
    assert [i["id"] for i in listing.json()] == [image_id]

    deleted = await client.delete(f"/api/firmware/images/{image_id}", headers=_auth(token))
    assert deleted.status_code == 204

    listing_after = await client.get("/api/firmware/images", headers=_auth(token))
    assert listing_after.json() == []


async def test_create_firmware_job_requires_command_for_types_with_no_default(client: AsyncClient, unique_email):
    token = await _register(client, unique_email)
    await client.post(
        "/api/credentials", headers=_auth(token), json={"name": "lab", "username": "apc", "password": "apc"}
    )
    # apc_pdu deliberately has no built-in copy command (there's no
    # sensible "push a firmware file" CLI to default to), so leaving it
    # out of commands_by_device_type must be a clean 400 naming the type.
    device = await client.post(
        "/api/devices", headers=_auth(token), json={"name": "pdu1", "host": "192.0.2.1", "device_type": "apc_pdu"}
    )
    device_id = device.json()["id"]

    upload = await client.post(
        "/api/firmware/images",
        headers=_auth(token),
        files={"file": ("apc.bin", io.BytesIO(b"bytes"), "application/octet-stream")},
    )
    image_id = upload.json()["id"]

    resp = await client.post(
        "/api/firmware/jobs",
        headers=_auth(token),
        json={
            "firmware_image_id": image_id,
            "device_ids": [device_id],
            "protocol": "tftp",
            "server_host": "127.0.0.1",
            "commands_by_device_type": {},
        },
    )
    assert resp.status_code == 400, resp.text
    assert "apc_pdu" in resp.json()["detail"]
    assert "no built-in default" in resp.json()["detail"]


async def test_create_firmware_job_rejects_bad_placeholder_up_front(client: AsyncClient, unique_email):
    token = await _register(client, unique_email)
    await client.post(
        "/api/credentials", headers=_auth(token), json={"name": "lab", "username": "admin", "password": "cisco123"}
    )
    device = await client.post(
        "/api/devices", headers=_auth(token), json={"name": "sw1", "host": "192.0.2.1", "device_type": "cisco_ios"}
    )
    upload = await client.post(
        "/api/firmware/images",
        headers=_auth(token),
        files={"file": ("ios.bin", io.BytesIO(b"bytes"), "application/octet-stream")},
    )

    resp = await client.post(
        "/api/firmware/jobs",
        headers=_auth(token),
        json={
            "firmware_image_id": upload.json()["id"],
            "device_ids": [device.json()["id"]],
            "protocol": "tftp",
            "server_host": "127.0.0.1",
            "commands_by_device_type": {"cisco_ios": "copy {ur} flash:"},
        },
    )
    assert resp.status_code == 400, resp.text
    assert "{ur}" in resp.json()["detail"]


async def test_create_firmware_job_rejects_unknown_image(client: AsyncClient, unique_email):
    token = await _register(client, unique_email)
    device = await client.post(
        "/api/devices", headers=_auth(token), json={"name": "sw1", "host": "192.0.2.1", "device_type": "cisco_ios"}
    )
    device_id = device.json()["id"]

    resp = await client.post(
        "/api/firmware/jobs",
        headers=_auth(token),
        json={
            "firmware_image_id": "00000000-0000-0000-0000-000000000000",
            "device_ids": [device_id],
            "protocol": "tftp",
            "server_host": "127.0.0.1",
            "commands_by_device_type": {"cisco_ios": "show version"},
        },
    )
    assert resp.status_code == 400
    assert "not found" in resp.json()["detail"].lower()


async def test_firmware_job_fails_cleanly_for_unreachable_device(client: AsyncClient, unique_email):
    # No commands_by_device_type at all: cisco_ios takes the built-in
    # "copy {url} flash:" default, so job creation succeeds and the only
    # failure is the (deliberately unreachable) device itself.
    token = await _register(client, unique_email)
    await client.post(
        "/api/credentials", headers=_auth(token), json={"name": "lab", "username": "admin", "password": "cisco123"}
    )
    device = await client.post(
        "/api/devices",
        headers=_auth(token),
        # TEST-NET-1 (RFC 5737): guaranteed unroutable, so this fails
        # deterministically without needing a real device - same trick
        # test_api.py's collection-job test uses.
        json={"name": "sw1", "host": "192.0.2.1", "device_type": "cisco_ios"},
    )
    device_id = device.json()["id"]

    upload = await client.post(
        "/api/firmware/images",
        headers=_auth(token),
        files={"file": ("ios.bin", io.BytesIO(b"bytes"), "application/octet-stream")},
    )
    image_id = upload.json()["id"]

    resp = await client.post(
        "/api/firmware/jobs",
        headers=_auth(token),
        json={
            "firmware_image_id": image_id,
            "device_ids": [device_id],
            "protocol": "tftp",
            "server_host": "127.0.0.1",
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "failed"
    assert len(body["items"]) == 1
    assert body["items"][0]["status"] == "failed"
    assert body["items"][0]["error_message"]
    # The transcript still shows the connection attempt that failed.
    assert "Connecting to 192.0.2.1:22" in body["items"][0]["live_output"]
