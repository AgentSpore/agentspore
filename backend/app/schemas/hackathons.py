"""Hackathon schemas."""

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


class HackathonCreateRequest(BaseModel):
    title: str = Field(..., min_length=3, max_length=300)
    theme: str = Field(..., min_length=3, max_length=200)
    description: str = Field(default="")
    starts_at: datetime
    ends_at: datetime
    voting_ends_at: datetime
    prize_pool_usd: float = Field(default=0, ge=0)
    prize_description: str = Field(default="")


class HackathonUpdateRequest(BaseModel):
    title: Optional[str] = Field(default=None, min_length=3, max_length=300)
    theme: Optional[str] = Field(default=None, min_length=3, max_length=200)
    description: Optional[str] = None
    starts_at: Optional[datetime] = None
    ends_at: Optional[datetime] = None
    voting_ends_at: Optional[datetime] = None
    status: Optional[str] = None
    prize_pool_usd: Optional[float] = Field(default=None, ge=0)
    prize_description: Optional[str] = None


class HackathonResponse(BaseModel):
    id: str
    title: str
    theme: str
    description: str
    starts_at: str
    ends_at: str
    voting_ends_at: str
    status: str
    winner_project_id: str | None
    prize_pool_usd: float
    prize_description: str
    created_at: str


class HackathonDetailResponse(HackathonResponse):
    projects: list[dict[str, Any]] = []
