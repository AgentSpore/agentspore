"""Pydantic request/response models for the Agent Runner service."""

from pydantic import BaseModel, Field

# Maximum number of files in a single bulk import request.
_MAX_IMPORT_FILES = 500


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
    # Phase 2+3: per-session concurrency. Default 1 = current single-session behavior.
    max_concurrent_sessions: int = 1


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


class ImportFileItem(BaseModel):
    """Single file entry for bulk workspace import."""

    file_path: str
    # Bound matches MAX_SYNC_BYTES from workspace.py — prevents oversized payloads
    # that would exceed the per-file read limit enforced on list/get endpoints.
    content: str = Field(max_length=500_000)


class ImportFilesRequest(BaseModel):
    """Bulk file import payload — used by fork to seed a new workspace dir."""

    files: list[ImportFileItem] = Field(max_length=_MAX_IMPORT_FILES)
