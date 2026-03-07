"""Analytics schemas."""

from pydantic import BaseModel


class OverviewStats(BaseModel):
    total_agents: int
    active_agents: int
    total_projects: int
    total_commits: int
    total_reviews: int
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
