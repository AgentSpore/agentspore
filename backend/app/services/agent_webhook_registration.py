"""Validate webhook destinations before storing an agent's delivery configuration."""

import secrets

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.agent_webhook_repo import AgentWebhookRepository
from app.schemas.agents import AgentWebhookRequest
from app.services.webhook_destination import UnsafeWebhookError, parse_webhook_url, resolve_webhook


class AgentWebhookRegistration:
    def __init__(self, db: AsyncSession):
        self.repo = AgentWebhookRepository(db)

    async def update_webhook(self, agent_id: str, body: AgentWebhookRequest) -> dict:
        url = body.url
        if url is not None:
            try:
                await resolve_webhook(parse_webhook_url(url))
            except UnsafeWebhookError:
                raise HTTPException(
                    status_code=400, detail="public HTTPS webhook URL required"
                ) from None
        secret = (body.secret or secrets.token_urlsafe(32)) if url else None
        await self.repo.update_webhook(agent_id, url, secret)
        return {"webhook_url": url, "webhook_secret": secret}
