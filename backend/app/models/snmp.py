import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import GUID, Base_, UTCDateTime
from app.models.job import JobStatus


class SnmpVersion(str, enum.Enum):
    V2C = "v2c"
    V3 = "v3"


class SnmpSecurityLevel(str, enum.Enum):
    NO_AUTH_NO_PRIV = "noAuthNoPriv"
    AUTH_NO_PRIV = "authNoPriv"
    AUTH_PRIV = "authPriv"


class SnmpAuthProtocol(str, enum.Enum):
    MD5 = "MD5"
    SHA = "SHA"
    SHA224 = "SHA224"
    SHA256 = "SHA256"
    SHA384 = "SHA384"
    SHA512 = "SHA512"


class SnmpPrivProtocol(str, enum.Enum):
    DES = "DES"
    TRIPLE_DES = "3DES"
    AES128 = "AES128"
    AES192 = "AES192"
    AES256 = "AES256"


class SnmpProfile(Base_):
    """How to talk SNMP to a device: a v2c community string, or a v3 user
    with its security level and auth/privacy protocols and passwords. The
    SNMP counterpart of Credential - secrets are Fernet-encrypted at rest
    (see core/encryption.py) and never returned by the API. One profile per
    org can be the default, used by any device without its own
    snmp_profile_id; a poll job can also name a profile to use for the run."""

    __tablename__ = "snmp_profiles"

    org_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("organizations.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    version: Mapped[SnmpVersion] = mapped_column(Enum(SnmpVersion), nullable=False)
    port: Mapped[int] = mapped_column(Integer, default=161, nullable=False)
    # Per-request timeout and retry count. SNMP is UDP; a device that
    # doesn't answer just goes quiet, so these bound how long a poll of an
    # unreachable device takes.
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    retries: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    # v2c
    encrypted_community: Mapped[str | None] = mapped_column(String(512), nullable=True)

    # v3
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    security_level: Mapped[SnmpSecurityLevel | None] = mapped_column(Enum(SnmpSecurityLevel), nullable=True)
    auth_protocol: Mapped[SnmpAuthProtocol | None] = mapped_column(Enum(SnmpAuthProtocol), nullable=True)
    encrypted_auth_password: Mapped[str | None] = mapped_column(String(512), nullable=True)
    priv_protocol: Mapped[SnmpPrivProtocol | None] = mapped_column(Enum(SnmpPrivProtocol), nullable=True)
    encrypted_priv_password: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # Optional SNMPv3 context name (some multi-instance platforms need one).
    context_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    organization: Mapped["Organization"] = relationship(back_populates="snmp_profiles")


class SnmpJob(Base_):
    """One 'poll these devices over SNMP' request - system details, the
    interface error table and the device's own syslog history buffer, plus
    any extra OIDs asked for - fanning out to one SnmpJobItem per device.
    Same shape and status lifecycle as CollectionJob."""

    __tablename__ = "snmp_jobs"

    org_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("organizations.id"), nullable=False, index=True)
    created_by_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("users.id"), nullable=False)
    # Profile chosen for this run, overriding each device's own/the org
    # default. Nullable + SET NULL so deleting a profile keeps job history.
    snmp_profile_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("snmp_profiles.id", ondelete="SET NULL"), nullable=True
    )
    # Comma-separated extra OIDs to walk on every device, on top of the
    # built-in set (see services/snmp_poll.py).
    extra_oids: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[JobStatus] = mapped_column(Enum(JobStatus), default=JobStatus.PENDING, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    organization: Mapped["Organization"] = relationship(back_populates="snmp_jobs")
    items: Mapped[list["SnmpJobItem"]] = relationship(
        back_populates="job", cascade="all, delete-orphan", order_by="SnmpJobItem.created_at"
    )


class SnmpJobItem(Base_):
    """Per-device outcome of an SnmpJob. `report` is the gathered data as a
    human-readable text document (the SNMP analogue of a config snapshot);
    `live_output` narrates progress the same way collection items do."""

    __tablename__ = "snmp_job_items"

    job_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("snmp_jobs.id"), nullable=False, index=True)
    device_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("devices.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # Which profile was actually used (the run's override, the device's
    # own, or the org default) - recorded by name so history stays
    # readable after a profile is renamed or deleted.
    profile_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[JobStatus] = mapped_column(Enum(JobStatus), default=JobStatus.PENDING, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    live_output: Mapped[str] = mapped_column(Text, default="", nullable=False)
    report: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)

    job: Mapped["SnmpJob"] = relationship(back_populates="items")
    device: Mapped["Device | None"] = relationship()
