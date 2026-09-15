"""Interactive SSH terminal to a device, bridged over a WebSocket.

The browser runs xterm.js; this endpoint opens a real SSH shell to the
chosen device with the same credential the collector would use (the
device's own, or the org default - including the keyboard-interactive
fallback and passcode-MFA handling) and shuttles bytes both ways. Auth for
the socket itself is the normal JWT, passed as a `token` query parameter
since a browser's WebSocket API can't set an Authorization header.

Wire protocol: the server sends raw terminal output as binary frames; the
client sends JSON text frames - {"type": "input", "data": "..."} for
keystrokes and {"type": "resize", "cols": N, "rows": N} when the terminal
is resized.
"""

import asyncio
import json
from dataclasses import dataclass
from uuid import UUID

import paramiko
from fastapi import APIRouter, WebSocket
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.encryption import decrypt_secret
from app.core.security import decode_access_token
from app.database import async_session_factory
from app.models.credential import Credential, MfaMode
from app.models.device import Device
from app.models.user import User
from app.services.collector import _SSHClientWithKeyboardInteractiveFallback

router = APIRouter(tags=["terminal"])

# How often the output pump checks the SSH channel when it has nothing to
# read. Paramiko's channel has no awaitable "readable" signal, so this is a
# short poll - 20ms is well under what anyone perceives as lag.
_OUTPUT_POLL_SECONDS = 0.02
_RECV_CHUNK = 65536


@dataclass(frozen=True)
class ConnectionParams:
    host: str
    port: int
    username: str
    password: str
    timeout: int


@dataclass(frozen=True)
class AuthorizationError:
    message: str


async def authorize(token: str | None, device_id: UUID, otp: str | None) -> ConnectionParams | AuthorizationError:
    """Validates the caller's JWT, that the device belongs to their org, and
    resolves which credential (and, for passcode MFA, which one-time code)
    the shell should log in with. Returns an error rather than raising so
    the caller can report it into the terminal itself - by the time it
    runs, the WebSocket is already accepted, and a bare close code is a
    terrible way to learn your OTP was missing."""
    payload = decode_access_token(token) if token else None
    if payload is None or "sub" not in payload:
        return AuthorizationError("Not authenticated - log in again.")

    async with async_session_factory() as db:
        user = await db.get(User, UUID(payload["sub"]))
        if user is None:
            return AuthorizationError("Not authenticated - log in again.")

        device = await db.scalar(
            select(Device)
            .options(selectinload(Device.credential))
            .where(Device.id == device_id, Device.org_id == user.org_id)
        )
        if device is None:
            return AuthorizationError("Device not found.")

        credential = device.credential
        if credential is None:
            credential = await db.scalar(
                select(Credential).where(Credential.org_id == user.org_id, Credential.is_default.is_(True))
            )
        if credential is None:
            return AuthorizationError(
                "This device has no credential assigned and no org-wide default credential is configured."
            )

        password = decrypt_secret(credential.encrypted_password)
        if credential.mfa_mode == MfaMode.PASSCODE:
            if not otp:
                return AuthorizationError(f"Credential '{credential.name}' needs a one-time passcode.")
            password = f"{password}{credential.otp_delimiter}{otp}"

        return ConnectionParams(
            host=device.host,
            port=device.port,
            username=credential.username,
            password=password,
            timeout=credential.auth_timeout_seconds,
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


def handle_client_message(channel, text: str) -> None:
    """One JSON frame from the browser -> the SSH channel. Anything
    malformed is ignored rather than tearing the session down over it."""
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


async def bridge(websocket, channel) -> None:
    """Pumps SSH output to the socket and socket input to SSH until either
    side goes away. `websocket` only needs receive()/send_bytes(), and
    `channel` only recv_ready()/recv()/closed/exit_status_ready()/sendall()/
    resize_pty() - so both can be stand-ins in tests."""

    async def pump_output() -> None:
        while True:
            if channel.recv_ready():
                data = channel.recv(_RECV_CHUNK)
                if not data:
                    return
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

    # Accept first so any failure below can be explained inside the
    # terminal - a handshake rejection reaches the browser as a bare
    # connection error with no message at all.
    await websocket.accept()

    auth = await authorize(params.get("token"), device_id, params.get("otp") or None)
    if isinstance(auth, AuthorizationError):
        await _send_notice(websocket, auth.message, error=True)
        await websocket.close(code=1008)
        return

    await _send_notice(websocket, f"Connecting to {auth.host}:{auth.port} as {auth.username}...")
    try:
        client, channel = await asyncio.to_thread(open_shell, auth, cols, rows)
    except Exception as exc:  # noqa: BLE001 - any connect/auth failure is reported to the user, not raised
        await _send_notice(websocket, f"Connection failed: {exc}", error=True)
        await websocket.close(code=1011)
        return

    try:
        await bridge(websocket, channel)
    finally:
        try:
            channel.close()
        finally:
            client.close()
        try:
            await websocket.close()
        except Exception:  # noqa: BLE001 - already closed by the client is the normal case here
            pass
