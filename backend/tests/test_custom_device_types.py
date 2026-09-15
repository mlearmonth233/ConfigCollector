"""Org-defined device types: CRUD, validation, and that a device of a custom
type flows through the device-types listing, device creation, job creation
and the worker exactly like a built-in type."""

import pytest
from httpx import AsyncClient

from app import tasks as tasks_module
from app.services import collector as collector_module
from app.services.collector import collect_device_config
from app.services.device_types import DEVICE_TYPE_REGISTRY, DeviceTypeSpec, build_catalog, list_netmiko_drivers

pytestmark = pytest.mark.asyncio


async def _register(client: AsyncClient, email: str, org_name: str = "CustomTypeOrg") -> str:
    resp = await client.post(
        "/api/auth/register", json={"org_name": org_name, "email": email, "password": "password123"}
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["access_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


JUNOS = {
    "key": "juniper_srx",
    "label": "Juniper SRX",
    "category": "firewall",
    "netmiko_driver": "juniper_junos",
    "default_commands": ["set cli screen-length 0", "show configuration | display set", "show log messages | last 200"],
    "secret_supported": False,
    "timing_read": True,
}


# --- pure helpers -------------------------------------------------------------


def test_netmiko_driver_list_has_common_platforms_and_no_telnet_variants():
    drivers = list_netmiko_drivers()
    for expected in ("juniper_junos", "arista_eos", "paloalto_panos", "linux", "generic_termserver", "cisco_ios"):
        assert expected in drivers
    assert not any(d.endswith("_telnet") for d in drivers)


def test_build_catalog_adds_custom_types_but_never_shadows_a_builtin():
    class Row:
        def __init__(self, key, label="X", driver="linux", commands="uptime", timing=True, secret=False):
            self.key, self.label, self.category = key, label, "server"
            self.netmiko_driver, self.default_commands = driver, commands
            self.timing_read, self.secret_supported = timing, secret

    catalog = build_catalog([Row("linux_box", commands="uptime, dmesg | tail -50"), Row("cisco_ios", label="impostor")])
    assert catalog["linux_box"].custom is True
    assert catalog["linux_box"].default_commands == ("uptime", "dmesg | tail -50")
    assert catalog["linux_box"].uses_timing_read is True
    assert catalog["cisco_ios"] is DEVICE_TYPE_REGISTRY["cisco_ios"]  # built-in wins


def test_uses_timing_read_follows_the_explicit_flag_for_custom_types():
    explicit_pattern = DeviceTypeSpec("Arista", "switch", "arista_eos", ("show run",), timing_read=False, custom=True)
    explicit_timing = DeviceTypeSpec("Odd box", "other", "arista_eos", ("show run",), timing_read=True, custom=True)
    assert explicit_pattern.uses_timing_read is False
    assert explicit_timing.uses_timing_read is True
    # Built-ins keep the driver/category rule.
    assert DEVICE_TYPE_REGISTRY["apc_pdu"].uses_timing_read is True
    assert DEVICE_TYPE_REGISTRY["cisco_ios"].uses_timing_read is False


class _FakeConnection:
    def __init__(self):
        self.pattern_calls: list[str] = []
        self.timing_calls: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def send_command(self, command, read_timeout=None, **kwargs):
        self.pattern_calls.append(command)
        return f"out:{command}"

    def send_command_timing(self, command, last_read=None, read_timeout=None):
        self.timing_calls.append(command)
        return f"out:{command}"


def test_collect_device_config_uses_a_supplied_custom_spec(monkeypatch):
    fake = _FakeConnection()
    captured = {}

    def _connect(**kwargs):
        captured.update(kwargs)
        return fake

    monkeypatch.setattr(collector_module, "ConnectHandler", _connect)
    spec = DeviceTypeSpec("Juniper SRX", "firewall", "juniper_junos", ("show configuration", "show log messages"), timing_read=True, custom=True)

    output = collect_device_config(
        host="10.0.0.7", port=22, device_type="juniper_srx", username="u", password="p", secret=None,
        custom_commands=None, auth_timeout=5, spec=spec,
    )

    assert captured["device_type"] == "juniper_junos"  # the custom type's Netmiko driver, not its key
    assert fake.timing_calls == ["show configuration", "show log messages"]
    assert fake.pattern_calls == []
    assert "out:show log messages" in output


# --- API ------------------------------------------------------------------------


async def test_create_list_update_delete_custom_device_type(client: AsyncClient, unique_email):
    token = await _register(client, unique_email)

    created = await client.post("/api/custom-device-types", headers=_auth(token), json=JUNOS)
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["key"] == "juniper_srx"
    assert body["default_commands"] == JUNOS["default_commands"]
    assert body["device_count"] == 0
    type_id = body["id"]

    listing = await client.get("/api/custom-device-types", headers=_auth(token))
    assert [t["key"] for t in listing.json()] == ["juniper_srx"]

    # It shows up in the general device-types listing, flagged custom.
    types = await client.get("/api/device-types", headers=_auth(token))
    juniper = next(t for t in types.json() if t["key"] == "juniper_srx")
    assert juniper["custom"] is True
    assert juniper["label"] == "Juniper SRX"
    assert juniper["default_commands"] == JUNOS["default_commands"]
    assert next(t for t in types.json() if t["key"] == "cisco_ios")["custom"] is False

    updated = await client.patch(
        f"/api/custom-device-types/{type_id}",
        headers=_auth(token),
        json={"default_commands": "show configuration | display set\nshow log messages", "timing_read": False},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["default_commands"] == ["show configuration | display set", "show log messages"]
    assert updated.json()["timing_read"] is False

    deleted = await client.delete(f"/api/custom-device-types/{type_id}", headers=_auth(token))
    assert deleted.status_code == 204
    assert (await client.get("/api/custom-device-types", headers=_auth(token))).json() == []


@pytest.mark.parametrize(
    ("override", "fragment"),
    [
        ({"key": "cisco_ios"}, "built-in"),
        ({"key": "Bad Key!"}, "lowercase"),
        ({"netmiko_driver": "not_a_real_driver"}, "not a Netmiko"),
        ({"category": "spaceship"}, "category must be one of"),
    ],
)
async def test_create_custom_device_type_validation(client: AsyncClient, unique_email, override, fragment):
    token = await _register(client, unique_email)
    resp = await client.post("/api/custom-device-types", headers=_auth(token), json={**JUNOS, **override})
    assert resp.status_code in (400, 422), resp.text
    assert fragment in resp.text


async def test_duplicate_key_in_same_org_is_rejected_but_other_org_is_fine(client: AsyncClient, unique_email):
    token_a = await _register(client, unique_email)
    assert (await client.post("/api/custom-device-types", headers=_auth(token_a), json=JUNOS)).status_code == 201
    dup = await client.post("/api/custom-device-types", headers=_auth(token_a), json=JUNOS)
    assert dup.status_code == 400
    assert "already exists" in dup.json()["detail"]

    token_b = await _register(client, f"other-{unique_email}", org_name="OtherCustomTypeOrg")
    assert (await client.post("/api/custom-device-types", headers=_auth(token_b), json=JUNOS)).status_code == 201
    # ...and org B can't see org A's types.
    assert len((await client.get("/api/custom-device-types", headers=_auth(token_b))).json()) == 1


async def test_non_admin_cannot_manage_custom_device_types(client: AsyncClient, unique_email):
    token = await _register(client, unique_email)
    member_email = f"member-{unique_email}"
    assert (
        await client.post("/api/users", headers=_auth(token), json={"email": member_email, "password": "password123"})
    ).status_code == 201
    member_token = (
        await client.post("/api/auth/login", json={"email": member_email, "password": "password123"})
    ).json()["access_token"]
    resp = await client.post("/api/custom-device-types", headers=_auth(member_token), json=JUNOS)
    assert resp.status_code == 403


async def test_device_can_use_a_custom_type_and_type_cannot_be_deleted_while_in_use(client: AsyncClient, unique_email):
    token = await _register(client, unique_email)
    type_id = (await client.post("/api/custom-device-types", headers=_auth(token), json=JUNOS)).json()["id"]

    device = await client.post(
        "/api/devices", headers=_auth(token), json={"name": "edge-fw1", "host": "192.0.2.50", "device_type": "juniper_srx"}
    )
    assert device.status_code == 201, device.text
    assert device.json()["device_type"] == "juniper_srx"

    # An unknown type is still rejected, and the message lists the custom one as valid.
    bad = await client.post(
        "/api/devices", headers=_auth(token), json={"name": "x", "host": "192.0.2.51", "device_type": "nope"}
    )
    assert bad.status_code == 400
    assert "juniper_srx" in bad.json()["detail"]

    blocked = await client.delete(f"/api/custom-device-types/{type_id}", headers=_auth(token))
    assert blocked.status_code == 400
    assert "1 device(s) still use" in blocked.json()["detail"]
    listing = await client.get("/api/custom-device-types", headers=_auth(token))
    assert listing.json()[0]["device_count"] == 1


async def test_job_for_custom_type_device_runs_the_types_commands_with_its_driver(client: AsyncClient, unique_email, monkeypatch):
    token = await _register(client, unique_email)
    await client.post(
        "/api/credentials", headers=_auth(token), json={"name": "lab", "username": "admin", "password": "pw"}
    )
    await client.post("/api/custom-device-types", headers=_auth(token), json=JUNOS)
    device = await client.post(
        "/api/devices", headers=_auth(token), json={"name": "edge-fw1", "host": "192.0.2.50", "device_type": "juniper_srx"}
    )

    fake = _FakeConnection()
    captured = {}

    def _connect(**kwargs):
        captured.update(kwargs)
        return fake

    monkeypatch.setattr(collector_module, "ConnectHandler", _connect)

    job = await client.post("/api/jobs", headers=_auth(token), json={"device_ids": [device.json()["id"]]})
    assert job.status_code == 201, job.text
    item = job.json()["items"][0]
    assert item["status"] == "completed", item
    assert captured["device_type"] == "juniper_junos"
    assert fake.timing_calls == JUNOS["default_commands"]

    snapshot = await client.get(f"/api/snapshots/{item['snapshot_id']}", headers=_auth(token))
    assert "show log messages | last 200" in snapshot.json()["content"]


async def test_job_for_custom_type_device_uses_the_commands_page_override(client: AsyncClient, unique_email, monkeypatch):
    token = await _register(client, unique_email)
    await client.post(
        "/api/credentials", headers=_auth(token), json={"name": "lab", "username": "admin", "password": "pw"}
    )
    await client.post("/api/custom-device-types", headers=_auth(token), json=JUNOS)
    device = await client.post(
        "/api/devices", headers=_auth(token), json={"name": "edge-fw1", "host": "192.0.2.50", "device_type": "juniper_srx"}
    )
    # The Commands-page override endpoint accepts a custom type's key too.
    override = await client.put(
        "/api/command-profiles/juniper_srx", headers=_auth(token), json={"commands": ["show system alarms"]}
    )
    assert override.status_code == 200, override.text

    fake = _FakeConnection()
    monkeypatch.setattr(collector_module, "ConnectHandler", lambda **kwargs: fake)
    job = await client.post("/api/jobs", headers=_auth(token), json={"device_ids": [device.json()["id"]]})
    assert job.json()["items"][0]["status"] == "completed"
    assert fake.timing_calls == ["show system alarms"]


async def test_worker_fails_cleanly_when_a_custom_type_was_deleted(client: AsyncClient, unique_email, monkeypatch):
    token = await _register(client, unique_email)
    await client.post(
        "/api/credentials", headers=_auth(token), json={"name": "lab", "username": "admin", "password": "pw"}
    )
    type_id = (await client.post("/api/custom-device-types", headers=_auth(token), json=JUNOS)).json()["id"]
    device = await client.post(
        "/api/devices", headers=_auth(token), json={"name": "edge-fw1", "host": "192.0.2.50", "device_type": "juniper_srx"}
    )
    # Bypass the in-use guard the way a direct DB edit would.
    db = tasks_module.SyncSessionLocal()
    try:
        from app.models.custom_device_type import CustomDeviceType

        db.delete(db.get(CustomDeviceType, type_id))
        db.commit()
    finally:
        db.close()

    monkeypatch.setattr(collector_module, "ConnectHandler", lambda **kwargs: _FakeConnection())
    job = await client.post(
        "/api/jobs",
        headers=_auth(token),
        json={"device_ids": [device.json()["id"]], "commands_by_device_type": {"juniper_srx": "show version"}},
    )
    assert job.status_code == 201, job.text
    item = job.json()["items"][0]
    assert item["status"] == "failed"
    assert "no longer exists" in item["error_message"]
