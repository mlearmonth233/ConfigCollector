"""Changing CREDENTIAL_ENCRYPTION_KEY must never lock stored passwords away:
old keys keep decrypting, the API re-encrypts everything at start-up, and
the terminal explains an unreadable secret instead of dropping the
connection."""

from uuid import UUID

import pytest
from cryptography.fernet import Fernet
from httpx import AsyncClient

from app.config import INSECURE_ENCRYPTION_KEY
from app.core import encryption
from app.db_sync import SyncSessionLocal
from app.models.credential import Credential
from app.services import secret_rekey
from app.api import terminal

pytestmark = pytest.mark.asyncio

NEW_KEY = Fernet.generate_key().decode()
OTHER_KEY = Fernet.generate_key().decode()


@pytest.fixture
def keyring():
    """Restores the module key ring after each test."""
    current = encryption._primary  # noqa: SLF001
    ring = encryption._all  # noqa: SLF001
    yield encryption.configure_keys
    encryption._primary = current  # noqa: SLF001
    encryption._all = ring  # noqa: SLF001


def test_secrets_saved_under_the_placeholder_key_still_decrypt_after_a_key_change(keyring):
    keyring(INSECURE_ENCRYPTION_KEY)
    old = encryption.encrypt_secret("cisco123")
    keyring(NEW_KEY)  # what dev-env.ps1 does on first run: a generated key, no previous keys listed
    assert encryption.decrypt_secret(old) == "cisco123"
    assert encryption.needs_rekey(old) is True
    fresh = encryption.encrypt_secret("cisco123")
    assert encryption.needs_rekey(fresh) is False
    rotated = encryption.rekey(old)
    assert rotated != old and encryption.decrypt_secret(rotated) == "cisco123" and encryption.needs_rekey(rotated) is False


def test_explicit_previous_keys_are_honoured_and_unknown_keys_are_reported(keyring):
    keyring(OTHER_KEY)
    secret = encryption.encrypt_secret("s3cret")
    keyring(NEW_KEY)
    with pytest.raises(encryption.SecretUndecryptable) as info:
        encryption.decrypt_secret(secret)
    assert "CREDENTIAL_ENCRYPTION_PREVIOUS_KEYS" in str(info.value)
    assert encryption.needs_rekey(secret) is False  # nothing can read it, so nothing to rotate
    keyring(NEW_KEY, [OTHER_KEY])
    assert encryption.decrypt_secret(secret) == "s3cret" and encryption.needs_rekey(secret) is True


async def _register(client: AsyncClient, email: str) -> str:
    resp = await client.post("/api/auth/register", json={"org_name": "RekeyOrg", "email": email, "password": "password123"})
    assert resp.status_code == 201, resp.text
    return resp.json()["access_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def test_startup_rekey_rotates_every_stored_secret(client: AsyncClient, unique_email, keyring):
    token = await _register(client, unique_email)
    keyring(INSECURE_ENCRYPTION_KEY)
    cred = await client.post(
        "/api/credentials", headers=_auth(token), json={"name": "lab", "username": "admin", "password": "cisco123", "enable_secret": "en4ble"}
    )
    assert cred.status_code == 201, cred.text
    prof = await client.post("/api/snmp/profiles", headers=_auth(token), json={"name": "ro", "version": "v2c", "community": "public"})
    assert prof.status_code == 201, prof.text
    smtp = await client.put(
        "/api/alerts/settings",
        headers=_auth(token),
        json={"recipients": ["noc@example.com"], "smtp_host": "smtp.example.com", "smtp_port": 587, "smtp_password": "mailpass", "smtp_starttls": True, "smtp_ssl": False, "alert_config_change": True},
    )
    assert smtp.status_code == 200, smtp.text

    keyring(NEW_KEY)  # the install now runs on a generated key
    rotated = secret_rekey.rekey_stored_secrets(SyncSessionLocal)
    assert rotated >= 4  # password, enable secret, community, smtp password (other tests' rows may add to it)

    db = SyncSessionLocal()
    try:
        row = db.get(Credential, UUID(cred.json()["id"]))
        assert not encryption.needs_rekey(row.encrypted_password) and not encryption.needs_rekey(row.encrypted_enable_secret)
    finally:
        db.close()
    # Everything now reads with the current key alone.
    keyring(NEW_KEY)
    encryption._all = encryption.MultiFernet([encryption.Fernet(NEW_KEY.encode())])  # noqa: SLF001 - drop the fallbacks on purpose
    db = SyncSessionLocal()
    try:
        row = db.get(Credential, UUID(cred.json()["id"]))
        assert encryption.decrypt_secret(row.encrypted_password) == "cisco123"
        assert encryption.decrypt_secret(row.encrypted_enable_secret) == "en4ble"
    finally:
        db.close()
    # Running again finds nothing to do.
    keyring(NEW_KEY)
    assert secret_rekey.rekey_stored_secrets(SyncSessionLocal) == 0


async def test_terminal_explains_an_undecryptable_credential_instead_of_crashing(client: AsyncClient, unique_email, keyring):
    token = await _register(client, unique_email)
    keyring(OTHER_KEY)
    await client.post("/api/credentials", headers=_auth(token), json={"name": "lab", "username": "admin", "password": "cisco123"})
    device = await client.post("/api/devices", headers=_auth(token), json={"name": "sw1", "host": "192.0.2.1", "device_type": "cisco_ios"})
    assert device.status_code == 201, device.text

    keyring(NEW_KEY)  # OTHER_KEY was lost: the secret is unreadable
    result = await terminal.authorize(token, UUID(device.json()["id"]), None)
    assert isinstance(result, terminal.AuthorizationError)
    assert "Credential 'lab'" in result.message and "CREDENTIAL_ENCRYPTION_KEY has changed" in result.message
