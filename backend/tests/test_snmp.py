"""SNMP module: profile CRUD (secrets write-only), auth building, report
formatting, the poller against fake get/walk primitives, and a real poll of
an unreachable host through the job API."""

import pytest
from httpx import AsyncClient
from pysnmp.hlapi.v3arch.asyncio import CommunityData, UsmUserData

from app.models.snmp import SnmpAuthProtocol, SnmpPrivProtocol, SnmpSecurityLevel, SnmpVersion
from app.services.snmp_poll import (
    IF_COLUMNS,
    SYSLOG_COLUMNS,
    SYSTEM_OIDS,
    SnmpAuth,
    SnmpError,
    build_auth_data,
    format_report,
    poll_device,
    validate_auth,
)

pytestmark = pytest.mark.asyncio


async def _register(client: AsyncClient, email: str, org_name: str = "SnmpOrg") -> str:
    resp = await client.post("/api/auth/register", json={"org_name": org_name, "email": email, "password": "password123"})
    assert resp.status_code == 201, resp.text
    return resp.json()["access_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


V2C = {"name": "public-ro", "version": "v2c", "community": "s3cret-community"}
V3 = {
    "name": "netops-v3",
    "version": "v3",
    "username": "netops",
    "security_level": "authPriv",
    "auth_protocol": "SHA256",
    "auth_password": "auth-pass-123",
    "priv_protocol": "AES128",
    "priv_password": "priv-pass-123",
}


# --- auth building / validation --------------------------------------------------


def test_v2c_auth_builds_community_data():
    data = build_auth_data(SnmpAuth(SnmpVersion.V2C, community="public"))
    assert isinstance(data, CommunityData)
    assert data.communityName == "public"
    assert data.message_processing_model == 1


def test_v3_authpriv_builds_usm_user_with_both_keys():
    data = build_auth_data(
        SnmpAuth(
            SnmpVersion.V3, username="netops", security_level=SnmpSecurityLevel.AUTH_PRIV,
            auth_protocol=SnmpAuthProtocol.SHA256, auth_password="a" * 8,
            priv_protocol=SnmpPrivProtocol.AES128, priv_password="p" * 8,
        )
    )
    assert isinstance(data, UsmUserData)
    assert data.userName == "netops"
    assert data.authKey == "a" * 8 and data.privKey == "p" * 8


@pytest.mark.parametrize(
    ("auth", "message"),
    [
        (SnmpAuth(SnmpVersion.V2C), "community"),
        (SnmpAuth(SnmpVersion.V3), "username"),
        (SnmpAuth(SnmpVersion.V3, username="u", security_level=SnmpSecurityLevel.AUTH_NO_PRIV), "authentication protocol"),
        (
            SnmpAuth(SnmpVersion.V3, username="u", security_level=SnmpSecurityLevel.AUTH_PRIV,
                     auth_protocol=SnmpAuthProtocol.SHA, auth_password="x"),
            "privacy protocol",
        ),
    ],
)
def test_validate_auth_rejects_incomplete_profiles(auth, message):
    with pytest.raises(ValueError, match=message):
        validate_auth(auth)


# --- poller + report over fakes ---------------------------------------------------------


def _fake_primitives():
    """A pretend Cisco switch: system group, two interfaces, two syslog entries."""
    system = {
        SYSTEM_OIDS["sysDescr"]: "Cisco IOS Software, C9300, Version 17.9.5",
        SYSTEM_OIDS["sysObjectID"]: "1.3.6.1.4.1.9.1.2494",
        SYSTEM_OIDS["sysUpTime"]: "8640000",  # 100 ticks/s -> 1 day
        SYSTEM_OIDS["sysContact"]: "netops@example.com",
        SYSTEM_OIDS["sysName"]: "HQ-CORE-SW01",
        SYSTEM_OIDS["sysLocation"]: "Denver MDF",
    }
    tables = {
        IF_COLUMNS["ifDescr"]: [("1", "GigabitEthernet1/0/1"), ("2", "GigabitEthernet1/0/2")],
        IF_COLUMNS["ifAdminStatus"]: [("1", "1"), ("2", "2")],
        IF_COLUMNS["ifOperStatus"]: [("1", "1"), ("2", "2")],
        IF_COLUMNS["ifInErrors"]: [("1", "0"), ("2", "17")],
        IF_COLUMNS["ifOutErrors"]: [("1", "0"), ("2", "0")],
        IF_COLUMNS["ifInDiscards"]: [("1", "0"), ("2", "3")],
        IF_COLUMNS["ifOutDiscards"]: [("1", "0"), ("2", "0")],
        IF_COLUMNS["ifAlias"]: [("1", "Uplink to SW02")],
        SYSLOG_COLUMNS["facility"]: [("1", "LINK"), ("2", "SYS")],
        SYSLOG_COLUMNS["severity"]: [("1", "4"), ("2", "6")],
        SYSLOG_COLUMNS["msgName"]: [("1", "UPDOWN"), ("2", "CONFIG_I")],
        SYSLOG_COLUMNS["msgText"]: [("1", "Interface Gi1/0/2, changed state to down"), ("2", "Configured from console by netops")],
        SYSLOG_COLUMNS["timestamp"]: [("1", "8630000"), ("2", "8635000")],
        "1.3.6.1.4.1.9.9.109.1.1.1.1.7": [("1", "23")],  # cpmCPUTotal5minRev
    }
    calls = {"get": 0, "walk": []}

    async def getter(host, auth, oids):
        calls["get"] += 1
        return {oid: system.get(oid) for oid in oids}

    async def walker(host, auth, base):
        calls["walk"].append(base)
        return tables.get(base, [])

    return getter, walker, calls


async def test_poll_device_builds_tables_and_report():
    getter, walker, calls = _fake_primitives()
    auth = SnmpAuth(SnmpVersion.V2C, community="public")
    lines: list[str] = []

    result = await poll_device("10.10.0.1", auth, ["1.3.6.1.4.1.9.9.109.1.1.1.1.7"], getter=getter, walker=walker, on_output=lines.append)

    assert result.system["sysName"] == "HQ-CORE-SW01"
    assert [r["ifDescr"] for r in result.interfaces] == ["GigabitEthernet1/0/1", "GigabitEthernet1/0/2"]
    assert result.interfaces[1]["ifInErrors"] == "17"
    assert result.interfaces[0]["ifAlias"] == "Uplink to SW02" and "ifAlias" not in result.interfaces[1]
    assert len(result.syslog) == 2 and result.syslog_supported
    assert result.extra["1.3.6.1.4.1.9.9.109.1.1.1.1.7"] == [("1", "23")]
    assert calls["get"] == 1
    assert any("Interfaces: 2 rows" in ln for ln in lines)

    report = format_report("HQ-CORE-SW01", "10.10.0.1", auth, result, collected_at="2026-09-15 20:00:00 UTC")
    assert "sysUpTime:   1 day, 0:00:00" in report
    assert "GigabitEthernet1/0/2" in report and "down" in report
    assert "%LINK-3-UPDOWN: Interface Gi1/0/2, changed state to down  (err)" in report
    assert "%SYS-5-CONFIG_I" in report
    assert "## Extra OIDs" in report and ".1 = 23" in report
    assert "s3cret" not in report and "public" not in report  # the community never appears


async def test_poll_device_aborts_when_the_system_group_fails():
    async def getter(host, auth, oids):
        raise SnmpError("No SNMP response (timed out).")

    async def walker(host, auth, base):
        raise AssertionError("must not walk after the system GET failed")

    with pytest.raises(SnmpError, match="timed out"):
        await poll_device("192.0.2.1", SnmpAuth(SnmpVersion.V2C, community="x"), getter=getter, walker=walker)


async def test_poll_device_tolerates_a_device_without_the_syslog_mib():
    getter, walker, _ = _fake_primitives()

    async def no_syslog_walker(host, auth, base):
        if base in SYSLOG_COLUMNS.values():
            return []
        return await walker(host, auth, base)

    result = await poll_device("10.10.0.1", SnmpAuth(SnmpVersion.V2C, community="x"), getter=getter, walker=no_syslog_walker)
    assert result.syslog == [] and result.syslog_supported is False
    report = format_report("sw", "10.10.0.1", SnmpAuth(SnmpVersion.V2C, community="x"), result, collected_at="now")
    assert "no CISCO-SYSLOG-MIB history table" in report


# --- API: profiles -----------------------------------------------------------------------


async def test_profile_crud_never_returns_secrets(client: AsyncClient, unique_email):
    token = await _register(client, unique_email)

    created = await client.post("/api/snmp/profiles", headers=_auth(token), json=V3)
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["username"] == "netops" and body["security_level"] == "authPriv"
    assert body["has_auth_password"] is True and body["has_priv_password"] is True
    assert body["is_default"] is True  # first profile becomes the default
    for secret in ("auth-pass-123", "priv-pass-123"):
        assert secret not in created.text
    profile_id = body["id"]

    second = await client.post("/api/snmp/profiles", headers=_auth(token), json=V2C)
    assert second.status_code == 201
    assert second.json()["has_community"] is True and second.json()["is_default"] is False
    assert "s3cret-community" not in second.text

    made_default = await client.post(f"/api/snmp/profiles/{second.json()['id']}/set-default", headers=_auth(token))
    assert made_default.json()["is_default"] is True
    listing = (await client.get("/api/snmp/profiles", headers=_auth(token))).json()
    assert [p["is_default"] for p in listing] == [False, True]

    renamed = await client.patch(f"/api/snmp/profiles/{profile_id}", headers=_auth(token), json={"name": "renamed", "auth_password": ""})
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "renamed" and renamed.json()["has_auth_password"] is True  # blank leaves it alone

    deleted = await client.delete(f"/api/snmp/profiles/{profile_id}", headers=_auth(token))
    assert deleted.status_code == 204


@pytest.mark.parametrize(
    ("payload", "fragment"),
    [
        ({"name": "x", "version": "v2c"}, "community"),
        ({"name": "x", "version": "v3", "security_level": "authPriv"}, "username"),
        ({"name": "x", "version": "v3", "username": "u", "security_level": "authNoPriv"}, "authentication protocol"),
    ],
)
async def test_incomplete_profile_is_rejected(client: AsyncClient, unique_email, payload, fragment):
    token = await _register(client, unique_email)
    resp = await client.post("/api/snmp/profiles", headers=_auth(token), json=payload)
    assert resp.status_code == 400, resp.text
    assert fragment in resp.json()["detail"]


async def test_non_admin_cannot_manage_profiles(client: AsyncClient, unique_email):
    token = await _register(client, unique_email)
    member_email = f"member-{unique_email}"
    await client.post("/api/users", headers=_auth(token), json={"email": member_email, "password": "password123"})
    member = (await client.post("/api/auth/login", json={"email": member_email, "password": "password123"})).json()["access_token"]
    assert (await client.post("/api/snmp/profiles", headers=_auth(member), json=V2C)).status_code == 403
    assert (await client.get("/api/snmp/profiles", headers=_auth(member))).status_code == 200


# --- API: jobs ---------------------------------------------------------------------------


async def _device(client, token, name="sw1", host="192.0.2.1", **extra) -> str:
    resp = await client.post("/api/devices", headers=_auth(token), json={"name": name, "host": host, "device_type": "cisco_ios", **extra})
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def test_job_requires_a_profile_somewhere(client: AsyncClient, unique_email):
    token = await _register(client, unique_email)
    device_id = await _device(client, token)
    resp = await client.post("/api/snmp/jobs", headers=_auth(token), json={"device_ids": [device_id]})
    assert resp.status_code == 400
    assert "no org default SNMP profile" in resp.json()["detail"]


async def test_job_polls_unreachable_device_and_fails_cleanly(client: AsyncClient, unique_email):
    token = await _register(client, unique_email)
    # TEST-NET-1 never answers: a real pysnmp round trip that times out fast.
    fast = {**V2C, "timeout_seconds": 1, "retries": 0}
    profile = (await client.post("/api/snmp/profiles", headers=_auth(token), json=fast)).json()
    device_id = await _device(client, token)

    resp = await client.post(
        "/api/snmp/jobs",
        headers=_auth(token),
        json={"device_ids": [device_id], "extra_oids": ["1.3.6.1.4.1.9.9.109.1.1.1.1.7"]},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "failed"
    assert body["extra_oids"] == ["1.3.6.1.4.1.9.9.109.1.1.1.1.7"]
    item = body["items"][0]
    assert item["status"] == "failed"
    assert item["profile_name"] == profile["name"]
    assert "No SNMP response" in item["error_message"]
    assert "SNMPv2c" in item["live_output"]
    assert "s3cret-community" not in resp.text


async def test_job_uses_the_run_override_then_device_profile_then_default(client: AsyncClient, unique_email, monkeypatch):
    token = await _register(client, unique_email)
    default = (await client.post("/api/snmp/profiles", headers=_auth(token), json={**V2C, "name": "default"})).json()
    per_device = (await client.post("/api/snmp/profiles", headers=_auth(token), json={**V2C, "name": "per-device"})).json()
    override = (await client.post("/api/snmp/profiles", headers=_auth(token), json={**V2C, "name": "override"})).json()
    assert default["is_default"]

    d_default = await _device(client, token, name="a", host="192.0.2.1")
    d_own = await _device(client, token, name="b", host="192.0.2.2", snmp_profile_id=per_device["id"])

    # Fake the network layer so this is about profile selection, not reachability.
    from app.services import snmp_poll

    async def getter(host, auth, oids):
        return {oid: f"{auth.community}-value" for oid in oids}

    async def walker(host, auth, base):
        return []

    monkeypatch.setattr(snmp_poll, "snmp_get", getter)
    monkeypatch.setattr(snmp_poll, "snmp_walk", walker)
    # poll_device binds the defaults at definition time; route through the patched names.
    original = snmp_poll.poll_device

    async def poll_device_patched(host, auth, extra=None, **kwargs):
        kwargs.setdefault("getter", snmp_poll.snmp_get)
        kwargs.setdefault("walker", snmp_poll.snmp_walk)
        return await original(host, auth, extra, **kwargs)

    monkeypatch.setattr(snmp_poll, "poll_device", poll_device_patched)

    job = (await client.post("/api/snmp/jobs", headers=_auth(token), json={"device_ids": [d_default, d_own]})).json()
    by_name = {i["device_name"]: i for i in job["items"]}
    assert job["status"] == "completed"
    assert by_name["a"]["profile_name"] == "default"
    assert by_name["b"]["profile_name"] == "per-device"
    assert "# SNMP report: a (192.0.2.1)" in by_name["a"]["report"]

    job2 = (
        await client.post(
            "/api/snmp/jobs", headers=_auth(token), json={"device_ids": [d_default, d_own], "snmp_profile_id": override["id"]}
        )
    ).json()
    assert {i["profile_name"] for i in job2["items"]} == {"override"}
    assert job2["snmp_profile_id"] == override["id"]


async def test_job_rejects_non_numeric_extra_oid(client: AsyncClient, unique_email):
    token = await _register(client, unique_email)
    await client.post("/api/snmp/profiles", headers=_auth(token), json=V2C)
    device_id = await _device(client, token)
    resp = await client.post("/api/snmp/jobs", headers=_auth(token), json={"device_ids": [device_id], "extra_oids": ["sysDescr.0"]})
    assert resp.status_code == 422
    assert "numeric OID" in resp.text


async def test_device_snmp_profile_can_be_set_and_cleared(client: AsyncClient, unique_email):
    token = await _register(client, unique_email)
    profile = (await client.post("/api/snmp/profiles", headers=_auth(token), json=V2C)).json()
    device_id = await _device(client, token)

    set_resp = await client.patch(f"/api/devices/{device_id}", headers=_auth(token), json={"snmp_profile_id": profile["id"]})
    assert set_resp.json()["snmp_profile_id"] == profile["id"]
    assert (await client.get("/api/snmp/profiles", headers=_auth(token))).json()[0]["device_count"] == 1

    cleared = await client.patch(f"/api/devices/{device_id}", headers=_auth(token), json={"clear_snmp_profile": True})
    assert cleared.json()["snmp_profile_id"] is None
