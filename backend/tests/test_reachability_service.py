import subprocess

import pytest

from app.services import reachability

pytestmark = pytest.mark.asyncio


class _FakeCompletedProcess:
    def __init__(self, returncode: int):
        self.returncode = returncode


async def test_ping_true_when_subprocess_succeeds(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _FakeCompletedProcess(0))
    assert await reachability._ping("10.0.0.1") is True


async def test_ping_false_when_subprocess_fails(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _FakeCompletedProcess(1))
    assert await reachability._ping("10.0.0.1") is False


async def test_ping_false_when_ping_binary_missing(monkeypatch):
    def raise_missing(*a, **k):
        raise FileNotFoundError()

    monkeypatch.setattr(subprocess, "run", raise_missing)
    assert await reachability._ping("10.0.0.1") is False


async def test_ping_false_when_event_loop_does_not_support_subprocess_transport(monkeypatch):
    """The bug this module was actually hit by: on Windows, uvicorn's
    --reload supervisor forces a SelectorEventLoop, and asyncio's own
    subprocess-transport-based APIs raise NotImplementedError under it. This
    module sidesteps that entirely by running ping via a plain blocking
    subprocess.run() in a worker thread instead - this test simulates what
    would happen if that were ever regressed back to a loop-transport call
    that fails this way, confirming it still degrades to False rather than
    raising out of _ping()."""

    def raise_not_implemented(*a, **k):
        raise NotImplementedError()

    monkeypatch.setattr(subprocess, "run", raise_not_implemented)
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
