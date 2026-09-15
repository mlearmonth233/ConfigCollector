import uuid
from datetime import datetime

from sqlalchemy import Boolean, Enum, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import GUID, Base_, UTCDateTime
from app.models.job import JobStatus


class DnsCheckJob(Base_):
    """One bulk ping/DNS/reverse-DNS check request, fanning out to one
    DnsCheckJobItem per pasted target - mirrors CollectionJob/CollectionJobItem's
    shape and status lifecycle, run as a background job (rather than a
    single synchronous request/response, as an earlier version of this
    feature did) so a paste of hundreds or thousands of targets doesn't
    have to be held open on one HTTP request."""

    __tablename__ = "dns_check_jobs"

    org_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("organizations.id"), nullable=False, index=True)
    created_by_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("users.id"), nullable=False)
    status: Mapped[JobStatus] = mapped_column(Enum(JobStatus), default=JobStatus.PENDING, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    organization: Mapped["Organization"] = relationship(back_populates="dns_check_jobs")
    items: Mapped[list["DnsCheckJobItem"]] = relationship(
        back_populates="job", cascade="all, delete-orphan", order_by="DnsCheckJobItem.created_at"
    )


class DnsCheckJobItem(Base_):
    """Per-target outcome of a DnsCheckJob. Unlike CollectionJobItem/
    FirmwareUpgradeJobItem, a target here is never a Device row - just
    whatever string the user pasted - so there's no device_id at all, and
    "failure" isn't really a concept: a target that doesn't answer a ping
    or has no DNS record still completes normally, it just reports that
    outcome (see services/dns_check.py's module docstring)."""

    __tablename__ = "dns_check_job_items"

    job_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("dns_check_jobs.id"), nullable=False, index=True)
    target: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[JobStatus] = mapped_column(Enum(JobStatus), default=JobStatus.PENDING, nullable=False)
    ping_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    forward_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    # Comma-separated resolved IP(s) - a hostname can resolve to more than
    # one (round-robin DNS, dual-stack) - empty/null if forward_ok is
    # False or the target was already an IP (see run_dns_check).
    forward_ips: Mapped[str | None] = mapped_column(Text, nullable=True)
    reverse_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    reverse_hostname: Mapped[str | None] = mapped_column(String(255), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)

    job: Mapped["DnsCheckJob"] = relationship(back_populates="items")
