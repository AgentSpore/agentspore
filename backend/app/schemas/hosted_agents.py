"""Hosted agent schemas — creation, management, files, owner chat."""

from uuid import UUID

from pydantic import BaseModel, Field


# ── Free models available on OpenRouter ──

DEFAULT_RUNTIME = "python-minimal"


# ── Create / Update ──


class HostedAgentCreateRequest(BaseModel):
    name: str = Field(..., min_length=3, max_length=200)
    description: str = Field(default="")
    specialization: str = Field(default="programmer")
    system_prompt: str = Field(..., min_length=10, max_length=10000)
    model: str = Field(default="qwen/qwen3-coder:free")
    skills: list[str] = Field(default=[])


class HostedAgentUpdateRequest(BaseModel):
    system_prompt: str | None = Field(default=None, max_length=10000)
    model: str | None = None
    budget_usd: float | None = Field(default=None, ge=0.1, le=100.0)
    heartbeat_enabled: bool | None = None
    heartbeat_seconds: int | None = Field(default=None, ge=60, le=86400)
    stuck_loop_detection: bool | None = None


class HostedAgentResponse(BaseModel):
    id: str
    agent_id: str
    agent_name: str
    agent_handle: str
    system_prompt: str
    model: str
    status: str
    memory_limit_mb: int
    heartbeat_enabled: bool
    heartbeat_seconds: int
    stuck_loop_detection: bool
    total_cost_usd: float
    budget_usd: float
    started_at: str | None
    stopped_at: str | None
    created_at: str
    forked_from_agent_name: str | None = None

    @classmethod
    def from_dict(cls, h: dict) -> "HostedAgentResponse":
        return cls(
            id=str(h["id"]),
            agent_id=str(h["agent_id"]),
            agent_name=h["agent_name"],
            agent_handle=h["agent_handle"],
            system_prompt=h["system_prompt"],
            model=h["model"],
            status=h["status"],
            memory_limit_mb=h["memory_limit_mb"],
            heartbeat_enabled=h.get("heartbeat_enabled", True),
            heartbeat_seconds=h.get("heartbeat_seconds", 3600),
            stuck_loop_detection=h.get("stuck_loop_detection", False),
            total_cost_usd=h["total_cost_usd"],
            budget_usd=h["budget_usd"],
            started_at=str(h["started_at"]) if h.get("started_at") else None,
            stopped_at=str(h["stopped_at"]) if h.get("stopped_at") else None,
            created_at=str(h["created_at"]),
            forked_from_agent_name=h.get("forked_from_agent_name"),
        )


class HostedAgentListItem(BaseModel):
    id: str
    agent_id: str
    agent_name: str
    agent_handle: str
    status: str
    model: str
    total_cost_usd: float
    created_at: str
    forked_from_agent_name: str | None = None

    @classmethod
    def from_dict(cls, h: dict) -> "HostedAgentListItem":
        return cls(
            id=str(h["id"]),
            agent_id=str(h["agent_id"]),
            agent_name=h["agent_name"],
            agent_handle=h["agent_handle"],
            status=h["status"],
            model=h["model"],
            total_cost_usd=h["total_cost_usd"],
            created_at=str(h["created_at"]),
            forked_from_agent_name=h.get("forked_from_agent_name"),
        )


# ── Files ──


class AgentFileResponse(BaseModel):
    id: str
    file_path: str
    file_type: str
    content: str | None = None
    size_bytes: int
    updated_at: str


class AgentFileWriteRequest(BaseModel):
    file_path: str = Field(..., min_length=1, max_length=500)
    content: str = Field(..., max_length=100000)
    file_type: str = Field(default="text")


# ── Owner chat ──


class OwnerMessageRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=10000)


class OwnerMessageResponse(BaseModel):
    id: str
    sender_type: str
    content: str
    tool_calls: list[dict] | None = None
    thinking: str | None = None
    edited_at: str | None = None
    is_deleted: bool = False
    created_at: str


# ── Cron tasks ──


class CronTaskCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    cron_expression: str = Field(..., min_length=1, max_length=100)
    task_prompt: str = Field(..., min_length=1, max_length=10000)
    enabled: bool = True
    auto_start: bool = True
    max_runs: int | None = Field(default=None, ge=1)


class CronTaskUpdateRequest(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    cron_expression: str | None = Field(default=None, max_length=100)
    task_prompt: str | None = Field(default=None, max_length=10000)
    enabled: bool | None = None
    auto_start: bool | None = None
    max_runs: int | None = None


class CronTaskResponse(BaseModel):
    id: str
    hosted_agent_id: str
    name: str
    cron_expression: str
    task_prompt: str
    enabled: bool
    auto_start: bool
    last_run_at: str | None
    next_run_at: str | None
    run_count: int
    max_runs: int | None
    last_error: str | None
    created_at: str

    @classmethod
    def from_dict(cls, d: dict) -> "CronTaskResponse":
        return cls(
            id=str(d["id"]),
            hosted_agent_id=str(d["hosted_agent_id"]),
            name=d["name"],
            cron_expression=d["cron_expression"],
            task_prompt=d["task_prompt"],
            enabled=d["enabled"],
            auto_start=d["auto_start"],
            last_run_at=str(d["last_run_at"]) if d.get("last_run_at") else None,
            next_run_at=str(d["next_run_at"]) if d.get("next_run_at") else None,
            run_count=d["run_count"],
            max_runs=d.get("max_runs"),
            last_error=d.get("last_error"),
            created_at=str(d["created_at"]),
        )


# ── Control ──


class AgentActionResponse(BaseModel):
    status: str
    message: str


# ── Info ──


# ── Forking ──


class ForkAgentRequest(BaseModel):
    name: str | None = Field(default=None, min_length=3, max_length=200)
    system_prompt: str | None = Field(default=None, min_length=10, max_length=10000)


class ForkableAgentItem(BaseModel):
    id: str
    agent_id: str
    agent_name: str
    agent_handle: str
    model: str
    specialization: str
    skills: list[str]
    description: str
    fork_count: int
    forked_from_agent_name: str | None = None


class FreeModelInfo(BaseModel):
    id: str
    name: str


class FreeModelsResponse(BaseModel):
    models: list[FreeModelInfo]
