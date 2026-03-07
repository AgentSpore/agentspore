"""Team schemas."""

from pydantic import BaseModel, Field


class TeamCreateRequest(BaseModel):
    name: str = Field(..., min_length=2, max_length=100)
    description: str = Field(default="", max_length=500)


class TeamUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=100)
    description: str | None = Field(default=None, max_length=500)


class TeamMemberAddRequest(BaseModel):
    agent_id: str | None = None
    user_id: str | None = None
    role: str = "member"


class TeamMessageRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=2000)
    message_type: str = "text"


class TeamProjectLinkRequest(BaseModel):
    project_id: str
