import os
import shutil
import socket
import subprocess
import time
from unittest.mock import Mock

import paramiko
import pytest
from paramiko.ssh_exception import AuthenticationException as ParamikoAuthenticationException

from app.services import collector as collector_module
from app.services.collector import AuthenticationError, CollectionCancelled, collect_device_config
from app.services.device_types import DEVICE_TYPE_REGISTRY, DeviceTypeSpec, parse_command_list, resolve_commands

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


def test_netmiko_uses_the_keyboard_interactive_fallback_ssh_client():
    # Regression guard for the fallback itself being wired up - if this
    # stops being true, every device goes back to plain paramiko.SSHClient
    # and TACACS+-backed logins that only work over keyboard-interactive
    # silently lose the fallback again.
    from netmiko.base_connection import BaseConnection

    assert (
        BaseConnection._build_ssh_client
        is collector_module._build_ssh_client_with_keyboard_interactive_fallback
    )


def test_keyboard_interactive_fallback_not_used_when_password_auth_succeeds():
    # A device that already authenticates fine today (the overwhelming
    # majority) must see zero behavior change - the fallback should never
    # even be attempted if plain password auth just works.
    client = collector_module._SSHClientWithKeyboardInteractiveFallback()
    transport = Mock()
    client.get_transport = Mock(return_value=transport)

    client._auth("admin", "cisco123")

    transport.auth_password.assert_called_once_with("admin", "cisco123")
    transport.auth_interactive.assert_not_called()


def test_keyboard_interactive_fallback_used_when_password_auth_is_rejected():
    # The actual bug this guards against: a TACACS+-backed account (or any
    # device whose AAA integration only completes over keyboard-interactive)
    # gets flatly rejected by plain "password" auth even with fully correct
    # credentials - Paramiko's own SSHClient never retries via
    # keyboard-interactive once a password was supplied (see
    # SSHClient._auth), so without this fallback that looks identical to a
    # wrong password.
    client = collector_module._SSHClientWithKeyboardInteractiveFallback()
    transport = Mock()
    transport.auth_password.side_effect = ParamikoAuthenticationException("Authentication failed.")
    client.get_transport = Mock(return_value=transport)

    client._auth("ml", "correct-password")

    transport.auth_password.assert_called_once_with("ml", "correct-password")
    transport.auth_interactive.assert_called_once()
    username, handler = transport.auth_interactive.call_args.args
    assert username == "ml"
    # The device may ask one prompt or several (e.g. a TACACS+ challenge) -
    # either way, every prompt gets answered with the same password, since
    # this app only ever has the one credential string to offer.
    assert handler("title", "instructions", [("Password: ", False)]) == ["correct-password"]
    assert handler("title", "instructions", [("Password: ", False), ("Again: ", False)]) == [
        "correct-password",
        "correct-password",
    ]


def test_keyboard_interactive_fallback_still_raises_if_both_methods_fail():
    # A genuinely wrong credential must still fail loudly, exactly as before
    # this fallback existed - it should never mask a real auth failure.
    client = collector_module._SSHClientWithKeyboardInteractiveFallback()
    transport = Mock()
    transport.auth_password.side_effect = ParamikoAuthenticationException("Authentication failed.")
    transport.auth_interactive.side_effect = ParamikoAuthenticationException("Authentication failed.")
    client.get_transport = Mock(return_value=transport)

    with pytest.raises(ParamikoAuthenticationException):
        client._auth("admin", "definitely-wrong")


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
                device_type="apc_pdu",
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
                device_type="apc_pdu",
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


def test_resolve_commands_requires_custom_for_types_with_no_default(monkeypatch):
    # No real registry entry has an empty default_commands list anymore
    # (pdu_generic and console_server, the two that used to, were both
    # removed once every device in this org's actual environment turned
    # out to be Cisco or APC) - insert a temporary one to keep exercising
    # this guard.
    monkeypatch.setitem(
        DEVICE_TYPE_REGISTRY,
        "test_no_default_commands",
        DeviceTypeSpec("Test type with no default", "test", "generic_termserver", (), secret_supported=False),
    )
    with pytest.raises(ValueError, match="no default command"):
        resolve_commands("test_no_default_commands", None)


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
        self.auto_find_prompt_values: list[bool] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def send_command(self, command, read_timeout=None, cmd_verify=True, auto_find_prompt=True):
        self.pattern_based_calls.append(command)
        self.cmd_verify_values.append(cmd_verify)
        self.auto_find_prompt_values.append(auto_find_prompt)
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
    assert fake.auto_find_prompt_values == [True]


@pytest.mark.parametrize("device_type", ["cisco_wlc", "cisco_wlc_9800"])
def test_wlc_default_commands_do_not_redundantly_disable_paging(device_type):
    # Netmiko's own driver already disables paging automatically while
    # connecting, for both WLC generations (cisco_wlc runs "config paging
    # disable" itself; cisco_xe runs "terminal length 0") - sending "config
    # paging disable" again as a first "real" command doesn't just
    # duplicate that, it actively broke AireOS's prompt detection for
    # whatever command came right after it.
    commands = " ".join(DEVICE_TYPE_REGISTRY[device_type].default_commands).lower()
    assert "paging" not in commands
    assert "terminal length" not in commands
    assert "term len" not in commands


@pytest.mark.parametrize("device_type", ["cisco_wlc", "cisco_wlc_9800"])
def test_wlc_devices_use_timing_based_read(monkeypatch, device_type):
    # Both WLC generations are slow/chatty enough that Netmiko's normal
    # pattern-based send_command() misfires on them in more than one way in
    # turn: cmd_verify's hardcoded 10s command-echo wait can time out even
    # though the device would have answered fine; disabling just that and
    # leaving auto_find_prompt on, its fresh per-command prompt probe can
    # instead grab a trailing fragment of the *previous* (often large,
    # tabular) command's still-draining output and wait for that on the
    # *next* command; and disabling both in favor of the one stable prompt
    # captured at connect time still risks that same prompt string
    # coincidentally matching partway through a large table's own contents,
    # cutting a command's output off early and leaving the rest to be swept
    # up by the *next* command's read. send_command_timing() (the same
    # channel-quiet-based, prompt-agnostic read already used for
    # generic_termserver devices) sidesteps all of it at once.
    fake = _FakeConnection()
    monkeypatch.setattr(collector_module, "ConnectHandler", lambda **kwargs: fake)

    output = collect_device_config(
        host="10.0.0.7",
        port=22,
        device_type=device_type,
        username="admin",
        password="cisco123",
        secret=None,
        custom_commands=None,
        auth_timeout=5,
        commands_override=["show cdp neighbors"],
    )

    assert fake.timing_based_calls == ["show cdp neighbors"]
    assert fake.pattern_based_calls == []
    assert "timing-output:show cdp neighbors" in output


def test_non_wlc_switch_on_shared_cisco_xe_driver_keeps_pattern_based_read(monkeypatch):
    # A plain IOS-XE switch/router uses the exact same Netmiko driver
    # ("cisco_xe") as the Catalyst 9800 WLC type, but isn't itself a WLC -
    # the timing-based-read switch must key off the device *type*'s
    # category, not the underlying driver name, or a perfectly well-behaved
    # switch would lose the more precise pattern-based read too. No such
    # switch type is in the registry right now (this org's environment is
    # Cisco IOS + APC only), so one is inserted temporarily just to prove
    # the category-based keying.
    monkeypatch.setitem(
        DEVICE_TYPE_REGISTRY,
        "test_cisco_xe_switch",
        DeviceTypeSpec("Test IOS-XE switch", "switch", "cisco_xe", ("show running-config",)),
    )
    fake = _FakeConnection()
    monkeypatch.setattr(collector_module, "ConnectHandler", lambda **kwargs: fake)

    collect_device_config(
        host="10.0.0.8",
        port=22,
        device_type="test_cisco_xe_switch",
        username="admin",
        password="cisco123",
        secret=None,
        custom_commands=None,
        auth_timeout=5,
        commands_override=["show running-config"],
    )

    assert fake.cmd_verify_values == [True]
    assert fake.auto_find_prompt_values == [True]


def test_should_cancel_stops_before_the_next_command(monkeypatch):
    # should_cancel() is checked before each command, not mid-command - a
    # live SSH call is opaque until it returns - so a cancel noticed after
    # the first command starts still lets that one finish, then raises
    # CollectionCancelled instead of running any of the rest.
    fake = _FakeConnection()
    monkeypatch.setattr(collector_module, "ConnectHandler", lambda **kwargs: fake)

    checks = {"count": 0}

    def should_cancel() -> bool:
        checks["count"] += 1
        return checks["count"] > 1  # False before the 1st command, True before the 2nd

    with pytest.raises(CollectionCancelled):
        collect_device_config(
            host="10.0.0.9",
            port=22,
            device_type="cisco_ios",
            username="admin",
            password="cisco123",
            secret=None,
            custom_commands=None,
            auth_timeout=5,
            commands_override=["show version", "show run"],
            should_cancel=should_cancel,
        )

    assert fake.pattern_based_calls == ["show version"]
