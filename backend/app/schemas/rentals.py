"""Rental schemas."""

from typing import Literal

from pydantic import BaseModel, Field


class CreateRentalRequest(BaseModel):
    agent_id: str = Field(..., description="Agent to hire")
    title: str = Field(..., min_length=1, max_length=300, description="Task title / first message")
    pay_with_aspore: bool = Field(default=False, description="Pay with $ASPORE (requires rental_payment_enabled)")


class RentalMessageRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=5000)
    message_type: Literal["text", "file"] = "text"
    file_url: str | None = None
    file_name: str | None = None


class AgentRentalMessageRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=5000)
    message_type: Literal["text", "file"] = "text"
    file_url: str | None = None
    file_name: str | None = None


class CompleteRentalRequest(BaseModel):
    rating: int = Field(..., ge=1, le=5)
    review: str | None = Field(default=None, max_length=2000)


class CancelRentalRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=500)
