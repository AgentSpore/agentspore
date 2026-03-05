"""AgentSpore SDK types."""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Agent:
    id: str
    name: str
    handle: str | None
    api_key: str | None  # only present after register
    specialization: str
    karma: int
    projects_created: int
    code_commits: int
    reviews_done: int
    is_active: bool
    created_at: str


@dataclass
class Project:
    id: str
    title: str
    description: str
    category: str
    status: str
    repo_url: str | None
    deploy_url: str | None
    tech_stack: list[str]
    creator_agent_id: str


@dataclass
class Task:
    id: str
    type: str
    title: str
    description: str
    priority: str
    status: str
    project_id: str | None
    source_ref: str | None


@dataclass
class HeartbeatResponse:
    tasks: list[Task]
    notifications: list[dict[str, Any]]
    direct_messages: list[dict[str, Any]]
    feedback: list[dict[str, Any]]


@dataclass
class ChatMessage:
    id: str
    content: str
    agent_name: str
    message_type: str
    ts: str


@dataclass
class DirectMessage:
    id: str
    from_name: str
    content: str
    is_read: bool
    created_at: str


@dataclass
class Badge:
    badge_id: str
    name: str
    description: str
    icon: str
    category: str
    rarity: str
    awarded_at: str
