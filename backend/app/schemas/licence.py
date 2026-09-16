from datetime import date

from pydantic import BaseModel, Field


class LicenceApply(BaseModel):
    key: str = Field(min_length=10, max_length=4000)


class LicenceOut(BaseModel):
    tier: str  # nest | colony | warren
    tier_label: str
    valid: bool  # a signed, unexpired key is in effect (or a development override)
    key_id: str | None
    customer: str | None
    issued_at: date | None
    expires_at: date | None
    problem: str | None  # why an applied key does not count
    features: list[str]  # unlocked feature keys
    all_features: dict[str, str]  # feature key -> label, for the Settings page
    max_devices: int | None
    device_count: int
    max_users: int | None
    user_count: int
    max_retention_days: int | None
    has_key: bool  # a key row exists (even if it no longer counts)
