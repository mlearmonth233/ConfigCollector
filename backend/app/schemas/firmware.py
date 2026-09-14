import re
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from app.models.firmware import FirmwareJobStatus

# Plain flash-filesystem-safe filenames only: this becomes part of a
# "copy scp://..." CLI command built by this app (see services/
# firmware_push.py) and the SCP grant's expected remote filename (see
# services/scp_grants.py) - no spaces, quotes, or path separators that could
# complicate either.
_SAFE_FILENAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,254}$")


def _validate_flash_filename(value: str) -> str:
    if not _SAFE_FILENAME_RE.match(value):
        raise ValueError(
            "must be a plain filename - letters, digits, '.', '_', '-' only, no spaces or path separators"
        )
    return value


class FirmwareImageOut(BaseModel):
    id: UUID
    filename: str
    description: str | None
    size_bytes: int
    md5: str
    created_at: datetime

    model_config = {"from_attributes": True}


class FirmwareImageUpdate(BaseModel):
    description: str | None = None


class FirmwareJobCreate(BaseModel):
    firmware_image_id: UUID
    device_ids: list[UUID] = Field(min_length=1)
    # Filename to write into each device's flash: - defaults to the image's
    # own filename when omitted.
    target_filename: str | None = None
    verify_checksum: bool = True
    reload_after: bool = False
    # One-time passcode per credential (keyed by credential id, as a
    # string) - same contract as jobs.py's JobCreate.credential_otps.
    credential_otps: dict[str, str] | None = None

    @field_validator("target_filename")
    @classmethod
    def _check_target_filename(cls, value: str | None) -> str | None:
        return _validate_flash_filename(value) if value is not None else None


class FirmwareJobItemOut(BaseModel):
    id: UUID
    device_id: UUID | None
    device_name: str
    status: FirmwareJobStatus
    error_message: str | None
    used_fallback_credential: bool
    checksum_verified: bool | None
    live_output: str
    started_at: datetime | None
    finished_at: datetime | None

    model_config = {"from_attributes": True}


class FirmwareJobOut(BaseModel):
    id: UUID
    status: FirmwareJobStatus
    firmware_filename: str
    target_filename: str
    verify_checksum: bool
    reload_after: bool
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    item_count: int

    model_config = {"from_attributes": True}


class FirmwareJobDetailOut(FirmwareJobOut):
    items: list[FirmwareJobItemOut]
