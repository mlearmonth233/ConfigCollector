import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


async def _register(client: AsyncClient, email: str, org_name: str = "SettingsOrg") -> str:
    resp = await client.post(
        "/api/auth/register",
        json={"org_name": org_name, "email": email, "password": "password123"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["access_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def test_retention_defaults_to_keep_forever(client: AsyncClient, unique_email):
    token = await _register(client, unique_email)
    resp = await client.get("/api/organization", headers=_auth(token))
    assert resp.status_code == 200, resp.text
    assert resp.json()["snapshot_retention_days"] is None


async def test_set_and_clear_retention_days(client: AsyncClient, unique_email):
    token = await _register(client, unique_email)

    set_resp = await client.patch(
        "/api/organization", headers=_auth(token), json={"snapshot_retention_days": 90}
    )
    assert set_resp.status_code == 200, set_resp.text
    assert set_resp.json()["snapshot_retention_days"] == 90

    get_resp = await client.get("/api/organization", headers=_auth(token))
    assert get_resp.json()["snapshot_retention_days"] == 90

    clear_resp = await client.patch("/api/organization", headers=_auth(token), json={"clear_retention": True})
    assert clear_resp.status_code == 200, clear_resp.text
    assert clear_resp.json()["snapshot_retention_days"] is None


async def test_retention_days_must_be_positive(client: AsyncClient, unique_email):
    token = await _register(client, unique_email)
    resp = await client.patch("/api/organization", headers=_auth(token), json={"snapshot_retention_days": 0})
    assert resp.status_code == 422


async def test_only_admin_can_update_retention(client: AsyncClient, unique_email):
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

    resp = await client.patch(
        "/api/organization", headers=_auth(member_token), json={"snapshot_retention_days": 30}
    )
    assert resp.status_code == 403
