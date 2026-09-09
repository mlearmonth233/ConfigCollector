from pydantic import BaseModel, field_validator


class CommandProfileUpdate(BaseModel):
    commands: list[str]

    @field_validator("commands")
    @classmethod
    def _non_empty_commands(cls, value: list[str]) -> list[str]:
        cleaned = [c.strip() for c in value if c.strip()]
        if not cleaned:
            raise ValueError("At least one command is required")
        return cleaned


class CommandProfileOut(BaseModel):
    device_type: str
    label: str
    category: str
    commands: list[str]
    suggested_commands: list[str]
    # True once an org has saved its own override for this device type -
    # `commands` is then that override rather than the built-in default.
    is_custom: bool
