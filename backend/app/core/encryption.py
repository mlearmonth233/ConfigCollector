"""Encryption for secrets (device/credential passwords) stored at rest.

Uses Fernet (symmetric, authenticated) keyed by CREDENTIAL_ENCRYPTION_KEY. The
key never touches the database - only ciphertext is stored - so a DB leak
alone does not expose device passwords.

Key rotation: new secrets are always encrypted with the current key, but
decryption also tries every *previous* key (CREDENTIAL_ENCRYPTION_PREVIOUS_KEYS,
plus the placeholder key the app ships with), so changing the key never
makes stored passwords unreadable. services/secret_rekey.py re-encrypts
anything still under an old key when the API starts.
"""

from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from app.config import INSECURE_ENCRYPTION_KEY, get_settings


class SecretUndecryptable(Exception):
    """A stored secret cannot be decrypted with the current key or any
    previous one - CREDENTIAL_ENCRYPTION_KEY changed and the old value was
    not kept."""

    def __init__(self) -> None:
        super().__init__(
            "A stored password can't be decrypted: CREDENTIAL_ENCRYPTION_KEY has changed since it was saved. "
            "Either put the previous key in CREDENTIAL_ENCRYPTION_PREVIOUS_KEYS (backend/.env) and restart, "
            "or re-enter the password on the Credentials page."
        )


# Replaced by configure_keys() below (module import) - placeholders so the
# names exist for tooling.
_primary: Fernet = Fernet(INSECURE_ENCRYPTION_KEY.encode())
_all: MultiFernet = MultiFernet([_primary])


def configure_keys(current: str, previous: list[str] | tuple[str, ...] = ()) -> None:
    """(Re)builds the key ring: `current` encrypts, everything decrypts."""
    global _primary, _all  # noqa: PLW0603 - module-level key ring by design
    ring = [current]
    for key in [*previous, INSECURE_ENCRYPTION_KEY]:
        if key and key not in ring:
            ring.append(key)
    _primary = Fernet(current.encode())
    _all = MultiFernet([Fernet(k.encode()) for k in ring])


def _previous_keys_from_settings() -> list[str]:
    return [k.strip() for k in get_settings().credential_encryption_previous_keys.split(",") if k.strip()]


configure_keys(get_settings().credential_encryption_key, _previous_keys_from_settings())


def encrypt_secret(plaintext: str) -> str:
    return _primary.encrypt(plaintext.encode()).decode()


def decrypt_secret(ciphertext: str) -> str:
    try:
        return _all.decrypt(ciphertext.encode()).decode()
    except InvalidToken:
        raise SecretUndecryptable() from None


def needs_rekey(ciphertext: str) -> bool:
    """True when the value decrypts, but only with a previous key."""
    token = ciphertext.encode()
    try:
        _primary.decrypt(token)
        return False
    except InvalidToken:
        pass
    try:
        _all.decrypt(token)
        return True
    except InvalidToken:
        return False  # unreadable by any key: rekey can't help, decrypt_secret reports it


def rekey(ciphertext: str) -> str:
    """Re-encrypts a value under the current key (same plaintext)."""
    return _all.rotate(ciphertext.encode()).decode()
