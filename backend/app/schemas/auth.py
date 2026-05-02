"""Auth schemas."""

import re
import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr, Field, field_validator


def _norm_email(v: str) -> str:
    # Normalize email to lowercase on all inbound auth payloads.
    # Combined with func.lower() in queries this eliminates case-sensitive
    # duplicate accounts (Foo@x.com + foo@x.com) and login mismatches.
    return v.strip().lower()


_PASSWORD_LETTER = re.compile(r"[a-zA-Z]")
_PASSWORD_DIGIT = re.compile(r"\d")


class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    name: str = Field(min_length=1, max_length=128)

    @field_validator("email", mode="before")
    @classmethod
    def _lower_email(cls, v: str) -> str:
        return _norm_email(v)

    @field_validator("password", mode="after")
    @classmethod
    def _password_complexity(cls, v: str) -> str:
        if not _PASSWORD_LETTER.search(v):
            raise ValueError("Password must contain at least one letter")
        if not _PASSWORD_DIGIT.search(v):
            raise ValueError("Password must contain at least one digit")
        return v


class UserLogin(BaseModel):
    email: EmailStr
    password: str

    @field_validator("email", mode="before")
    @classmethod
    def _lower_email(cls, v: str) -> str:
        return _norm_email(v)


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class TokenRefresh(BaseModel):
    refresh_token: str


class ForgotPasswordRequest(BaseModel):
    email: EmailStr

    @field_validator("email", mode="before")
    @classmethod
    def _lower_email(cls, v: str) -> str:
        return _norm_email(v)


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str


class RegisterResponse(BaseModel):
    """Returned after registration. Account is not usable until email is verified."""

    requires_verification: bool = True
    message: str = "Check your email to verify your account before logging in."


class UserResponse(BaseModel):
    id: uuid.UUID
    email: str
    name: str
    avatar_url: str | None
    token_balance: int
    is_verified: bool = False
    solana_wallet: str | None = None
    created_at: datetime

    class Config:
        from_attributes = True
