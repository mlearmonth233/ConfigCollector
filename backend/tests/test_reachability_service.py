import subprocess

import pytest

from app.services import reachability

pytestmark = pytest.mark.asyncio


class _FakeCompletedProcess:
    def __init__(self, returncode: int, stdout: bytes = b"64 bytes from 192.0.2.1: icmp_seq=1 ttl=64 time=0.5 ms\n"):
        self.returncode = returncode
        self.stdout = stdout


async def test_windows_ping_needs_a_real_echo_reply_not_just_exit_code_zero(monkeypatch):
    """Windows ping exits 0 for 'Destination host unreachable' (a router
    answered, the device didn't). Only a reply carrying a TTL counts."""
    monkeypatch.setattr(reachability, "_is_windows", lambda: True)
    unreachable = b"Pinging 192.0.2.9 with 32 bytes of data:\r\nReply from 10.0.0.1: Destination host unreachable.\r\n"
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _FakeCompletedProcess(0, unreachable))
    assert await reachability._ping("192.0.2.9") is False
    reply = b"Pinging 192.0.2.1 with 32 bytes of data:\r\nReply from 192.0.2.1: bytes=32 time=1ms TTL=254\r\n"
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _FakeCompletedProcess(0, reply))
    assert await reachability._ping("192.0.2.1") is True


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
