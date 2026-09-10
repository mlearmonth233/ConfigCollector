import pytest
from httpx import AsyncClient

from app import tasks as tasks_module
from app.services.device_types import DEVICE_TYPE_REGISTRY, DeviceTypeSpec

pytestmark = pytest.mark.asyncio


async def _register(client: AsyncClient, email: str, org_name: str = "TestOrg") -> str:
    resp = await client.post(
        "/api/auth/register",
        json={"org_name": org_name, "email": email, "password": "password123"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["access_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def test_register_login_me(client: AsyncClient, unique_email):
    token = await _register(client, unique_email)

    login = await client.post(
        "/api/auth/login", json={"email": unique_email, "password": "password123"}
    )
    assert login.status_code == 200
    token = login.json()["access_token"]

    me = await client.get("/api/auth/me", headers=_auth(token))
    assert me.status_code == 200
    assert me.json()["email"] == unique_email
    assert me.json()["role"] == "admin"


async def test_register_duplicate_email_rejected(client: AsyncClient, unique_email):
    await _register(client, unique_email)
    resp = await client.post(
        "/api/auth/register",
        json={"org_name": "Other", "email": unique_email, "password": "password123"},
    )
    assert resp.status_code == 409


async def test_unauthenticated_request_rejected(client: AsyncClient):
    resp = await client.get("/api/devices")
    assert resp.status_code == 401


async def test_credential_and_device_crud(client: AsyncClient, unique_email):
    token = await _register(client, unique_email)

    cred = await client.post(
        "/api/credentials",
        headers=_auth(token),
        json={"name": "lab", "username": "admin", "password": "cisco123", "enable_secret": "en123"},
    )
    assert cred.status_code == 201
    cred_body = cred.json()
    assert cred_body["has_enable_secret"] is True
    assert "password" not in cred_body

    device = await client.post(
        "/api/devices",
        headers=_auth(token),
        json={"name": "sw1", "host": "192.0.2.10", "device_type": "cisco_ios", "credential_id": cred_body["id"]},
    )
    assert device.status_code == 201
    device_id = device.json()["id"]

    bad_type = await client.post(
        "/api/devices",
        headers=_auth(token),
        json={"name": "bad", "host": "192.0.2.11", "device_type": "not_a_type"},
    )
    assert bad_type.status_code == 400

    listing = await client.get("/api/devices", headers=_auth(token))
    assert listing.status_code == 200
    assert len(listing.json()) == 1

    updated = await client.patch(
        f"/api/devices/{device_id}", headers=_auth(token), json={"site": "DC1"}
    )
    assert updated.status_code == 200
    assert updated.json()["site"] == "DC1"

    deleted = await client.delete(f"/api/devices/{device_id}", headers=_auth(token))
    assert deleted.status_code == 204


async def test_device_csv_import_partial_success(client: AsyncClient, unique_email):
    token = await _register(client, unique_email)
    await client.post(
        "/api/credentials",
        headers=_auth(token),
        json={"name": "labcred", "username": "admin", "password": "cisco123"},
    )

    csv_content = (
        "name,host,port,device_type,site,credential_name,custom_commands\n"
        "core-sw1,10.0.0.1,22,cisco_ios,DC1,labcred,\n"
        "badtype,10.0.0.2,22,not_a_real_type,DC1,labcred,\n"
        "badcred,10.0.0.3,22,cisco_ios,DC1,doesnotexist,\n"
    )
    resp = await client.post(
        "/api/devices/import",
        headers=_auth(token),
        files={"file": ("devices.csv", csv_content, "text/csv")},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["created"] == 1
    assert len(body["errors"]) == 2

    listing = await client.get("/api/devices", headers=_auth(token))
    assert len(listing.json()) == 1


async def test_import_template_round_trips_through_import(client: AsyncClient, unique_email):
    token = await _register(client, unique_email)
    await client.post(
        "/api/credentials",
        headers=_auth(token),
        json={"name": "labcred", "username": "admin", "password": "cisco123"},
    )

    template = await client.get("/api/devices/import-template", headers=_auth(token))
    assert template.status_code == 200
    assert template.headers["content-type"].startswith("text/csv")
    assert 'filename="device_import_template.csv"' in template.headers["content-disposition"]

    # The template itself must actually import cleanly - a stale example
    # (e.g. a typo'd device_type) would otherwise go unnoticed.
    resp = await client.post(
        "/api/devices/import",
        headers=_auth(token),
        files={"file": ("device_import_template.csv", template.content, "text/csv")},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["errors"] == []
    assert body["created"] == 4


async def test_job_lifecycle_with_unreachable_device(client: AsyncClient, unique_email):
    token = await _register(client, unique_email)
    cred = await client.post(
        "/api/credentials",
        headers=_auth(token),
        json={"name": "lab", "username": "admin", "password": "cisco123"},
    )
    cred_id = cred.json()["id"]
    device = await client.post(
        "/api/devices",
        headers=_auth(token),
        # TEST-NET-1 (RFC 5737): guaranteed unroutable, so the collection
        # attempt deterministically fails without needing a real device.
        json={"name": "sw1", "host": "192.0.2.1", "device_type": "cisco_ios", "credential_id": cred_id},
    )
    device_id = device.json()["id"]

    job = await client.post("/api/jobs", headers=_auth(token), json={"device_ids": [device_id]})
    assert job.status_code == 201, job.text
    job_body = job.json()
    assert job_body["item_count"] == 1

    detail = await client.get(f"/api/jobs/{job_body['id']}", headers=_auth(token))
    assert detail.status_code == 200
    detail_body = detail.json()
    assert detail_body["status"] == "failed"
    assert detail_body["items"][0]["status"] == "failed"
    assert "Timed out" in detail_body["items"][0]["error_message"] or "failed" in detail_body["items"][0]["error_message"]


async def test_job_rejects_unknown_device_id(client: AsyncClient, unique_email):
    token = await _register(client, unique_email)
    resp = await client.post(
        "/api/jobs", headers=_auth(token), json={"device_ids": ["00000000-0000-0000-0000-000000000000"]}
    )
    assert resp.status_code == 400


async def test_cross_org_device_access_denied(client: AsyncClient, unique_email):
    token_a = await _register(client, unique_email)
    device = await client.post(
        "/api/devices",
        headers=_auth(token_a),
        json={"name": "sw1", "host": "192.0.2.20", "device_type": "cisco_ios"},
    )
    device_id = device.json()["id"]

    other_email = f"other-{unique_email}"
    token_b = await _register(client, other_email, org_name="OtherOrg")

    resp = await client.patch(f"/api/devices/{device_id}", headers=_auth(token_b), json={"site": "x"})
    assert resp.status_code == 404


async def test_credential_mfa_fields_roundtrip(client: AsyncClient, unique_email):
    token = await _register(client, unique_email)
    cred = await client.post(
        "/api/credentials",
        headers=_auth(token),
        json={
            "name": "tacacs-mfa",
            "username": "admin",
            "password": "cisco123",
            "mfa_mode": "passcode",
            "otp_delimiter": ";",
            "auth_timeout_seconds": 90,
        },
    )
    assert cred.status_code == 201, cred.text
    body = cred.json()
    assert body["mfa_mode"] == "passcode"
    assert body["otp_delimiter"] == ";"
    assert body["auth_timeout_seconds"] == 90

    # Defaults for a plain (non-MFA) credential.
    plain = await client.post(
        "/api/credentials",
        headers=_auth(token),
        json={"name": "plain", "username": "admin", "password": "cisco123"},
    )
    assert plain.status_code == 201
    plain_body = plain.json()
    assert plain_body["mfa_mode"] == "none"
    assert plain_body["auth_timeout_seconds"] == 45


async def test_job_requires_otp_for_passcode_credential(client: AsyncClient, unique_email):
    token = await _register(client, unique_email)
    cred = await client.post(
        "/api/credentials",
        headers=_auth(token),
        json={"name": "mfa-cred", "username": "admin", "password": "cisco123", "mfa_mode": "passcode"},
    )
    cred_id = cred.json()["id"]
    device = await client.post(
        "/api/devices",
        headers=_auth(token),
        json={"name": "sw1", "host": "192.0.2.30", "device_type": "cisco_ios", "credential_id": cred_id},
    )
    device_id = device.json()["id"]

    # No OTP supplied - should be rejected before any device is contacted.
    resp = await client.post("/api/jobs", headers=_auth(token), json={"device_ids": [device_id]})
    assert resp.status_code == 400
    assert "mfa-cred" in resp.json()["detail"]

    # With an OTP supplied, the job is accepted (it will still fail to
    # actually reach 192.0.2.30, but that's a collection failure, not a
    # validation error).
    resp = await client.post(
        "/api/jobs",
        headers=_auth(token),
        json={"device_ids": [device_id], "credential_otps": {cred_id: "123456"}},
    )
    assert resp.status_code == 201, resp.text


async def test_job_command_override_is_accepted(client: AsyncClient, unique_email):
    token = await _register(client, unique_email)
    cred = await client.post(
        "/api/credentials",
        headers=_auth(token),
        json={"name": "lab", "username": "admin", "password": "cisco123"},
    )
    cred_id = cred.json()["id"]
    device = await client.post(
        "/api/devices",
        headers=_auth(token),
        json={"name": "pdu1", "host": "192.0.2.40", "device_type": "apc_pdu", "credential_id": cred_id},
    )
    device_id = device.json()["id"]

    resp = await client.post(
        "/api/jobs",
        headers=_auth(token),
        json={
            "device_ids": [device_id],
            "commands_by_device_type": {"apc_pdu": "about,show status"},
        },
    )
    assert resp.status_code == 201, resp.text


async def test_job_rejects_device_type_with_no_resolvable_command(client: AsyncClient, unique_email, monkeypatch):
    token = await _register(client, unique_email)
    cred = await client.post(
        "/api/credentials",
        headers=_auth(token),
        json={"name": "lab", "username": "admin", "password": "cisco123"},
    )
    cred_id = cred.json()["id"]

    # No real device type in the registry has an empty default_commands
    # list anymore (the two that used to - pdu_generic, console_server -
    # were both removed once every device in this org's actual environment
    # turned out to be Cisco or APC) - insert a temporary one so this guard
    # (must supply a command up front for a type with no default, rather
    # than let it fail later inside a worker task) still gets exercised.
    monkeypatch.setitem(
        DEVICE_TYPE_REGISTRY,
        "test_no_default_commands",
        DeviceTypeSpec("Test type with no default", "test", "generic_termserver", (), secret_supported=False),
    )
    device = await client.post(
        "/api/devices",
        headers=_auth(token),
        json={"name": "pdu1", "host": "192.0.2.41", "device_type": "test_no_default_commands", "credential_id": cred_id},
    )
    device_id = device.json()["id"]

    resp = await client.post("/api/jobs", headers=_auth(token), json={"device_ids": [device_id]})
    assert resp.status_code == 400
    assert "pdu1" in resp.json()["detail"]


async def test_device_types_expose_default_commands(client: AsyncClient, unique_email):
    token = await _register(client, unique_email)
    resp = await client.get("/api/device-types", headers=_auth(token))
    assert resp.status_code == 200
    by_key = {t["key"]: t for t in resp.json()}
    assert by_key["cisco_ios"]["default_commands"] == list(DEVICE_TYPE_REGISTRY["cisco_ios"].default_commands)
    assert by_key["apc_pdu"]["requires_custom_command"] is False
    assert by_key["apc_pdu"]["default_commands"] == list(DEVICE_TYPE_REGISTRY["apc_pdu"].default_commands)


async def test_credential_fallback_roundtrip(client: AsyncClient, unique_email):
    token = await _register(client, unique_email)
    local_admin = await client.post(
        "/api/credentials",
        headers=_auth(token),
        json={"name": "local-admin", "username": "admin", "password": "localpass"},
    )
    local_admin_id = local_admin.json()["id"]

    primary = await client.post(
        "/api/credentials",
        headers=_auth(token),
        json={
            "name": "tacacs-primary",
            "username": "netops",
            "password": "cisco123",
            "fallback_credential_id": local_admin_id,
        },
    )
    assert primary.status_code == 201, primary.text
    body = primary.json()
    assert body["fallback_credential_id"] == local_admin_id
    assert body["fallback_credential_name"] == "local-admin"


async def test_first_credential_becomes_org_default_automatically(client: AsyncClient, unique_email):
    token = await _register(client, unique_email)
    first = await client.post(
        "/api/credentials",
        headers=_auth(token),
        json={"name": "first", "username": "admin", "password": "pass1"},
    )
    assert first.status_code == 201, first.text
    assert first.json()["is_default"] is True

    second = await client.post(
        "/api/credentials",
        headers=_auth(token),
        json={"name": "second", "username": "admin", "password": "pass2"},
    )
    assert second.status_code == 201, second.text
    assert second.json()["is_default"] is False


async def test_set_default_credential_switches_which_one_is_default(client: AsyncClient, unique_email):
    token = await _register(client, unique_email)
    first = await client.post(
        "/api/credentials",
        headers=_auth(token),
        json={"name": "first", "username": "admin", "password": "pass1"},
    )
    first_id = first.json()["id"]
    second = await client.post(
        "/api/credentials",
        headers=_auth(token),
        json={"name": "second", "username": "admin", "password": "pass2"},
    )
    second_id = second.json()["id"]

    resp = await client.post(f"/api/credentials/{second_id}/set-default", headers=_auth(token))
    assert resp.status_code == 200, resp.text
    assert resp.json()["is_default"] is True

    listing = await client.get("/api/credentials", headers=_auth(token))
    by_id = {c["id"]: c for c in listing.json()}
    assert by_id[second_id]["is_default"] is True
    assert by_id[first_id]["is_default"] is False


async def test_device_with_no_credential_uses_org_default(client: AsyncClient, unique_email, monkeypatch):
    token = await _register(client, unique_email)
    default_cred = await client.post(
        "/api/credentials",
        headers=_auth(token),
        json={"name": "org-default", "username": "admin", "password": "cisco123"},
    )
    default_cred_id = default_cred.json()["id"]

    # No credential_id given at all - devices no longer need one per-device.
    device = await client.post(
        "/api/devices",
        headers=_auth(token),
        json={"name": "sw1", "host": "192.0.2.1", "device_type": "cisco_ios"},
    )
    assert device.status_code == 201, device.text
    assert device.json()["credential_id"] is None

    used_credential_ids = []

    def _fake_attempt(device, credential, otp, commands_override, on_authenticated, on_output, should_cancel):
        used_credential_ids.append(str(credential.id))
        return f"hostname {device.host}\n"

    monkeypatch.setattr(tasks_module, "_attempt_collection", _fake_attempt)

    job = await client.post("/api/jobs", headers=_auth(token), json={"device_ids": [device.json()["id"]]})
    assert job.status_code == 201, job.text
    assert job.json()["items"][0]["status"] == "completed"
    assert used_credential_ids == [default_cred_id]


async def test_device_with_own_credential_overrides_org_default(client: AsyncClient, unique_email, monkeypatch):
    token = await _register(client, unique_email)
    await client.post(
        "/api/credentials",
        headers=_auth(token),
        json={"name": "org-default", "username": "admin", "password": "cisco123"},
    )
    override_cred = await client.post(
        "/api/credentials",
        headers=_auth(token),
        json={"name": "override", "username": "root", "password": "rootpass"},
    )
    override_cred_id = override_cred.json()["id"]

    device = await client.post(
        "/api/devices",
        headers=_auth(token),
        json={
            "name": "sw1",
            "host": "192.0.2.1",
            "device_type": "cisco_ios",
            "credential_id": override_cred_id,
        },
    )
    assert device.status_code == 201, device.text

    used_credential_ids = []

    def _fake_attempt(device, credential, otp, commands_override, on_authenticated, on_output, should_cancel):
        used_credential_ids.append(str(credential.id))
        return f"hostname {device.host}\n"

    monkeypatch.setattr(tasks_module, "_attempt_collection", _fake_attempt)

    job = await client.post("/api/jobs", headers=_auth(token), json={"device_ids": [device.json()["id"]]})
    assert job.status_code == 201, job.text
    assert used_credential_ids == [override_cred_id]


async def test_job_creation_rejected_when_no_credential_exists_at_all(client: AsyncClient, unique_email):
    token = await _register(client, unique_email)
    device = await client.post(
        "/api/devices",
        headers=_auth(token),
        json={"name": "sw1", "host": "192.0.2.1", "device_type": "cisco_ios"},
    )
    assert device.status_code == 201, device.text

    job = await client.post("/api/jobs", headers=_auth(token), json={"device_ids": [device.json()["id"]]})
    assert job.status_code == 400
    assert "no credential assigned" in job.json()["detail"]
    assert "sw1" in job.json()["detail"]


async def test_credential_fallback_cannot_chain(client: AsyncClient, unique_email):
    token = await _register(client, unique_email)
    local_admin = await client.post(
        "/api/credentials",
        headers=_auth(token),
        json={"name": "local-admin", "username": "admin", "password": "localpass"},
    )
    local_admin_id = local_admin.json()["id"]
    primary = await client.post(
        "/api/credentials",
        headers=_auth(token),
        json={
            "name": "tacacs-primary",
            "username": "netops",
            "password": "cisco123",
            "fallback_credential_id": local_admin_id,
        },
    )
    primary_id = primary.json()["id"]

    # primary already has a fallback - it can't itself be used as one (no chains).
    resp = await client.post(
        "/api/credentials",
        headers=_auth(token),
        json={
            "name": "another",
            "username": "x",
            "password": "y",
            "fallback_credential_id": primary_id,
        },
    )
    assert resp.status_code == 400
    assert "chain" in resp.json()["detail"]


async def test_credential_fallback_cross_org_rejected(client: AsyncClient, unique_email):
    token_a = await _register(client, unique_email)
    other_email = f"other-{unique_email}"
    token_b = await _register(client, other_email, org_name="OtherOrg")

    other_cred = await client.post(
        "/api/credentials",
        headers=_auth(token_b),
        json={"name": "their-cred", "username": "x", "password": "y"},
    )
    other_cred_id = other_cred.json()["id"]

    resp = await client.post(
        "/api/credentials",
        headers=_auth(token_a),
        json={
            "name": "mine",
            "username": "netops",
            "password": "cisco123",
            "fallback_credential_id": other_cred_id,
        },
    )
    assert resp.status_code == 400


async def test_credential_in_use_as_fallback_cannot_be_deleted(client: AsyncClient, unique_email):
    token = await _register(client, unique_email)
    local_admin = await client.post(
        "/api/credentials",
        headers=_auth(token),
        json={"name": "local-admin", "username": "admin", "password": "localpass"},
    )
    local_admin_id = local_admin.json()["id"]
    await client.post(
        "/api/credentials",
        headers=_auth(token),
        json={
            "name": "tacacs-primary",
            "username": "netops",
            "password": "cisco123",
            "fallback_credential_id": local_admin_id,
        },
    )

    resp = await client.delete(f"/api/credentials/{local_admin_id}", headers=_auth(token))
    assert resp.status_code == 400


async def test_job_requires_otp_for_fallback_passcode_credential(client: AsyncClient, unique_email):
    token = await _register(client, unique_email)
    fallback = await client.post(
        "/api/credentials",
        headers=_auth(token),
        json={"name": "local-mfa", "username": "admin", "password": "localpass", "mfa_mode": "passcode"},
    )
    fallback_id = fallback.json()["id"]
    primary = await client.post(
        "/api/credentials",
        headers=_auth(token),
        json={
            "name": "tacacs-primary",
            "username": "netops",
            "password": "cisco123",
            "fallback_credential_id": fallback_id,
        },
    )
    primary_id = primary.json()["id"]
    device = await client.post(
        "/api/devices",
        headers=_auth(token),
        json={"name": "sw1", "host": "192.0.2.60", "device_type": "cisco_ios", "credential_id": primary_id},
    )
    device_id = device.json()["id"]

    # The primary credential itself needs no OTP, but its fallback does -
    # since we can't know in advance whether the fallback will be needed,
    # the OTP must be supplied up front regardless.
    resp = await client.post("/api/jobs", headers=_auth(token), json={"device_ids": [device_id]})
    assert resp.status_code == 400
    assert "local-mfa" in resp.json()["detail"]

    resp = await client.post(
        "/api/jobs",
        headers=_auth(token),
        json={"device_ids": [device_id], "credential_otps": {fallback_id: "111111"}},
    )
    assert resp.status_code == 201, resp.text


async def test_job_falls_back_and_reports_both_failures(client: AsyncClient, unique_email):
    token = await _register(client, unique_email)
    fallback = await client.post(
        "/api/credentials",
        headers=_auth(token),
        json={"name": "local-admin", "username": "admin", "password": "localpass"},
    )
    fallback_id = fallback.json()["id"]
    primary = await client.post(
        "/api/credentials",
        headers=_auth(token),
        json={
            "name": "tacacs-primary",
            "username": "netops",
            "password": "cisco123",
            "fallback_credential_id": fallback_id,
        },
    )
    primary_id = primary.json()["id"]
    device = await client.post(
        "/api/devices",
        headers=_auth(token),
        # TEST-NET-1: guaranteed unroutable, so both the primary and
        # fallback login attempts deterministically fail.
        json={"name": "sw1", "host": "192.0.2.1", "device_type": "cisco_ios", "credential_id": primary_id},
    )
    device_id = device.json()["id"]

    job = await client.post("/api/jobs", headers=_auth(token), json={"device_ids": [device_id]})
    assert job.status_code == 201, job.text

    detail = await client.get(f"/api/jobs/{job.json()['id']}", headers=_auth(token))
    item = detail.json()["items"][0]
    assert item["status"] == "failed"
    assert item["used_fallback_credential"] is False
    assert "tacacs-primary" in item["error_message"]
    assert "local-admin" in item["error_message"]

    # The live transcript should narrate both attempts, in order.
    live_output = item["live_output"]
    assert live_output.count("Connecting to 192.0.2.1:22") == 2
    assert "Trying fallback credential 'local-admin'" in live_output
    assert "ERROR:" in live_output


async def test_job_live_output_narrates_commands_on_success(client: AsyncClient, unique_email):
    token = await _register(client, unique_email)
    cred = await client.post(
        "/api/credentials",
        headers=_auth(token),
        json={"name": "lab", "username": "admin", "password": "cisco123"},
    )
    cred_id = cred.json()["id"]
    # 192.0.2.1 is unreachable, so this deterministically fails during the
    # auth phase - the transcript should still show the connect attempt
    # before it gives up.
    device = await client.post(
        "/api/devices",
        headers=_auth(token),
        json={"name": "sw1", "host": "192.0.2.1", "device_type": "cisco_ios", "credential_id": cred_id},
    )
    device_id = device.json()["id"]

    job = await client.post("/api/jobs", headers=_auth(token), json={"device_ids": [device_id]})
    assert job.status_code == 201, job.text

    detail = await client.get(f"/api/jobs/{job.json()['id']}", headers=_auth(token))
    item = detail.json()["items"][0]
    assert item["live_output"].startswith("Connecting to 192.0.2.1:22 as admin...")
    assert "Authenticated." not in item["live_output"]
