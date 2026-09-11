import pytest
from httpx import AsyncClient

from app.models.device import NetworkZone
from app.services.hostname_detection import (
    DEVICE_ROLES,
    detect,
    detect_device_role,
    detect_network_zone,
    device_type_for_role,
)


def test_detect_device_role_matches_each_code():
    assert detect_device_role("GBGYSP01SWA001") == "access_switch"
    assert detect_device_role("GBGYSP01SWS001") == "server_switch"
    assert detect_device_role("GBGYSP01SWC001") == "core_switch"
    assert detect_device_role("GBGYSP01SWD001") == "distribution_switch"
    assert detect_device_role("GBGYSP01WLC001") == "wlc"
    assert detect_device_role("GBGYSP01PDU001") == "pdu"
    assert detect_device_role("GBGYSP01CON001") == "console_server"
    assert detect_device_role("GBGYSP01FWL001") == "firewall"
    assert detect_device_role("GBGYSP01RTR001") == "router"


def test_detect_device_role_case_insensitive():
    assert detect_device_role("gbgysp01swa001") == "access_switch"


def test_detect_device_role_none_when_no_code_present():
    assert detect_device_role("random-hostname-1") is None


def test_detect_network_zone():
    assert detect_network_zone("GBGYSP01SWA001") == NetworkZone.IT
    assert detect_network_zone("GBGYO01SWA001") == NetworkZone.OT
    assert detect_network_zone("no-zone-marker-here") is None


def test_device_type_for_role_omits_wlc_and_console_server():
    assert device_type_for_role("access_switch") == "cisco_ios"
    assert device_type_for_role("pdu") == "apc_pdu"
    assert device_type_for_role("firewall") == "fortinet"
    assert device_type_for_role("router") == "versa"
    # Deliberately ambiguous - hostname alone can't tell AireOS from
    # Catalyst 9800, so this always requires an explicit choice.
    assert device_type_for_role("wlc") is None
    # No console-server device type is in the registry at all anymore -
    # nothing to suggest, so this also requires an explicit choice.
    assert device_type_for_role("console_server") is None
    assert device_type_for_role(None) is None


def test_detect_combines_role_zone_and_type():
    result = detect("GBGYSP01SWA001")
    assert result.device_role == "access_switch"
    assert result.device_role_label == DEVICE_ROLES["access_switch"]
    assert result.network_zone == NetworkZone.IT
    assert result.suggested_device_type == "cisco_ios"


def test_detect_firewall_from_fwl_code():
    result = detect("GBGYSP01FWL001")
    assert result.device_role == "firewall"
    assert result.device_role_label == DEVICE_ROLES["firewall"]
    assert result.suggested_device_type == "fortinet"


async def _register(client: AsyncClient, email: str, org_name: str = "DetectOrg") -> str:
    resp = await client.post(
        "/api/auth/register",
        json={"org_name": org_name, "email": email, "password": "password123"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["access_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_detect_endpoint(client: AsyncClient, unique_email):
    token = await _register(client, unique_email)
    resp = await client.get("/api/devices/detect", headers=_auth(token), params={"name": "GBGYSP01SWA001"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["device_role"] == "access_switch"
    assert body["network_zone"] == "it"
    assert body["suggested_device_type"] == "cisco_ios"


@pytest.mark.asyncio
async def test_device_roles_endpoint(client: AsyncClient, unique_email):
    token = await _register(client, unique_email)
    resp = await client.get("/api/device-roles", headers=_auth(token))
    assert resp.status_code == 200
    keys = {r["key"] for r in resp.json()}
    assert keys == set(DEVICE_ROLES)


@pytest.mark.asyncio
async def test_create_device_auto_fills_type_role_zone_from_name(client: AsyncClient, unique_email):
    token = await _register(client, unique_email)
    resp = await client.post(
        "/api/devices",
        headers=_auth(token),
        json={"name": "GBGYSP01SWA001", "host": "10.0.0.1"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["device_type"] == "cisco_ios"
    assert body["device_role"] == "access_switch"
    assert body["network_zone"] == "it"


@pytest.mark.asyncio
async def test_create_device_auto_fills_router_type_from_name(client: AsyncClient, unique_email):
    token = await _register(client, unique_email)
    resp = await client.post(
        "/api/devices",
        headers=_auth(token),
        json={"name": "GBGYSP01RTR001", "host": "10.0.0.1"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["device_type"] == "versa"
    assert body["device_role"] == "router"


@pytest.mark.asyncio
async def test_create_device_explicit_values_win_over_detection(client: AsyncClient, unique_email):
    token = await _register(client, unique_email)
    resp = await client.post(
        "/api/devices",
        headers=_auth(token),
        json={
            "name": "GBGYSP01SWA001",
            "host": "10.0.0.1",
            "device_type": "cisco_nxos",
            "device_role": "core_switch",
            "network_zone": "ot",
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["device_type"] == "cisco_nxos"
    assert body["device_role"] == "core_switch"
    assert body["network_zone"] == "ot"


@pytest.mark.asyncio
async def test_create_device_wlc_without_explicit_type_requires_a_choice(client: AsyncClient, unique_email):
    token = await _register(client, unique_email)
    resp = await client.post(
        "/api/devices",
        headers=_auth(token),
        json={"name": "GBGYSP01WLC001", "host": "10.0.0.1"},
    )
    assert resp.status_code == 400
    assert "AireOS" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_create_device_no_recognizable_name_requires_explicit_type(client: AsyncClient, unique_email):
    token = await _register(client, unique_email)
    resp = await client.post(
        "/api/devices",
        headers=_auth(token),
        json={"name": "totally-generic-name", "host": "10.0.0.1"},
    )
    assert resp.status_code == 400
    assert "could not be determined" in resp.json()["detail"]


