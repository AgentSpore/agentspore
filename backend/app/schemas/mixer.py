"""Privacy Mixer schemas — request/response models for mixer sessions, chunks, messages."""

from typing import Any, Literal

from pydantic import BaseModel, Field


# ── Session ──────────────────────────────────────────────────────────


class CreateMixerSessionRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=300)
    description: str | None = Field(default=None, max_length=2000)
    task_text: str = Field(..., min_length=1, max_length=50000, description="Full task with {{PRIVATE:value}} or {{PRIVATE:category:value}} markers")
    passphrase: str = Field(..., min_length=8, max_length=128, description="Encryption passphrase (never stored)")
    fragment_ttl_hours: int = Field(default=24, ge=1, le=168, description="Auto-delete fragments after N hours")


class UpdateMixerSessionRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    description: str | None = Field(default=None, max_length=2000)


# ── Chunks ───────────────────────────────────────────────────────────


class AddMixerChunkRequest(BaseModel):
    agent_id: str = Field(..., description="Agent UUID to assign this chunk to")
    title: str = Field(..., min_length=1, max_length=300)
    instructions: str = Field(..., min_length=1, max_length=50000, description="Chunk text with {{MIX_xxx}} placeholders")


class UpdateMixerChunkRequest(BaseModel):
    agent_id: str | None = Field(default=None)
    title: str | None = Field(default=None, min_length=1, max_length=300)
    instructions: str | None = Field(default=None, min_length=1, max_length=50000)


# ── Agent interaction ────────────────────────────────────────────────


class AgentCompleteMixerChunkRequest(BaseModel):
    output_text: str = Field(..., min_length=1, max_length=50000)


# ── Assembly ─────────────────────────────────────────────────────────


class AssembleMixerRequest(BaseModel):
    passphrase: str = Field(..., min_length=8, max_length=128, description="Passphrase to decrypt fragments")


# ── Messages ─────────────────────────────────────────────────────────


class MixerChunkMessageRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=5000)
    message_type: Literal["text"] = "text"


# ── Reject ───────────────────────────────────────────────────────────


class RejectMixerChunkRequest(BaseModel):
    feedback: str = Field(..., min_length=1, max_length=5000, description="Why the output was rejected")
