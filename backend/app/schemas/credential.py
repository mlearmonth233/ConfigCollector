from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class CredentialCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    username: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=1)
    enable_secret: str | None = None


class CredentialOut(BaseModel):
    id: UUID
    name: str
    username: str
    has_enable_secret: bool
    created_at: datetime

    model_config = {"from_attributes": True}
