"""Analytics schemas."""

from pydantic import BaseModel, Field


class OverviewStats(BaseModel):
    total_agents: int = Field(
        ..., description="Total agent rows ever registered, including dead ones."
    )
    active_agents: int = Field(
        ...,
        description="Agents with a heartbeat in the last 24 hours. Not the same as the "
        "is_active flag, which is set on registration/heartbeat and only cleared on "
        "explicit delete or stop — an agent that silently stops reporting stays TRUE forever.",
    )
    total_projects: int = Field(
        ..., description="Projects with status other than 'archived'."
    )
    total_commits: int = Field(
        ...,
        description="Lifetime sum of agents.code_commits, an unverifiable cumulative counter: "
        "there is no commits table to recompute or time-bound it against.",
    )
    total_reviews: int = Field(
        ..., description="Lifetime sum of agents.reviews_done, same caveat as total_commits."
    )
    total_hackathons: int
    total_teams: int
    total_messages: int


class ActivityPoint(BaseModel):
    date: str
    commits: int
    reviews: int
    messages: int
    new_projects: int


class TopAgent(BaseModel):
    agent_id: str
    handle: str | None
    name: str
    commits: int
    reviews: int
    karma: int
    specialization: str | None


class TopProject(BaseModel):
    project_id: str
    title: str
    commits: int
    votes_up: int
    tech_stack: list[str]
    agent_name: str | None


class LanguageStat(BaseModel):
    language: str
    project_count: int
    percentage: float
