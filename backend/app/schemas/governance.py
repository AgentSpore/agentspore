"""Governance schemas."""

from uuid import UUID

from pydantic import BaseModel


class VoteRequest(BaseModel):
    vote: str           # "approve" | "reject"
    comment: str = ""


class AddContributorRequest(BaseModel):
    user_id: UUID
    role: str = "contributor"   # contributor | admin


class JoinRequest(BaseModel):
    message: str = ""
