"""Blocking Netmiko collection logic. Runs inside a Celery worker process,
never on the FastAPI event loop.

TACACS+/RADIUS AAA (and any MFA layered on top, e.g. Duo) is configured on
the device itself, not here - from this app's point of view that just means
the login step can take much longer than a plain local-auth SSH login, and
may need a one-time passcode appended to the password. See Credential.mfa_mode.
"""

from collections.abc import Callable
from hashlib import sha1

import paramiko
from cryptography.hazmat.primitives import hashes
from netmiko import ConnectHandler
from netmiko.exceptions import NetmikoAuthenticationException, NetmikoTimeoutException
from paramiko.kex_group14 import KexGroup14SHA256
from paramiko.rsakey import RSAKey

from app.services.device_types import get_device_type_spec, resolve_commands


class _KexGroup14SHA1(KexGroup14SHA256):
    """diffie-hellman-group14-sha1: identical Diffie-Hellman group/exchange
    to KexGroup14SHA256, just hashed with SHA-1 - Paramiko implemented this
    for years but dropped it (along with every other SHA-1 kex) as of its
    3.x series. Reimplementing it here is 2 lines because the group
    parameters (the only real cryptographic content) are unchanged; only
    the transcript hash differs."""

    name = "diffie-hellman-group14-sha1"
    hash_algo = sha1


# Some still-deployed switches/WLCs (old IOS, older AireOS controllers, etc.)
# only speak diffie-hellman-group14-sha1 and never got past Paramiko
# dropping it. Re-enable just this one (a fixed, well-vetted 2048-bit group -
# not the weaker, dynamically-negotiated group-exchange-sha1 variant) as a
# last-resort fallback: it's registered but appended to the *end* of the
# preferred list, so Paramiko still tries every stronger algorithm first and
# only falls back to this one when a device offers nothing better - this
# doesn't weaken negotiation against any device that already supports
# something stronger.
if _KexGroup14SHA1.name not in paramiko.Transport._preferred_kex:
    paramiko.Transport._preferred_kex = paramiko.Transport._preferred_kex + (_KexGroup14SHA1.name,)
    paramiko.Transport._kex_info[_KexGroup14SHA1.name] = _KexGroup14SHA1

# Same story for the *host key* side: Paramiko also dropped the legacy
# "ssh-rsa" (RSA key, SHA-1 signature) server host key algorithm, which is
# the only kind of RSA host key many older devices' SSH servers ever
# learned to sign with (they predate the newer rsa-sha2-256/512 signature
# scheme, even though the underlying key itself is still a perfectly
# ordinary RSA key). Without this, a device offering only "ssh-rsa" fails
# with "Incompatible ssh peer (no acceptable host key)" - the host-key
# equivalent of the kex failure above, and the next thing an old device
# hits once kex itself succeeds. Same last-resort placement: appended to
# the end of the preferred list, so a device offering a modern host key
# algorithm is unaffected.
if "ssh-rsa" not in paramiko.Transport._preferred_keys:
    paramiko.Transport._preferred_keys = paramiko.Transport._preferred_keys + ("ssh-rsa",)
    paramiko.Transport._key_info["ssh-rsa"] = RSAKey
    RSAKey.HASHES["ssh-rsa"] = hashes.SHA1


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


class EnableModeError(CommandExecutionError):
    """Login succeeded, but entering privileged/enable mode failed - most
    often a wrong (or missing) enable secret, or a device/device_type
    mismatch (e.g. an AireOS WLC misconfigured as a 9800, which does
    expect enable mode). A CommandExecutionError subclass since login
    itself did succeed, but distinguished from a plain one so callers can
    retry with a fallback credential's own enable secret, the same way an
    AuthenticationError triggers a fallback login attempt."""


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
                    try:
                        conn.enable()
                    except Exception as exc:  # noqa: BLE001
                        raise EnableModeError(
                            f"Failed to enter enable mode on {host}:{port}: {exc}"
                        ) from exc
                # "generic_termserver" (most PDUs, Opengear console servers)
                # and Cisco WLCs (both AireOS and Catalyst 9800) all use
                # timing-based reads instead of Netmiko's normal pattern-
                # based send_command():
                #
                # - generic_termserver has no vendor-specific prompt
                #   handling at all (session_preparation() is a no-op), so a
                #   pattern-based wait has nothing reliable to match against
                #   and can sit blocked for the full read_timeout on every
                #   command.
                #
                # - WLCs are slow/chatty enough that pattern-based reads
                #   misfire in more than one way, tried and found lacking in
                #   turn: cmd_verify's hardcoded 10s command-echo wait can
                #   time out even though the device would have answered
                #   fine; disabling just that and leaving auto_find_prompt
                #   on, its fresh per-command prompt probe can instead grab
                #   a trailing fragment of the *previous* (often large,
                #   tabular) command's still-draining output and wait for
                #   that on the *next* command; and disabling both in favor
                #   of the one stable prompt captured at connect time still
                #   risks that same prompt string coincidentally matching
                #   partway through a large table's own contents, cutting a
                #   command's output off early and leaving the rest to be
                #   swept up by the *next* command's read - the exact "each
                #   command's output is actually the previous command's"
                #   symptom that combination produced.
                #
                # send_command_timing() sidesteps all of it - it just waits
                # for the channel to go quiet for a couple seconds,
                # regardless of what's been said or what the prompt looks
                # like - at the cost of not being able to tell a genuinely
                # slow command apart from one that's already finished, which
                # is why well-behaved network-OS drivers still use the
                # normal pattern-based read below instead.
                use_timing_read = spec.netmiko_driver == "generic_termserver" or spec.category == "wlc"
                outputs = []
                for command in commands:
                    outputs.append(f"! ---- {command} ----")
                    _emit(f"\n$ {command}\n")
                    # A handful of these (show tech-support, show run on a
                    # large config, etc.) can routinely take minutes on real
                    # hardware, well past a 60s timeout.
                    if use_timing_read:
                        output = conn.send_command_timing(command, last_read=2, read_timeout=300)
                    else:
                        output = conn.send_command(command, read_timeout=300)
                    outputs.append(output)
                    _emit(output + "\n")
                return "\n".join(outputs)
            except EnableModeError:
                raise
            except Exception as exc:  # noqa: BLE001 - reported as a job failure, not a crash
                raise CommandExecutionError(
                    f"Authenticated to {host}:{port} but failed while running commands: {exc}"
                ) from exc
    except CommandExecutionError:
        raise
    except NetmikoAuthenticationException as exc:
        # The device actively rejected the login - a real credentials
        # problem, not a network one.
        raise AuthenticationError(
            f"Authentication was rejected for {host}:{port}: {exc}. Check the credential's "
            "username/password, and for passcode-based MFA confirm a fresh one-time code was "
            "supplied for this run."
        ) from exc
    except NetmikoTimeoutException as exc:
        # Netmiko labels this exception class "Timeout", but it actually
        # wraps *any* low-level SSH failure that isn't a straightforward
        # credential rejection - a real connect timeout, but also things
        # like an SSH key-exchange/cipher mismatch with an old device's
        # SSH implementation. Always show the underlying message (str(exc))
        # rather than just the exception's class name, since that's what
        # actually distinguishes "unreachable" from "reachable but the SSH
        # negotiation itself failed" - two very different fixes.
        raise AuthenticationError(
            f"Could not establish an SSH session with {host}:{port}: {exc}. If this looks like a "
            "plain connect timeout, the device is most likely unreachable (wrong host/IP, DNS, "
            "firewall, VPN, or port 22 not open) rather than a credentials problem - raising "
            "auth_timeout_seconds won't help that. If the message above instead mentions a cipher, "
            "key exchange, or other SSH negotiation detail, the device's SSH implementation is too "
            "old/restrictive for this app's current settings."
        ) from exc
    except CollectionError:
        raise
    except Exception as exc:  # noqa: BLE001 - any other failure during connect is an auth-phase failure
        raise AuthenticationError(f"Failed to connect/authenticate to {host}:{port}: {exc}") from exc
