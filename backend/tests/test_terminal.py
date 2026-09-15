import asyncio
import json
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient

from app.api import terminal
from app.api.terminal import AuthorizationError, ConnectionParams

pytestmark = pytest.mark.asyncio


async def _register(client: AsyncClient, email: str, org_name: str = "TerminalOrg") -> str:
    resp = await client.post(
        "/api/auth/register",
        json={"org_name": org_name, "email": email, "password": "password123"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["access_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


class FakeChannel:
    def __init__(self, output: list[bytes]):
        self._output = list(output)
        self.sent: list[bytes] = []
        self.resizes: list[tuple[int, int]] = []
        self.closed = False

    def recv_ready(self) -> bool:
        return bool(self._output)

    def recv(self, _n: int) -> bytes:
        return self._output.pop(0)

    def exit_status_ready(self) -> bool:
        return self.closed

    def sendall(self, data: bytes) -> None:
        self.sent.append(data)

    def resize_pty(self, width: int, height: int) -> None:
        self.resizes.append((width, height))

    def close(self) -> None:
        self.closed = True


class FakeWebSocket:
    def __init__(self, incoming: list[dict]):
        self._incoming = list(incoming)
        self.sent: list[bytes] = []

    async def receive(self) -> dict:
        if self._incoming:
            return self._incoming.pop(0)
        await asyncio.sleep(3600)  # nothing more to say - hang like a real idle socket
        raise AssertionError("unreachable")

    async def send_bytes(self, data: bytes) -> None:
        self.sent.append(data)


async def test_handle_client_message_input_and_resize():
    channel = FakeChannel([])
    terminal.handle_client_message(channel, json.dumps({"type": "input", "data": "show ver\n"}))
    terminal.handle_client_message(channel, json.dumps({"type": "resize", "cols": 200, "rows": 50}))
    assert channel.sent == [b"show ver\n"]
    assert channel.resizes == [(200, 50)]


async def test_handle_client_message_ignores_garbage():
    channel = FakeChannel([])
    terminal.handle_client_message(channel, "not json")
    terminal.handle_client_message(channel, json.dumps(["not", "a", "dict"]))
    terminal.handle_client_message(channel, json.dumps({"type": "resize", "cols": -1, "rows": 10}))
    terminal.handle_client_message(channel, json.dumps({"type": "input", "data": 42}))
    assert channel.sent == []
    assert channel.resizes == []


async def test_bridge_pumps_output_and_input_until_disconnect():
    channel = FakeChannel([b"Router>", b" "])
    websocket = FakeWebSocket(
        [
            {"type": "websocket.receive", "text": json.dumps({"type": "input", "data": "en\n"})},
            {"type": "websocket.disconnect"},
        ]
    )

    await asyncio.wait_for(terminal.bridge(websocket, channel), timeout=5)

    assert b"".join(websocket.sent) == b"Router> "
    assert channel.sent == [b"en\n"]


async def test_bridge_ends_when_channel_closes():
    channel = FakeChannel([b"bye"])
    channel.closed = True
    websocket = FakeWebSocket([])  # never sends anything - the device hanging up must still end it

    await asyncio.wait_for(terminal.bridge(websocket, channel), timeout=5)
    assert websocket.sent == [b"bye"]


async def test_authorize_rejects_bad_token():
    result = await terminal.authorize("not-a-jwt", uuid4(), None)
    assert isinstance(result, AuthorizationError)
    assert "log in" in result.message


async def test_authorize_resolves_device_and_org_default_credential(client: AsyncClient, unique_email):
    token = await _register(client, unique_email)
    await client.post(
        "/api/credentials", headers=_auth(token), json={"name": "lab", "username": "admin", "password": "cisco123"}
    )
    device = await client.post(
        "/api/devices",
        headers=_auth(token),
        json={"name": "sw1", "host": "10.0.0.1", "device_type": "cisco_ios", "port": 2200},
    )
    device_id = UUID(device.json()["id"])

    result = await terminal.authorize(token, device_id, None)
    assert isinstance(result, ConnectionParams), result
    assert result.host == "10.0.0.1"
    assert result.port == 2200
    assert result.username == "admin"
    assert result.password == "cisco123"


async def test_authorize_requires_otp_for_passcode_credential(client: AsyncClient, unique_email):
    token = await _register(client, unique_email)
    cred = await client.post(
        "/api/credentials",
        headers=_auth(token),
        json={"name": "mfa", "username": "admin", "password": "cisco123", "mfa_mode": "passcode", "otp_delimiter": ","},
    )
    device = await client.post(
        "/api/devices",
        headers=_auth(token),
        json={"name": "sw1", "host": "10.0.0.2", "device_type": "cisco_ios", "credential_id": cred.json()["id"]},
    )
    device_id = UUID(device.json()["id"])

    missing = await terminal.authorize(token, device_id, None)
    assert isinstance(missing, AuthorizationError)
    assert "passcode" in missing.message

    ok = await terminal.authorize(token, device_id, "654321")
    assert isinstance(ok, ConnectionParams)
    assert ok.password == "cisco123,654321"


async def test_authorize_scoped_to_caller_org(client: AsyncClient, unique_email):
    token_a = await _register(client, unique_email, org_name="OrgA")
    token_b = await _register(client, f"other-{unique_email}", org_name="OrgB")
    await client.post(
        "/api/credentials", headers=_auth(token_a), json={"name": "lab", "username": "admin", "password": "x"}
    )
    device = await client.post(
        "/api/devices", headers=_auth(token_a), json={"name": "sw1", "host": "10.0.0.3", "device_type": "cisco_ios"}
    )

    result = await terminal.authorize(token_b, UUID(device.json()["id"]), None)
    assert isinstance(result, AuthorizationError)
    assert result.message == "Device not found."
