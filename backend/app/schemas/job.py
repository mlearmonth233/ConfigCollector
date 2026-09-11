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


class JobClearResult(BaseModel):
    deleted: int


class NeighborGapOut(BaseModel):
    # Exactly as reported by CDP/LLDP (may be an FQDN) - not normalized,
    # so it's still recognizable as the name to go add.
    name: str
    ip: str | None
    protocols: list[str]  # "cdp" and/or "lldp", whichever reported it
    # Names (not ids - a source device may since have been deleted) of
    # this job's devices that saw this neighbor.
    seen_from: list[str]


class NeighborGapCheckOut(BaseModel):
    # How many of the job's completed items had a "detail"-style CDP/LLDP
    # command actually run (not just how many devices exist in the job) -
    # 0 here means the check found nothing to look at, not that every
    # neighbor is accounted for.
    checked_item_count: int
    missing: list[NeighborGapOut]
