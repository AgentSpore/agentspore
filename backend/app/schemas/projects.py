"""Projects schemas."""

from pydantic import BaseModel, Field, field_validator


class VoteRequest(BaseModel):
    vote: int = Field(..., description="1 = upvote, -1 = downvote")

    @field_validator("vote")
    @classmethod
    def validate_vote(cls, v: int) -> int:
        if v not in (1, -1):
            raise ValueError("vote must be 1 or -1")
        return v
