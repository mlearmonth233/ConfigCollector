"""Unattended passcode MFA: a stored TOTP seed generates the code."""

import pytest
from httpx import AsyncClient

from app import tasks as tasks_module
from app.core import totp

pytestmark = pytest.mark.asyncio

# RFC 6238 appendix B test vectors (SHA-1, the 20-byte ASCII seed "12345678901234567890").
RFC_SEED_B32 = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"


def test_rfc6238_vectors():
    assert totp.totp_code(RFC_SEED_B32, at=59, digits=8) == "94287082"
    assert totp.totp_code(RFC_SEED_B32, at=1111111109, digits=8) == "07081804"
    assert totp.totp_code(RFC_SEED_B32, at=1234567890, digits=8) == "89005924"
    assert totp.totp_code(RFC_SEED_B32, at=59) == "287082"  # the usual 6 digits


def test_secret_normalisation_accepts_app_spellings_and_rejects_junk():
    assert totp.normalize_secret("jbsw y3dp ehpk 3pxp") == "JBSWY3DPEHPK3PXP"
    assert totp.normalize_secret("JBSW-Y3DP-EHPK-3PXP") == "JBSWY3DPEHPK3PXP"
    assert totp.normalize_secret("otpauth://totp/Duo:admin?secret=JBSWY3DPEHPK3PXP&issuer=Duo") == "JBSWY3DPEHPK3PXP"
    assert totp.totp_code("jbsw y3dp ehpk 3pxp", at=59) == totp.totp_code("JBSWY3DPEHPK3PXP", at=59)
    for bad in ("", "   ", "not base32!!", "ABC"):
        with pytest.raises(totp.InvalidTotpSecret):
            totp.normalize_secret(bad)


async def _register(client: AsyncClient, email: str) -> str:
    resp = await client.post("/api/auth/register", json={"org_name": "TotpOrg", "email": email, "password": "password123"})
    assert resp.status_code == 201, resp.text
    return resp.json()["access_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def test_totp_credential_runs_unattended_and_the_code_reaches_the_device(client: AsyncClient, unique_email, monkeypatch):
    token = await _register(client, unique_email)
    cred = await client.post(
        "/api/credentials",
        headers=_auth(token),
        json={"name": "duo", "username": "admin", "password": "cisco123", "mfa_mode": "passcode", "otp_delimiter": ",", "totp_secret": "jbsw y3dp ehpk 3pxp"},
    )
    assert cred.status_code == 201, cred.text
    assert cred.json()["has_totp_secret"] is True and "totp" not in {k for k in cred.json() if "secret" in k and k != "has_totp_secret" and k != "has_enable_secret"}
    device = await client.post("/api/devices", headers=_auth(token), json={"name": "sw1", "host": "192.0.2.1", "device_type": "cisco_ios"})
    device_id = device.json()["id"]

    seen: dict = {}

    def fake_collect(**kwargs):
        seen.update(kwargs)
        return "hostname sw1\n"

    monkeypatch.setattr(tasks_module, "collect_device_config", fake_collect)

    # No passcode supplied: previously a 400, now the worker generates it.
    job = await client.post("/api/jobs", headers=_auth(token), json={"device_ids": [device_id]})
    assert job.status_code == 201, job.text
    assert job.json()["status"] == "completed"
    assert seen["mfa_mode"] == "passcode" and seen["otp"] == totp.totp_code("JBSWY3DPEHPK3PXP") and len(seen["otp"]) == 6

    # A code typed by the user still wins over the generated one.
    job2 = await client.post("/api/jobs", headers=_auth(token), json={"device_ids": [device_id], "credential_otps": {cred.json()["id"]: "111222"}})
    assert job2.status_code == 201 and seen["otp"] == "111222"


async def test_passcode_credential_without_a_seed_still_needs_a_code(client: AsyncClient, unique_email):
    token = await _register(client, unique_email)
    cred = await client.post("/api/credentials", headers=_auth(token), json={"name": "duo", "username": "admin", "password": "cisco123", "mfa_mode": "passcode"})
    assert cred.json()["has_totp_secret"] is False
    device = await client.post("/api/devices", headers=_auth(token), json={"name": "sw1", "host": "192.0.2.1", "device_type": "cisco_ios"})
    resp = await client.post("/api/jobs", headers=_auth(token), json={"device_ids": [device.json()["id"]]})
    assert resp.status_code == 400 and "one-time passcode is required" in resp.text


async def test_totp_secret_is_validated(client: AsyncClient, unique_email):
    token = await _register(client, unique_email)
    bad = await client.post("/api/credentials", headers=_auth(token), json={"name": "x", "username": "a", "password": "p", "mfa_mode": "passcode", "totp_secret": "not base32!!"})
    assert bad.status_code == 400 and "base32" in bad.text
    wrong_mode = await client.post("/api/credentials", headers=_auth(token), json={"name": "x", "username": "a", "password": "p", "mfa_mode": "none", "totp_secret": "JBSWY3DPEHPK3PXP"})
    assert wrong_mode.status_code == 400 and "passcode" in wrong_mode.text
