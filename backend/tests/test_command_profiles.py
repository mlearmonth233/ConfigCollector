import pytest
from httpx import AsyncClient

from app.services.device_types import DEVICE_TYPE_REGISTRY

pytestmark = pytest.mark.asyncio

CISCO_IOS_DEFAULT = list(DEVICE_TYPE_REGISTRY["cisco_ios"].default_commands)


async def _register(client: AsyncClient, email: str, org_name: str = "CommandsOrg") -> str:
    resp = await client.post(
        "/api/auth/register",
        json={"org_name": org_name, "email": email, "password": "password123"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["access_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def test_list_command_profiles_defaults_to_registry(client: AsyncClient, unique_email):
    token = await _register(client, unique_email)
    resp = await client.get("/api/command-profiles", headers=_auth(token))
    assert resp.status_code == 200
    by_type = {p["device_type"]: p for p in resp.json()}

    cisco_ios = by_type["cisco_ios"]
    assert cisco_ios["commands"] == CISCO_IOS_DEFAULT
    assert cisco_ios["is_custom"] is False
    assert "show running-config" in cisco_ios["suggested_commands"]
    assert "show tech-support" in cisco_ios["suggested_commands"]

    pdu = by_type["pdu_generic"]
    assert pdu["commands"] == []
    assert pdu["suggested_commands"]  # still offers suggestions even with no default


async def test_save_and_reset_command_profile(client: AsyncClient, unique_email):
    token = await _register(client, unique_email)

    put_resp = await client.put(
        "/api/command-profiles/cisco_ios",
        headers=_auth(token),
        json={"commands": ["show running-config", "show version"]},
    )
    assert put_resp.status_code == 200, put_resp.text
    body = put_resp.json()
    assert body["commands"] == ["show running-config", "show version"]
    assert body["is_custom"] is True

    # The saved override shows up on both the list endpoint and device-types.
    list_resp = await client.get("/api/command-profiles", headers=_auth(token))
    by_type = {p["device_type"]: p for p in list_resp.json()}
    assert by_type["cisco_ios"]["commands"] == ["show running-config", "show version"]

    types_resp = await client.get("/api/device-types", headers=_auth(token))
    by_key = {t["key"]: t for t in types_resp.json()}
    assert by_key["cisco_ios"]["default_commands"] == ["show running-config", "show version"]
    assert by_key["cisco_ios"]["is_custom_default"] is True

    reset_resp = await client.delete("/api/command-profiles/cisco_ios", headers=_auth(token))
    assert reset_resp.status_code == 200
    reset_body = reset_resp.json()
    assert reset_body["commands"] == CISCO_IOS_DEFAULT
    assert reset_body["is_custom"] is False


async def test_save_command_profile_for_type_with_no_default_makes_it_resolvable(client: AsyncClient, unique_email):
    token = await _register(client, unique_email)

    resp = await client.put(
        "/api/command-profiles/pdu_generic",
        headers=_auth(token),
        json={"commands": ["about", "show status"]},
    )
    assert resp.status_code == 200
    assert resp.json()["commands"] == ["about", "show status"]

    device = await client.post(
        "/api/devices",
        headers=_auth(token),
        json={"name": "pdu1", "host": "192.0.2.5", "device_type": "pdu_generic"},
    )
    assert device.status_code == 201, device.text

    # Previously this device type had no resolvable default and no
    # custom_commands, so job creation would reject it - the saved org
    # profile now supplies one.
    job = await client.post("/api/jobs", headers=_auth(token), json={"device_ids": [device.json()["id"]]})
    assert job.status_code == 201, job.text


async def test_save_command_profile_rejects_empty_commands(client: AsyncClient, unique_email):
    token = await _register(client, unique_email)
    resp = await client.put(
        "/api/command-profiles/cisco_ios", headers=_auth(token), json={"commands": ["  ", ""]}
    )
    assert resp.status_code == 422


async def test_command_profile_unknown_device_type_404s(client: AsyncClient, unique_email):
    token = await _register(client, unique_email)
    resp = await client.put(
        "/api/command-profiles/not_a_real_type", headers=_auth(token), json={"commands": ["x"]}
    )
    assert resp.status_code == 404


async def test_command_profiles_are_scoped_per_org(client: AsyncClient, unique_email):
    token_a = await _register(client, unique_email)
    await client.put(
        "/api/command-profiles/cisco_ios", headers=_auth(token_a), json={"commands": ["show version"]}
    )

    other_email = f"other-{unique_email}"
    token_b = await _register(client, other_email, org_name="OtherCommandsOrg")
    resp = await client.get("/api/command-profiles", headers=_auth(token_b))
    by_type = {p["device_type"]: p for p in resp.json()}
    assert by_type["cisco_ios"]["commands"] == CISCO_IOS_DEFAULT
    assert by_type["cisco_ios"]["is_custom"] is False


async def test_device_custom_commands_take_priority_over_org_profile(client: AsyncClient, unique_email):
    token = await _register(client, unique_email)
    await client.put(
        "/api/command-profiles/cisco_ios", headers=_auth(token), json={"commands": ["show version"]}
    )
    device = await client.post(
        "/api/devices",
        headers=_auth(token),
        json={
            "name": "sw1",
            "host": "192.0.2.6",
            "device_type": "cisco_ios",
            "custom_commands": "show clock",
        },
    )
    assert device.status_code == 201

    # This only asserts job creation succeeds (i.e. the device is considered
    # to have a resolvable command) - which credential/command actually runs
    # is exercised by the Celery task layer, not this HTTP-level test.
    job = await client.post("/api/jobs", headers=_auth(token), json={"device_ids": [device.json()["id"]]})
    assert job.status_code == 201, job.text
