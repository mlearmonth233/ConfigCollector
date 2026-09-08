import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


async def _register(client: AsyncClient, email: str, org_name: str = "TestOrg") -> str:
    resp = await client.post(
        "/api/auth/register",
        json={"org_name": org_name, "email": email, "password": "password123"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["access_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def test_register_login_me(client: AsyncClient, unique_email):
    token = await _register(client, unique_email)

    login = await client.post(
        "/api/auth/login", json={"email": unique_email, "password": "password123"}
    )
    assert login.status_code == 200
    token = login.json()["access_token"]

    me = await client.get("/api/auth/me", headers=_auth(token))
    assert me.status_code == 200
    assert me.json()["email"] == unique_email
    assert me.json()["role"] == "admin"


async def test_register_duplicate_email_rejected(client: AsyncClient, unique_email):
    await _register(client, unique_email)
    resp = await client.post(
        "/api/auth/register",
        json={"org_name": "Other", "email": unique_email, "password": "password123"},
    )
    assert resp.status_code == 409


async def test_unauthenticated_request_rejected(client: AsyncClient):
    resp = await client.get("/api/devices")
    assert resp.status_code == 401


async def test_credential_and_device_crud(client: AsyncClient, unique_email):
    token = await _register(client, unique_email)

    cred = await client.post(
        "/api/credentials",
        headers=_auth(token),
        json={"name": "lab", "username": "admin", "password": "cisco123", "enable_secret": "en123"},
    )
    assert cred.status_code == 201
    cred_body = cred.json()
    assert cred_body["has_enable_secret"] is True
    assert "password" not in cred_body

    device = await client.post(
        "/api/devices",
        headers=_auth(token),
        json={"name": "sw1", "host": "192.0.2.10", "device_type": "cisco_ios", "credential_id": cred_body["id"]},
    )
    assert device.status_code == 201
    device_id = device.json()["id"]

    bad_type = await client.post(
        "/api/devices",
        headers=_auth(token),
        json={"name": "bad", "host": "192.0.2.11", "device_type": "not_a_type"},
    )
    assert bad_type.status_code == 400

    listing = await client.get("/api/devices", headers=_auth(token))
    assert listing.status_code == 200
    assert len(listing.json()) == 1

    updated = await client.patch(
        f"/api/devices/{device_id}", headers=_auth(token), json={"site": "DC1"}
    )
    assert updated.status_code == 200
    assert updated.json()["site"] == "DC1"

    deleted = await client.delete(f"/api/devices/{device_id}", headers=_auth(token))
    assert deleted.status_code == 204


async def test_device_csv_import_partial_success(client: AsyncClient, unique_email):
    token = await _register(client, unique_email)
    await client.post(
        "/api/credentials",
        headers=_auth(token),
        json={"name": "labcred", "username": "admin", "password": "cisco123"},
    )

    csv_content = (
        "name,host,port,device_type,site,credential_name,custom_commands\n"
        "core-sw1,10.0.0.1,22,cisco_ios,DC1,labcred,\n"
        "badtype,10.0.0.2,22,not_a_real_type,DC1,labcred,\n"
        "badcred,10.0.0.3,22,cisco_ios,DC1,doesnotexist,\n"
    )
    resp = await client.post(
        "/api/devices/import",
        headers=_auth(token),
        files={"file": ("devices.csv", csv_content, "text/csv")},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["created"] == 1
    assert len(body["errors"]) == 2

    listing = await client.get("/api/devices", headers=_auth(token))
    assert len(listing.json()) == 1


async def test_job_lifecycle_with_unreachable_device(client: AsyncClient, unique_email):
    token = await _register(client, unique_email)
    cred = await client.post(
        "/api/credentials",
        headers=_auth(token),
        json={"name": "lab", "username": "admin", "password": "cisco123"},
    )
    cred_id = cred.json()["id"]
    device = await client.post(
        "/api/devices",
        headers=_auth(token),
        # TEST-NET-1 (RFC 5737): guaranteed unroutable, so the collection
        # attempt deterministically fails without needing a real device.
        json={"name": "sw1", "host": "192.0.2.1", "device_type": "cisco_ios", "credential_id": cred_id},
    )
    device_id = device.json()["id"]

    job = await client.post("/api/jobs", headers=_auth(token), json={"device_ids": [device_id]})
    assert job.status_code == 201, job.text
    job_body = job.json()
    assert job_body["item_count"] == 1

    detail = await client.get(f"/api/jobs/{job_body['id']}", headers=_auth(token))
    assert detail.status_code == 200
    detail_body = detail.json()
    assert detail_body["status"] == "failed"
    assert detail_body["items"][0]["status"] == "failed"
    assert "Timed out" in detail_body["items"][0]["error_message"] or "failed" in detail_body["items"][0]["error_message"]


async def test_job_rejects_unknown_device_id(client: AsyncClient, unique_email):
    token = await _register(client, unique_email)
    resp = await client.post(
        "/api/jobs", headers=_auth(token), json={"device_ids": ["00000000-0000-0000-0000-000000000000"]}
    )
    assert resp.status_code == 400


async def test_cross_org_device_access_denied(client: AsyncClient, unique_email):
    token_a = await _register(client, unique_email)
    device = await client.post(
        "/api/devices",
        headers=_auth(token_a),
        json={"name": "sw1", "host": "192.0.2.20", "device_type": "cisco_ios"},
    )
    device_id = device.json()["id"]

    other_email = f"other-{unique_email}"
    token_b = await _register(client, other_email, org_name="OtherOrg")

    resp = await client.patch(f"/api/devices/{device_id}", headers=_auth(token_b), json={"site": "x"})
    assert resp.status_code == 404
