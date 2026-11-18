import re
from uuid import UUID
from datetime import datetime
from pydantic import BaseModel, EmailStr, Field, field_validator

class SignupRequest(BaseModel):
    full_name: str = Field(..., min_length=2, max_length=120)
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=128)

    @field_validator("password")
    @classmethod
    def validate_strong_password(cls, v: str) -> str:
        if not re.search(r"[A-Z]", v):
            raise ValueError("Password must contain at least one uppercase letter.")
        if not re.search(r"[a-z]", v):
            raise ValueError("Password must contain at least one lowercase letter.")
        if not re.search(r"\d", v):
            raise ValueError("Password must contain at least one number.")
        if not re.search(r"[!@#$%^&*(),.?\":{}|<>]", v):
            raise ValueError("Password must contain at least one special character.")
        return v

class VerifyEmailRequest(BaseModel):
    email: EmailStr
    code: str = Field(..., min_length=6, max_length=6)

class ResendCodeRequest(BaseModel):
    email: EmailStr

class LoginRequest(BaseModel):
    email: EmailStr
    password: str
    device_name: str | None = "Flutter Mobile App"

class RefreshRequest(BaseModel):
    refresh_token: str

class LogoutRequest(BaseModel):
    refresh_token: str

class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in_seconds: int

class ClinicianProfileResponse(BaseModel):
    clinician_id: UUID
    full_name: str
    email: EmailStr
    email_verified: bool
    push_enabled: bool
    created_at: datetime

    class Config:
        from_attributes = True
