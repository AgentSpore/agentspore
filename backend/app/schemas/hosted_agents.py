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
    total_cost_usd: float
    budget_usd: float
    started_at: str | None
    stopped_at: str | None
    created_at: str


class HostedAgentListItem(BaseModel):
    id: str
    agent_id: str
    agent_name: str
    agent_handle: str
    status: str
    model: str
    total_cost_usd: float
    created_at: str


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


# ── Control ──


class AgentActionResponse(BaseModel):
    status: str
    message: str


# ── Info ──


class FreeModelInfo(BaseModel):
    id: str
    name: str


class FreeModelsResponse(BaseModel):
    models: list[FreeModelInfo]
