"""Idea schemas."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class IdeaCreate(BaseModel):
    title: str
    problem: str
    solution: str
    category: str


class IdeaUpdate(BaseModel):
    title: str | None = None
    problem: str | None = None
    solution: str | None = None


class IdeaResponse(BaseModel):
    id: uuid.UUID
    title: str
    problem: str
    solution: str
    category: str
    author_id: uuid.UUID
    author_name: str | None = None
    votes_up: int
    votes_down: int
    score: int
    status: str
    ai_generated: bool
    comments_count: int = 0
    created_at: datetime

    class Config:
        from_attributes = True


class VoteRequest(BaseModel):
    value: Literal[1, -1]


class CommentCreate(BaseModel):
    content: str


class CommentResponse(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    user_name: str | None = None
    content: str
    created_at: datetime

    class Config:
        from_attributes = True


class IdeasListResponse(BaseModel):
    items: list[IdeaResponse]
    total: int
    page: int
    per_page: int
