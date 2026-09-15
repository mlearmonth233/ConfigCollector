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
    # Per-device_type command template to run over SSH once the device has
    # (or is expected to) pull the image - required, never defaulted: unlike
    # config-collection's default_commands, there's no vendor upgrade
    # sequence this app is confident enough to guess on its own (a wrong
    # "show" command just errors; a wrong upgrade/reload sequence can brick
    # a device). Reference the transfer server with the placeholders
    # "{host}", "{port}", "{protocol}", and "{filename}", e.g.
    # "copy {protocol}://{host}:{port}/{filename} flash:, reload".
    commands_by_device_type: dict[str, str]
    credential_otps: dict[str, str] | None = None


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
