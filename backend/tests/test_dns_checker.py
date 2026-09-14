import pytest
from httpx import AsyncClient

from app.services import dns_checker as dns_checker_module
from app.services.dns_checker import parse_bulk_hosts


def test_parse_bulk_hosts_splits_on_newlines_and_commas():
    raw = "sw1.example.com, 10.0.0.1\n  sw2.example.com\n\n192.0.2.5,  "
    assert parse_bulk_hosts(raw) == ["sw1.example.com", "10.0.0.1", "sw2.example.com", "192.0.2.5"]


def test_parse_bulk_hosts_dedupes_preserving_first_occurrence_order():
    assert parse_bulk_hosts("a, b, a, c, b") == ["a", "b", "c"]


def test_parse_bulk_hosts_empty_input_returns_empty_list():
    assert parse_bulk_hosts("   \n \n ") == []


async def _register(client: AsyncClient, email: str, org_name: str = "DnsCheckerOrg") -> str:
    resp = await client.post(
        "/api/auth/register",
        json={"org_name": org_name, "email": email, "password": "password123"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["access_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_ip_input_is_classified_and_reverse_looked_up(client: AsyncClient, unique_email, monkeypatch):
    token = await _register(client, unique_email)

    async def fake_ping_host(host):
        return host == "192.0.2.10"

    async def fake_resolve_reverse(ip):
        return "host.example.com" if ip == "192.0.2.10" else None

    async def fake_resolve_forward(host):
        raise AssertionError("forward lookup should not be attempted for an IP input")

    monkeypatch.setattr(dns_checker_module, "ping_host", fake_ping_host)
    monkeypatch.setattr(dns_checker_module, "resolve_reverse", fake_resolve_reverse)
    monkeypatch.setattr(dns_checker_module, "resolve_forward", fake_resolve_forward)

    resp = await client.post("/api/dns-checker", headers=_auth(token), json={"input": "192.0.2.10"})
    assert resp.status_code == 200, resp.text
    [result] = resp.json()
    assert result["input"] == "192.0.2.10"
    assert result["input_type"] == "ip"
    assert result["resolved_ip"] == "192.0.2.10"
    assert result["reverse_hostname"] == "host.example.com"
    assert result["dns_ok"] is True
    assert result["ping_ok"] is True
    assert result["error"] is None


@pytest.mark.asyncio
async def test_hostname_input_resolves_forward_then_reverse(client: AsyncClient, unique_email, monkeypatch):
    token = await _register(client, unique_email)

    async def fake_ping_host(host):
        return True

    async def fake_resolve_forward(host):
        return (True, "10.0.0.5") if host == "sw1.example.com" else (False, None)

    async def fake_resolve_reverse(ip):
        return "sw1.example.com" if ip == "10.0.0.5" else None

    monkeypatch.setattr(dns_checker_module, "ping_host", fake_ping_host)
    monkeypatch.setattr(dns_checker_module, "resolve_forward", fake_resolve_forward)
    monkeypatch.setattr(dns_checker_module, "resolve_reverse", fake_resolve_reverse)

    resp = await client.post("/api/dns-checker", headers=_auth(token), json={"input": "sw1.example.com"})
    assert resp.status_code == 200, resp.text
    [result] = resp.json()
    assert result["input_type"] == "hostname"
    assert result["resolved_ip"] == "10.0.0.5"
    assert result["reverse_hostname"] == "sw1.example.com"
    assert result["dns_ok"] is True
    assert result["error"] is None


@pytest.mark.asyncio
async def test_hostname_that_does_not_resolve_reports_error_and_skips_ping(
    client: AsyncClient, unique_email, monkeypatch
):
    token = await _register(client, unique_email)

    async def fake_ping_host(host):
        raise AssertionError("pinging a hostname that already failed DNS resolution is wasted work")

    async def fake_resolve_forward(host):
        return (False, None)

    async def fake_resolve_reverse(ip):
        raise AssertionError("reverse lookup should not run without a resolved IP")

    monkeypatch.setattr(dns_checker_module, "ping_host", fake_ping_host)
    monkeypatch.setattr(dns_checker_module, "resolve_forward", fake_resolve_forward)
    monkeypatch.setattr(dns_checker_module, "resolve_reverse", fake_resolve_reverse)

    resp = await client.post("/api/dns-checker", headers=_auth(token), json={"input": "doesnotexist.invalid"})
    assert resp.status_code == 200, resp.text
    [result] = resp.json()
    assert result["dns_ok"] is False
    assert result["resolved_ip"] is None
    assert result["reverse_hostname"] is None
    assert "resolve" in result["error"].lower()


@pytest.mark.asyncio
async def test_bulk_mixed_input_processes_every_entry(client: AsyncClient, unique_email, monkeypatch):
    token = await _register(client, unique_email)

    async def fake_ping_host(host):
        return True

    async def fake_resolve_forward(host):
        return (True, "10.0.0.9")

    async def fake_resolve_reverse(ip):
        return None

    monkeypatch.setattr(dns_checker_module, "ping_host", fake_ping_host)
    monkeypatch.setattr(dns_checker_module, "resolve_forward", fake_resolve_forward)
    monkeypatch.setattr(dns_checker_module, "resolve_reverse", fake_resolve_reverse)

    resp = await client.post(
        "/api/dns-checker",
        headers=_auth(token),
        json={"input": "sw1.example.com, 10.0.0.9\nsw2.example.com"},
    )
    assert resp.status_code == 200, resp.text
    results = resp.json()
    assert [r["input"] for r in results] == ["sw1.example.com", "10.0.0.9", "sw2.example.com"]
    assert {r["input_type"] for r in results} == {"hostname", "ip"}


@pytest.mark.asyncio
async def test_empty_input_rejected(client: AsyncClient, unique_email):
    token = await _register(client, unique_email)
    resp = await client.post("/api/dns-checker", headers=_auth(token), json={"input": "   "})
    assert resp.status_code == 400, resp.text


@pytest.mark.asyncio
async def test_too_many_entries_rejected(client: AsyncClient, unique_email, monkeypatch):
    token = await _register(client, unique_email)
    monkeypatch.setattr(dns_checker_module, "MAX_BULK_ENTRIES", 3)
    # The API module imported MAX_BULK_ENTRIES by value at import time -
    # patch it there too so the endpoint actually sees the lowered limit.
    from app.api import dns_checker as dns_checker_api_module

    monkeypatch.setattr(dns_checker_api_module, "MAX_BULK_ENTRIES", 3)

    resp = await client.post(
        "/api/dns-checker", headers=_auth(token), json={"input": "a, b, c, d, e"}
    )
    assert resp.status_code == 400, resp.text
    assert "at most 3" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_requires_authentication(client: AsyncClient):
    resp = await client.post("/api/dns-checker", json={"input": "10.0.0.1"})
    assert resp.status_code == 401
