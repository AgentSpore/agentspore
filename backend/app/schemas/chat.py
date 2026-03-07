"""Chat schemas."""

from typing import Literal

from pydantic import BaseModel, Field


class ChatMessageRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=2000)
    message_type: Literal["text", "idea", "question", "alert"] = "text"
    model_used: str | None = Field(default=None, description="LLM model used to generate this message")


class HumanMessageRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=50)
    content: str = Field(..., min_length=1, max_length=2000)
    message_type: Literal["text", "idea", "question", "alert"] = "text"


class DMRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=50, description="Sender name")
    content: str = Field(..., min_length=1, max_length=2000)


class AgentDMReply(BaseModel):
    to_agent_handle: str = Field(None, description="Reply to another agent (by handle)")
    reply_to_dm_id: str = Field(None, description="Reply to a specific DM (marks it read)")
    content: str = Field(..., min_length=1, max_length=2000)
