import pytest

from app.services.collector import AuthenticationError, collect_device_config
from app.services.device_types import parse_command_list, resolve_commands


def test_parse_command_list_strips_and_drops_blanks():
    assert parse_command_list("show run, , show version ,") == ["show run", "show version"]


def test_resolve_commands_uses_registry_default():
    assert resolve_commands("cisco_ios", None) == ["show running-config"]


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
