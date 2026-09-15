from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from app.models.firmware import TransferProtocol
from app.models.job import JobStatus


class FirmwareImageOut(BaseModel):
    id: UUID
    original_filename: str
    label: str | None
    size_bytes: int
    created_at: datetime

    model_config = {"from_attributes": True}


class NetworkInterfaceOut(BaseModel):
    name: str
    address: str


class FirmwareJobCreate(BaseModel):
    firmware_image_id: UUID
    device_ids: list[UUID]
    protocol: TransferProtocol
    # Which of *this app's own machine's* network interfaces devices should
    # use to reach the transfer server for this job (see GET
    # /api/firmware/network-interfaces and services/network_interfaces.py) -
    # a per-job choice, not a fixed setting, since a multi-homed host may
    # only be reachable from a given device on one of its addresses.
    server_host: str
    # Per-device_type copy command template to run over SSH - the command
    # that makes the device pull the image from this app's transfer server
    # onto its own storage, and nothing more (no install/reload: this is a
    # file push). Optional per type: any type left out falls back to
    # services/firmware_push.py's DEFAULT_PUSH_COMMANDS (e.g. "copy {url}
    # flash:" for Cisco IOS); a type with no default there must be given
    # one here. Placeholders: "{url}" (the ready-made, protocol-aware URL),
    # or the pieces it's built from - "{protocol}", "{host}", "{port}",
    # "{filename}".
    commands_by_device_type: dict[str, str] = {}
    credential_otps: dict[str, str] | None = None


class PushDefaultsOut(BaseModel):
    # device_type -> default copy command template (see FirmwareJobCreate),
    # for types that have one; the UI prefills these so the common case
    # needs no typing while still leaving every command editable.
    commands_by_device_type: dict[str, str]
    placeholders: list[str]


class FirmwareJobItemOut(BaseModel):
    id: UUID
    device_id: UUID | None
    device_name: str
    status: JobStatus
    error_message: str | None
    live_output: str
    started_at: datetime | None
    finished_at: datetime | None

    model_config = {"from_attributes": True}


class FirmwareJobOut(BaseModel):
    cancel_requested: bool = False
    id: UUID
    firmware_image_id: UUID | None
    protocol: TransferProtocol
    status: JobStatus
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    item_count: int

    model_config = {"from_attributes": True}


class FirmwareJobDetailOut(FirmwareJobOut):
    items: list[FirmwareJobItemOut]
