import os
import shutil
import socket
import subprocess
import time

import paramiko
import pytest

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


@_REQUIRES_SSHD
def test_can_negotiate_with_a_server_offering_only_legacy_kex(tmp_path):
    # Regression test for the real failure this was built to fix: a device
    # whose SSH implementation only offers diffie-hellman-group14-sha1 used
    # to fail here with "no acceptable kex algorithm" (surfacing to users as
    # a misleading NetmikoTimeoutException) even though collector.py claims
    # to support it - re-adding the algorithm's *name* without a working
    # implementation behind it would look fixed but still fail exactly like
    # this, so this spins up a real, restricted sshd rather than trusting
    # the registration alone.
    port = 2222
    pid_file = tmp_path / "sshd.pid"
    config = tmp_path / "sshd_config"
    config.write_text(
        f"Port {port}\n"
        "ListenAddress 127.0.0.1\n"
        f"HostKey {_HOST_KEY}\n"
        "KexAlgorithms diffie-hellman-group14-sha1\n"
        "PasswordAuthentication yes\n"
        "UsePAM no\n"
        f"PidFile {pid_file}\n"
    )
    os.makedirs("/run/sshd", exist_ok=True)

    proc = subprocess.Popen([_SSHD, "-f", str(config), "-D"], stderr=subprocess.PIPE)
    try:
        for _ in range(20):
            try:
                socket.create_connection(("127.0.0.1", port), timeout=0.5).close()
                break
            except OSError:
                time.sleep(0.25)
        else:
            pytest.fail("test sshd never started listening")

        # Wrong credentials on purpose - the point is proving the SSH
        # session/key-exchange itself succeeds. A credentials rejection
        # (NetmikoAuthenticationException) means it got past kex; the bug
        # this guards against fails earlier, as NetmikoTimeoutException.
        with pytest.raises(AuthenticationError) as excinfo:
            collect_device_config(
                host="127.0.0.1",
                port=port,
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
