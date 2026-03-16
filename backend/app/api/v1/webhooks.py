"""
Webhooks — роутер для приёма событий от GitHub и GitLab.

Бизнес-логика вынесена в WebhookService (app/services/webhook_service.py).

Настройка GitHub:
1. GitHub → Organization → Settings → Webhooks → Add webhook
   URL: https://agentspore.com/api/v1/webhooks/github
   Content type: application/json
   Secret: значение из GITHUB_WEBHOOK_SECRET
   Events: Issues, Issue comments, Pull requests, Pull request review comments,
           Pushes, Repositories, Stars

Настройка GitLab:
1. GitLab → Group → Settings → Webhooks → Add new webhook
   URL: https://agentspore.com/api/v1/webhooks/gitlab
   Secret token: значение из GITLAB_WEBHOOK_SECRET
   Triggers: Push events, Issues events, Comments, Merge request events
"""

import hashlib
import hmac
import os

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.services.webhook_service import GitHubWebhookService, GitLabWebhookService

from loguru import logger
router = APIRouter(prefix="/webhooks", tags=["webhooks"])

GITHUB_WEBHOOK_SECRET = os.getenv("GITHUB_WEBHOOK_SECRET", "")
GITLAB_WEBHOOK_SECRET = os.getenv("GITLAB_WEBHOOK_SECRET", "")


def _verify_github_signature(payload: bytes, signature: str | None) -> bool:
    if not GITHUB_WEBHOOK_SECRET:
        logger.error("GITHUB_WEBHOOK_SECRET not set — rejecting webhook.")
        return False
    if not signature or not signature.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(
        GITHUB_WEBHOOK_SECRET.encode(), payload, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


def _verify_gitlab_token(token: str | None) -> bool:
    if not GITLAB_WEBHOOK_SECRET:
        logger.warning("GITLAB_WEBHOOK_SECRET not set — skipping verification")
        return True
    return bool(token) and hmac.compare_digest(token, GITLAB_WEBHOOK_SECRET)


@router.post("/github")
async def github_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_hub_signature_256: str | None = Header(default=None),
    x_github_event: str | None = Header(default=None),
):
    payload = await request.body()
    if not _verify_github_signature(payload, x_hub_signature_256):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")
    if not x_github_event:
        return {"status": "ignored", "reason": "no event type"}

    try:
        data = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    service = GitHubWebhookService(db)
    result = await service.handle(event=x_github_event, data=data)
    await db.commit()
    return result


@router.post("/gitlab")
async def gitlab_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_gitlab_token: str | None = Header(default=None),
    x_gitlab_event: str | None = Header(default=None),
):
    if not _verify_gitlab_token(x_gitlab_token):
        raise HTTPException(status_code=401, detail="Invalid GitLab webhook token")
    if not x_gitlab_event:
        return {"status": "ignored", "reason": "no event type"}

    try:
        data = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    service = GitLabWebhookService(db)
    result = await service.handle(event=x_gitlab_event, data=data)
    await db.commit()
    return result
