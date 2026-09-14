"""A minimal, single-purpose SCP server: it authenticates a connection
against a short-lived grant (see scp_grants.py) and, if the SSH client then
requests exactly the file that grant names, streams that one file out and
closes the connection. Nothing else - no shell, no SFTP subsystem, no
directory listing, no arbitrary path access, and no ability to *receive* a
file (a device attempting `scp -t ...`, i.e. an upload, is refused).

This plays the "source" role of the classic SCP protocol (the same role
OpenSSH's own server plays when a client runs `scp remote:file local`) -
Cisco IOS/IOS-XE's `copy scp://user@host/path flash:` is exactly that kind
of download, connecting out to us as the SCP client and expecting us, the
server, to send the file. The protocol itself is tiny and undocumented by
any RFC, but stable and unchanged for decades; see OpenSSH's scp.c
(source()/sink()/response()) for the canonical implementation this mirrors.

Runs as its own long-lived process (app/scp_server_main.py) separate from
the FastAPI app and the Celery worker - see docker-compose.yml's
`scp-server` service.
"""

from __future__ import annotations

import hmac
import os
import posixpath
import shlex
import socket
import threading
from dataclasses import dataclass

import paramiko

from app.services.scp_grants import ScpGrant, invalidate_grant, peek_grant

# How long to wait for a connected client to actually authenticate and
# request a channel/exec before giving up on it - generous for a slow WAN
# link's SSH handshake, but bounded so a connection that never completes
# the handshake can't tie up a thread forever.
_HANDSHAKE_TIMEOUT_SECONDS = 30
# Generous per-read/write timeout on the data channel itself once a transfer
# is underway - a large firmware image over a slow link can legitimately
# take minutes, but a channel that goes silent mid-transfer should still
# eventually be reclaimed.
_TRANSFER_TIMEOUT_SECONDS = 600
_CHUNK_SIZE = 256 * 1024


@dataclass(frozen=True)
class _ParsedScpCommand:
    mode: str  # "from" (server sends the file - the only mode this server supports) or "to"
    path: str


def _parse_scp_command(command: str) -> _ParsedScpCommand | None:
    try:
        tokens = shlex.split(command)
    except ValueError:
        return None
    if not tokens or tokens[0] != "scp":
        return None
    mode: str | None = None
    path: str | None = None
    for token in tokens[1:]:
        if token.startswith("-"):
            if "f" in token:
                mode = "from"
            if "t" in token:
                mode = "to"
            continue
        path = token  # scp only ever passes a single trailing path argument
    if mode is None or path is None:
        return None
    return _ParsedScpCommand(mode=mode, path=path)


class _GrantAuthServer(paramiko.ServerInterface):
    """One instance per TCP connection. Authentication succeeds only for the
    exact username/password of a still-valid grant; once authenticated, the
    only exec command accepted is a download ("scp -f ...") of that same
    grant's file - anything else (a shell, an upload, a mismatched path) is
    refused."""

    def __init__(self) -> None:
        self.granted: ScpGrant | None = None
        self.requested_path: str | None = None
        self.command_ready = threading.Event()

    def check_channel_request(self, kind: str, chanid: int) -> int:
        if kind == "session":
            return paramiko.OPEN_SUCCEEDED
        return paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED

    def get_allowed_auths(self, username: str) -> str:
        return "password"

    def check_auth_none(self, username: str) -> int:
        return paramiko.AUTH_FAILED

    def check_auth_password(self, username: str, password: str) -> int:
        grant = peek_grant(username)
        if grant is not None and _constant_time_eq(grant.password, password):
            self.granted = grant
            return paramiko.AUTH_SUCCESSFUL
        return paramiko.AUTH_FAILED

    def check_channel_exec_request(self, channel: paramiko.Channel, command: bytes) -> bool:
        if self.granted is None:
            return False
        parsed = _parse_scp_command(command.decode("utf-8", errors="replace"))
        if parsed is None or parsed.mode != "from":
            return False
        if posixpath.basename(parsed.path) != self.granted.remote_filename:
            return False
        self.requested_path = parsed.path
        self.command_ready.set()
        return True

    def check_channel_shell_request(self, channel: paramiko.Channel) -> bool:
        return False

    def check_channel_subsystem_request(self, channel: paramiko.Channel, name: str) -> bool:
        return False


def _constant_time_eq(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())


def _read_scp_ack(channel: paramiko.Channel) -> int:
    """Reads one SCP protocol acknowledgement byte: 0 = ok, 1 = warning,
    2 = fatal error (both followed by a human-readable message up to the
    next newline, which is read and discarded here - there's no one to show
    it to on this side). Returns -1 if the channel closed early."""
    first = channel.recv(1)
    if not first:
        return -1
    code = first[0]
    if code in (1, 2):
        while True:
            b = channel.recv(1)
            if not b or b == b"\n":
                break
    return code


def _send_scp_error(channel: paramiko.Channel, message: str) -> None:
    channel.sendall(b"\x02" + message.encode() + b"\n")


def _serve_file(channel: paramiko.Channel, grant: ScpGrant) -> None:
    channel.settimeout(_TRANSFER_TIMEOUT_SECONDS)
    try:
        file_stat = os.stat(grant.file_path)
    except OSError as exc:
        _send_scp_error(channel, f"cannot access file: {exc}")
        channel.send_exit_status(1)
        return

    header = f"C0644 {file_stat.st_size} {grant.remote_filename}\n".encode()
    channel.sendall(header)
    if _read_scp_ack(channel) != 0:
        # The far end rejected the header (or the connection dropped) -
        # nothing more to do.
        return

    try:
        with open(grant.file_path, "rb") as f:
            remaining = file_stat.st_size
            while remaining > 0:
                chunk = f.read(min(_CHUNK_SIZE, remaining))
                if not chunk:
                    break
                channel.sendall(chunk)
                remaining -= len(chunk)
    except OSError as exc:
        _send_scp_error(channel, f"error reading file: {exc}")
        channel.send_exit_status(1)
        return

    channel.sendall(b"\x00")
    _read_scp_ack(channel)
    channel.send_exit_status(0)


def _load_or_generate_host_key(path: str) -> paramiko.RSAKey:
    """Persists the server's identity across restarts (generating one on
    first run) so devices/administrators aren't shown a different host key
    every time the process restarts."""
    if os.path.exists(path):
        return paramiko.RSAKey(filename=path)
    key = paramiko.RSAKey.generate(2048)
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    key.write_private_key_file(path)
    return key


def _handle_connection(client_sock: socket.socket, host_key: paramiko.RSAKey) -> None:
    transport = paramiko.Transport(client_sock)
    transport.add_server_key(host_key)
    server = _GrantAuthServer()
    try:
        transport.start_server(server=server)
    except paramiko.SSHException:
        transport.close()
        return

    channel = None
    try:
        channel = transport.accept(_HANDSHAKE_TIMEOUT_SECONDS)
        if channel is None:
            return
        if not server.command_ready.wait(_HANDSHAKE_TIMEOUT_SECONDS) or server.granted is None:
            return
        _serve_file(channel, server.granted)
    finally:
        if server.granted is not None:
            # One grant is good for exactly one exec attempt, whatever its
            # outcome - a second connection reusing the same username/
            # password must never succeed, even if this transfer failed
            # partway through (the job item itself is what a user retries).
            invalidate_grant(server.granted.username)
        if channel is not None:
            try:
                channel.close()
            except EOFError:
                pass  # the far end (or our own transport.close() below) may have already torn this down
        transport.close()


def bind_listener(bind_host: str, port: int) -> socket.socket:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind((bind_host, port))
    listener.listen(100)
    return listener


def accept_loop(listener: socket.socket, host_key: paramiko.RSAKey) -> None:
    """Accepts connections on an already-bound/listening socket forever, one
    daemon thread per connection - split out from serve_forever() so tests
    can bind an ephemeral port (port=0) and drive this loop directly instead
    of needing a fixed, collision-prone port."""
    while True:
        client_sock, addr = listener.accept()
        threading.Thread(
            target=_handle_connection, args=(client_sock, host_key), daemon=True, name=f"scp-conn-{addr}"
        ).start()


def serve_forever(*, bind_host: str, port: int, host_key_path: str) -> None:
    host_key = _load_or_generate_host_key(host_key_path)
    listener = bind_listener(bind_host, port)
    print(f"[scp-server] listening on {bind_host}:{port}")
    try:
        accept_loop(listener, host_key)
    finally:
        listener.close()
