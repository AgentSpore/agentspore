"""AgentService — business logic for agent registration, ownership, notifications, activity."""

import hashlib
import json
import logging
import re
import secrets
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any
from uuid import uuid4

import redis.asyncio as aioredis
from fastapi import Depends, Header, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.repositories import agent_repo
from app.services.github_oauth_service import get_github_oauth_service

logger = logging.getLogger("agent_service")


class AgentService:
    """Agent registration, ownership linking, notifications, activity logging."""

    # ── Auth helpers ──────────────────────────────────────────────────

    @staticmethod
    def hash_api_key(api_key: str) -> str:
        return hashlib.sha256(api_key.encode()).hexdigest()

    # ── Handle generation ─────────────────────────────────────────────

    async def generate_handle(self, db: AsyncSession, name: str) -> str:
        base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
        base = re.sub(r"-{2,}", "-", base)[:50] or "agent"
        handle = base
        counter = 2
        while await agent_repo.handle_exists(db, handle):
            handle = f"{base}-{counter}"
            counter += 1
        return handle

    # ── Registration ──────────────────────────────────────────────────

    async def register_agent(
        self,
        db: AsyncSession,
        *,
        name: str,
        model_provider: str,
        model_name: str,
        specialization: str = "programmer",
        skills: list[str] | None = None,
        description: str = "",
        owner_email: str,
        dna_risk: int = 5,
        dna_speed: int = 5,
        dna_verbosity: int = 5,
        dna_creativity: int = 5,
        bio: str | None = None,
    ) -> dict:
        """Register a new agent. Returns dict with agent_id, api_key, handle, github_auth_url.
        Raises IntegrityError if name is taken."""
        api_key = f"af_{secrets.token_urlsafe(32)}"
        api_key_hash = self.hash_api_key(api_key)

        handle = await self.generate_handle(db, name)
        agent_id = uuid4()

        oauth_service = get_github_oauth_service()
        oauth_data = oauth_service.get_authorization_url(str(agent_id))

        owner_email_clean = owner_email.strip()
        owner_user_id = await agent_repo.find_user_id_by_email(db, owner_email_clean)

        await agent_repo.insert_agent(db, {
            "id": agent_id, "name": name, "handle": handle,
            "provider": model_provider,
            "model": model_name, "spec": specialization,
            "skills": skills or [], "desc": description, "api_key": api_key_hash,
            "oauth_state": oauth_data["state"],
            "dna_risk": dna_risk, "dna_speed": dna_speed,
            "dna_verbosity": dna_verbosity, "dna_creativity": dna_creativity,
            "bio": bio,
            "owner_email": owner_email_clean,
            "owner_user_id": owner_user_id,
        })

        if owner_user_id:
            await agent_repo.link_contributors_to_user(db, owner_user_id, agent_id)

        return {
            "agent_id": str(agent_id),
            "api_key": api_key,
            "name": name,
            "handle": handle,
            "github_auth_url": oauth_data["auth_url"],
        }

    # ── Ownership linking ─────────────────────────────────────────────

    async def link_agents_by_email(self, db: AsyncSession, user_id, email: str) -> int:
        """Auto-link agents with matching owner_email to the given user."""
        return await agent_repo.link_agents_by_email(db, user_id, email)

    # ── Activity logging ──────────────────────────────────────────────

    async def log_activity(
        self,
        db: AsyncSession,
        redis: aioredis.Redis,
        agent_id: Any,
        action_type: str,
        description: str,
        project_id: Any = None,
        metadata: dict | None = None,
    ) -> None:
        """Record activity in DB and publish event to Redis pub/sub."""
        await agent_repo.insert_activity(db, agent_id, action_type, description, project_id, metadata)
        event = {
            "agent_id": str(agent_id),
            "action_type": action_type,
            "description": description,
            "ts": datetime.now(timezone.utc).isoformat(),
        }
        if project_id:
            event["project_id"] = str(project_id)
        await redis.publish("agentspore:activity", json.dumps(event))

    # ── GitHub OAuth token ────────────────────────────────────────────

    async def ensure_github_token(self, agent: dict, db: AsyncSession) -> str | None:
        """Check and refresh GitHub OAuth token. Returns valid token or None."""
        token = agent.get("github_oauth_token")
        if not token:
            return None

        oauth_svc = get_github_oauth_service()
        result = await oauth_svc.ensure_valid_token(
            token=token,
            refresh_token=agent.get("github_oauth_refresh_token"),
            expires_at=agent.get("github_oauth_expires_at"),
        )

        if result is None:
            return token

        new_token = result["access_token"]
        if new_token is None:
            logger.warning("GitHub OAuth token invalid for agent %s, clearing", agent["id"])
            await agent_repo.clear_github_oauth(db, agent["id"])
            await db.commit()
            return None

        await agent_repo.update_github_oauth_tokens(
            db, agent["id"], new_token, result["refresh_token"], result["expires_at"]
        )
        await db.commit()
        return new_token

    # ── Notifications ─────────────────────────────────────────────────

    @staticmethod
    def parse_mentions(text: str) -> list[str]:
        """Extract @handle mentions from text. Returns list of lowercase handles."""
        return list({m.lower() for m in re.findall(r"@([a-z][a-z0-9_-]{0,49})", text, re.IGNORECASE)})

    async def create_notification_task(
        self,
        db: AsyncSession,
        assigned_to_agent_id: Any,
        task_type: str,
        title: str,
        project_id: Any,
        source_ref: str,
        source_key: str,
        priority: str = "medium",
        created_by_agent_id: Any = None,
        source_type: str = "github_notification",
    ) -> None:
        """Create a notification task with deduplication."""
        if await agent_repo.check_notification_exists(db, assigned_to_agent_id, source_key):
            return
        await agent_repo.insert_notification_task(db, {
            "type": task_type,
            "title": title,
            "project_id": project_id,
            "priority": priority,
            "assigned_to": assigned_to_agent_id,
            "created_by_agent": created_by_agent_id,
            "source_ref": source_ref,
            "source_key": source_key,
            "source_type": source_type,
        })

    async def complete_notification_tasks(
        self, db: AsyncSession, agent_id: Any, source_key: str,
    ) -> None:
        """Mark pending tasks as completed when agent has responded."""
        await agent_repo.complete_notification_tasks(db, agent_id, source_key)

    async def cancel_notification_tasks(
        self, db: AsyncSession, source_key: str,
    ) -> None:
        """Cancel all pending tasks for a closed issue/PR."""
        await agent_repo.cancel_notification_tasks(db, source_key)

    # ── README generation ─────────────────────────────────────────────

    @staticmethod
    def build_project_readme(
        title: str,
        description: str,
        agent: dict,
        owner_name: str | None,
        project_id: str,
        idea_id: str | None = None,
        hackathon_id: str | None = None,
        category: str | None = None,
        tech_stack: list[str] | None = None,
        platform_url: str = "https://agentspore.com",
    ) -> str:
        agent_name = agent.get("name", "Agent")
        handle = agent.get("handle", "")
        agent_id = str(agent.get("id", ""))
        handle_str = f"@{handle}" if handle else agent_name
        created_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

        rows = [
            f"| **Agent** | [{handle_str}]({platform_url}/agents/{agent_id}) |",
            f"| **Agent ID** | `{agent_id}` |",
        ]
        if handle:
            rows.append(f"| **Handle** | `@{handle}` |")
        if owner_name:
            rows.append(f"| **Owner** | {owner_name} |")
        if category:
            rows.append(f"| **Category** | {category} |")
        if tech_stack:
            rows.append(f"| **Tech Stack** | {', '.join(tech_stack)} |")
        if idea_id:
            rows.append(f"| **Source Idea** | `{idea_id}` |")
        if hackathon_id:
            rows.append(f"| **Hackathon** | `{hackathon_id}` |")
        rows.append(f"| **Project ID** | `{project_id}` |")
        rows.append(f"| **Created** | {created_at} |")
        rows.append(f"| **Platform** | [{platform_url}]({platform_url}) |")

        parts = [
            f"# {title}",
            "",
            f"> {description}" if description else "",
            "",
            "## 🤖 Project Provenance",
            "",
            "This project was autonomously created by an AI agent on [AgentSpore]"
            f"({platform_url}). See below for full attribution metadata.",
            "",
            "| Field | Value |",
            "|-------|-------|",
            *rows,
            "",
            "---",
            "",
            f"*View agent profile: [{handle_str}]({platform_url}/agents/{agent_id})*",
        ]
        return "\n".join(parts)


@lru_cache
def get_agent_service() -> AgentService:
    return AgentService()


async def get_agent_by_api_key(
    x_api_key: str = Header(..., alias="X-API-Key"),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Authenticate agent by API key from X-API-Key header."""
    key_hash = AgentService.hash_api_key(x_api_key)
    agent = await agent_repo.get_agent_by_api_key_hash(db, key_hash)
    if not agent:
        raise HTTPException(status_code=401, detail="Invalid or inactive API key")
    return agent
