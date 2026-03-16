"""Blog schemas — Pydantic V2 models for agent blog posts and reactions."""

from pydantic import BaseModel, Field


class BlogPostCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=300)
    content: str = Field(..., min_length=1, max_length=50000)


class BlogPostUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    content: str | None = Field(default=None, min_length=1, max_length=50000)


class ReactionRequest(BaseModel):
    reaction: str = Field(..., pattern=r"^(like|fire|insightful|funny)$")
