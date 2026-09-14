"""Netmiko-driven firmware push.

Connects to a device (same authentication/enable-mode handling as
collector.py's config collection), mints a short-lived, single-use SCP
grant for the target firmware file (see scp_grants.py/scp_server.py), and
drives the device's own "copy scp://..." command interactively - answering
the destination-filename/overwrite/password prompts a human at a terminal
would - streaming its output live as it goes rather than blocking silently
until an often multi-minute transfer finishes.

Only device types with a known flash-style filesystem (see device_types.py's
FIRMWARE_FLASH_PREFIXES) are supported - Cisco IOS/NX-OS switches and
Catalyst 9800 WLCs. Everything else (AireOS WLCs, FortiGate, APC PDUs,
Versa) manages firmware a different way entirely and isn't handled here.

Runs inside a Celery worker process, never on the FastAPI event loop - same
constraint as collector.py.

Caveat: the interactive prompt-handling below was written against publicly
documented Cisco IOS/IOS-XE "copy" CLI behavior and verified end-to-end
against this app's own SCP server (see tests/test_scp_server.py), but has
not been exercised against real Cisco hardware - prompt wording has been
known to vary across IOS trains. Test carefully against a lab device before
relying on this for production firmware rollouts, especially reload_after.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass

from netmiko import ConnectHandler
from netmiko.exceptions import NetmikoAuthenticationException, NetmikoTimeoutException

# Importing collector.py (rather than just the exception classes below)
# also applies its module-level side effects - re-enabling the legacy
# diffie-hellman-group14-sha1/ssh-rsa algorithms and the keyboard-
# interactive SSH auth fallback - which firmware pushes need exactly as
# much as config collection does, since it's often the very same devices.
from app.services import collector
from app.services.collector import AuthenticationError, CollectionError, EnableModeError
from app.services.device_types import FIRMWARE_FLASH_PREFIXES, get_device_type_spec
from app.services.scp_grants import create_grant, invalidate_grant

assert collector  # keep the import (and its side effects) - not otherwise referenced

# Minimum throughput assumed when budgeting how long to wait for a transfer
# to finish before giving up - deliberately conservative (a real transfer
# over even a slow WAN link should comfortably beat this), so a genuinely
# stalled transfer eventually times out instead of hanging a worker forever,
# while a legitimately slow-but-working one is never cut off early.
_MIN_TRANSFER_THROUGHPUT_BYTES_PER_SEC = 200 * 1024  # 200 KB/s
_MIN_TRANSFER_TIMEOUT_SECONDS = 300.0
_MAX_TRANSFER_TIMEOUT_SECONDS = 6 * 60 * 60
# "verify /md5" has to read and hash the whole file back off flash -
# budgeted separately at a faster assumed throughput since it's a local
# flash read, not a network transfer.
_MIN_VERIFY_THROUGHPUT_BYTES_PER_SEC = 5 * 1024 * 1024  # 5 MB/s
_MIN_VERIFY_TIMEOUT_SECONDS = 120.0

_PROMPT_PASSWORD = re.compile(r"[Pp]assword:\s*$")
_PROMPT_QUESTION = re.compile(r"\?\s*$")
_PROMPT_CONFIRM = re.compile(r"\[confirm\]\s*$", re.IGNORECASE)
_PROMPT_YES_NO = re.compile(r"\[\s*yes/no\s*\]:?\s*$", re.IGNORECASE)
_DEVICE_PROMPT = re.compile(r"(?:^|[\r\n])\S{1,64}[>#]\s*$")
_SUCCESS_MARKER = re.compile(r"bytes copied in|\[OK", re.IGNORECASE)
_ERROR_MARKER = re.compile(r"%Error|Error (?:copying|opening)", re.IGNORECASE)
_MD5_LINE = re.compile(r"=\s*([0-9a-fA-F]{32})\s*$")


class FirmwarePushError(CollectionError):
    """Base class for firmware-push-specific failures - reuses
    CollectionError's contract (a safe-to-display message, never containing
    credentials) so callers can handle it alongside collector.py's own
    auth/command error types."""


class UnsupportedDeviceTypeError(FirmwarePushError):
    """This device type has no known flash-style filesystem to push
    firmware onto."""


class TransferError(FirmwarePushError):
    """Login (and enable mode) succeeded, but the firmware copy itself
    failed, timed out, or couldn't be confirmed successful from the
    device's own output."""


class VerificationFailedError(FirmwarePushError):
    """The transfer completed, but the device's own "verify /md5" checksum
    didn't match the uploaded file's - the flash copy may be corrupt and
    should not be trusted or booted."""


@dataclass(frozen=True)
class PushResult:
    transcript: str
    checksum_verified: bool | None  # None if verification wasn't requested


def push_firmware_to_device(
    *,
    host: str,
    port: int,
    device_type: str,
    username: str,
    password: str,
    secret: str | None,
    auth_timeout: int,
    mfa_mode: str,
    otp: str | None,
    otp_delimiter: str,
    firmware_path: str,
    firmware_size_bytes: int,
    firmware_md5: str,
    target_filename: str,
    verify_checksum: bool,
    reload_after: bool,
    scp_public_host: str,
    scp_port: int,
    on_authenticated: Callable[[], None] | None = None,
    on_output: Callable[[str], None] | None = None,
) -> PushResult:
    """Pushes one firmware file to one device. Raises AuthenticationError /
    EnableModeError (from collector.py) for a pre-transfer failure, or one
    of this module's own FirmwarePushError subclasses for anything that
    goes wrong afterward - callers (tasks.py) treat all of these as a plain
    job-item failure the same way collect_device_config's exceptions are."""

    def _emit(text: str) -> None:
        if on_output is not None:
            on_output(text)

    try:
        spec = get_device_type_spec(device_type)
    except ValueError as exc:
        raise UnsupportedDeviceTypeError(str(exc)) from exc
    flash_prefix = FIRMWARE_FLASH_PREFIXES.get(device_type)
    if flash_prefix is None:
        raise UnsupportedDeviceTypeError(
            f"Device type '{device_type}' has no known flash-style filesystem - firmware push isn't "
            "supported for it."
        )
    if not scp_public_host:
        raise FirmwarePushError(
            "SCP_SERVER_PUBLIC_HOST is not configured - set it to an address the managed devices can "
            "reach before starting a firmware push."
        )

    effective_password = password
    if mfa_mode == "passcode":
        if not otp:
            raise CollectionError(
                f"A one-time passcode is required for {host}:{port} but none was supplied for this run"
            )
        effective_password = f"{password}{otp_delimiter}{otp}"

    connection_params = {
        "device_type": spec.netmiko_driver,
        "host": host,
        "port": port,
        "username": username,
        "password": effective_password,
        "timeout": auth_timeout,
        "auth_timeout": auth_timeout,
        "banner_timeout": auth_timeout,
        "conn_timeout": auth_timeout,
    }
    if secret and spec.secret_supported:
        connection_params["secret"] = secret

    grant = create_grant(file_path=firmware_path, remote_filename=target_filename)
    try:
        _emit(f"Connecting to {host}:{port} as {username}...\n")
        with ConnectHandler(**connection_params) as conn:
            if on_authenticated is not None:
                on_authenticated()
            _emit("Authenticated.\n")

            if secret and spec.secret_supported:
                try:
                    conn.enable()
                except Exception as exc:  # noqa: BLE001
                    raise EnableModeError(f"Failed to enter enable mode on {host}:{port}: {exc}") from exc

            copy_command = (
                f"copy scp://{grant.username}@{scp_public_host}:{scp_port}/{target_filename} "
                f"{flash_prefix}{target_filename}"
            )
            transfer_timeout = _transfer_timeout_seconds(firmware_size_bytes)
            _emit(f"\n$ copy scp://***:***@{scp_public_host}:{scp_port}/{target_filename} "
                  f"{flash_prefix}{target_filename}\n")
            transcript = _run_interactive_copy(
                conn,
                copy_command=copy_command,
                scp_password=grant.password,
                overall_timeout=transfer_timeout,
                on_output=_emit,
            )

            if _ERROR_MARKER.search(transcript) or not _SUCCESS_MARKER.search(transcript):
                raise TransferError(
                    f"Firmware copy to {host}:{port} did not report success - see the console output above."
                )

            checksum_verified: bool | None = None
            if verify_checksum:
                verify_command = f"verify /md5 {flash_prefix}{target_filename}"
                _emit(f"\n$ {verify_command}\n")
                verify_timeout = max(
                    _MIN_VERIFY_TIMEOUT_SECONDS, firmware_size_bytes / _MIN_VERIFY_THROUGHPUT_BYTES_PER_SEC
                )
                verify_output = conn.send_command_timing(verify_command, last_read=2, read_timeout=verify_timeout)
                _emit(verify_output + "\n")
                match = _MD5_LINE.search(verify_output.strip())
                device_md5 = match.group(1).lower() if match else None
                checksum_verified = device_md5 is not None and device_md5 == firmware_md5.lower()
                if not checksum_verified:
                    raise VerificationFailedError(
                        f"Checksum verification failed on {host}:{port}: expected {firmware_md5}, device "
                        f"reported {device_md5 or 'no checksum (unrecognized output)'}"
                    )

            if reload_after:
                _emit("\n$ reload\n")
                _issue_reload(conn, on_output=_emit)

            return PushResult(transcript=transcript, checksum_verified=checksum_verified)
    except FirmwarePushError:
        raise
    except NetmikoAuthenticationException as exc:
        raise AuthenticationError(f"Authentication was rejected for {host}:{port}: {exc}") from exc
    except NetmikoTimeoutException as exc:
        raise AuthenticationError(f"Could not establish an SSH session with {host}:{port}: {exc}") from exc
    except CollectionError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise AuthenticationError(f"Failed to connect/authenticate to {host}:{port}: {exc}") from exc
    finally:
        # One grant is good for exactly one attempt, whatever the outcome -
        # this fires whether the transfer succeeded, failed, or was never
        # even reached (e.g. device authentication itself failed first).
        invalidate_grant(grant.username)


def _transfer_timeout_seconds(size_bytes: int) -> float:
    estimate = size_bytes / _MIN_TRANSFER_THROUGHPUT_BYTES_PER_SEC
    return min(max(estimate, _MIN_TRANSFER_TIMEOUT_SECONDS), _MAX_TRANSFER_TIMEOUT_SECONDS)


def _redact(text: str, secret: str) -> str:
    return text.replace(secret, "[redacted]") if secret else text


def _run_interactive_copy(conn, *, copy_command: str, scp_password: str, overall_timeout: float, on_output) -> str:
    """Sends `copy_command` and drives the resulting interactive dialogue to
    completion, answering each prompt as it arrives and streaming output via
    `on_output` live (rather than only returning once everything is done) -
    important since a large firmware transfer can legitimately run for
    several minutes and a user watching the console should see progress,
    not silence."""
    transcript = ""
    conn.write_channel(conn.normalize_cmd(copy_command))
    start = time.monotonic()
    last_data_at = time.monotonic()
    password_sent = False

    while True:
        if time.monotonic() - start > overall_timeout:
            raise TransferError(f"Timed out after {int(overall_timeout)}s waiting for the firmware copy to finish")

        chunk = conn.read_channel()
        if not chunk:
            quiet_for = time.monotonic() - last_data_at
            if transcript and quiet_for > 3.0 and _DEVICE_PROMPT.search(transcript[-160:]):
                break
            time.sleep(0.2)
            continue

        last_data_at = time.monotonic()
        transcript += chunk
        on_output(_redact(chunk, scp_password))
        tail = transcript[-160:]

        if _PROMPT_PASSWORD.search(tail):
            if password_sent:
                # Already sent our one-time SCP password once - a second
                # prompt for it means something is wrong (and the grant is
                # single-use regardless), so stop rather than loop forever.
                raise TransferError("Device repeatedly prompted for a password during firmware copy")
            conn.write_channel(conn.normalize_cmd(scp_password))
            password_sent = True
        elif _PROMPT_CONFIRM.search(tail) or _PROMPT_YES_NO.search(tail) or _PROMPT_QUESTION.search(tail):
            conn.write_channel(conn.normalize_cmd(""))  # accept the default for anything else asked

    return transcript


def _issue_reload(conn, on_output: Callable[[str], None]) -> None:
    """Best-effort: issues 'reload', declines saving any unrelated pending
    config changes (a firmware push has no business silently persisting
    those as a side effect), then accepts the final confirmation - without
    waiting for the device to actually come back up, since a dropped SSH
    session at this point is the expected, successful outcome, not a
    failure."""
    try:
        conn.write_channel(conn.normalize_cmd("reload"))
        time.sleep(1)
        output = conn.read_channel()
        on_output(output)
        if re.search(r"[Ss]ave\b.*\?", output) or _PROMPT_YES_NO.search(output.rstrip()):
            conn.write_channel(conn.normalize_cmd("no"))
            time.sleep(1)
            on_output(conn.read_channel())
        conn.write_channel(conn.normalize_cmd(""))  # accept "Proceed with reload? [confirm]"
        time.sleep(1)
        on_output(conn.read_channel())
    except Exception:  # noqa: BLE001 - the device dropping the connection here is success, not failure
        pass
