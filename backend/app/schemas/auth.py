"""Auth schemas."""

import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr, field_validator


def _norm_email(v: str) -> str:
    # Normalize email to lowercase on all inbound auth payloads.
    # Combined with func.lower() in queries this eliminates case-sensitive
    # duplicate accounts (Foo@x.com + foo@x.com) and login mismatches.
    return v.strip().lower()


class UserCreate(BaseModel):
    email: EmailStr
    password: str
    name: str

    @field_validator("email", mode="before")
    @classmethod
    def _lower_email(cls, v: str) -> str:
        return _norm_email(v)


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


class UserResponse(BaseModel):
    id: uuid.UUID
    email: str
    name: str
    avatar_url: str | None
    token_balance: int
    solana_wallet: str | None = None
    created_at: datetime

    class Config:
        from_attributes = True
