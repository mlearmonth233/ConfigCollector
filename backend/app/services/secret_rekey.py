"""Re-encrypts stored secrets under the current CREDENTIAL_ENCRYPTION_KEY.

Runs once at API start-up (after migrations). Any value that only a
previous key can read - typically everything, right after an install moves
off the shipped placeholder key to a generated one - is rotated to the
current key, so the previous key can eventually be dropped from the
configuration. Values no key can read are counted and logged, never
touched: the user has to re-enter those.
"""

from __future__ import annotations

import logging

from app.core.encryption import needs_rekey, rekey
from app.models.alerting import AlertSettings
from app.models.credential import Credential
from app.models.snmp import SnmpProfile

log = logging.getLogger(__name__)

# Every encrypted column in the schema. Keep in step with the models.
ENCRYPTED_COLUMNS: tuple[tuple[type, tuple[str, ...]], ...] = (
    (Credential, ("encrypted_password", "encrypted_enable_secret", "encrypted_totp_secret")),
    (SnmpProfile, ("encrypted_community", "encrypted_auth_password", "encrypted_priv_password")),
    (AlertSettings, ("encrypted_smtp_password",)),
)


def rekey_stored_secrets(session_factory) -> int:
    """Returns how many values were re-encrypted."""
    from app.core.encryption import decrypt_secret, SecretUndecryptable  # noqa: PLC0415

    rotated = 0
    unreadable: dict[str, int] = {}
    db = session_factory()
    try:
        for model, columns in ENCRYPTED_COLUMNS:
            for row in db.query(model).all():
                for column in columns:
                    value = getattr(row, column)
                    if not value:
                        continue
                    if needs_rekey(value):
                        setattr(row, column, rekey(value))
                        rotated += 1
                        continue
                    try:
                        decrypt_secret(value)
                    except SecretUndecryptable:
                        unreadable[model.__tablename__] = unreadable.get(model.__tablename__, 0) + 1
        if rotated:
            db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

    if rotated:
        log.warning("Re-encrypted %d stored secret(s) with the current CREDENTIAL_ENCRYPTION_KEY (they were saved under a previous key).", rotated)
    if unreadable:
        where = ", ".join(f"{n} in {table}" for table, n in unreadable.items())
        log.error(
            "%s stored secret(s) cannot be decrypted with the current CREDENTIAL_ENCRYPTION_KEY or any previous key (%s). "
            "Add the key they were saved with to CREDENTIAL_ENCRYPTION_PREVIOUS_KEYS in backend/.env and restart, or re-enter those passwords.",
            sum(unreadable.values()),
            where,
        )
    return rotated
