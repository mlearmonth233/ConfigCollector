import pytest

from app.services import dns_check

pytestmark = pytest.mark.asyncio


async def test_is_ip_address():
    assert dns_check._is_ip_address("10.0.0.1") is True
    assert dns_check._is_ip_address("::1") is True
    assert dns_check._is_ip_address("core-sw1") is False
    assert dns_check._is_ip_address("") is False


async def test_forward_lookup_succeeds_for_localhost():
    ok, ips = await dns_check._forward_lookup("localhost")
    assert ok is True
    assert "127.0.0.1" in ips or "::1" in ips


async def test_forward_lookup_fails_for_bogus_name():
    ok, ips = await dns_check._forward_lookup("this-host-does-not-exist.invalid")
    assert ok is False
    assert ips == []


async def test_no_forward_lookup_reports_not_applicable():
    ok, ips = await dns_check._no_forward_lookup()
    assert ok is False
    assert ips == []


async def test_run_dns_check_for_a_hostname_combines_ping_forward_reverse(monkeypatch):
    async def fake_ping(host: str) -> bool:
        assert host == "core-sw1"
        return True

    async def fake_forward(hostname: str):
        assert hostname == "core-sw1"
        return True, ["10.0.0.5"]

    async def fake_reverse(ip: str):
        assert ip == "10.0.0.5"
        return True, "core-sw1.example.com"

    monkeypatch.setattr(dns_check, "_ping", fake_ping)
    monkeypatch.setattr(dns_check, "_forward_lookup", fake_forward)
    monkeypatch.setattr(dns_check, "_reverse_lookup", fake_reverse)

    result = await dns_check.run_dns_check("core-sw1")
    assert result.target == "core-sw1"
    assert result.ping_ok is True
    assert result.forward_ok is True
    assert result.forward_ips == ["10.0.0.5"]
    assert result.reverse_ok is True
    assert result.reverse_hostname == "core-sw1.example.com"


async def test_run_dns_check_for_an_ip_skips_forward_lookup_but_still_reverses(monkeypatch):
    async def fake_ping(host: str) -> bool:
        assert host == "10.0.0.5"
        return True

    async def fake_reverse(ip: str):
        assert ip == "10.0.0.5"
        return True, "core-sw1.example.com"

    monkeypatch.setattr(dns_check, "_ping", fake_ping)
    monkeypatch.setattr(dns_check, "_reverse_lookup", fake_reverse)

    result = await dns_check.run_dns_check("10.0.0.5")
    assert result.forward_ok is False
    assert result.forward_ips == []
    assert result.reverse_ok is True
    assert result.reverse_hostname == "core-sw1.example.com"


async def test_run_dns_check_skips_reverse_lookup_when_forward_lookup_fails(monkeypatch):
    reverse_called = False

    async def fake_ping(host: str) -> bool:
        return False

    async def fake_forward(hostname: str):
        return False, []

    async def fake_reverse(ip: str):
        nonlocal reverse_called
        reverse_called = True
        return True, "should-not-be-called"

    monkeypatch.setattr(dns_check, "_ping", fake_ping)
    monkeypatch.setattr(dns_check, "_forward_lookup", fake_forward)
    monkeypatch.setattr(dns_check, "_reverse_lookup", fake_reverse)

    result = await dns_check.run_dns_check("no-such-host.invalid")
    assert result.reverse_ok is False
    assert result.reverse_hostname is None
    assert reverse_called is False


async def test_run_dns_checks_runs_every_target(monkeypatch):
    async def fake_ping(host: str) -> bool:
        return host == "up"

    async def fake_forward(hostname: str):
        return False, []

    monkeypatch.setattr(dns_check, "_ping", fake_ping)
    monkeypatch.setattr(dns_check, "_forward_lookup", fake_forward)

    results = await dns_check.run_dns_checks(["up", "10.0.0.9"])
    by_target = {r.target: r for r in results}
    assert by_target["up"].ping_ok is True
    assert by_target["10.0.0.9"].ping_ok is False
