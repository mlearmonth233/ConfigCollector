import pytest
from httpx import AsyncClient

from app.api import devices as devices_module
from app.services.reachability import ReachabilityResult

pytestmark = pytest.mark.asyncio


async def _register(client: AsyncClient, email: str, org_name: str = "ReachOrg") -> str:
    resp = await client.post(
        "/api/auth/register",
        json={"org_name": org_name, "email": email, "password": "password123"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["access_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def test_reachability_endpoint_reports_per_device_results(
    client: AsyncClient, unique_email, monkeypatch
):
    token = await _register(client, unique_email)

    up = await client.post(
        "/api/devices", headers=_auth(token), json={"name": "up-sw", "host": "10.0.0.1", "device_type": "cisco_ios"}
    )
    down = await client.post(
        "/api/devices",
        headers=_auth(token),
        json={"name": "down-sw", "host": "no-such-host.invalid", "device_type": "cisco_ios"},
    )
    assert up.status_code == 201 and down.status_code == 201

    async def fake_check_reachability(host: str) -> ReachabilityResult:
        if host == "10.0.0.1":
            return ReachabilityResult(host=host, ping_ok=True, dns_ok=True, resolved_ip="10.0.0.1")
        return ReachabilityResult(host=host, ping_ok=False, dns_ok=False, resolved_ip=None)

    monkeypatch.setattr(devices_module, "check_reachability", fake_check_reachability)

    resp = await client.get("/api/devices/reachability", headers=_auth(token))
    assert resp.status_code == 200, resp.text
    by_host = {r["host"]: r for r in resp.json()}

    assert by_host["10.0.0.1"]["ping_ok"] is True
    assert by_host["10.0.0.1"]["dns_ok"] is True
    assert by_host["10.0.0.1"]["resolved_ip"] == "10.0.0.1"

    assert by_host["no-such-host.invalid"]["ping_ok"] is False
    assert by_host["no-such-host.invalid"]["dns_ok"] is False
    assert by_host["no-such-host.invalid"]["resolved_ip"] is None


async def test_reachability_endpoint_scoped_to_caller_org(client: AsyncClient, unique_email, monkeypatch):
    token_a = await _register(client, unique_email, org_name="OrgA")
    token_b = await _register(client, f"other-{unique_email}", org_name="OrgB")

    await client.post(
        "/api/devices", headers=_auth(token_a), json={"name": "a-sw", "host": "10.0.0.1", "device_type": "cisco_ios"}
    )

    async def fake_check_reachability(host: str) -> ReachabilityResult:
        return ReachabilityResult(host=host, ping_ok=True, dns_ok=True, resolved_ip=host)

    monkeypatch.setattr(devices_module, "check_reachability", fake_check_reachability)

    resp = await client.get("/api/devices/reachability", headers=_auth(token_b))
    assert resp.status_code == 200
    assert resp.json() == []
