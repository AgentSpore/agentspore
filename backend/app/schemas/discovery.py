"""Discovery schemas."""

from typing import Optional
from uuid import UUID

from pydantic import BaseModel


class ProblemResponse(BaseModel):
    id: Optional[UUID] = None
    problem: str
    title: Optional[str] = None
    description: Optional[str] = None
    source: str
    url: Optional[str] = None
    audience: str
    importance: str
    severity: Optional[int] = None
    category: Optional[str] = None
    status: Optional[str] = "new"


class ProblemCreate(BaseModel):
    title: str
    description: str
    source: str
    url: Optional[str] = None
    severity: int = 5
    category: Optional[str] = None


class ProblemUpdate(BaseModel):
    status: Optional[str] = None


class GenerateIdeaRequest(BaseModel):
    problem: str


class GeneratedIdeaResponse(BaseModel):
    title: str
    problem: str
    solution: str
    features: list[str]
    business_model: str
    category: str
