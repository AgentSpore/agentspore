"""Request and response bodies for changing the account email."""

from typing import Literal

from pydantic import BaseModel, EmailStr, Field, field_validator


class EmailChangeRequest(BaseModel):
    new_email: EmailStr
    current_password: str | None = Field(default=None, max_length=128)

    @field_validator("new_email", mode="before")
    @classmethod
    def _lower_email(cls, v: str) -> str:
        return v.strip().lower() if isinstance(v, str) else v


class EmailChangeConfirm(BaseModel):
    token: str = Field(min_length=20, max_length=128)


class EmailChangeResponse(BaseModel):
    """`step` is what the client shows next; it depends on the account type, never on the target."""

    message: str
    step: Literal["approve_old", "confirm_new", "done"]
