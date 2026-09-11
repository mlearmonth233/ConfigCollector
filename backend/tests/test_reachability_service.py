import asyncio

import pytest

from app.services import reachability

pytestmark = pytest.mark.asyncio


class _FakeProcess:
    def __init__(self, returncode: int):
        self._returncode = returncode

    async def wait(self) -> int:
        return self._returncode


async def test_ping_true_when_subprocess_succeeds(monkeypatch):
    async def fake_exec(*args, **kwargs):
        return _FakeProcess(0)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    assert await reachability._ping("10.0.0.1") is True


async def test_ping_false_when_subprocess_fails(monkeypatch):
    async def fake_exec(*args, **kwargs):
        return _FakeProcess(1)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    assert await reachability._ping("10.0.0.1") is False


async def test_ping_false_when_ping_binary_missing(monkeypatch):
    async def fake_exec(*args, **kwargs):
        raise FileNotFoundError()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    assert await reachability._ping("10.0.0.1") is False


async def test_resolve_dns_succeeds_for_localhost():
    dns_ok, resolved_ip = await reachability._resolve_dns("localhost")
    assert dns_ok is True
    assert resolved_ip in ("127.0.0.1", "::1")


async def test_resolve_dns_fails_for_bogus_name():
    dns_ok, resolved_ip = await reachability._resolve_dns("this-host-does-not-exist.invalid")
    assert dns_ok is False
    assert resolved_ip is None


async def test_check_reachability_combines_both_signals(monkeypatch):
    async def fake_ping(host: str) -> bool:
        return True

    async def fake_resolve(host: str):
        return True, "10.0.0.1"

    monkeypatch.setattr(reachability, "_ping", fake_ping)
    monkeypatch.setattr(reachability, "_resolve_dns", fake_resolve)

    result = await reachability.check_reachability("core-sw1")
    assert result.host == "core-sw1"
    assert result.ping_ok is True
    assert result.dns_ok is True
    assert result.resolved_ip == "10.0.0.1"
