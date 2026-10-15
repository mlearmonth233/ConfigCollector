"""python -m app.create_admin: a username admin can be created, log in,
and have its password reset."""

import pytest
from httpx import AsyncClient

from app.create_admin import create_admin, main

pytestmark = pytest.mark.asyncio


async def test_create_and_reset_admin(client: AsyncClient):
    assert "Created admin" in await create_admin("cli-admin", "first-Passw0rd", "CLI Org")
    resp = await client.post("/api/auth/login", json={"email": "cli-admin", "password": "first-Passw0rd"})
    assert resp.status_code == 200, resp.text
    me = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {resp.json()['access_token']}"})
    assert me.json()["role"] == "admin" and me.json()["org_name"] == "CLI Org"

    assert "Reset the password" in await create_admin("cli-admin", "second-Passw0rd", "ignored")
    assert (await client.post("/api/auth/login", json={"email": "cli-admin", "password": "first-Passw0rd"})).status_code == 401
    assert (await client.post("/api/auth/login", json={"email": "cli-admin", "password": "second-Passw0rd"})).status_code == 200


async def test_short_password_rejected():
    with pytest.raises(SystemExit):
        main(["someone", "short"])
