"""Out-of-band console access: the per-device console path, the terminal
routing that uses it, and the telnet channel's negotiation and BREAK."""

from uuid import UUID

import pytest
from httpx import AsyncClient

from app.api import terminal
from app.api.terminal import AuthorizationError, ConnectionParams
from app.services.telnet import BRK, DO, DONT, IAC, OPT_ECHO, OPT_NAWS, OPT_SGA, OPT_TTYPE, SB, SE, WILL, WONT, TelnetChannel, TelnetNegotiator

pytestmark = pytest.mark.asyncio


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _register(client: AsyncClient, email: str) -> str:
    resp = await client.post("/api/auth/register", json={"org_name": "OOB Org", "email": email, "password": "password123"})
    assert resp.status_code == 201, resp.text
    return resp.json()["access_token"]


# --- device console path --------------------------------------------------------------


async def test_console_path_defaults_and_clearing(client: AsyncClient, unique_email):
    token = await _register(client, unique_email)
    created = await client.post(
        "/api/devices",
        headers=_auth(token),
        json={"name": "sw1", "host": "10.0.0.1", "device_type": "cisco_ios", "console_host": "  oob-cs1.example  ", "console_connect_command": " connect line 3 "},
    )
    assert created.status_code == 201, created.text
    body = created.json()
    # Host given alone -> SSH on 22, command trimmed.
    assert body["console_host"] == "oob-cs1.example" and body["console_protocol"] == "ssh" and body["console_port"] == 22
    assert body["console_connect_command"] == "connect line 3"

    # Switch to telnet: default port becomes 23 and no credential applies.
    updated = await client.patch(f"/api/devices/{body['id']}", headers=_auth(token), json={"console_protocol": "telnet", "console_port": None})
    assert updated.status_code == 200, updated.text
    assert updated.json()["console_protocol"] == "telnet" and updated.json()["console_port"] == 23

    # Explicit port sticks; unrelated PATCHes leave the console path alone.
    updated = await client.patch(f"/api/devices/{body['id']}", headers=_auth(token), json={"console_port": 2003})
    assert updated.json()["console_port"] == 2003
    renamed = await client.patch(f"/api/devices/{body['id']}", headers=_auth(token), json={"site": "HQ"})
    assert renamed.json()["console_host"] == "oob-cs1.example" and renamed.json()["console_port"] == 2003

    # clear_console removes the whole path.
    cleared = await client.patch(f"/api/devices/{body['id']}", headers=_auth(token), json={"clear_console": True})
    assert cleared.json()["console_host"] is None and cleared.json()["console_port"] is None and cleared.json()["console_protocol"] is None

    # Bad protocol is rejected; a foreign credential is rejected.
    bad = await client.patch(f"/api/devices/{body['id']}", headers=_auth(token), json={"console_host": "cs", "console_protocol": "serial"})
    assert bad.status_code == 422
    other = await _register(client, f"other-{unique_email}")
    cred = await client.post("/api/credentials", headers=_auth(other), json={"name": "theirs", "username": "u", "password": "p"})
    foreign = await client.patch(f"/api/devices/{body['id']}", headers=_auth(token), json={"console_host": "cs", "console_credential_id": cred.json()["id"]})
    assert foreign.status_code == 400 and "Console credential" in foreign.json()["detail"]


async def test_monitor_overview_flags_devices_with_a_console(client: AsyncClient, unique_email):
    token = await _register(client, unique_email)
    await client.post("/api/devices", headers=_auth(token), json={"name": "with", "host": "192.0.2.1", "device_type": "cisco_ios", "console_host": "cs", "console_port": 3001})
    await client.post("/api/devices", headers=_auth(token), json={"name": "without", "host": "192.0.2.2", "device_type": "cisco_ios"})
    overview = (await client.get("/api/ping/overview", headers=_auth(token))).json()
    flags = {d["name"]: d["console"] for d in overview["devices"]}
    assert flags == {"with": True, "without": False}


# --- terminal routing -------------------------------------------------------------------


async def test_authorize_via_console_over_ssh_uses_console_credential_and_command(client: AsyncClient, unique_email):
    token = await _register(client, unique_email)
    default = await client.post("/api/credentials", headers=_auth(token), json={"name": "tacacs", "username": "netops", "password": "pw1"})
    cs_cred = await client.post("/api/credentials", headers=_auth(token), json={"name": "console server", "username": "oob", "password": "pw2"})
    assert default.status_code == 201 and cs_cred.status_code == 201
    device = await client.post(
        "/api/devices",
        headers=_auth(token),
        json={
            "name": "core", "host": "10.0.0.1", "device_type": "cisco_ios",
            "console_host": "10.99.0.5", "console_port": 22, "console_protocol": "ssh",
            "console_credential_id": cs_cred.json()["id"], "console_connect_command": "connect line 3",
        },
    )
    device_id = UUID(device.json()["id"])

    mgmt = await terminal.authorize(token, device_id, None)
    assert isinstance(mgmt, ConnectionParams)
    assert (mgmt.host, mgmt.port, mgmt.username, mgmt.via, mgmt.connect_command) == ("10.0.0.1", 22, "netops", "management", None)

    console = await terminal.authorize(token, device_id, None, via="console")
    assert isinstance(console, ConnectionParams)
    assert (console.host, console.port, console.protocol) == ("10.99.0.5", 22, "ssh")
    assert (console.username, console.password) == ("oob", "pw2")
    assert console.connect_command == "connect line 3" and console.via == "console"


async def test_authorize_via_console_falls_back_to_device_credential(client: AsyncClient, unique_email):
    token = await _register(client, unique_email)
    await client.post("/api/credentials", headers=_auth(token), json={"name": "tacacs", "username": "netops", "password": "pw1"})
    device = await client.post("/api/devices", headers=_auth(token), json={"name": "core", "host": "10.0.0.1", "device_type": "cisco_ios", "console_host": "cs.example", "console_port": 3001})
    result = await terminal.authorize(token, UUID(device.json()["id"]), None, via="console")
    assert isinstance(result, ConnectionParams)
    assert result.username == "netops" and result.port == 3001


async def test_authorize_via_console_telnet_needs_no_credential(client: AsyncClient, unique_email):
    token = await _register(client, unique_email)
    device = await client.post("/api/devices", headers=_auth(token), json={"name": "core", "host": "10.0.0.1", "device_type": "cisco_ios", "console_host": "cs.example", "console_port": 2003, "console_protocol": "telnet"})
    result = await terminal.authorize(token, UUID(device.json()["id"]), None, via="console")
    assert isinstance(result, ConnectionParams)
    assert (result.protocol, result.host, result.port, result.username) == ("telnet", "cs.example", 2003, "")


async def test_authorize_via_console_explains_a_missing_path_and_unknown_via(client: AsyncClient, unique_email):
    token = await _register(client, unique_email)
    device = await client.post("/api/devices", headers=_auth(token), json={"name": "core", "host": "10.0.0.1", "device_type": "cisco_ios"})
    missing = await terminal.authorize(token, UUID(device.json()["id"]), None, via="console")
    assert isinstance(missing, AuthorizationError) and "no console path" in missing.message
    unknown = await terminal.authorize(token, UUID(device.json()["id"]), None, via="carrier-pigeon")
    assert isinstance(unknown, AuthorizationError)


# --- telnet ---------------------------------------------------------------------------


def test_negotiator_strips_iac_and_answers_options():
    neg = TelnetNegotiator(cols=100, rows=40)
    data, reply = neg.feed(bytes([IAC, WILL, OPT_ECHO, IAC, WILL, OPT_SGA, IAC, WILL, 99]) + b"Router>" + bytes([IAC, IAC]) + b"!")
    assert data == b"Router>\xff!"
    assert reply == bytes([IAC, DO, OPT_ECHO, IAC, DO, OPT_SGA, IAC, DONT, 99])

    # Server asks us to provide window size and terminal type.
    data, reply = neg.feed(bytes([IAC, DO, OPT_NAWS, IAC, DO, OPT_TTYPE, IAC, DO, 5]))
    assert data == b""
    assert reply.startswith(bytes([IAC, WILL, OPT_NAWS, IAC, SB, OPT_NAWS, 0, 100, 0, 40, IAC, SE, IAC, WILL, OPT_TTYPE]))
    assert reply.endswith(bytes([IAC, WONT, 5]))
    assert neg.naws_enabled

    # Terminal type subnegotiation.
    data, reply = neg.feed(bytes([IAC, SB, OPT_TTYPE, 1, IAC, SE]))
    assert reply == bytes([IAC, SB, OPT_TTYPE, 0]) + b"xterm-256color" + bytes([IAC, SE])


def test_negotiator_handles_sequences_split_across_reads():
    neg = TelnetNegotiator()
    data, reply = neg.feed(b"abc" + bytes([IAC]))
    assert data == b"abc" and reply == b""
    data, reply = neg.feed(bytes([WILL]))
    assert data == b"" and reply == b""
    data, reply = neg.feed(bytes([OPT_ECHO]) + b"def")
    assert data == b"def" and reply == bytes([IAC, DO, OPT_ECHO])


def test_negotiator_escapes_outgoing_iac_and_ignores_wont_dont():
    assert TelnetNegotiator.escape(b"a\xffb") == b"a\xff\xffb"
    neg = TelnetNegotiator()
    _, reply = neg.feed(bytes([IAC, WONT, OPT_ECHO, IAC, DONT, OPT_NAWS]))
    assert reply == b""


class FakeSocket:
    def __init__(self, incoming: bytes = b""):
        self.incoming = incoming
        self.sent = bytearray()
        self.blocking = None
        self.closed = False

    def setblocking(self, flag):
        self.blocking = flag

    def recv(self, n):
        if not self.incoming:
            raise BlockingIOError
        chunk, self.incoming = self.incoming[:n], self.incoming[n:]
        return chunk

    def sendall(self, data):
        self.sent += data

    def close(self):
        self.closed = True

    def fileno(self):
        return 0


def test_telnet_channel_replies_during_recv_and_sends_break(monkeypatch):
    sock = FakeSocket(bytes([IAC, DO, OPT_NAWS]) + b"Press RETURN to get started.")
    channel = TelnetChannel(sock, cols=80, rows=24)  # type: ignore[arg-type]
    assert channel.recv(4096) == b"Press RETURN to get started."
    assert bytes(sock.sent).startswith(bytes([IAC, WILL, OPT_NAWS, IAC, SB, OPT_NAWS, 0, 80, 0, 24, IAC, SE]))
    sock.sent.clear()

    channel.sendall(b"en\r")
    channel.send_break()
    channel.resize_pty(width=132, height=50)
    assert bytes(sock.sent) == b"en\r" + bytes([IAC, BRK]) + bytes([IAC, SB, OPT_NAWS, 0, 132, 0, 50, IAC, SE])

    # No data pending is not a close; an empty read is.
    assert channel.recv(4096) == b"" and not channel.closed
    sock.recv = lambda n: b""  # type: ignore[method-assign]
    assert channel.recv(4096) == b"" and channel.closed and channel.exit_status_ready()
    channel.close()
    assert sock.closed


def test_handle_client_message_break_reaches_the_channel():
    class Chan:
        def __init__(self):
            self.breaks = 0

        def send_break(self):
            self.breaks += 1

    chan = Chan()
    terminal.handle_client_message(chan, '{"type": "break"}')
    assert chan.breaks == 1
    # Channels that can't break (no transport, no send_break) are a no-op, not a crash.
    assert terminal.send_break(object()) is False
