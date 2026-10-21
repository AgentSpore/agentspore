"""Strict, bounded offline recovery manifest; no credentials or ownership fields."""

from datetime import date
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

Sha = Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]


class RecoveryRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")


class HistoricalAgent(RecoveryRecord):
    id: UUID
    name: str = Field(min_length=3, max_length=200)
    source: Literal["retained_local_mapping"]
    verified: Literal[False]
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class HistoricalCommit(RecoveryRecord):
    sha: Sha
    created_at: AwareDatetime
    url: str
    subject: str = Field(max_length=200)


class HistoricalProject(RecoveryRecord):
    id: UUID
    id_kind: Literal["original_platform_id", "new_catalog_id"]
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,99}$")
    url: str
    description: str = Field(max_length=4000)
    created_at: AwareDatetime
    pushed_at: AwareDatetime | None
    history_total: int = Field(ge=0)
    branch: str = Field(min_length=1, max_length=255)
    commits: list[HistoricalCommit] = Field(max_length=20)

    @model_validator(mode="after")
    def validate_public_sources(self):
        """Bind every outbound source link to this exact public repository."""
        if self.url != f"https://github.com/AgentSpore/{self.slug}":
            raise ValueError("Invalid repository source.")
        if self.history_total < len(self.commits):
            raise ValueError("Invalid history coverage.")
        if any(c.url != f"{self.url}/commit/{c.sha}" for c in self.commits):
            raise ValueError("Invalid commit source.")
        return self


class RecoveryManifest(RecoveryRecord):
    version: Literal[1]
    source: Literal["github_public_history"]
    observed_on: date
    agents: list[HistoricalAgent] = Field(max_length=6)
    projects: list[HistoricalProject] = Field(max_length=20)
