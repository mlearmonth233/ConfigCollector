"""Interactive terminal to a device, bridged over a WebSocket.

The browser runs xterm.js; this endpoint opens a real session to the
chosen device and shuttles bytes both ways. Two paths:

- management (default): an SSH shell to the device's own address with the
  same credential the collector would use (the device's own, or the org
  default - including the keyboard-interactive fallback and passcode-MFA
  handling).
- console (`?via=console`): out-of-band, through the device's configured
  console path (see Device.console_*). Over SSH that is a shell on the
  console server, optionally followed by a connect command typed for you
  ("connect line 3"); over telnet it is a reverse-telnet socket straight
  onto the serial line, with real BREAK support.

Auth for the socket itself is the normal JWT, passed as a `token` query
parameter since a browser's WebSocket API can't set an Authorization
header.

Wire protocol: the server sends raw terminal output as binary frames; the
client sends JSON text frames - {"type": "input", "data": "..."} for
keystrokes, {"type": "resize", "cols": N, "rows": N} when the terminal is
resized, and {"type": "break"} to send a serial BREAK.
"""

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass
from uuid import UUID

import paramiko
from fastapi import APIRouter, WebSocket
from paramiko.common import cMSG_CHANNEL_REQUEST
from paramiko.message import Message
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.encryption import SecretUndecryptable, decrypt_secret
from app.core.security import decode_access_token
from app.database import async_session_factory
from app.models.credential import Credential, MfaMode
from app.models.device import Device
from app.models.user import User
from app.services.collector import _SSHClientWithKeyboardInteractiveFallback
from app.services.telnet import TelnetChannel, open_telnet

router = APIRouter(tags=["terminal"])
log = logging.getLogger(__name__)

# How often the output pump checks the channel when it has nothing to
# read. Neither paramiko's channel nor a raw socket has an awaitable
# "readable" signal, so this is a short poll - 20ms is well under what
# anyone perceives as lag.
_OUTPUT_POLL_SECONDS = 0.02
_RECV_CHUNK = 65536


@dataclass(frozen=True)
class ConnectionParams:
    host: str
    port: int
    username: str
    password: str
    timeout: int
    protocol: str = "ssh"  # "ssh" | "telnet"
    # Typed into the shell once it is up (console servers: "connect line 3").
    connect_command: str | None = None
    via: str = "management"


@dataclass(frozen=True)
class AuthorizationError:
    message: str


async def authorize(token: str | None, device_id: UUID, otp: str | None, via: str = "management") -> ConnectionParams | AuthorizationError:
    """Validates the caller's JWT, that the device belongs to their org, and
    resolves where to connect and with what. Returns an error rather than
    raising so the caller can report it into the terminal itself - by the
    time it runs, the WebSocket is already accepted, and a bare close code
    is a terrible way to learn your OTP was missing."""
    payload = decode_access_token(token) if token else None
    if payload is None or "sub" not in payload:
        return AuthorizationError("Not authenticated - log in again.")
    if via not in ("management", "console"):
        return AuthorizationError(f"Unknown connection path '{via}'.")

    async with async_session_factory() as db:
        user = await db.get(User, UUID(payload["sub"]))
        if user is None:
            return AuthorizationError("Not authenticated - log in again.")

        device = await db.scalar(
            select(Device)
            .options(selectinload(Device.credential), selectinload(Device.console_credential))
            .where(Device.id == device_id, Device.org_id == user.org_id)
        )
        if device is None:
            return AuthorizationError("Device not found.")

        if via == "console":
            if not device.console_host:
                return AuthorizationError(
                    f"{device.name} has no console path. Edit the device and fill in 'Console access' "
                    "(the console server's address and this device's port) to connect out of band."
                )
            if device.console_protocol == "telnet":
                # Reverse telnet drops straight onto the serial line - the
                # device's own console prompt asks for whatever it asks for.
                return ConnectionParams(
                    host=device.console_host,
                    port=device.console_port or 23,
                    username="",
                    password="",
                    timeout=10,
                    protocol="telnet",
                    connect_command=device.console_connect_command,
                    via="console",
                )
            credential = device.console_credential or device.credential
            target_host, target_port = device.console_host, device.console_port or 22
            what = f"console server {device.console_host}"
        else:
            credential = device.credential
            target_host, target_port = device.host, device.port
            what = "this device"

        if credential is None:
            credential = await db.scalar(
                select(Credential).where(Credential.org_id == user.org_id, Credential.is_default.is_(True))
            )
        if credential is None:
            return AuthorizationError(
                f"No credential for {what}: none assigned and no org-wide default credential is configured."
            )

        try:
            password = decrypt_secret(credential.encrypted_password)
        except SecretUndecryptable as exc:
            return AuthorizationError(f"Credential '{credential.name}': {exc}")
        if credential.mfa_mode == MfaMode.PASSCODE:
            if not otp:
                return AuthorizationError(f"Credential '{credential.name}' needs a one-time passcode.")
            password = f"{password}{credential.otp_delimiter}{otp}"

        return ConnectionParams(
            host=target_host,
            port=target_port,
            username=credential.username,
            password=password,
            timeout=credential.auth_timeout_seconds,
            protocol="ssh",
            connect_command=device.console_connect_command if via == "console" else None,
            via=via,
        )


def open_shell(params: ConnectionParams, cols: int, rows: int) -> tuple[paramiko.SSHClient, paramiko.Channel]:
    """Blocking: connects and requests an interactive PTY. Uses the same
    SSHClient subclass the collector does, so a device that only accepts
    keyboard-interactive auth (see collector.py) works here too."""
    client = _SSHClientWithKeyboardInteractiveFallback()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        hostname=params.host,
        port=params.port,
        username=params.username,
        password=params.password,
        look_for_keys=False,
        allow_agent=False,
        timeout=params.timeout,
        banner_timeout=params.timeout,
        auth_timeout=params.timeout,
    )
    channel = client.invoke_shell(term="xterm-256color", width=cols, height=rows)
    return client, channel


def send_break(channel) -> bool:
    """A serial BREAK on whichever channel this is: telnet's IAC BRK, or the
    SSH 'break' channel request (RFC 4335) that console servers translate
    onto the serial line. Returns False if the channel can't do it."""
    if isinstance(channel, TelnetChannel) or hasattr(channel, "send_break"):
        channel.send_break()
        return True
    transport = getattr(channel, "transport", None)
    remote_id = getattr(channel, "remote_chanid", None)
    if transport is None or remote_id is None:
        return False
    try:
        m = Message()
        m.add_byte(cMSG_CHANNEL_REQUEST)
        m.add_int(remote_id)
        m.add_string("break")
        m.add_boolean(False)
        m.add_int(500)  # break length in ms
        transport._send_user_message(m)  # noqa: SLF001 - paramiko has no public break API
        return True
    except Exception:  # noqa: BLE001
        log.warning("SSH break request failed", exc_info=True)
        return False


def handle_client_message(channel, text: str) -> None:
    """One JSON frame from the browser -> the channel. Anything malformed
    is ignored rather than tearing the session down over it."""
    try:
        message = json.loads(text)
    except ValueError:
        return
    if not isinstance(message, dict):
        return
    kind = message.get("type")
    if kind == "input":
        data = message.get("data")
        if isinstance(data, str) and data:
            channel.sendall(data.encode("utf-8"))
    elif kind == "resize":
        cols, rows = message.get("cols"), message.get("rows")
        if isinstance(cols, int) and isinstance(rows, int) and cols > 0 and rows > 0:
            channel.resize_pty(width=cols, height=rows)
    elif kind == "break":
        send_break(channel)


async def bridge(websocket, channel) -> None:
    """Pumps channel output to the socket and socket input to the channel
    until either side goes away. `websocket` only needs receive()/
    send_bytes(), and `channel` only recv_ready()/recv()/closed/
    exit_status_ready()/sendall()/resize_pty() - so both can be stand-ins
    in tests."""

    async def pump_output() -> None:
        while True:
            if channel.recv_ready():
                data = channel.recv(_RECV_CHUNK)
                if not data:
                    if channel.closed:
                        return
                    await asyncio.sleep(_OUTPUT_POLL_SECONDS)
                    continue
                await websocket.send_bytes(data)
                continue
            if channel.closed or channel.exit_status_ready():
                return
            await asyncio.sleep(_OUTPUT_POLL_SECONDS)

    async def pump_input() -> None:
        while True:
            message = await websocket.receive()
            if message.get("type") == "websocket.disconnect":
                return
            text = message.get("text")
            if text is not None:
                handle_client_message(channel, text)
            elif message.get("bytes"):
                channel.sendall(message["bytes"])

    tasks = [asyncio.create_task(pump_output()), asyncio.create_task(pump_input())]
    try:
        _done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
    finally:
        for task in tasks:
            task.cancel()


def _int_param(raw: str | None, default: int) -> int:
    try:
        value = int(raw) if raw is not None else default
    except ValueError:
        return default
    return value if 0 < value < 1000 else default


async def _send_notice(websocket: WebSocket, text: str, *, error: bool = False) -> None:
    color = "\x1b[31m" if error else "\x1b[90m"
    try:
        await websocket.send_bytes(f"\r\n{color}{text}\x1b[0m\r\n".encode("utf-8"))
    except Exception:  # noqa: BLE001 - the socket may already be gone; nothing left to tell anyone
        pass


@router.websocket("/api/terminal/{device_id}")
async def device_terminal(websocket: WebSocket, device_id: UUID) -> None:
    params = websocket.query_params
    cols = _int_param(params.get("cols"), 120)
    rows = _int_param(params.get("rows"), 32)
    via = params.get("via") or "management"

    # Accept first so any failure below can be explained inside the
    # terminal - a handshake rejection reaches the browser as a bare
    # connection error with no message at all.
    await websocket.accept()

    try:
        auth = await authorize(params.get("token"), device_id, params.get("otp") or None, via)
    except Exception as exc:  # noqa: BLE001 - explained in the terminal, with a reference into the log
        ref = uuid.uuid4().hex[:8]
        log.exception("Terminal session to device %s could not start (ref=%s)", device_id, ref)
        await _send_notice(websocket, f"Could not start the session: {exc} (ref {ref} in packrat-api.log)", error=True)
        await websocket.close(code=1011)
        return
    if isinstance(auth, AuthorizationError):
        await _send_notice(websocket, auth.message, error=True)
        await websocket.close(code=1008)
        return

    client = None
    if auth.protocol == "telnet":
        await _send_notice(websocket, f"Opening console via telnet to {auth.host}:{auth.port}...")
        try:
            channel = await asyncio.to_thread(open_telnet, auth.host, auth.port, cols, rows)
        except Exception as exc:  # noqa: BLE001
            await _send_notice(websocket, f"Connection failed: {exc}", error=True)
            await websocket.close(code=1011)
            return
        await _send_notice(websocket, "Connected to the serial line. Press Enter to wake the console; use Send break for ROMMON.")
    else:
        where = f"console server {auth.host}:{auth.port}" if auth.via == "console" else f"{auth.host}:{auth.port}"
        await _send_notice(websocket, f"Connecting to {where} as {auth.username}...")
        try:
            client, channel = await asyncio.to_thread(open_shell, auth, cols, rows)
        except Exception as exc:  # noqa: BLE001 - any connect/auth failure is reported to the user, not raised
            await _send_notice(websocket, f"Connection failed: {exc}", error=True)
            await websocket.close(code=1011)
            return
        if auth.connect_command:
            # Give the console server a moment to print its prompt, then type
            # the connect command as the user would.
            await asyncio.sleep(0.8)
            await _send_notice(websocket, f"Sending '{auth.connect_command}'...")
            channel.sendall((auth.connect_command + "\r").encode("utf-8"))

    log.info("Terminal session opened to device %s via %s (%s %s:%s)", device_id, auth.via, auth.protocol, auth.host, auth.port)
    try:
        await bridge(websocket, channel)
    finally:
        try:
            channel.close()
        finally:
            if client is not None:
                client.close()
        log.info("Terminal session to device %s closed", device_id)
        try:
            await websocket.close()
        except Exception:  # noqa: BLE001 - already closed by the client is the normal case here
            pass
