"""Blocking Netmiko collection logic. Runs inside a Celery worker process,
never on the FastAPI event loop.

TACACS+/RADIUS AAA (and any MFA layered on top, e.g. Duo) is configured on
the device itself, not here - from this app's point of view that just means
the login step can take much longer than a plain local-auth SSH login, and
may need a one-time passcode appended to the password. See Credential.mfa_mode.
"""

from collections.abc import Callable

from netmiko import ConnectHandler
from netmiko.exceptions import NetmikoAuthenticationException, NetmikoTimeoutException

from app.services.device_types import get_device_type_spec, resolve_commands


class CollectionError(Exception):
    """Raised when a device could not be reached or its config could not be
    read. Message is safe to store/display - never includes credentials."""


class AuthenticationError(CollectionError):
    """Login itself failed or timed out - wrong credentials, an
    unreachable/down device, or (commonly, for TACACS+/MFA-backed accounts)
    the AAA round trip or MFA approval simply took longer than
    auth_timeout_seconds."""


class CommandExecutionError(CollectionError):
    """Login succeeded, but something went wrong while running commands
    against the device (e.g. a command timed out or was rejected)."""


def collect_device_config(
    *,
    host: str,
    port: int,
    device_type: str,
    username: str,
    password: str,
    secret: str | None,
    custom_commands: str | None,
    auth_timeout: int,
    commands_override: list[str] | None = None,
    mfa_mode: str = "none",
    otp: str | None = None,
    otp_delimiter: str = ",",
    on_authenticated: Callable[[], None] | None = None,
    on_output: Callable[[str], None] | None = None,
) -> str:
    """Connect, authenticate, and run the configured show/config command(s).

    `on_authenticated`, if given, is called the moment the SSH login (and any
    TACACS+/MFA challenge behind it) has succeeded - before any commands are
    sent - so callers can flip a device's status from "authenticating" to
    "running" at exactly that point. It should not raise; any error it
    encounters is its own concern (e.g. a DB status update) and shouldn't be
    mistaken for a device authentication failure.

    `on_output`, if given, is called with each successive chunk of a
    human-readable transcript (connecting, authenticated, each command sent
    and its output) as it becomes available, so callers can show live
    progress instead of just a static status. Same no-raise contract as
    `on_authenticated`.
    """

    def _emit(text: str) -> None:
        if on_output is not None:
            on_output(text)
    try:
        spec = get_device_type_spec(device_type)
        commands = commands_override or resolve_commands(device_type, custom_commands)
    except ValueError as exc:
        # Unknown device_type, or a type with no default command (e.g. most
        # PDUs/console servers) and no custom_commands/override supplied -
        # a configuration problem, not a device/network failure, but still
        # something the caller needs reported as a clean job-item failure
        # rather than an unhandled exception crashing the worker task.
        raise CollectionError(str(exc)) from exc

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
        # All three cover different stages of the SSH/AAA handshake; a slow
        # TACACS+/RADIUS backend or a push-MFA approval can stretch any of
        # them, so all get the same generous, credential-configurable budget.
        "timeout": auth_timeout,
        "auth_timeout": auth_timeout,
        "banner_timeout": auth_timeout,
        "conn_timeout": auth_timeout,
    }
    if secret and spec.secret_supported:
        connection_params["secret"] = secret

    _emit(f"Connecting to {host}:{port} as {username}...\n")
    try:
        with ConnectHandler(**connection_params) as conn:
            # Netmiko's constructor above blocks until the SSH session is
            # fully authenticated (raising on failure/timeout before we ever
            # reach this line), so by here the device has accepted the
            # login - authentication is done, only commands remain.
            if on_authenticated is not None:
                on_authenticated()
            _emit("Authenticated.\n")

            try:
                if secret and spec.secret_supported:
                    conn.enable()
                outputs = []
                for command in commands:
                    outputs.append(f"! ---- {command} ----")
                    _emit(f"\n$ {command}\n")
                    # show tech-support (Cisco IOS's default command) routinely
                    # takes minutes on real hardware, well past a 60s timeout.
                    output = conn.send_command(command, read_timeout=300)
                    outputs.append(output)
                    _emit(output + "\n")
                return "\n".join(outputs)
            except Exception as exc:  # noqa: BLE001 - reported as a job failure, not a crash
                raise CommandExecutionError(
                    f"Authenticated to {host}:{port} but failed while running commands: {exc}"
                ) from exc
    except CommandExecutionError:
        raise
    except (NetmikoAuthenticationException, NetmikoTimeoutException) as exc:
        raise AuthenticationError(
            f"Authentication failed or timed out for {host}:{port} ({exc.__class__.__name__}). "
            "If this device authenticates via TACACS+/RADIUS or requires MFA approval, try raising "
            "the credential's auth_timeout_seconds, and for passcode-based MFA confirm a fresh "
            "one-time code was supplied for this run."
        ) from exc
    except CollectionError:
        raise
    except Exception as exc:  # noqa: BLE001 - any other failure during connect is an auth-phase failure
        raise AuthenticationError(f"Failed to connect/authenticate to {host}:{port}: {exc}") from exc
