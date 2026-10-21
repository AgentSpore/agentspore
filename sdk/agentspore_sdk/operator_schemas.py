"""Bounded public observations consumed by the trusted operator controller."""

from typing import Any, Literal, TypedDict


class Observation(TypedDict):
    """A source projection, with missing coverage represented explicitly."""

    value: Any
    status: Literal["complete", "partial", "unavailable"]
    reason: str | None


class PublicSnapshot(TypedDict):
    """Public-only evidence; counters never imply daily events or human identity."""

    window: dict[str, str]
    health: dict[str, int | None]
    counters: Observation
    projects: Observation
    github_repos: Observation
    github_events: Observation
    github_ci: Observation
