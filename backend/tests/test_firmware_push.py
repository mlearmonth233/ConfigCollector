"""Unit coverage for services/firmware_push.py's interactive copy-dialogue
handling - the part with no real Cisco hardware to verify against, so it's
exercised here against a small fake connection that plays out a realistic
IOS "copy scp:" prompt sequence (destination filename, password, overwrite
confirmation, progress, and a final status line), plus the higher-level
push_firmware_to_device() success/failure/verification paths against a fake
ConnectHandler - the same style test_collector.py already uses for
collect_device_config.
"""

import pytest
import redis as redis_lib

from app.config import get_settings
from app.services import firmware_push
from app.services.firmware_push import (
    TransferError,
    UnsupportedDeviceTypeError,
    VerificationFailedError,
    _redact,
    _run_interactive_copy,
    _transfer_timeout_seconds,
    push_firmware_to_device,
)


def _redis_available() -> bool:
    try:
        redis_lib.Redis.from_url(get_settings().redis_url, socket_connect_timeout=1).ping()
        return True
    except Exception:  # noqa: BLE001
        return False


requires_redis = pytest.mark.skipif(not _redis_available(), reason="Redis is required for SCP grant storage")


def test_redact_replaces_every_occurrence():
    assert _redact("Password: s3cret\ns3cret again", "s3cret") == "Password: [redacted]\n[redacted] again"
    assert _redact("nothing to redact", "") == "nothing to redact"


def test_transfer_timeout_scales_with_size_but_stays_bounded():
    small = _transfer_timeout_seconds(1024)
    large = _transfer_timeout_seconds(2 * 1024 * 1024 * 1024)  # 2 GB
    assert small == pytest.approx(300.0)  # floor
    assert large > small
    assert large <= 6 * 60 * 60  # ceiling


class _FakeIosCopySession:
    """Plays out a realistic Cisco IOS "copy scp:" interactive dialogue in
    response to whatever gets written to the channel, one step per write -
    exactly the shape _run_interactive_copy has to drive to completion."""

    def __init__(self, final_line: str = "1234 bytes copied in 0.502 secs (2458 bytes/sec)\nRouter#"):
        self._step = 0
        self.written: list[str] = []
        self._pending: list[str] = []
        self._final_line = final_line

    def normalize_cmd(self, command: str) -> str:
        return command.rstrip() + "\n"

    def write_channel(self, data: str) -> None:
        self.written.append(data)
        if self._step == 0:
            self._pending.append("Destination filename [img.bin]? ")
        elif self._step == 1:
            self._pending.append("\r\nPassword: ")
        elif self._step == 2:
            self._pending.append(
                "\r\n%Warning: There is a file already existing with this name\r\nDo you want to over write? [confirm]"
            )
        elif self._step == 3:
            self._pending.append("\r\n" + "!" * 20 + "\r\n[OK - 1234 bytes]\r\n\r\n" + self._final_line)
        self._step += 1

    def read_channel(self) -> str:
        if self._pending:
            return self._pending.pop(0)
        return ""


def test_run_interactive_copy_drives_full_prompt_sequence_to_completion():
    conn = _FakeIosCopySession()
    outputs: list[str] = []

    transcript = _run_interactive_copy(
        conn,
        copy_command="copy scp://grantuser@10.0.0.1:2222/img.bin flash:img.bin",
        scp_password="s3cret-grant-password",
        overall_timeout=5,
        on_output=outputs.append,
    )

    assert "Destination filename" in transcript
    assert "bytes copied in" in transcript
    # The real password was sent to the device...
    assert any(w.strip() == "s3cret-grant-password" for w in conn.written)
    # ...but never appears in anything streamed to the live console.
    assert not any("s3cret-grant-password" in chunk for chunk in outputs)


def test_run_interactive_copy_times_out_if_device_never_responds():
    class _SilentConn:
        def normalize_cmd(self, command: str) -> str:
            return command + "\n"

        def write_channel(self, data: str) -> None:
            pass

        def read_channel(self) -> str:
            return ""

    with pytest.raises(TransferError, match="Timed out"):
        _run_interactive_copy(
            _SilentConn(), copy_command="copy scp://x@y/z flash:z", scp_password="pw", overall_timeout=0.3,
            on_output=lambda _t: None,
        )


def test_run_interactive_copy_stops_if_password_prompted_twice():
    class _RepeatedPasswordPrompt:
        def normalize_cmd(self, command: str) -> str:
            return command + "\n"

        def __init__(self):
            self._n = 0

        def write_channel(self, data: str) -> None:
            self._n += 1

        def read_channel(self) -> str:
            return "Password: "

    with pytest.raises(TransferError, match="repeatedly prompted"):
        _run_interactive_copy(
            _RepeatedPasswordPrompt(), copy_command="copy scp://x@y/z flash:z", scp_password="pw",
            overall_timeout=5, on_output=lambda _t: None,
        )


class _FakeConnectHandler:
    """Stands in for netmiko.ConnectHandler - same role _FakeConnection
    plays in test_collector.py."""

    instances: list["_FakeConnectHandler"] = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.enabled = False
        self.verify_output = ""
        self.copy_session = _FakeIosCopySession()
        _FakeConnectHandler.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def enable(self):
        self.enabled = True

    def write_channel(self, data):
        self.copy_session.write_channel(data)

    def read_channel(self):
        return self.copy_session.read_channel()

    def normalize_cmd(self, command):
        return self.copy_session.normalize_cmd(command)

    def send_command_timing(self, command, last_read=None, read_timeout=None):
        return self.verify_output


@requires_redis
def test_push_firmware_to_device_success_verifies_checksum(monkeypatch, tmp_path):
    firmware = tmp_path / "img.bin"
    firmware.write_bytes(b"firmware-bytes")
    md5 = __import__("hashlib").md5(firmware.read_bytes()).hexdigest()

    _FakeConnectHandler.instances.clear()

    def _fake_handler(**kwargs):
        handler = _FakeConnectHandler(**kwargs)
        handler.verify_output = f"verify /md5 (flash:img.bin) = {md5}\n"
        return handler

    monkeypatch.setattr(firmware_push, "ConnectHandler", _fake_handler)

    result = push_firmware_to_device(
        host="10.0.0.5", port=22, device_type="cisco_ios", username="admin", password="cisco123",
        secret="enable-secret", auth_timeout=5, mfa_mode="none", otp=None, otp_delimiter=",",
        firmware_path=str(firmware), firmware_size_bytes=firmware.stat().st_size, firmware_md5=md5,
        target_filename="img.bin", verify_checksum=True, reload_after=False,
        scp_public_host="10.0.0.100", scp_port=2222,
    )

    assert result.checksum_verified is True
    assert _FakeConnectHandler.instances[0].enabled is True


@requires_redis
def test_push_firmware_to_device_raises_on_checksum_mismatch(monkeypatch, tmp_path):
    firmware = tmp_path / "img.bin"
    firmware.write_bytes(b"firmware-bytes")
    md5 = __import__("hashlib").md5(firmware.read_bytes()).hexdigest()

    def _fake_handler(**kwargs):
        handler = _FakeConnectHandler(**kwargs)
        handler.verify_output = "verify /md5 (flash:img.bin) = " + ("0" * 32) + "\n"
        return handler

    monkeypatch.setattr(firmware_push, "ConnectHandler", _fake_handler)

    with pytest.raises(VerificationFailedError):
        push_firmware_to_device(
            host="10.0.0.6", port=22, device_type="cisco_ios", username="admin", password="cisco123",
            secret=None, auth_timeout=5, mfa_mode="none", otp=None, otp_delimiter=",",
            firmware_path=str(firmware), firmware_size_bytes=firmware.stat().st_size, firmware_md5=md5,
            target_filename="img.bin", verify_checksum=True, reload_after=False,
            scp_public_host="10.0.0.100", scp_port=2222,
        )


@requires_redis
def test_push_firmware_to_device_raises_transfer_error_when_device_reports_no_success(monkeypatch, tmp_path):
    firmware = tmp_path / "img.bin"
    firmware.write_bytes(b"firmware-bytes")

    class _FailingCopySession(_FakeIosCopySession):
        def __init__(self):
            super().__init__(final_line="%Error copying scp://x (Timeout)\nRouter#")

    def _fake_handler(**kwargs):
        handler = _FakeConnectHandler(**kwargs)
        handler.copy_session = _FailingCopySession()
        return handler

    monkeypatch.setattr(firmware_push, "ConnectHandler", _fake_handler)

    with pytest.raises(TransferError):
        push_firmware_to_device(
            host="10.0.0.7", port=22, device_type="cisco_ios", username="admin", password="cisco123",
            secret=None, auth_timeout=5, mfa_mode="none", otp=None, otp_delimiter=",",
            firmware_path=str(firmware), firmware_size_bytes=firmware.stat().st_size,
            firmware_md5="0" * 32, target_filename="img.bin", verify_checksum=False, reload_after=False,
            scp_public_host="10.0.0.100", scp_port=2222,
        )


def test_unsupported_device_type_raises_before_connecting(monkeypatch, tmp_path):
    firmware = tmp_path / "img.bin"
    firmware.write_bytes(b"x")

    def _boom(**kwargs):
        raise AssertionError("should never attempt to connect for an unsupported device type")

    monkeypatch.setattr(firmware_push, "ConnectHandler", _boom)

    with pytest.raises(UnsupportedDeviceTypeError):
        push_firmware_to_device(
            host="10.0.0.8", port=22, device_type="apc_pdu", username="admin", password="cisco123",
            secret=None, auth_timeout=5, mfa_mode="none", otp=None, otp_delimiter=",",
            firmware_path=str(firmware), firmware_size_bytes=1, firmware_md5="0" * 32,
            target_filename="img.bin", verify_checksum=False, reload_after=False,
            scp_public_host="10.0.0.100", scp_port=2222,
        )


def test_missing_scp_public_host_raises_clear_error(monkeypatch, tmp_path):
    firmware = tmp_path / "img.bin"
    firmware.write_bytes(b"x")

    def _boom(**kwargs):
        raise AssertionError("should never attempt to connect with no SCP public host configured")

    monkeypatch.setattr(firmware_push, "ConnectHandler", _boom)

    with pytest.raises(firmware_push.FirmwarePushError, match="SCP_SERVER_PUBLIC_HOST"):
        push_firmware_to_device(
            host="10.0.0.9", port=22, device_type="cisco_ios", username="admin", password="cisco123",
            secret=None, auth_timeout=5, mfa_mode="none", otp=None, otp_delimiter=",",
            firmware_path=str(firmware), firmware_size_bytes=1, firmware_md5="0" * 32,
            target_filename="img.bin", verify_checksum=False, reload_after=False,
            scp_public_host="", scp_port=2222,
        )
