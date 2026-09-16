"""Licence keys and tiers.

A key is "PKR1.<payload>.<signature>": a base64url JSON payload signed with
Ed25519 by the vendor's private key (scripts/make_licence.py). The app
carries only the public key, so it can check a key offline and nobody can
mint one without the private half. No key, an invalid key or an expired
key all mean the free tier (Nest).

The tier table below is the single source of truth for what each tier
allows; the website's pricing section describes the same table in words.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from app.config import get_settings

# Ed25519 public key (raw, base64). The matching private key never ships.
PUBLIC_KEY_B64 = "pzjSCSbJd427Lw5N8YaendgWofKZAEoTfmAscXR6iBc="

# Features a tier can unlock. Anything not listed here is free on every tier.
FEATURE_LABELS: dict[str, str] = {
    "schedules": "Scheduled backups",
    "alert_delivery": "Alert delivery to email, Teams and Slack",
    "snmp_monitoring": "SNMP monitoring",
    "firmware_push": "Firmware push",
    "inventory_export": "Inventory Excel export",
    "multi_user": "More than one user",
}
ALL_FEATURES = frozenset(FEATURE_LABELS)


@dataclass(frozen=True)
class Tier:
    key: str
    label: str
    max_devices: int | None  # None = unlimited
    max_users: int | None
    max_retention_days: int | None  # None = unlimited history
    features: frozenset[str]


TIERS: dict[str, Tier] = {
    "nest": Tier("nest", "Nest", max_devices=10, max_users=1, max_retention_days=14, features=frozenset()),
    "colony": Tier("colony", "Colony", max_devices=500, max_users=None, max_retention_days=None, features=ALL_FEATURES),
    "warren": Tier("warren", "Warren", max_devices=None, max_users=None, max_retention_days=None, features=ALL_FEATURES),
}
FREE_TIER = TIERS["nest"]
PAID_TIER_LABELS = "Colony and Warren"


class InvalidLicence(ValueError):
    pass


@dataclass
class LicenceInfo:
    tier: Tier
    valid: bool = False  # True when a signed, unexpired key is applied
    key_id: str | None = None
    customer: str | None = None
    issued_at: date | None = None
    expires_at: date | None = None
    problem: str | None = None  # why an applied key does not count (expired, tampered...)
    max_devices: int | None = None  # after any per-key override
    features: frozenset[str] = field(default_factory=frozenset)

    @classmethod
    def free(cls, problem: str | None = None) -> "LicenceInfo":
        return cls(tier=FREE_TIER, problem=problem, max_devices=FREE_TIER.max_devices, features=FREE_TIER.features)

    def has(self, feature: str) -> bool:
        return feature in self.features


_public_key: Ed25519PublicKey = Ed25519PublicKey.from_public_bytes(base64.b64decode(PUBLIC_KEY_B64))


def configure_public_key(b64: str) -> None:
    """Tests (and a vendor rotating keys) swap the verifying key."""
    global _public_key  # noqa: PLW0603
    _public_key = Ed25519PublicKey.from_public_bytes(base64.b64decode(b64))


def _b64url_decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def parse_key(key: str) -> dict:
    """Checks the signature and returns the payload; raises InvalidLicence."""
    raw = (key or "").strip()
    parts = raw.split(".")
    if len(parts) != 3 or parts[0] != "PKR1":
        raise InvalidLicence("That is not a Packrat licence key (expected PKR1.<payload>.<signature>)")
    try:
        payload_bytes = _b64url_decode(parts[1])
        signature = _b64url_decode(parts[2])
    except Exception as exc:  # noqa: BLE001
        raise InvalidLicence("The licence key is damaged - copy it again exactly as it was sent") from exc
    try:
        _public_key.verify(signature, payload_bytes)
    except InvalidSignature as exc:
        raise InvalidLicence("The licence key's signature does not check out - it was altered or was not issued for this product") from exc
    try:
        payload = json.loads(payload_bytes)
    except ValueError as exc:
        raise InvalidLicence("The licence key's contents are unreadable") from exc
    if payload.get("tier") not in TIERS or payload.get("tier") == "nest":
        raise InvalidLicence("The licence key names an unknown tier")
    return payload


def licence_from_key(key: str | None, *, today: date | None = None) -> LicenceInfo:
    """The tier a stored key grants right now. Never raises: a bad or
    expired key yields the free tier with `problem` explaining why."""
    if not key:
        return LicenceInfo.free()
    try:
        payload = parse_key(key)
    except InvalidLicence as exc:
        return LicenceInfo.free(problem=str(exc))
    tier = TIERS[payload["tier"]]
    today = today or datetime.now(timezone.utc).date()
    expires = _parse_date(payload.get("expires"))
    issued = _parse_date(payload.get("issued"))
    info = LicenceInfo(
        tier=tier,
        valid=True,
        key_id=payload.get("id"),
        customer=payload.get("customer"),
        issued_at=issued,
        expires_at=expires,
        max_devices=payload.get("devices", tier.max_devices),
        features=tier.features,
    )
    if expires is not None and today > expires:
        free = LicenceInfo.free(problem=f"The {tier.label} licence expired on {expires.isoformat()}")
        free.key_id, free.customer, free.expires_at = info.key_id, info.customer, expires
        return free
    return info


def _parse_date(value) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def development_override() -> LicenceInfo | None:
    """LICENCE_OVERRIDE_TIER=colony|warren makes a development or test
    install behave as that tier without a key. Ignored in production."""
    settings = get_settings()
    tier_key = (settings.licence_override_tier or "").strip().lower()
    if not tier_key or settings.environment.lower() not in ("development", "dev", "test", "testing"):
        return None
    tier = TIERS.get(tier_key)
    if tier is None:
        return None
    return LicenceInfo(tier=tier, valid=True, key_id="dev-override", customer="development override", max_devices=tier.max_devices, features=tier.features)


def effective_retention_days(configured: int | None, info: LicenceInfo) -> int | None:
    """The retention the org actually gets: its own setting, capped by the
    tier. None = keep forever (paid tiers only)."""
    cap = info.tier.max_retention_days
    if cap is None:
        return configured
    if configured is None:
        return cap
    return min(configured, cap)


def upgrade_message(feature: str, info: LicenceInfo) -> str:
    label = FEATURE_LABELS.get(feature, feature)
    tail = f" ({info.problem})" if info.problem else ""
    return f"{label} is part of {PAID_TIER_LABELS}. This organization is on {info.tier.label}{tail}. Enter a licence key under Settings > Licence to unlock it."
