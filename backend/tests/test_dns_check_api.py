import pytest
from httpx import AsyncClient

from app.api import dns_check as dns_check_module
from app.services.dns_check import DnsCheckResult

pytestmark = pytest.mark.asyncio


async def _register(client: AsyncClient, email: str, org_name: str = "DnsCheckOrg") -> str:
    resp = await client.post(
        "/api/auth/register",
        json={"org_name": org_name, "email": email, "password": "password123"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["access_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def test_dns_check_endpoint_returns_results_per_target(client: AsyncClient, unique_email, monkeypatch):
    token = await _register(client, unique_email)

    async def fake_run_dns_checks(targets: list[str]) -> list[DnsCheckResult]:
        return [
            DnsCheckResult(
                target=t,
                ping_ok=(t == "core-sw1"),
                forward_ok=True,
                forward_ips=["10.0.0.5"],
                reverse_ok=True,
                reverse_hostname="core-sw1.example.com",
            )
            for t in targets
        ]

    monkeypatch.setattr(dns_check_module, "run_dns_checks", fake_run_dns_checks)

    resp = await client.post(
        "/api/dns-check", headers=_auth(token), json={"targets": ["core-sw1", "10.0.0.9"]}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body) == 2
    by_target = {r["target"]: r for r in body}
    assert by_target["core-sw1"]["ping_ok"] is True
    assert by_target["10.0.0.9"]["ping_ok"] is False
    assert by_target["core-sw1"]["reverse_hostname"] == "core-sw1.example.com"


async def test_dns_check_dedupes_and_trims_targets(client: AsyncClient, unique_email, monkeypatch):
    token = await _register(client, unique_email)
    seen_targets = None

    async def fake_run_dns_checks(targets: list[str]) -> list[DnsCheckResult]:
        nonlocal seen_targets
        seen_targets = targets
        return [
            DnsCheckResult(target=t, ping_ok=True, forward_ok=False, forward_ips=[], reverse_ok=False, reverse_hostname=None)
            for t in targets
        ]

    monkeypatch.setattr(dns_check_module, "run_dns_checks", fake_run_dns_checks)

    resp = await client.post(
        "/api/dns-check",
        headers=_auth(token),
        json={"targets": [" core-sw1 ", "core-sw1", "", "  ", "core-sw2"]},
    )
    assert resp.status_code == 200, resp.text
    assert seen_targets == ["core-sw1", "core-sw2"]


async def test_dns_check_rejects_empty_target_list(client: AsyncClient, unique_email):
    token = await _register(client, unique_email)
    resp = await client.post("/api/dns-check", headers=_auth(token), json={"targets": ["", "   "]})
    assert resp.status_code == 400
    assert "at least one" in resp.json()["detail"]


async def test_dns_check_rejects_too_many_targets(client: AsyncClient, unique_email):
    token = await _register(client, unique_email)
    resp = await client.post(
        "/api/dns-check", headers=_auth(token), json={"targets": [f"host{i}" for i in range(101)]}
    )
    assert resp.status_code == 400
    assert "Too many targets" in resp.json()["detail"]


async def test_dns_check_requires_login(client: AsyncClient):
    resp = await client.post("/api/dns-check", json={"targets": ["core-sw1"]})
    assert resp.status_code == 401
