import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

RoleName = str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    email: str
    full_name: str
    roles: list[str]
    is_active: bool
    last_login_at: datetime | None = None


class UserCreate(BaseModel):
    email: EmailStr
    full_name: str = Field(min_length=1, max_length=200)
    password: str
    roles: list[str] = Field(min_length=1)


class UserUpdate(BaseModel):
    full_name: str | None = Field(default=None, max_length=200)
    roles: list[str] | None = None
    is_active: bool | None = None


class AuditOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    occurred_at: datetime
    user_id: uuid.UUID | None
    action: str
    entity_type: str | None
    entity_id: str | None
    patient_id: uuid.UUID | None
    ip_address: str | None


class AuditVerifyOut(BaseModel):
    ok: bool
    checked: int
    first_bad_id: int | None = None
    reason: str | None = None
