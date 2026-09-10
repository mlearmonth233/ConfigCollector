import os
import shutil
import socket
import subprocess
import time

import paramiko
import pytest

from app.services import collector as collector_module
from app.services.collector import AuthenticationError, collect_device_config
from app.services.device_types import DEVICE_TYPE_REGISTRY, parse_command_list, resolve_commands

_SSHD = shutil.which("sshd") or "/usr/sbin/sshd"
_HOST_KEY = "/etc/ssh/ssh_host_rsa_key"
_REQUIRES_SSHD = pytest.mark.skipif(
    not (os.path.exists(_SSHD) and os.path.exists(_HOST_KEY)),
    reason="requires a system openssh-server install (sshd + host keys) to test against",
)


def test_legacy_kex_algorithm_enabled_for_old_devices():
    # Some still-deployed switches/WLCs only speak diffie-hellman-group14-sha1,
    # which recent Paramiko releases dropped from their default offer -
    # collector.py re-enables it as a last-resort fallback on import.
    assert "diffie-hellman-group14-sha1" in paramiko.Transport._preferred_kex
    assert "diffie-hellman-group14-sha1" in paramiko.Transport._kex_info


def test_legacy_rsa_host_key_algorithm_enabled_for_old_devices():
    # Some still-deployed switches only ever learned to sign their (RSA)
    # host key the old way ("ssh-rsa", SHA-1), predating the newer
    # rsa-sha2-256/512 signature scheme Paramiko now prefers exclusively -
    # collector.py re-enables the legacy name, its class mapping, and the
    # SHA-1 hash needed to actually verify such a signature.
    assert "ssh-rsa" in paramiko.Transport._preferred_keys
    assert "ssh-rsa" in paramiko.Transport._key_info
    assert "ssh-rsa" in paramiko.rsakey.RSAKey.HASHES


def _start_test_sshd(tmp_path, port: int, extra_config: str) -> subprocess.Popen:
    """Starts a real, restricted sshd on localhost for a collector.py
    end-to-end test - regression tests for these SSH-compatibility fixes
    spin up an actual server rather than just checking that a name got
    registered, since re-adding an algorithm's *name* without a working
    implementation behind it would look fixed but still fail the same way
    a real device does."""
    pid_file = tmp_path / "sshd.pid"
    config = tmp_path / "sshd_config"
    config.write_text(
        f"Port {port}\n"
        "ListenAddress 127.0.0.1\n"
        f"HostKey {_HOST_KEY}\n"
        "PasswordAuthentication yes\n"
        "UsePAM no\n"
        f"PidFile {pid_file}\n" + extra_config
    )
    os.makedirs("/run/sshd", exist_ok=True)

    proc = subprocess.Popen([_SSHD, "-f", str(config), "-D"], stderr=subprocess.PIPE)
    for _ in range(20):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.5).close()
            return proc
        except OSError:
            time.sleep(0.25)
    proc.terminate()
    proc.wait(timeout=5)
    pytest.fail("test sshd never started listening")


@_REQUIRES_SSHD
def test_can_negotiate_with_a_server_offering_only_legacy_kex(tmp_path):
    # Regression test for the real failure this was built to fix: a device
    # whose SSH implementation only offers diffie-hellman-group14-sha1 used
    # to fail here with "no acceptable kex algorithm" (surfacing to users as
    # a misleading NetmikoTimeoutException).
    proc = _start_test_sshd(tmp_path, 2222, "KexAlgorithms diffie-hellman-group14-sha1\n")
    try:
        # Wrong credentials on purpose - the point is proving the SSH
        # session/key-exchange itself succeeds. A credentials rejection
        # (NetmikoAuthenticationException) means it got past kex; the bug
        # this guards against fails earlier, as NetmikoTimeoutException.
        with pytest.raises(AuthenticationError) as excinfo:
            collect_device_config(
                host="127.0.0.1",
                port=2222,
                device_type="linux",
                username="root",
                password="definitely-wrong-password",
                secret=None,
                custom_commands=None,
                auth_timeout=5,
            )
        assert "rejected" in str(excinfo.value)
    finally:
        proc.terminate()
        proc.wait(timeout=5)


@_REQUIRES_SSHD
def test_can_negotiate_with_a_server_offering_only_legacy_rsa_host_key(tmp_path):
    # Regression test for the follow-on failure once kex itself succeeds: a
    # device offering only the legacy "ssh-rsa" host key algorithm used to
    # fail with "no acceptable host key".
    proc = _start_test_sshd(
        tmp_path, 2224, "HostKeyAlgorithms ssh-rsa\nPubkeyAcceptedAlgorithms +ssh-rsa\n"
    )
    try:
        with pytest.raises(AuthenticationError) as excinfo:
            collect_device_config(
                host="127.0.0.1",
                port=2224,
                device_type="linux",
                username="root",
                password="definitely-wrong-password",
                secret=None,
                custom_commands=None,
                auth_timeout=5,
            )
        assert "rejected" in str(excinfo.value)
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def test_parse_command_list_strips_and_drops_blanks():
    assert parse_command_list("show run, , show version ,") == ["show run", "show version"]


def test_resolve_commands_uses_registry_default():
    assert resolve_commands("cisco_ios", None) == list(DEVICE_TYPE_REGISTRY["cisco_ios"].default_commands)


def test_resolve_commands_requires_custom_for_types_with_no_default():
    with pytest.raises(ValueError, match="no default command"):
        resolve_commands("pdu_generic", None)


def test_unreachable_device_raises_authentication_error_not_command_error():
    # TEST-NET-1 (RFC 5737) is guaranteed unroutable, so this deterministically
    # fails during connect/login - it should be classified as an
    # AuthenticationError (pre-command failure), never a CommandExecutionError.
    with pytest.raises(AuthenticationError):
        collect_device_config(
            host="192.0.2.1",
            port=22,
            device_type="cisco_ios",
            username="admin",
            password="cisco123",
            secret=None,
            custom_commands=None,
            auth_timeout=2,
        )


def test_unreachable_device_error_points_at_network_not_credentials():
    # NetmikoTimeoutException (no response at all) is almost always a
    # reachability problem, not a credentials one - the error message
    # shouldn't send users chasing TACACS+/MFA settings for it.
    with pytest.raises(AuthenticationError) as excinfo:
        collect_device_config(
            host="192.0.2.1",
            port=22,
            device_type="cisco_ios",
            username="admin",
            password="cisco123",
            secret=None,
            custom_commands=None,
            auth_timeout=2,
        )
    message = str(excinfo.value)
    assert "unreachable" in message
    assert "credentials problem" in message


def test_on_output_receives_connecting_line_before_failure():
    chunks: list[str] = []
    with pytest.raises(AuthenticationError):
        collect_device_config(
            host="192.0.2.1",
            port=22,
            device_type="cisco_ios",
            username="admin",
            password="cisco123",
            secret=None,
            custom_commands=None,
            auth_timeout=2,
            on_output=chunks.append,
        )
    transcript = "".join(chunks)
    assert transcript.startswith("Connecting to 192.0.2.1:22 as admin...")
    # Login never succeeded, so nothing past the connect attempt was emitted.
    assert "Authenticated" not in transcript


def test_passcode_mfa_without_otp_raises_before_connecting():
    with pytest.raises(Exception, match="one-time passcode is required"):
        collect_device_config(
            host="192.0.2.1",
            port=22,
            device_type="cisco_ios",
            username="admin",
            password="cisco123",
            secret=None,
            custom_commands=None,
            auth_timeout=2,
            mfa_mode="passcode",
            otp=None,
        )


class _FakeConnection:
    """Stands in for Netmiko's ConnectHandler context manager - just records
    which read method collect_device_config actually called, since that's
    the only thing these two tests care about."""

    def __init__(self):
        self.pattern_based_calls: list[str] = []
        self.timing_based_calls: list[str] = []
        self.cmd_verify_values: list[bool] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def send_command(self, command, read_timeout=None, cmd_verify=True):
        self.pattern_based_calls.append(command)
        self.cmd_verify_values.append(cmd_verify)
        return f"pattern-output:{command}"

    def send_command_timing(self, command, last_read=None, read_timeout=None):
        self.timing_based_calls.append(command)
        return f"timing-output:{command}"


def test_generic_termserver_devices_use_timing_based_read(monkeypatch):
    # apc_pdu (like every "generic_termserver" device type - most PDUs and
    # console servers) has no Netmiko driver support for its actual prompt,
    # so pattern-based send_command() has nothing reliable to wait for and
    # can block for the full read_timeout on every command. It should use
    # send_command_timing() (channel-quiet-based, prompt-agnostic) instead.
    fake = _FakeConnection()
    monkeypatch.setattr(collector_module, "ConnectHandler", lambda **kwargs: fake)

    output = collect_device_config(
        host="10.0.0.5",
        port=22,
        device_type="apc_pdu",
        username="apc",
        password="apc",
        secret=None,
        custom_commands=None,
        auth_timeout=5,
        commands_override=["about"],
    )

    assert fake.timing_based_calls == ["about"]
    assert fake.pattern_based_calls == []
    assert "timing-output:about" in output


def test_network_os_devices_still_use_pattern_based_read(monkeypatch):
    # A real Netmiko driver (cisco_ios here) knows the device's actual
    # prompt, so the normal pattern-based send_command() - which can tell a
    # genuinely slow command apart from one that's already finished - is
    # still used, not the timing-based fallback.
    fake = _FakeConnection()
    monkeypatch.setattr(collector_module, "ConnectHandler", lambda **kwargs: fake)

    output = collect_device_config(
        host="10.0.0.6",
        port=22,
        device_type="cisco_ios",
        username="admin",
        password="cisco123",
        secret=None,
        custom_commands=None,
        auth_timeout=5,
        commands_override=["show version"],
    )

    assert fake.pattern_based_calls == ["show version"]
    assert fake.timing_based_calls == []
    assert "pattern-output:show version" in output
    assert fake.cmd_verify_values == [True]


@pytest.mark.parametrize("device_type", ["cisco_wlc", "cisco_wlc_9800"])
def test_wlc_devices_skip_command_echo_verification(monkeypatch, device_type):
    # Both WLC generations are slow/chatty enough echoing a command back
    # that Netmiko's cmd_verify step - a hardcoded 10s wait, unaffected by
    # our own read_timeout=300 - can time out with "Pattern not detected"
    # even though the device would have answered fine. cmd_verify=False
    # skips straight to waiting for the real prompt instead.
    fake = _FakeConnection()
    monkeypatch.setattr(collector_module, "ConnectHandler", lambda **kwargs: fake)

    collect_device_config(
        host="10.0.0.7",
        port=22,
        device_type=device_type,
        username="admin",
        password="cisco123",
        secret=None,
        custom_commands=None,
        auth_timeout=5,
        commands_override=["sh cdp nei"],
    )

    assert fake.cmd_verify_values == [False]


def test_non_wlc_switch_on_shared_cisco_xe_driver_keeps_command_echo_verification(monkeypatch):
    # cisco_xe (plain IOS-XE switch/router) uses the exact same Netmiko
    # driver as the Catalyst 9800 WLC type, but isn't itself a WLC - the
    # cmd_verify skip must key off the device *type*'s category, not the
    # underlying driver name, or a perfectly well-behaved switch would lose
    # this safety check too.
    fake = _FakeConnection()
    monkeypatch.setattr(collector_module, "ConnectHandler", lambda **kwargs: fake)

    collect_device_config(
        host="10.0.0.8",
        port=22,
        device_type="cisco_xe",
        username="admin",
        password="cisco123",
        secret=None,
        custom_commands=None,
        auth_timeout=5,
        commands_override=["show running-config"],
    )

    assert fake.cmd_verify_values == [True]
