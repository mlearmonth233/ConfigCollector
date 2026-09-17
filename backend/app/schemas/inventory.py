from datetime import datetime

from pydantic import BaseModel


class InventoryDeviceOut(BaseModel):
    device_id: str
    name: str
    host: str
    device_type: str
    site: str | None
    role: str | None
    hostname: str | None
    model: str | None
    serial: str | None
    software: str | None
    uptime: str | None
    base_mac: str | None
    collected_at: datetime | None
    snapshot_id: str | None
    has_snapshot: bool
    commands_present: list[str]
    commands_missing: list[str]


class HardwareItemOut(BaseModel):
    device_id: str
    device_name: str
    name: str
    description: str | None
    pid: str | None
    vid: str | None
    serial: str | None


class NeighborOut(BaseModel):
    device_id: str
    device_name: str
    local_port: str | None
    name: str | None
    ip: str | None
    platform: str | None
    capabilities: str | None
    remote_port: str | None
    protocol: str
    managed_device_id: str | None
    managed_device_name: str | None


class AccessPointOut(BaseModel):
    controller_id: str
    controller_name: str
    name: str
    model: str | None
    mac: str | None
    ip: str | None
    serial: str | None
    software: str | None


class EndpointOut(BaseModel):
    device_id: str
    device_name: str
    port: str
    vlan: str | None
    mac: str
    ip: str | None
    entry_type: str | None
    on_uplink: bool
    manufacturer: str | None = None


class UnmanagedDeviceOut(BaseModel):
    name: str | None
    ip: str | None
    platform: str | None
    capabilities: str | None
    kind: str
    seen_from: list[str]
    protocols: list[str]


class SubnetAddressOut(BaseModel):
    device_id: str
    device_name: str
    interface: str
    ip: str
    description: str | None
    vlan: str | None
    vrf: str | None
    secondary: bool


class SubnetOut(BaseModel):
    network: str
    prefix_len: int
    mask: str
    vlan: str | None
    name: str | None
    vrf: str | None
    addresses: list[SubnetAddressOut]
    hosts_seen: int
    usable: int
    source: str  # "config" or "seen"


class InventorySummaryOut(BaseModel):
    devices: int
    devices_with_config: int
    devices_with_serial: int
    hardware: int
    access_points: int
    neighbors: int
    unmanaged: int
    endpoints: int  # on access ports (excludes uplinks)
    subnets: int = 0


class InventoryOut(BaseModel):
    generated_at: datetime
    summary: InventorySummaryOut
    models: dict[str, int]
    devices: list[InventoryDeviceOut]
    hardware: list[HardwareItemOut]
    neighbors: list[NeighborOut]
    access_points: list[AccessPointOut]
    endpoints: list[EndpointOut]
    unmanaged: list[UnmanagedDeviceOut]
    subnets: list[SubnetOut] = []


class InventoryCommandCoverageOut(BaseModel):
    """Whether a device type's effective command list collects what the
    inventory reads."""

    device_type: str
    label: str
    device_count: int
    inventory_commands: list[str]
    missing: list[str]  # canonical commands not covered by the current list
    is_custom_profile: bool
