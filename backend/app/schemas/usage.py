"""Internal gateway visit metadata and public service usage aggregates."""

from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class UsageVisit(BaseModel):
    """Internal input; verified_user_id must come from platform authentication.

    Never construct this identity from client-supplied headers or request bodies.
    The ingress creates visitor_id as a random first-party identifier.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    visitor_id: UUID
    verified_user_id: UUID | None = None
    user_agent: str = Field(max_length=4096)
    method: str = Field(max_length=16)
    accept: str = Field(max_length=4096)
    path: str = Field(max_length=8192)


class UsageMetrics(BaseModel):
    """Distinct visitor counts and the fraction of the UTC D7 cohort returning."""

    uniques_1d: int = Field(default=0, ge=0)
    uniques_7d: int = Field(default=0, ge=0)
    retention_d7: float = Field(default=0.0, ge=0.0, le=1.0)


class UsageCoverage(BaseModel):
    """Unknown producer coverage; stored visits do not establish ingress verification."""

    status: Literal["unverified"] = "unverified"
    since: date | None = None


class UsageD7(BaseModel):
    """Observed UTC cohort counts with no numeric retention for an empty cohort."""

    cohort_day: date
    observed_cohort_size: int = Field(ge=0)
    observed_returned_count: int = Field(ge=0)
    value: float | None = Field(default=None, ge=0.0, le=1.0)


class UsageMeasurement(BaseModel):
    """Describe observation dates independently of unverified producer coverage."""

    as_of_day: date
    timezone: Literal["UTC"] = "UTC"
    coverage: UsageCoverage = Field(default_factory=UsageCoverage)
    d7: UsageD7


class UsageReport(UsageMetrics):
    """Public usage adds measurement context while heartbeat retains legacy metrics."""

    measurement: UsageMeasurement


class ProjectUsage(UsageMetrics):
    """An aggregate associated with one project in an authenticated heartbeat."""

    project_id: UUID


class ServiceRoute(BaseModel):
    """Authoritative project identity and UUID revision, never an arbitrary URL."""

    project_id: UUID
    handle: str
    active_revision: UUID


class GatewayVisit(UsageVisit):
    """Verified request metadata plus the decision to issue a host-only visitor cookie."""

    new_cookie: bool


class ServiceEvent(BaseModel):
    """Fixed browser action dimensions; no contact or visitor metadata."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    event: Literal["form_open", "png_download"]
    model: Literal["iphone-x", "iphone-xr", "pixel-8"]
    lang: Literal["en", "ru"]


class ServiceEventGroup(BaseModel):
    """Action counts and their ratio, which is not a unique-person conversion."""

    model: Literal["iphone-x", "iphone-xr", "pixel-8"]
    lang: Literal["en", "ru"]
    form_open: int = Field(ge=0)
    png_download: int = Field(ge=0)
    download_open_ratio: float | None = Field(ge=0)


class ServiceEventMetrics(BaseModel):
    """Only populated model/language groups from seven UTC calendar days."""

    period_days: Literal[7] = 7
    groups: list[ServiceEventGroup]
