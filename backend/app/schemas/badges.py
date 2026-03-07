"""Badge schemas."""

from pydantic import BaseModel


class BadgeDefinition(BaseModel):
    id: str
    name: str
    description: str
    icon: str
    category: str
    rarity: str


class AgentBadge(BaseModel):
    badge_id: str
    name: str
    description: str
    icon: str
    category: str
    rarity: str
    awarded_at: str
