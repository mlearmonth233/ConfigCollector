import enum
import uuid

from sqlalchemy import Enum, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import GUID, Base_


class MfaMode(str, enum.Enum):
    NONE = "none"
    # Device's AAA (e.g. TACACS+/RADIUS backed by Duo or similar) prompts
    # out-of-band and holds the login until approved. No extra input needed
    # from us - just enough patience (auth_timeout_seconds) to wait for it.
    PUSH = "push"
    # A one-time passcode must be appended to the password on each
    # collection run (common TACACS+/Duo pattern: "password,123456" or
    # "password,push"). The passcode is never stored - it's supplied fresh
    # per job by the user, since it's single-use.
    PASSCODE = "passcode"


class Credential(Base_):
    """A named username/password (+ optional enable secret) set, reusable
    across many devices within an org. Secrets are stored encrypted.

    TACACS+/RADIUS AAA (and any MFA layered on top of it, e.g. Duo) lives on
    the device's own config, not here - this model just needs to know how
    long to wait for that round trip, and whether a one-time passcode has
    to be supplied per run.
    """

    __tablename__ = "credentials"

    org_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("organizations.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    username: Mapped[str] = mapped_column(String(255), nullable=False)
    encrypted_password: Mapped[str] = mapped_column(String(512), nullable=False)
    encrypted_enable_secret: Mapped[str | None] = mapped_column(String(512), nullable=True)

    mfa_mode: Mapped[MfaMode] = mapped_column(Enum(MfaMode), default=MfaMode.NONE, nullable=False)
    # Delimiter used to append a passcode to the password, e.g. "," to send
    # "mypassword,123456" - the convention most TACACS+/Duo integrations
    # expect. Only relevant when mfa_mode == PASSCODE.
    otp_delimiter: Mapped[str] = mapped_column(String(8), default=",", nullable=False)
    # How long to wait for the full login (SSH + TACACS+/RADIUS round trip +
    # any MFA challenge/approval) before giving up. Plain local-auth devices
    # are fine with the default; push-MFA devices often need 60-90s to give
    # a human time to approve on their phone.
    auth_timeout_seconds: Mapped[int] = mapped_column(Integer, default=45, nullable=False)

    organization: Mapped["Organization"] = relationship(back_populates="credentials")
    devices: Mapped[list["Device"]] = relationship(back_populates="credential")
