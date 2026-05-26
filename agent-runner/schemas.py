"""Pydantic request/response models for the Agent Runner service."""

from pydantic import BaseModel


class StartRequest(BaseModel):
    agent_id: str
    agent_handle: str = ""
    system_prompt: str
    model: str = "mistralai/mistral-nemo"
    runtime: str = "python-minimal"
    memory_limit_mb: int = 256
    files: list[dict] = []
    api_key: str = ""
    heartbeat_seconds: int = 3600
    message_history: list[dict] = []
    context_max_tokens: int = 128_000
    stuck_loop_detection: bool = False
    provider_base_url: str = ""
    provider_api_key: str = ""


class ChatRequest(BaseModel):
    content: str
    owner_session_id: str | None = None


class ActionResponse(BaseModel):
    status: str
    message: str = ""
    container_id: str | None = None


class ChatResponse(BaseModel):
    reply: str
    tool_calls: list[dict] = []
    thinking: str | None = None


class RewindRequest(BaseModel):
    checkpoint_id: str


class WriteFileRequest(BaseModel):
    file_path: str
    content: str
