"""Agent name lookups used by registration."""

from fastapi import Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db


class AgentNameRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def exists(self, name: str) -> bool:
        result = await self.db.execute(
            text("SELECT 1 FROM agents WHERE name = :name"), {"name": name}
        )
        return result.first() is not None

    async def taken(self, candidates: list[str]) -> set[str]:
        result = await self.db.execute(
            text("SELECT name FROM agents WHERE name = ANY(:names)"), {"names": candidates}
        )
        return {row[0] for row in result}


def get_agent_name_repo(db: AsyncSession = Depends(get_db)) -> AgentNameRepository:
    return AgentNameRepository(db)
