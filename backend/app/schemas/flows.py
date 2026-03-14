"""Flow schemas."""

from typing import Literal

from pydantic import BaseModel, Field


class CreateFlowRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=300)
    description: str | None = Field(default=None, max_length=5000)


class UpdateFlowRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    description: str | None = Field(default=None, max_length=5000)


class AddStepRequest(BaseModel):
    agent_id: str
    title: str = Field(..., min_length=1, max_length=300)
    instructions: str | None = Field(default=None, max_length=10000)
    depends_on: list[str] = Field(default=[])
    auto_approve: bool = False


class UpdateStepRequest(BaseModel):
    agent_id: str | None = None
    title: str | None = Field(default=None, min_length=1, max_length=300)
    instructions: str | None = Field(default=None, max_length=10000)
    depends_on: list[str] | None = None
    auto_approve: bool | None = None


class ApproveStepRequest(BaseModel):
    edited_output: str | None = Field(default=None, max_length=50000)


class RejectStepRequest(BaseModel):
    feedback: str = Field(..., min_length=1, max_length=5000)


class SkipStepRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=500)


class StepMessageRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=5000)
    message_type: Literal["text", "file"] = "text"
    file_url: str | None = None
    file_name: str | None = None


class AgentCompleteStepRequest(BaseModel):
    output_text: str = Field(..., min_length=1, max_length=50000)
    output_files: list[dict] = Field(default=[])
