from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from app.models.job import JobStatus
from app.models.snmp import SnmpAuthProtocol, SnmpPrivProtocol, SnmpSecurityLevel, SnmpVersion


class SnmpProfileCreate(BaseModel):
    """v2c: `community`. v3: `username` + `security_level`, with
    auth_protocol/auth_password for authNoPriv and authPriv, and
    priv_protocol/priv_password for authPriv. Passwords and the community
    string are write-only - never returned by the API."""

    name: str = Field(min_length=1, max_length=255)
    version: SnmpVersion
    port: int = Field(default=161, ge=1, le=65535)
    timeout_seconds: int = Field(default=3, ge=1, le=60)
    retries: int = Field(default=1, ge=0, le=5)
    community: str | None = None
    username: str | None = None
    security_level: SnmpSecurityLevel | None = None
    auth_protocol: SnmpAuthProtocol | None = None
    auth_password: str | None = None
    priv_protocol: SnmpPrivProtocol | None = None
    priv_password: str | None = None
    context_name: str | None = None
    is_default: bool = False


class SnmpProfileUpdate(BaseModel):
    """Partial update; a secret is only changed when supplied non-empty."""

    name: str | None = Field(default=None, min_length=1, max_length=255)
    version: SnmpVersion | None = None
    port: int | None = Field(default=None, ge=1, le=65535)
    timeout_seconds: int | None = Field(default=None, ge=1, le=60)
    retries: int | None = Field(default=None, ge=0, le=5)
    community: str | None = None
    username: str | None = None
    security_level: SnmpSecurityLevel | None = None
    auth_protocol: SnmpAuthProtocol | None = None
    auth_password: str | None = None
    priv_protocol: SnmpPrivProtocol | None = None
    priv_password: str | None = None
    context_name: str | None = None


class SnmpProfileOut(BaseModel):
    id: UUID
    name: str
    is_default: bool
    version: SnmpVersion
    port: int
    timeout_seconds: int
    retries: int
    has_community: bool
    username: str | None
    security_level: SnmpSecurityLevel | None
    auth_protocol: SnmpAuthProtocol | None
    has_auth_password: bool
    priv_protocol: SnmpPrivProtocol | None
    has_priv_password: bool
    context_name: str | None
    device_count: int
    created_at: datetime


class SnmpJobCreate(BaseModel):
    device_ids: list[UUID] = Field(min_length=1)
    # Use this profile for every device in the run instead of each device's
    # own / the org default.
    snmp_profile_id: UUID | None = None
    # Extra OIDs to walk on each device (numeric dotted form), on top of the
    # built-in system / interface / syslog-history set.
    extra_oids: list[str] = []

    @field_validator("extra_oids")
    @classmethod
    def _numeric_oids(cls, value: list[str]) -> list[str]:
        cleaned = []
        for raw in value:
            oid = raw.strip().lstrip(".")
            if not oid:
                continue
            if not all(part.isdigit() for part in oid.split(".")):
                raise ValueError(f"'{raw}' is not a numeric OID (e.g. 1.3.6.1.2.1.2.2.1.2)")
            cleaned.append(oid)
        return cleaned


class SnmpJobItemOut(BaseModel):
    id: UUID
    device_id: UUID | None
    device_name: str
    device_host: str | None
    profile_name: str | None
    status: JobStatus
    error_message: str | None
    live_output: str
    report: str | None
    started_at: datetime | None
    finished_at: datetime | None


class SnmpJobOut(BaseModel):
    id: UUID
    status: JobStatus
    snmp_profile_id: UUID | None
    extra_oids: list[str]
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    item_count: int


class SnmpJobDetailOut(SnmpJobOut):
    items: list[SnmpJobItemOut]


class SnmpJobClearResult(BaseModel):
    deleted: int
