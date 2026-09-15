import re
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from app.services.device_types import DEVICE_TYPE_REGISTRY

_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")

CATEGORIES = ("switch", "router", "firewall", "wlc", "pdu", "server", "console_server", "other")


def _clean_commands(raw: str | list[str]) -> str:
    """Accepts a list, or a string with commands separated by commas and/or
    newlines (a textarea's natural format); stored comma-separated, the same
    format Device.custom_commands and CommandProfile.commands use."""
    if isinstance(raw, str):
        items = [c.strip() for c in re.split(r"[\n,]", raw) if c.strip()]
    else:
        items = [c.strip() for c in raw if c.strip()]
    return ", ".join(items)


class CustomDeviceTypeCreate(BaseModel):
    # Stable identifier stored on devices, e.g. "juniper_srx". Lowercase
    # letters, digits and underscores; must not collide with a built-in.
    key: str = Field(min_length=2, max_length=64)
    label: str = Field(min_length=1, max_length=255)
    category: str = "other"
    netmiko_driver: str = Field(min_length=1, max_length=64)
    # Comma-separated string or a list - stored comma-separated either way.
    default_commands: str | list[str] = ""
    secret_supported: bool = False
    timing_read: bool = True

    @field_validator("key")
    @classmethod
    def _valid_key(cls, value: str) -> str:
        value = value.strip().lower()
        if not _KEY_RE.match(value):
            raise ValueError("key must be 2-64 characters: lowercase letters, digits and underscores, starting with a letter")
        if value in DEVICE_TYPE_REGISTRY:
            raise ValueError(f"'{value}' is a built-in device type - pick another key")
        return value

    @field_validator("category")
    @classmethod
    def _valid_category(cls, value: str) -> str:
        value = value.strip().lower() or "other"
        if value not in CATEGORIES:
            raise ValueError("category must be one of: " + ", ".join(CATEGORIES))
        return value

    @field_validator("default_commands")
    @classmethod
    def _clean(cls, value: str | list[str]) -> str:
        return _clean_commands(value)


class CustomDeviceTypeUpdate(BaseModel):
    """Partial update. The key itself can't change - devices store it."""

    label: str | None = Field(default=None, min_length=1, max_length=255)
    category: str | None = None
    netmiko_driver: str | None = Field(default=None, min_length=1, max_length=64)
    default_commands: str | list[str] | None = None
    secret_supported: bool | None = None
    timing_read: bool | None = None

    @field_validator("category")
    @classmethod
    def _valid_category(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip().lower() or "other"
        if value not in CATEGORIES:
            raise ValueError("category must be one of: " + ", ".join(CATEGORIES))
        return value

    @field_validator("default_commands")
    @classmethod
    def _clean(cls, value: str | list[str] | None) -> str | None:
        return None if value is None else _clean_commands(value)


class CustomDeviceTypeOut(BaseModel):
    id: UUID
    key: str
    label: str
    category: str
    netmiko_driver: str
    default_commands: list[str]
    secret_supported: bool
    timing_read: bool
    device_count: int
    created_at: datetime


class NetmikoDriversOut(BaseModel):
    drivers: list[str]
    categories: list[str]
