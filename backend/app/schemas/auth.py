from uuid import UUID

from pydantic import BaseModel, EmailStr, Field


class RegisterRequest(BaseModel):
    org_name: str = Field(min_length=1, max_length=255)
    email: EmailStr
    password: str = Field(min_length=8, max_length=255)


class LoginRequest(BaseModel):
    # An email address, or a plain username created with
    # `python -m app.create_admin` (e.g. "admin").
    email: str = Field(min_length=1, max_length=255)
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class CurrentUser(BaseModel):
    id: UUID
    org_id: UUID
    org_name: str
    email: str
    role: str

    model_config = {"from_attributes": True}
