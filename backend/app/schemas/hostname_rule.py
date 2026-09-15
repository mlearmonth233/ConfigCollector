from pydantic import BaseModel, Field, field_validator

from app.models.device import NetworkZone
from app.services.hostname_detection import MatchMode


class HostnameRuleIn(BaseModel):
    pattern: str = Field(min_length=1, max_length=255)
    match_mode: MatchMode = MatchMode.CONTAINS
    device_role: str | None = None
    role_label: str | None = None
    network_zone: NetworkZone | None = None
    device_type: str | None = None

    @field_validator("device_role")
    @classmethod
    def _slug(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip().lower().replace(" ", "_").replace("-", "_")
        return value or None

    @field_validator("role_label", "device_type", "pattern")
    @classmethod
    def _strip(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None


class HostnameRulesReplace(BaseModel):
    """The org's complete rule list, in order - saving replaces what was
    there. An empty list means "go back to the built-in rules"."""

    rules: list[HostnameRuleIn]


class HostnameRuleOut(HostnameRuleIn):
    sort_order: int


class HostnameRulesOut(BaseModel):
    rules: list[HostnameRuleOut]
    # True when the org has saved no rules of its own and the built-ins
    # shown in `rules` are what applies.
    using_builtin: bool


class HostnameTestIn(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    # Optionally test rules that haven't been saved yet (the editor's
    # current state) instead of the org's saved ones.
    rules: list[HostnameRuleIn] | None = None
