"""Persistence for authenticated agent webhook configuration."""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class AgentWebhookRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def update_webhook(self, agent_id: str, url: str | None, secret: str | None) -> None:
        await self.db.execute(
            text("""UPDATE agents SET webhook_url = :url, webhook_secret = :secret,
                 webhook_failures_count = 0, webhook_disabled = FALSE WHERE id = :id"""),
            {"id": agent_id, "url": url, "secret": secret},
        )
