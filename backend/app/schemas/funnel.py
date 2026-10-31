"""Funnel DTOs: the visit beacon body and the admin report."""

from datetime import date
from typing import Annotated

from pydantic import BaseModel, Field, field_validator


class VisitBeacon(BaseModel):
    """Body of the anonymous page-visit beacon."""

    path: Annotated[str, Field(max_length=200)]
    utm_source: Annotated[str | None, Field(max_length=64)] = None
    referrer: str | None = None

    @field_validator("referrer")
    @classmethod
    def _cap_referrer(cls, value: str | None) -> str | None:
        """Oversized referrers are cut, never rejected: the beacon must not fail."""
        return value[:500] if value else None


class FunnelRow(BaseModel):
    day: date
    step: str
    source: str
    count: int


class FunnelReport(BaseModel):
    days: int
    rows: list[FunnelRow]
    totals: dict[str, int]
