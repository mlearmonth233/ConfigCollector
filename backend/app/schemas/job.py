from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from app.models.job import JobStatus


class JobCreate(BaseModel):
    device_ids: list[UUID] | None = None  # None/omitted = all devices in org
    # Per-device_type command override for this run only (does not persist
    # to Device.custom_commands) - e.g. {"cisco_ios": "show running-config",
    # "apc_pdu": "about,show status"}. Falls back to each device's own
    # custom_commands, then the device type's registry default.
    commands_by_device_type: dict[str, str] | None = None
    # One-time passcode per credential (keyed by credential id, as a
    # string), required for any credential whose mfa_mode is "passcode" -
    # including a fallback credential, since which one ends up needed isn't
    # known until the device is actually contacted. Never stored - used
    # once to authenticate, then discarded.
    credential_otps: dict[str, str] | None = None


class JobItemOut(BaseModel):
    id: UUID
    device_id: UUID | None
    device_name: str
    status: JobStatus
    error_message: str | None
    used_fallback_credential: bool
    live_output: str
    started_at: datetime | None
    finished_at: datetime | None
    snapshot_id: UUID | None

    model_config = {"from_attributes": True}


class JobOut(BaseModel):
    id: UUID
    status: JobStatus
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    item_count: int

    model_config = {"from_attributes": True}


class JobDetailOut(JobOut):
    items: list[JobItemOut]
