"""Time-based one-time passcodes (RFC 6238) for credentials whose MFA is a
passcode, so unattended (scheduled) collections can log in: the same
6-digit code an authenticator app would show, generated from the shared
secret the identity provider handed out when MFA was enrolled.

Standard library only (hmac + base32); no dependency."""

from __future__ import annotations

import base64
import hmac
import struct
import time


class InvalidTotpSecret(ValueError):
    pass


def normalize_secret(secret: str) -> str:
    """Accepts the usual authenticator-app spellings: spaces or dashes for
    readability, lower case, missing '=' padding. Returns canonical upper-
    case base32. An 'otpauth://' URI's secret= parameter is accepted too."""
    raw = secret.strip()
    if raw.lower().startswith("otpauth://"):
        from urllib.parse import parse_qs, urlparse  # noqa: PLC0415

        raw = parse_qs(urlparse(raw).query).get("secret", [""])[0]
    cleaned = "".join(ch for ch in raw.upper() if ch not in " -_")
    if not cleaned:
        raise InvalidTotpSecret("The TOTP secret is empty")
    padded = cleaned + "=" * (-len(cleaned) % 8)
    try:
        if len(base64.b32decode(padded, casefold=True)) < 10:
            raise InvalidTotpSecret("The TOTP secret is too short to be a real one")
    except Exception as exc:  # noqa: BLE001 - binascii.Error and friends
        raise InvalidTotpSecret("The TOTP secret is not valid base32 (the letters and digits an authenticator app shows)") from exc
    return cleaned


def totp_code(secret: str, *, at: float | None = None, digits: int = 6, period: int = 30, algorithm: str = "sha1") -> str:
    """The code valid at `at` (seconds since the epoch; now by default)."""
    key = base64.b32decode(normalize_secret(secret) + "=" * (-len(normalize_secret(secret)) % 8), casefold=True)
    counter = int((time.time() if at is None else at) // period)
    digest = hmac.new(key, struct.pack(">Q", counter), algorithm).digest()
    offset = digest[-1] & 0x0F
    number = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(number % (10**digits)).zfill(digits)
