"""Ephemeral, single-file file servers used during a firmware push job -
one of TFTP, FTP, or SCP/SFTP, picked per job (FirmwareUpgradeJob.protocol).
Each one:

- serves exactly the one firmware image a job is pushing out, nothing else
  on disk
- is bound only to the specific network interface address the user picked
  for that job (see app/services/network_interfaces.py), never 0.0.0.0 -
  so it's never reachable from a segment the job wasn't meant to touch
- is torn down deliberately when the job's `serve_file` context exits,
  bounded regardless by settings.firmware_transfer_timeout_seconds
- accepts any credentials (or none): these are short-lived, single-file,
  one-job-at-a-time servers, not standing multi-tenant services, so there's
  no real per-device credential to get right here - the security boundary
  is "which interface it's bound to and how long it runs", not a login.
"""

import asyncio
import os
import shutil
import struct
import tempfile
import threading
from contextlib import asynccontextmanager
from pathlib import Path

import asyncssh
from pyftpdlib.authorizers import DummyAuthorizer
from pyftpdlib.handlers import FTPHandler
from pyftpdlib.servers import FTPServer

from app.config import get_settings
from app.models.firmware import TransferProtocol

settings = get_settings()


def _prepare_serve_dir(file_path: Path) -> Path:
    """A throwaway directory containing just `file_path` - pyftpdlib and
    asyncssh's SFTP server both serve a whole directory rather than one
    arbitrary path, so this gives each a "root" that happens to contain
    nothing but the file a job is meant to push. Hardlinked where possible
    (same volume, no extra disk space) rather than copied; copy is only a
    fallback for filesystems that don't support it."""
    serve_dir = Path(tempfile.mkdtemp(prefix="cc_firmware_"))
    target = serve_dir / file_path.name
    try:
        os.link(file_path, target)
    except OSError:
        shutil.copy2(file_path, target)
    return serve_dir


# ---------------------------------------------------------------------------
# TFTP (RFC 1350) - hand-rolled rather than a dependency, since the subset
# actually needed (serve one file, read-only, over UDP) is small enough to
# get right directly: answer a read request (RRQ) with 512-byte DATA
# blocks, each acknowledged by the client, until a final block shorter than
# 512 bytes (or exactly empty) is ACKed. No write support, no options
# extension (RFC 2347) - a plain vendor "copy tftp:" read doesn't need it.
# ---------------------------------------------------------------------------

_TFTP_OPCODE_RRQ = 1
_TFTP_OPCODE_DATA = 3
_TFTP_OPCODE_ACK = 4
_TFTP_BLOCK_SIZE = 512


class _TftpTransfer(asyncio.DatagramProtocol):
    """One instance per in-progress transfer, bound to its own ephemeral
    UDP port - real TFTP: the initial request goes to the well-known port,
    but the reply (and the rest of the transfer) comes from, and continues
    on, a fresh port per client, so concurrent requests from different
    devices never interleave with each other's block/ACK sequence."""

    def __init__(self, file_path: Path, client_addr: tuple[str, int]):
        self._fh = open(file_path, "rb")
        self._client_addr = client_addr
        self._block = 0
        self._last_chunk_len = 0
        self.transport: asyncio.DatagramTransport | None = None

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport  # type: ignore[assignment]
        self._send_block(1)

    def _send_block(self, block_num: int) -> None:
        self._block = block_num
        self._fh.seek((block_num - 1) * _TFTP_BLOCK_SIZE)
        chunk = self._fh.read(_TFTP_BLOCK_SIZE)
        self._last_chunk_len = len(chunk)
        packet = struct.pack("!HH", _TFTP_OPCODE_DATA, block_num & 0xFFFF) + chunk
        assert self.transport is not None
        self.transport.sendto(packet, self._client_addr)

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        if len(data) < 4:
            return
        opcode, block = struct.unpack("!HH", data[:4])
        if opcode != _TFTP_OPCODE_ACK or block != (self._block & 0xFFFF):
            return
        if self._last_chunk_len < _TFTP_BLOCK_SIZE:
            self._finish()
            return
        self._send_block(self._block + 1)

    def error_received(self, exc: Exception) -> None:
        self._finish()

    def _finish(self) -> None:
        self._fh.close()
        if self.transport is not None:
            self.transport.close()


class _TftpListener(asyncio.DatagramProtocol):
    """Listens on the well-known TFTP port for read requests; each one
    spawns a dedicated _TftpTransfer on its own ephemeral port."""

    def __init__(self, file_path: Path, loop: asyncio.AbstractEventLoop):
        self._file_path = file_path
        self._loop = loop

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        if len(data) < 2 or struct.unpack("!H", data[:2])[0] != _TFTP_OPCODE_RRQ:
            return
        self._loop.create_task(self._start_transfer(addr))

    async def _start_transfer(self, client_addr: tuple[str, int]) -> None:
        await self._loop.create_datagram_endpoint(
            lambda: _TftpTransfer(self._file_path, client_addr),
            local_addr=("0.0.0.0", 0),
        )


@asynccontextmanager
async def _serve_tftp(file_path: Path, host: str):
    loop = asyncio.get_running_loop()
    transport, _protocol = await loop.create_datagram_endpoint(
        lambda: _TftpListener(file_path, loop), local_addr=(host, settings.firmware_tftp_port)
    )
    try:
        yield
    finally:
        transport.close()


# ---------------------------------------------------------------------------
# FTP - via pyftpdlib. Anonymous login only (read-only), which is exactly
# how network gear traditionally pulls firmware from a TFTP/FTP server
# without any configured credential.
# ---------------------------------------------------------------------------


@asynccontextmanager
async def _serve_ftp(file_path: Path, host: str):
    serve_dir = _prepare_serve_dir(file_path)
    try:
        authorizer = DummyAuthorizer()
        authorizer.add_anonymous(str(serve_dir), perm="elr")  # list/read only, no write

        handler = FTPHandler
        handler.authorizer = authorizer
        handler.banner = "ConfigCollector firmware transfer"

        server = FTPServer((host, settings.firmware_ftp_port), handler)
        # pyftpdlib's own event loop is synchronous (not asyncio) - run it
        # in a background thread for the life of this context, and stop it
        # via close_all() (safe to call cross-thread; it unblocks
        # serve_forever() promptly) on the way out.
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield
        finally:
            server.close_all()
            thread.join(timeout=5)
    finally:
        shutil.rmtree(serve_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# SCP/SFTP - via asyncssh, using its built-in allow_scp option so SCP
# requests are served from the same directory-backed SFTP server. No
# authentication is required for any username (see _OpenServer below) - see
# this module's own docstring for why that's an acceptable tradeoff here.
# ---------------------------------------------------------------------------


class _OpenServer(asyncssh.SSHServer):
    def begin_auth(self, username: str) -> bool:
        return False  # no authentication required for any username


@asynccontextmanager
async def _serve_scp(file_path: Path, host: str):
    serve_dir = _prepare_serve_dir(file_path)
    try:
        host_key = asyncssh.generate_private_key("ssh-ed25519")
        server = await asyncssh.create_server(
            _OpenServer,
            host=host,
            port=settings.firmware_scp_port,
            server_host_keys=[host_key],
            sftp_factory=lambda conn: asyncssh.SFTPServer(conn, chroot=str(serve_dir)),
            allow_scp=True,
        )
        try:
            yield
        finally:
            server.close()
            await server.wait_closed()
    finally:
        shutil.rmtree(serve_dir, ignore_errors=True)


@asynccontextmanager
async def serve_file(protocol: TransferProtocol, file_path: Path, host: str):
    """Starts the transfer server matching `protocol`, serving exactly
    `file_path`, bound to `host` (one of this machine's own addresses -
    see network_interfaces.py) on that protocol's configured port. Torn
    down on exiting the context, however it's exited."""
    if protocol == TransferProtocol.TFTP:
        async with _serve_tftp(file_path, host):
            yield
    elif protocol == TransferProtocol.FTP:
        async with _serve_ftp(file_path, host):
            yield
    elif protocol == TransferProtocol.SCP:
        async with _serve_scp(file_path, host):
            yield
    else:
        raise ValueError(f"Unknown transfer protocol: {protocol}")
