"""Sandbox schemas."""

import uuid
from datetime import datetime

from pydantic import BaseModel


class SandboxResponse(BaseModel):
    id: uuid.UUID
    idea_id: uuid.UUID
    idea_title: str
    prototype_url: str
    feedbacks_count: int
    features_count: int
    created_at: datetime

    class Config:
        from_attributes = True


class SandboxDetailResponse(SandboxResponse):
    prototype_html: str


class FeedbackCreate(BaseModel):
    rating: int  # 1-5
    comment: str


class FeedbackResponse(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    user_name: str | None
    rating: int
    comment: str
    created_at: datetime


class FeatureCreate(BaseModel):
    title: str
    description: str


class FeatureResponse(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    user_name: str | None
    title: str
    description: str
    votes: int
    created_at: datetime


class CodeUpdateRequest(BaseModel):
    code: str


class CodeModifyRequest(BaseModel):
    prompt: str


class CodeGenerateResponse(BaseModel):
    html: str
    features_applied: list[str]


class SandboxPreviewResponse(BaseModel):
    html: str
