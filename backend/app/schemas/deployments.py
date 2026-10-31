"""Deployment admission request contracts."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class DeploymentRequest(BaseModel):
    """Select an immutable GitHub commit without accepting runtime overrides."""

    model_config = ConfigDict(extra="forbid")

    commit_sha: str = Field(
        strict=True,
        min_length=40,
        max_length=40,
        pattern=r"^[0-9a-fA-F]{40}$",
        description="Full hexadecimal Git commit SHA-1.",
    )


class DeploymentEnqueue(DeploymentRequest):
    """Internal queue input, never accepted by the public deployment route."""

    model_config = ConfigDict(frozen=True)

    project_id: UUID
    idempotency_key: UUID
    repository_url: str = Field(min_length=1)
    handle_candidate: str | None = None


class DeploymentLease(BaseModel):
    """Fence identifying one worker's exclusive claim."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    deployment_id: UUID
    worker_id: UUID
    generation: int = Field(gt=0)

    @property
    def sandbox_namespace(self) -> str:
        """Canonical owned scope persisted by claim before external creation."""
        return f"a4-{self.deployment_id.hex}-{self.generation}"


class SandboxStopProof(BaseModel):
    """Trusted stop adapter's absence observation; never a public request body."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    lease: DeploymentLease
    sandbox_id: str = Field(min_length=1)


class SandboxNamespaceAbsenceProof(BaseModel):
    """Trusted complete namespace inventory observation, never a public input."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    lease: DeploymentLease
    namespace: str = Field(min_length=1)


class DeploymentRecoverySnapshot(BaseModel):
    """Immutable ownership captured before an external build-stop observation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    lease: DeploymentLease
    sandbox_id: str | None


class QuotaLimits(BaseModel):
    """Admission ceilings counted from `project_deployments.created_at`."""

    model_config = ConfigDict(frozen=True)

    agent_daily: int = 5
    agent_weekly: int = 20
    owner_active: int = 3
    platform_active: int = 10
    queue: int = 10


class DeploymentAccepted(BaseModel):
    """Body of the 202 answer to a deploy request."""

    deployment_id: UUID
    status: str = "queued"


class DeploymentSummary(BaseModel):
    """One deployment row as shown to the project's author."""

    id: UUID
    status: str
    reason_code: str | None
    commit_sha: str
    created_at: datetime


class DeploymentStatus(BaseModel):
    """Current deployment state of a project."""

    status: str
    reason_code: str | None
    active_revision: UUID | None
    url: str | None
    deployments: list[DeploymentSummary]
