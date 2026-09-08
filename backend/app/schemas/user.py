from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field

from app.models.user import UserRole


class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=255)
    role: UserRole = UserRole.MEMBER


class UserOut(BaseModel):
    id: UUID
    email: str
    role: UserRole
    created_at: datetime

    model_config = {"from_attributes": True}
