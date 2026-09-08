from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from app.models.credential import MfaMode


class CredentialCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    username: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=1)
    enable_secret: str | None = None
    mfa_mode: MfaMode = MfaMode.NONE
    otp_delimiter: str = Field(default=",", max_length=8)
    # Generous default: SSH + a TACACS+/RADIUS round trip is usually a few
    # seconds, but push-MFA approval can take much longer - bump this on
    # push/passcode credentials as needed.
    auth_timeout_seconds: int = Field(default=45, ge=5, le=300)


class CredentialOut(BaseModel):
    id: UUID
    name: str
    username: str
    has_enable_secret: bool
    mfa_mode: MfaMode
    otp_delimiter: str
    auth_timeout_seconds: int
    created_at: datetime

    model_config = {"from_attributes": True}
