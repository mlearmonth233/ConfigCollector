import enum
import uuid

from sqlalchemy import Boolean, Enum, ForeignKey, Integer, String
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

    org_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("organizations.id"), nullable=False, index=True)
    # The credential used for any device that doesn't have its own
    # credential_id set - i.e. nearly all of them, since devices no longer
    # need one picked per-device. Exactly one credential per org should
    # have this set at a time - enforced in code (credentials.py), not a DB
    # constraint, since SQLite/Postgres don't both support a clean partial-
    # unique-index syntax for "at most one row where is_default is true".
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
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

    # Retried when this credential's login fails outright (e.g. the TACACS+/
    # RADIUS server is unreachable, or the account is locked) - typically a
    # device's local/default account, or some other break-glass login.
    # One level deep only: a fallback's own fallback_credential_id, if any,
    # is never chased further, so a device tries at most two credentials.
    fallback_credential_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("credentials.id"), nullable=True
    )

    organization: Mapped["Organization"] = relationship(back_populates="credentials")
    devices: Mapped[list["Device"]] = relationship(back_populates="credential")
    fallback_credential: Mapped["Credential | None"] = relationship(remote_side="Credential.id")
