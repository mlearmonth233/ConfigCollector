"""End-to-end coverage of services/scp_server.py against a real SSH/SCP
client (paramiko playing the "sink" side of the protocol) - not mocked,
since the whole point of this module is correctly speaking a real, external
wire protocol that a Cisco device will also speak. Requires a reachable
Redis (see services/scp_grants.py) - skipped automatically if one isn't
running, same as this app's real async job queue mode already needs one.
"""

import hashlib
import os
import threading

import paramiko
import pytest
import redis as redis_lib

from app.config import get_settings
from app.services.scp_grants import create_grant
from app.services.scp_server import _load_or_generate_host_key, accept_loop, bind_listener


def _redis_available() -> bool:
    try:
        redis_lib.Redis.from_url(get_settings().redis_url, socket_connect_timeout=1).ping()
        return True
    except Exception:  # noqa: BLE001
        return False


pytestmark = pytest.mark.skipif(not _redis_available(), reason="Redis is required for SCP grant storage")


@pytest.fixture
def scp_server(tmp_path):
    host_key = _load_or_generate_host_key(str(tmp_path / "hostkey"))
    listener = bind_listener("127.0.0.1", 0)
    port = listener.getsockname()[1]
    thread = threading.Thread(target=accept_loop, args=(listener, host_key), daemon=True)
    thread.start()
    yield port
    listener.close()


def _recv_line(channel: paramiko.Channel) -> bytes:
    buf = bytearray()
    while True:
        b = channel.recv(1)
        if not b:
            raise EOFError("channel closed before a full line was received")
        if b == b"\n":
            break
        buf += b
    return bytes(buf)


def _download_via_scp(port: int, username: str, password: str, remote_filename: str) -> tuple[bytes, int]:
    """Plays the "sink" (receiving) half of the classic SCP protocol against
    our server's "source" (sending) half - see scp_server.py's module
    docstring for the protocol reference."""
    transport = paramiko.Transport(("127.0.0.1", port))
    try:
        transport.connect(username=username, password=password)
        channel = transport.open_session()
        channel.exec_command(f"scp -f {remote_filename}")

        header = _recv_line(channel)
        assert header[0:1] == b"C"
        _mode, size_str, name = header[1:].split(b" ", 2)
        size = int(size_str)
        assert name.decode() == remote_filename

        channel.sendall(b"\x00")  # ack: go ahead and send the file
        received = bytearray()
        while len(received) < size:
            chunk = channel.recv(min(65536, size - len(received)))
            if not chunk:
                raise EOFError("channel closed mid-transfer")
            received += chunk
        channel.recv(1)  # final zero-byte from the source
        channel.sendall(b"\x00")  # final ack
        exit_status = channel.recv_exit_status()
        try:
            channel.close()
        except EOFError:
            pass  # server already closed its side once the exec finished
        return bytes(received), exit_status
    finally:
        transport.close()


def test_serves_exact_file_content(scp_server, tmp_path):
    content = os.urandom(1024 * 1024 + 77)  # deliberately not a round chunk multiple
    source = tmp_path / "image.bin"
    source.write_bytes(content)

    grant = create_grant(file_path=str(source), remote_filename="cat9k.bin", ttl_seconds=30)
    received, exit_status = _download_via_scp(scp_server, grant.username, grant.password, "cat9k.bin")

    assert exit_status == 0
    assert received == content
    assert hashlib.md5(received).hexdigest() == hashlib.md5(content).hexdigest()


def test_grant_is_single_use(scp_server, tmp_path):
    source = tmp_path / "image.bin"
    source.write_bytes(b"firmware-bytes")
    grant = create_grant(file_path=str(source), remote_filename="img.bin", ttl_seconds=30)

    _download_via_scp(scp_server, grant.username, grant.password, "img.bin")

    with pytest.raises(paramiko.AuthenticationException):
        transport = paramiko.Transport(("127.0.0.1", scp_server))
        try:
            transport.connect(username=grant.username, password=grant.password)
        finally:
            transport.close()


def test_wrong_password_is_rejected(scp_server, tmp_path):
    source = tmp_path / "image.bin"
    source.write_bytes(b"firmware-bytes")
    grant = create_grant(file_path=str(source), remote_filename="img.bin", ttl_seconds=30)

    with pytest.raises(paramiko.AuthenticationException):
        transport = paramiko.Transport(("127.0.0.1", scp_server))
        try:
            transport.connect(username=grant.username, password="wrong-password")
        finally:
            transport.close()


def test_mismatched_filename_is_refused(scp_server, tmp_path):
    """A device is only ever granted the one specific file its job actually
    targets - asking for anything else (even a name that also exists on
    disk) must be refused outright, not silently substituted."""
    source = tmp_path / "image.bin"
    source.write_bytes(b"firmware-bytes")
    grant = create_grant(file_path=str(source), remote_filename="expected.bin", ttl_seconds=30)

    transport = paramiko.Transport(("127.0.0.1", scp_server))
    try:
        transport.connect(username=grant.username, password=grant.password)
        channel = transport.open_session()
        with pytest.raises(paramiko.SSHException):
            channel.exec_command("scp -f some-other-file.bin")
    finally:
        transport.close()


def test_upload_attempt_is_refused(scp_server, tmp_path):
    """This server only ever sends files (the "-f"/source role) - it must
    never accept "-t" (a device trying to upload/overwrite something)."""
    source = tmp_path / "image.bin"
    source.write_bytes(b"firmware-bytes")
    grant = create_grant(file_path=str(source), remote_filename="img.bin", ttl_seconds=30)

    transport = paramiko.Transport(("127.0.0.1", scp_server))
    try:
        transport.connect(username=grant.username, password=grant.password)
        channel = transport.open_session()
        with pytest.raises(paramiko.SSHException):
            channel.exec_command("scp -t img.bin")
    finally:
        transport.close()
