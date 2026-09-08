"""Encryption for secrets (device/credential passwords) stored at rest.

Uses Fernet (symmetric, authenticated) keyed by CREDENTIAL_ENCRYPTION_KEY. The
key never touches the database - only ciphertext is stored - so a DB leak
alone does not expose device passwords.
"""

from cryptography.fernet import Fernet

from app.config import get_settings

settings = get_settings()
_fernet = Fernet(settings.credential_encryption_key.encode())


def encrypt_secret(plaintext: str) -> str:
    return _fernet.encrypt(plaintext.encode()).decode()


def decrypt_secret(ciphertext: str) -> str:
    return _fernet.decrypt(ciphertext.encode()).decode()
