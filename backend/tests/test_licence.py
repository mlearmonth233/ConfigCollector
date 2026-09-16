"""Tiers and licence keys: the free tier's limits, what a signed key
unlocks, and that nothing but the vendor's private key can mint one."""

import base64
import json
from datetime import date, timedelta

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from httpx import AsyncClient

from app import tasks as tasks_module
from app.config import get_settings
from app.core import licence as lic

pytestmark = pytest.mark.asyncio

_PRIVATE = Ed25519PrivateKey.generate()
_PUBLIC_B64 = base64.b64encode(_PRIVATE.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode()


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def make_key(tier: str = "colony", *, expires: str | None = None, devices: int | None = None, customer: str = "Test Co", signer: Ed25519PrivateKey = _PRIVATE) -> str:
    payload = {"v": 1, "id": "abc12345", "tier": tier, "customer": customer, "issued": "2026-09-16", "expires": expires}
    if devices is not None:
        payload["devices"] = devices
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    return f"PKR1.{_b64url(body)}.{_b64url(signer.sign(body))}"


@pytest.fixture
def free_tier(monkeypatch):
    """Tests run as Warren via LICENCE_OVERRIDE_TIER (conftest); this fixture
    removes the override and installs the test keypair."""
    monkeypatch.setattr(get_settings(), "licence_override_tier", "")
    original = lic._public_key  # noqa: SLF001
    lic.configure_public_key(_PUBLIC_B64)
    yield
    lic._public_key = original  # noqa: SLF001


# --- keys -----------------------------------------------------------------------------------


def test_signed_key_unlocks_a_tier_and_tampering_is_caught(free_tier):
    info = lic.licence_from_key(make_key("colony", expires="2099-01-01"))
    assert info.valid and info.tier.key == "colony" and info.has("schedules") and info.max_devices == 500
    assert info.customer == "Test Co" and info.expires_at == date(2099, 1, 1)

    warren = lic.licence_from_key(make_key("warren", devices=None))
    assert warren.tier.key == "warren" and warren.max_devices is None

    key = make_key("colony")
    payload_b64 = key.split(".")[1]
    forged = json.loads(base64.urlsafe_b64decode(payload_b64 + "=" * (-len(payload_b64) % 4)))
    forged["tier"] = "warren"
    tampered = f"PKR1.{_b64url(json.dumps(forged).encode())}.{key.split('.')[2]}"
    bad = lic.licence_from_key(tampered)
    assert not bad.valid and bad.tier.key == "nest" and "signature" in bad.problem

    other_signer = Ed25519PrivateKey.generate()
    assert not lic.licence_from_key(make_key("warren", signer=other_signer)).valid
    assert lic.licence_from_key("not a key").problem.startswith("That is not a Packrat licence key")
    assert lic.licence_from_key(None).tier.key == "nest"


def test_expired_key_falls_back_to_free(free_tier):
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    info = lic.licence_from_key(make_key("colony", expires=yesterday))
    assert not info.valid and info.tier.key == "nest" and "expired" in info.problem and info.expires_at.isoformat() == yesterday


def test_retention_is_capped_by_tier():
    free = lic.LicenceInfo.free()
    assert lic.effective_retention_days(None, free) == 14
    assert lic.effective_retention_days(90, free) == 14
    assert lic.effective_retention_days(7, free) == 7
    paid = lic.licence_from_key(None)
    paid.tier = lic.TIERS["colony"]
    assert lic.effective_retention_days(None, paid) is None and lic.effective_retention_days(90, paid) == 90


# --- API ------------------------------------------------------------------------------------


async def _register(client: AsyncClient, email: str) -> str:
    resp = await client.post("/api/auth/register", json={"org_name": "TierOrg", "email": email, "password": "password123"})
    assert resp.status_code == 201, resp.text
    return resp.json()["access_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def test_free_tier_limits_and_a_key_lifts_them(client: AsyncClient, unique_email, free_tier, monkeypatch):
    token = await _register(client, unique_email)
    status = (await client.get("/api/licence", headers=_auth(token))).json()
    assert status["tier"] == "nest" and status["valid"] is False and status["features"] == [] and status["max_devices"] == 10 and status["has_key"] is False

    # Ten devices fit, the eleventh does not.
    for i in range(10):
        r = await client.post("/api/devices", headers=_auth(token), json={"name": f"sw{i}", "host": f"192.0.2.{i + 1}", "device_type": "cisco_ios"})
        assert r.status_code == 201, r.text
    eleventh = await client.post("/api/devices", headers=_auth(token), json={"name": "sw10", "host": "192.0.2.50", "device_type": "cisco_ios"})
    assert eleventh.status_code == 402 and "up to 10 devices" in eleventh.text

    # Paid features refuse with 402 and a message pointing at Settings.
    schedule = await client.post("/api/schedules", headers=_auth(token), json={"name": "Nightly", "frequency": "daily", "run_at_hour": 2, "run_at_minute": 0})
    assert schedule.status_code == 402 and "Scheduled backups" in schedule.text and "Settings > Licence" in schedule.text
    alerts = await client.put("/api/alerts/settings", headers=_auth(token), json={"recipients": ["noc@example.com"], "smtp_host": "smtp.example.com", "smtp_port": 587, "smtp_starttls": True, "smtp_ssl": False, "alert_config_change": True})
    assert alerts.status_code == 402
    # ...but the config-change toggle alone (no delivery) is fine on any tier.
    toggle = await client.put("/api/alerts/settings", headers=_auth(token), json={"recipients": [], "smtp_port": 587, "smtp_starttls": True, "smtp_ssl": False, "alert_config_change": False})
    assert toggle.status_code == 200
    monitor_body = {"enabled": True, "interval_minutes": 5, "alert_link_down": True, "alert_link_up": False, "alert_ap_down": True, "alert_ap_up": False, "alert_device_down": True, "alert_device_up": True, "alert_syslog_max_level": None}
    assert (await client.put("/api/snmp/monitor", headers=_auth(token), json=monitor_body)).status_code == 402
    assert (await client.put("/api/snmp/monitor", headers=_auth(token), json={**monitor_body, "enabled": False})).status_code == 200
    assert (await client.get("/api/inventory/export.xlsx", headers=_auth(token))).status_code == 402
    assert (await client.get("/api/inventory", headers=_auth(token))).status_code == 200  # viewing is free
    second_user = await client.post("/api/users", headers=_auth(token), json={"email": f"member-{unique_email}", "password": "password123"})
    assert second_user.status_code == 402 and "More than one user" in second_user.text
    # Retention is capped at 14 days on the free tier, even "forever".
    retention = await client.patch("/api/organization", headers=_auth(token), json={"snapshot_retention_days": 365})
    assert retention.json()["snapshot_retention_days"] == 14
    forever = await client.patch("/api/organization", headers=_auth(token), json={"clear_retention": True})
    assert forever.json()["snapshot_retention_days"] == 14

    # Apply a Colony key.
    bad = await client.put("/api/licence", headers=_auth(token), json={"key": "PKR1.junk.junk"})
    assert bad.status_code == 400
    applied = await client.put("/api/licence", headers=_auth(token), json={"key": make_key("colony", expires="2099-12-31")})
    assert applied.status_code == 200, applied.text
    body = applied.json()
    assert body["tier"] == "colony" and body["valid"] and body["customer"] == "Test Co" and body["has_key"] and set(body["features"]) == set(lic.FEATURE_LABELS)
    assert body["device_count"] == 10 and body["max_devices"] == 500

    assert (await client.post("/api/devices", headers=_auth(token), json={"name": "sw10", "host": "192.0.2.50", "device_type": "cisco_ios"})).status_code == 201
    assert (await client.post("/api/schedules", headers=_auth(token), json={"name": "Nightly", "frequency": "daily", "run_at_hour": 2, "run_at_minute": 0})).status_code == 201
    assert (await client.put("/api/snmp/monitor", headers=_auth(token), json=monitor_body)).status_code == 200
    assert (await client.post("/api/users", headers=_auth(token), json={"email": f"member-{unique_email}", "password": "password123"})).status_code == 201
    assert (await client.patch("/api/organization", headers=_auth(token), json={"clear_retention": True})).json()["snapshot_retention_days"] is None

    # Members see the licence but cannot change it; removing it drops back to Nest.
    member = (await client.post("/api/auth/login", json={"email": f"member-{unique_email}", "password": "password123"})).json()["access_token"]
    assert (await client.get("/api/licence", headers=_auth(member))).status_code == 200
    assert (await client.put("/api/licence", headers=_auth(member), json={"key": make_key("warren")})).status_code == 403
    removed = await client.delete("/api/licence", headers=_auth(token))
    assert removed.json()["tier"] == "nest" and removed.json()["has_key"] is False
    assert (await client.post("/api/devices", headers=_auth(token), json={"name": "sw11", "host": "192.0.2.51", "device_type": "cisco_ios"})).status_code == 402


async def test_expired_key_is_reported_and_refused(client: AsyncClient, unique_email, free_tier):
    token = await _register(client, unique_email)
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    refused = await client.put("/api/licence", headers=_auth(token), json={"key": make_key("colony", expires=yesterday)})
    assert refused.status_code == 400 and "expired" in refused.text


async def test_beat_dispatchers_skip_free_tier_orgs(client: AsyncClient, unique_email, free_tier, monkeypatch):
    """A schedule created while licensed keeps existing after the key goes,
    but the scheduler leaves it alone until a key is back."""
    token = await _register(client, unique_email)
    await client.put("/api/licence", headers=_auth(token), json={"key": make_key("colony")})
    await client.post("/api/credentials", headers=_auth(token), json={"name": "lab", "username": "admin", "password": "cisco123"})
    await client.post("/api/devices", headers=_auth(token), json={"name": "sw1", "host": "192.0.2.1", "device_type": "cisco_ios"})
    created = await client.post("/api/schedules", headers=_auth(token), json={"name": "Hourly", "frequency": "every_n_hours", "interval_hours": 1})
    assert created.status_code == 201, created.text
    await client.delete("/api/licence", headers=_auth(token))

    from app.db_sync import SyncSessionLocal  # noqa: PLC0415
    from app.models.schedule import Schedule  # noqa: PLC0415
    from datetime import datetime, timezone  # noqa: PLC0415

    db = SyncSessionLocal()
    try:
        schedule = db.get(Schedule, __import__("uuid").UUID(created.json()["id"]))
        schedule.next_run_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        db.commit()
    finally:
        db.close()
    ran = []
    monkeypatch.setattr(tasks_module, "_run_one_schedule", lambda db, s, now: ran.append(s.name))
    tasks_module.run_due_schedules()
    assert ran == []
    await client.put("/api/licence", headers=_auth(token), json={"key": make_key("colony")})
    tasks_module.run_due_schedules()
    assert ran == ["Hourly"]


def test_development_override_is_ignored_outside_development(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "licence_override_tier", "warren")
    monkeypatch.setattr(settings, "environment", "production")
    assert lic.development_override() is None
    monkeypatch.setattr(settings, "environment", "development")
    assert lic.development_override().tier.key == "warren"
