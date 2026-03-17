"""AgentService — all business logic for agents, projects, heartbeat, tasks, OAuth, reviews."""

import hashlib
import json
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Literal
from uuid import UUID, uuid4

import httpx
import redis.asyncio as aioredis
from fastapi import Depends, Header, HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db
from app.core.redis_client import get_redis
from app.repositories.agent_repo import AgentRepository, get_agent_repo
from app.services.badge_service import award_badges
from app.repositories.flow_repo import FlowRepository, get_flow_repo
from app.repositories.mixer_repo import MixerRepository, get_mixer_repo
from app.repositories.rental_repo import RentalRepository, get_rental_repo
from app.schemas.agents import (
    AgentProfile,
    HeartbeatResponseBody,
    PlatformStats,
    ProjectResponse,
)

from loguru import logger


class AgentService:
    """All business logic for agent operations."""

    def __init__(self, db: AsyncSession, redis: aioredis.Redis | None = None):
        self.db = db
        self.redis = redis
        self.repo = get_agent_repo(db)
        self.flow_repo = get_flow_repo(db)
        self.rental_repo = get_rental_repo(db)
        self.mixer_repo = get_mixer_repo(db)

    # ── Static / utility helpers ──────────────────────────────────────

    @staticmethod
    def hash_api_key(api_key: str) -> str:
        return hashlib.sha256(api_key.encode()).hexdigest()

    @staticmethod
    def parse_mentions(text: str) -> list[str]:
        """Extract @handle mentions from text. Returns list of lowercase handles."""
        return list({m.lower() for m in re.findall(r"@([a-z][a-z0-9_-]{0,49})", text, re.IGNORECASE)})

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

    @staticmethod
    def _agent_profile(a: dict) -> AgentProfile:
        return AgentProfile(
            id=str(a["id"]),
            name=a["name"],
            handle=a["handle"] or "",
            agent_type=a["agent_type"],
            model_provider=a["model_provider"] or "",
            model_name=a["model_name"] or "",
            specialization=a["specialization"],
            skills=list(a["skills"]) if a["skills"] else [],
            karma=a["karma"],
            projects_created=a["projects_created"],
            code_commits=a["code_commits"],
            reviews_done=a["reviews_done"],
            last_heartbeat=str(a["last_heartbeat"]) if a["last_heartbeat"] else None,
            is_active=a["is_active"],
            created_at=str(a["created_at"]),
            dna_risk=a["dna_risk"] if a["dna_risk"] is not None else 5,
            dna_speed=a["dna_speed"] if a["dna_speed"] is not None else 5,
            dna_verbosity=a["dna_verbosity"] if a["dna_verbosity"] is not None else 5,
            dna_creativity=a["dna_creativity"] if a["dna_creativity"] is not None else 5,
            bio=a["bio"],
        )

    # ── Handle generation ─────────────────────────────────────────────

    async def generate_handle(self, name: str) -> str:
        base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
        base = re.sub(r"-{2,}", "-", base)[:50] or "agent"
        handle = base
        counter = 2
        while await self.repo.handle_exists(handle):
            handle = f"{base}-{counter}"
            counter += 1
        return handle

    # ── Registration ──────────────────────────────────────────────────

    async def register_agent(
        self,
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
        from app.services.github_oauth_service import get_github_oauth_service

        api_key = f"af_{secrets.token_urlsafe(32)}"
        api_key_hash = self.hash_api_key(api_key)

        handle = await self.generate_handle(name)
        agent_id = uuid4()

        oauth_service = get_github_oauth_service()
        oauth_data = oauth_service.get_authorization_url(str(agent_id))

        owner_email_clean = owner_email.strip()
        owner_user_id = await self.repo.find_user_id_by_email(owner_email_clean)

        await self.repo.insert_agent({
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
            await self.repo.link_contributors_to_user(owner_user_id, agent_id)

        await self.log_activity(str(agent_id), "registered", f"Agent '{name}' joined AgentSpore")

        return {
            "agent_id": str(agent_id),
            "api_key": api_key,
            "name": name,
            "handle": handle,
            "github_auth_url": oauth_data["auth_url"],
        }

    # ── Ownership linking ─────────────────────────────────────────────

    async def link_agents_by_email(self, user_id, email: str) -> int:
        """Auto-link agents with matching owner_email to the given user."""
        return await self.repo.link_agents_by_email(user_id, email)

    # ── Activity logging ──────────────────────────────────────────────

    async def log_activity(
        self,
        agent_id: Any,
        action_type: str,
        description: str,
        project_id: Any = None,
        metadata: dict | None = None,
    ) -> None:
        """Record activity in DB and publish event to Redis pub/sub."""
        await self.repo.insert_activity(agent_id, action_type, description, project_id, metadata)
        if self.redis:
            event = {
                "agent_id": str(agent_id),
                "action_type": action_type,
                "description": description,
                "ts": datetime.now(timezone.utc).isoformat(),
            }
            if project_id:
                event["project_id"] = str(project_id)
            await self.redis.publish("agentspore:activity", json.dumps(event))

    # ── GitHub OAuth ──────────────────────────────────────────────────

    async def ensure_github_token(self, agent: dict) -> str | None:
        """Check and refresh GitHub OAuth token. Returns valid token or None."""
        from app.services.github_oauth_service import get_github_oauth_service

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
            await self.repo.clear_github_oauth(agent["id"])
            await self.db.commit()
            return None

        await self.repo.update_github_oauth_tokens(
            agent["id"], new_token, result["refresh_token"], result["expires_at"]
        )
        await self.db.commit()
        return new_token

    async def github_oauth_callback(self, code: str, state: str) -> dict:
        """Process GitHub OAuth callback. Returns response dict."""
        from app.services.github_oauth_service import get_github_oauth_service
        from app.services.git_service import get_git_service

        agent = await self.repo.get_agent_by_github_state(state)
        if not agent:
            return {"status": "error", "message": "Invalid or expired OAuth state. Please register again."}

        agent_id = agent["id"]

        oauth_service = get_github_oauth_service()
        token_data = await oauth_service.exchange_code_for_token(code)
        if not token_data or "access_token" not in token_data:
            return {"status": "error", "message": "Failed to exchange authorization code for token."}

        access_token = token_data["access_token"]
        scope = token_data.get("scope", "")
        refresh_token = token_data.get("refresh_token")
        expires_in = token_data.get("expires_in")

        user_info = await oauth_service.get_user_info(access_token)
        if not user_info:
            logger.warning("Could not fetch GitHub user info — proceeding with token only")
            user_info = {}

        github_id = str(user_info.get("id", ""))
        github_login = user_info.get("login", "")
        expires_at = datetime.utcnow() + timedelta(seconds=expires_in) if expires_in else None

        await self.repo.update_github_oauth(agent_id, {
            "github_id": github_id,
            "token": access_token,
            "refresh_token": refresh_token,
            "scope": scope,
            "expires_at": expires_at,
            "login": github_login,
        })

        await self.repo.insert_activity_simple(
            agent_id, "oauth_connected",
            f"GitHub OAuth connected: {github_login}",
            json.dumps({"github_login": github_login, "scope": scope}),
        )

        logger.info("Agent %s activated with GitHub identity: %s", agent_id, github_login)

        if github_login:
            try:
                git = get_git_service()
                await git.invite_to_org(github_login)
            except Exception as e:
                logger.warning("Failed to invite %s to org: %s", github_login, e)

            try:
                async with httpx.AsyncClient() as _http:
                    accept_resp = await _http.patch(
                        f"https://api.github.com/user/memberships/orgs/{git.github.org}",
                        headers={
                            "Authorization": f"Bearer {access_token}",
                            "Accept": "application/vnd.github+json",
                        },
                        json={"state": "active"},
                    )
                    if accept_resp.status_code == 200:
                        logger.info("Auto-accepted org invite for %s", github_login)
                    else:
                        logger.warning(
                            "Could not auto-accept invite for %s: %s %s",
                            github_login, accept_resp.status_code, accept_resp.text[:200],
                        )
            except Exception as e:
                logger.warning("Auto-accept invite error for %s: %s", github_login, e)

        return {
            "status": "connected",
            "agent_id": str(agent_id),
            "github_login": github_login,
            "message": f"Successfully connected GitHub account: {github_login}. Agent is now active!",
        }

    async def github_oauth_status(self, agent: dict) -> dict:
        """Return GitHub OAuth status for an agent."""
        connected = bool(agent.get("github_oauth_token"))
        github_login = agent.get("github_user_login")
        connected_at = str(agent["github_oauth_connected_at"]) if agent.get("github_oauth_connected_at") else None
        scope = agent.get("github_oauth_scope", "")
        scopes = scope.split(",") if scope else []
        return {
            "connected": connected,
            "github_login": github_login,
            "connected_at": connected_at,
            "scopes": scopes,
            "oauth_token": None,
        }

    async def github_connect_url(self, agent: dict) -> dict:
        """Generate new GitHub OAuth URL for an agent."""
        from app.services.github_oauth_service import get_github_oauth_service

        oauth_svc = get_github_oauth_service()
        result = oauth_svc.get_authorization_url(str(agent["id"]))
        await self.repo.update_github_oauth_state(agent["id"], result["state"])
        await self.db.commit()
        return {"auth_url": result["auth_url"]}

    async def github_revoke(self, agent: dict) -> dict:
        """Revoke GitHub OAuth access."""
        from app.services.github_oauth_service import get_github_oauth_service

        token = agent.get("github_oauth_token")
        if token:
            oauth_service = get_github_oauth_service()
            await oauth_service.revoke_token(token)

        await self.repo.revoke_github_oauth(agent["id"])
        return {
            "status": "revoked",
            "message": "GitHub OAuth access revoked. Agent is now inactive. Use /github/reconnect to get a new OAuth URL.",
        }

    async def github_reconnect(self, agent: dict) -> dict:
        """Generate new OAuth URL for reconnection."""
        from app.services.github_oauth_service import get_github_oauth_service

        oauth_service = get_github_oauth_service()
        oauth_data = oauth_service.get_authorization_url(str(agent["id"]))
        await self.repo.update_github_oauth_state(agent["id"], oauth_data["state"])
        return {
            "github_auth_url": oauth_data["auth_url"],
            "message": "Open this URL in browser to reconnect GitHub account.",
        }

    # ── GitLab OAuth ──────────────────────────────────────────────────

    async def gitlab_oauth_login(self, agent: dict) -> dict:
        """Generate GitLab OAuth login URL."""
        from app.services.gitlab_oauth_service import get_gitlab_oauth_service

        oauth_service = get_gitlab_oauth_service()
        oauth_data = oauth_service.get_authorization_url(str(agent["id"]))
        await self.repo.update_gitlab_oauth_state(agent["id"], oauth_data["state"])
        await self.db.commit()
        return {"gitlab_auth_url": oauth_data["auth_url"], "message": "Open this URL to connect your GitLab account."}

    async def gitlab_oauth_callback(self, code: str, state: str) -> dict:
        """Process GitLab OAuth callback."""
        from app.services.gitlab_oauth_service import get_gitlab_oauth_service
        from app.services.git_service import get_git_service

        agent = await self.repo.get_agent_by_gitlab_state(state)
        if not agent:
            return {"status": "error", "message": "Invalid or expired OAuth state. Please try again."}

        agent_id = agent["id"]
        oauth_service = get_gitlab_oauth_service()
        token_data = await oauth_service.exchange_code_for_token(code)
        if not token_data or "access_token" not in token_data:
            return {"status": "error", "message": "Failed to exchange authorization code for token."}

        access_token = token_data["access_token"]
        refresh_token = token_data.get("refresh_token")
        expires_in = token_data.get("expires_in")
        scope = token_data.get("scope", "")

        user_info = await oauth_service.get_user_info(access_token)
        if not user_info:
            return {"status": "error", "message": "Failed to get GitLab user information."}

        gitlab_id = str(user_info.get("id", ""))
        gitlab_login = user_info.get("username", "")
        expires_at = datetime.utcnow() + timedelta(seconds=expires_in) if expires_in else None

        await self.repo.update_gitlab_oauth(agent_id, {
            "gitlab_id": gitlab_id,
            "token": access_token,
            "refresh_token": refresh_token,
            "scope": scope,
            "expires_at": expires_at,
            "login": gitlab_login,
        })

        await self.repo.insert_activity_simple(
            agent_id, "oauth_connected",
            f"GitLab OAuth connected: {gitlab_login}",
            {"gitlab_login": gitlab_login, "scope": scope, "provider": "gitlab"},
        )

        logger.info("Agent %s connected GitLab identity: %s", agent_id, gitlab_login)

        if gitlab_login:
            try:
                git = get_git_service()
                await git.invite_to_org(gitlab_login, vcs_provider="gitlab")
            except Exception as e:
                logger.warning("Failed to add %s to GitLab group: %s", gitlab_login, e)

        await self.db.commit()
        return {
            "status": "connected",
            "agent_id": str(agent_id),
            "gitlab_login": gitlab_login,
            "message": f"Successfully connected GitLab account: {gitlab_login}",
        }

    async def gitlab_oauth_status(self, agent: dict) -> dict:
        """Return GitLab OAuth status for an agent."""
        connected = bool(agent.get("gitlab_oauth_token"))
        scope = agent.get("gitlab_oauth_scope", "")
        return {
            "connected": connected,
            "gitlab_login": agent.get("gitlab_user_login"),
            "connected_at": str(agent["gitlab_oauth_connected_at"]) if agent.get("gitlab_oauth_connected_at") else None,
            "scopes": scope.split(" ") if scope else [],
            "oauth_token": None,
        }

    async def gitlab_revoke(self, agent: dict) -> dict:
        """Revoke GitLab OAuth access."""
        await self.repo.revoke_gitlab_oauth(agent["id"])
        await self.db.commit()
        return {"status": "revoked", "message": "GitLab OAuth access revoked. Use /gitlab/login to reconnect."}

    # ── Agent self-service ────────────────────────────────────────────

    async def get_my_profile(self, agent: dict) -> dict:
        """Return agent's own profile data."""
        return {
            "agent_id": str(agent["id"]),
            "name": agent["name"],
            "handle": agent["handle"],
            "specialization": agent.get("specialization", ""),
            "description": agent.get("description", ""),
            "bio": agent.get("bio", ""),
            "skills": agent.get("skills", []),
            "model_provider": agent.get("model_provider", ""),
            "model_name": agent.get("model_name", ""),
            "karma": agent.get("karma", 0),
            "projects_created": agent.get("projects_created", 0),
            "code_commits": agent.get("code_commits", 0),
            "reviews_done": agent.get("reviews_done", 0),
            "is_active": agent.get("is_active", False),
            "last_heartbeat": str(agent["last_heartbeat"]) if agent.get("last_heartbeat") else None,
            "github_connected": bool(agent.get("github_oauth_token")),
            "github_login": agent.get("github_user_login"),
            "created_at": str(agent["created_at"]) if agent.get("created_at") else None,
        }

    async def rotate_api_key(self, agent: dict) -> dict:
        """Rotate agent's API key. Old key becomes invalid immediately."""
        new_api_key = f"af_{secrets.token_urlsafe(32)}"
        new_hash = self.hash_api_key(new_api_key)
        await self.repo.update_api_key_hash(agent["id"], new_hash)
        await self.db.commit()
        return {
            "api_key": new_api_key,
            "message": "API key rotated successfully. Old key is now invalid. Save this key — it won't be shown again.",
        }

    # ── Heartbeat ─────────────────────────────────────────────────────

    async def heartbeat(self, agent: dict, body) -> HeartbeatResponseBody:
        """Process agent heartbeat. Returns tasks, feedback, notifications, DMs."""

        agent_id = agent["id"]

        await self.repo.update_heartbeat(agent_id)
        await self.repo.insert_heartbeat_log(agent_id, body.status, len(body.completed_tasks))

        for task in body.completed_tasks:
            karma = {"write_code": 10, "add_feature": 15, "fix_bug": 10, "code_review": 5}.get(task.get("type", ""), 5)
            await self.repo.add_karma(agent_id, karma)

        # Acknowledge previously delivered DMs
        if body.read_dm_ids:
            await self.repo.mark_dms_read(body.read_dm_ids)

        # Feature requests
        features = await self.repo.get_feature_requests_for_agent(agent_id, body.current_capacity)
        tasks = []
        for fr in features:
            tasks.append({
                "type": "add_feature",
                "id": str(fr["id"]),
                "project_id": str(fr["project_id"]),
                "title": fr["title"],
                "description": fr["description"],
                "votes": fr["votes"],
                "priority": "high" if fr["votes"] >= 5 else "medium",
            })
            await self.repo.accept_feature_request(fr["id"], agent_id)

        # Bug reports
        if len(tasks) < body.current_capacity:
            bugs = await self.repo.get_bug_reports_for_agent(agent_id, body.current_capacity - len(tasks))
            for bug in bugs:
                tasks.append({
                    "type": "fix_bug",
                    "id": str(bug["id"]),
                    "project_id": str(bug["project_id"]),
                    "title": bug["title"],
                    "description": bug["description"],
                    "severity": bug["severity"],
                })
                await self.repo.assign_bug_report(bug["id"], agent_id)

        # Feedback
        comments_raw = await self.repo.get_project_comments_for_agent(agent_id)
        feedback = [
            {"type": "comment", "content": c["content"], "user": c["user_name"],
             "project": c["project_title"], "timestamp": str(c["created_at"])}
            for c in comments_raw
        ]

        # Notifications
        notif_raw = await self.repo.get_pending_notifications(agent_id)
        notifications = [
            {
                "id": str(n["id"]),
                "type": n["type"],
                "title": n["title"],
                "project_id": str(n["project_id"]) if n["project_id"] else None,
                "source_ref": n["source_ref"],
                "source_key": n["source_key"],
                "priority": n["priority"],
                "from": f"@{n['from_handle']}" if n["from_handle"] else n["from_name"] or "system",
                "created_at": str(n["created_at"]),
            }
            for n in notif_raw
        ]

        # Direct messages (delivered but NOT marked as read until agent confirms via read_dm_ids)
        dm_raw = await self.repo.get_unread_dms(agent_id)
        direct_messages = []
        for dm in dm_raw:
            direct_messages.append({
                "id": str(dm["id"]),
                "from": f"@{dm['from_agent_handle']}" if dm["from_agent_handle"] else dm["human_name"] or "anonymous",
                "from_name": dm["from_agent_name"] or dm["human_name"] or "anonymous",
                "content": dm["content"],
                "created_at": str(dm["created_at"]),
            })

        # Active rentals
        active_rentals_raw = await self.rental_repo.list_agent_rentals(str(agent_id), status="active")
        active_rentals = [
            {
                "rental_id": str(r["id"]),
                "user_name": r["user_name"],
                "title": r["title"],
                "created_at": str(r["created_at"]),
            }
            for r in active_rentals_raw
        ]

        # Flow steps
        flow_steps_raw = await self.flow_repo.get_agent_ready_steps(str(agent_id))
        flow_steps = [
            {
                "step_id": str(s["id"]),
                "flow_id": str(s["flow_id"]),
                "flow_title": s["flow_title"],
                "title": s["title"],
                "instructions": s.get("instructions"),
                "input_text": s.get("input_text"),
                "status": s["status"],
            }
            for s in flow_steps_raw
        ]

        # Mixer chunks
        mixer_chunks_raw = await self.mixer_repo.get_agent_ready_chunks(str(agent_id))
        mixer_chunks = [
            {
                "chunk_id": str(c["id"]),
                "session_id": str(c["session_id"]),
                "session_title": c["session_title"],
                "title": c["title"],
                "instructions": c.get("instructions"),
                "status": c["status"],
            }
            for c in mixer_chunks_raw
        ]

        await self.log_activity(
            agent_id, "heartbeat",
            f"Heartbeat: {body.status}, {len(tasks)} tasks, {len(notifications)} notifications, "
            f"{len(direct_messages)} DMs, {len(active_rentals)} rentals, "
            f"{len(flow_steps)} flow steps, {len(mixer_chunks)} mixer chunks",
        )

        try:
            await award_badges(str(agent_id), self.db)
        except Exception:
            pass

        warnings: list[str] = []
        if not agent.get("github_oauth_token"):
            warnings.append(
                "GitHub OAuth not connected. Connect via GET /api/v1/agents/github/connect "
                "to operate under your own identity. Without OAuth you cannot create projects, "
                "push code, or comment on issues."
            )

        return HeartbeatResponseBody(
            tasks=tasks, feedback=feedback, notifications=notifications,
            direct_messages=direct_messages, rentals=active_rentals,
            flow_steps=flow_steps, mixer_chunks=mixer_chunks, warnings=warnings,
        )

    # ── Notifications ─────────────────────────────────────────────────

    async def complete_notification(self, task_id: UUID, agent_id) -> None:
        """Mark a notification task as completed."""
        await self.repo.complete_notification_by_id(task_id, agent_id)
        await self.db.commit()

    async def create_notification_task(
        self,
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
        if await self.repo.check_notification_exists(assigned_to_agent_id, source_key):
            return
        await self.repo.insert_notification_task({
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

    async def complete_notification_tasks(self, agent_id: Any, source_key: str) -> None:
        """Mark pending tasks as completed when agent has responded."""
        await self.repo.complete_notification_tasks(agent_id, source_key)

    async def cancel_notification_tasks(self, source_key: str) -> None:
        """Cancel all pending tasks for a closed issue/PR."""
        await self.repo.cancel_notification_tasks(source_key)

    # ── Projects ──────────────────────────────────────────────────────

    async def create_project(self, agent: dict, body) -> ProjectResponse:
        """Create a new project with git repo, README, token, and contributor setup."""
        from app.services.git_service import get_git_service
        from app.services.web3_service import get_web3_service
        from app.repositories import governance_repo

        project_id = uuid4()
        agent_id = agent["id"]

        git = get_git_service()
        vcs = body.vcs_provider
        user_oauth_token = (await self.ensure_github_token(agent)) if vcs == "github" else None
        git_repo_url = await git.create_repo(
            body.title, body.description, vcs_provider=vcs, user_token=user_oauth_token,
        )
        if git_repo_url:
            try:
                await git.setup_repo_admin(body.title, vcs_provider=vcs)
            except Exception as e:
                logger.warning("setup_repo_admin failed for %s: %s", body.title, e)

            github_login = agent.get("github_user_login")
            if github_login and vcs == "github":
                try:
                    await git.add_repo_collaborator(body.title, github_login, "push", vcs_provider=vcs)
                except Exception as e:
                    logger.warning("add_repo_collaborator failed for %s/%s: %s", body.title, github_login, e)

        owner_name = await self.repo.get_project_owner_name(agent_id)

        if body.hackathon_id:
            h = await self.repo.get_hackathon_status(body.hackathon_id)
            if not h:
                raise HTTPException(status_code=404, detail="Hackathon not found")
            if h["status"] != "active":
                raise HTTPException(
                    status_code=400,
                    detail=f"Cannot submit to hackathon with status '{h['status']}' — only 'active' hackathons accept projects",
                )

        await self.repo.insert_project({
            "id": project_id, "title": body.title, "desc": body.description,
            "cat": body.category, "agent_id": agent_id, "stack": body.tech_stack,
            "git_url": git_repo_url, "hackathon_id": body.hackathon_id, "vcs": vcs,
        })

        if git_repo_url:
            readme_content = self.build_project_readme(
                title=body.title,
                description=body.description,
                agent=agent,
                owner_name=owner_name,
                project_id=str(project_id),
                idea_id=str(body.idea_id) if getattr(body, "idea_id", None) else None,
                hackathon_id=str(body.hackathon_id) if body.hackathon_id else None,
                category=body.category,
                tech_stack=list(body.tech_stack) if body.tech_stack else None,
            )
            readme_ok = await git.push_files(
                repo_name=body.title,
                files=[{"path": "README.md", "content": readme_content, "language": "markdown"}],
                commit_message="chore: add project provenance metadata",
                vcs_provider=vcs,
                user_token=user_oauth_token,
            )
            if not readme_ok:
                logger.warning("README push failed for project %s", project_id)

        await self.repo.increment_projects_created(agent_id)

        try:
            web3_svc = get_web3_service()
            contract_address, deploy_tx = await web3_svc.deploy_project_token(
                str(project_id), body.title
            )
            if contract_address:
                words = body.title.upper().split()
                symbol = "".join(w[0] for w in words if w)[:6] or "SPORE"
                await self.repo.insert_project_token(project_id, contract_address, symbol, deploy_tx or None)
        except Exception as exc:
            logger.warning("Token deploy failed for project %s: %s", project_id, exc)

        owner_user_id = await self.repo.get_agent_owner_user_id(agent_id)
        if owner_user_id:
            await governance_repo.auto_approve_contributor(self.db, project_id, owner_user_id)

        await self.log_activity(agent_id, "project_created", f"Created: {body.title}", project_id=project_id)

        project = await self.repo.get_project_full(project_id)
        return self._project_response(project)

    async def list_projects(
        self,
        *,
        limit: int,
        needs_review: bool | None,
        has_open_issues: bool | None,
        category: str | None,
        status: str | None,
        tech_stack: str | None,
        mine: bool | None,
        x_api_key: str | None,
    ) -> list[dict]:
        """List platform projects with filters."""
        where = ["1=1"]
        params: dict = {"limit": limit}

        if mine is True and x_api_key:
            key_hash = self.hash_api_key(x_api_key)
            agent_id = await self.repo.get_agent_id_by_api_key_hash(key_hash)
            if agent_id:
                where.append("p.creator_agent_id = :mine_agent_id")
                params["mine_agent_id"] = agent_id

        if category:
            where.append("p.category = :category")
            params["category"] = category
        if status:
            where.append("p.status = :status")
            params["status"] = status
        if tech_stack:
            where.append(":tech = ANY(p.tech_stack)")
            params["tech"] = tech_stack
        if needs_review is True:
            where.append("NOT EXISTS (SELECT 1 FROM code_reviews cr WHERE cr.project_id = p.id)")
            where.append("(EXISTS (SELECT 1 FROM code_files cf WHERE cf.project_id = p.id) OR p.repo_url IS NOT NULL)")
        if has_open_issues is True:
            where.append("EXISTS (SELECT 1 FROM bug_reports br WHERE br.project_id = p.id AND br.status = 'open')")

        where_clause = " AND ".join(where)
        rows = await self.repo.list_agent_projects(where_clause, params)
        return [
            {
                "id": str(r["id"]),
                "title": r["title"],
                "description": r["description"] or "",
                "status": r["status"],
                "repo_url": r["repo_url"],
                "category": r["category"],
                "tech_stack": list(r["tech_stack"]) if r["tech_stack"] else [],
                "creator_agent_id": str(r["creator_agent_id"]) if r["creator_agent_id"] else None,
                "creator_handle": r["creator_handle"] or "",
                "creator_name": r["creator_name"] or "",
            }
            for r in rows
        ]

    async def get_project_files(self, project_id: UUID, agent: dict) -> list[dict]:
        """Get project files from DB or VCS fallback."""
        from app.services.git_service import get_git_service

        db_files = await self.repo.get_project_code_files(project_id)
        if db_files:
            return db_files

        project = await self.repo.get_project_basic(project_id, "title, repo_url, vcs_provider")
        if not project or not project["repo_url"]:
            return []

        git = get_git_service()
        vcs = project.get("vcs_provider") or "github"
        try:
            tree = await git.get_repo_files(project["title"], vcs_provider=vcs)
            if not tree:
                return []

            TEXT_EXTS = {".py", ".js", ".ts", ".tsx", ".jsx", ".html", ".css", ".json", ".md",
                         ".yaml", ".yml", ".toml", ".cfg", ".ini", ".sh", ".sql", ".env",
                         ".svelte", ".vue", ".go", ".rs", ".java", ".kt", ".rb", ".php"}
            files = []
            for item in tree:
                if item.get("type") != "blob":
                    continue
                path = item.get("path", "")
                ext = "." + path.rsplit(".", 1)[-1] if "." in path else ""
                if ext.lower() not in TEXT_EXTS:
                    continue
                if len(files) >= 50:
                    break

                content = await git.get_file_content(project["title"], path, vcs_provider=vcs)
                if content:
                    lang = ext.lstrip(".") if ext else None
                    files.append({"path": path, "content": content, "language": lang, "version": 1})
            return files
        except Exception as e:
            logger.warning("Failed to fetch files from VCS for project %s: %s", project_id, e)
            return []

    async def get_project_feedback(self, project_id: UUID) -> dict:
        """Get project feedback (features, bugs, comments)."""
        return await self.repo.get_project_feedback(project_id)

    async def create_review(self, project_id: UUID, agent: dict, body) -> dict:
        """Create code review with GitHub issues for critical problems."""
        from app.services.git_service import get_git_service

        review_id = uuid4()
        await self.repo.insert_code_review(review_id, project_id, agent["id"], body.status, body.summary, body.model_used)

        if body.model_used:
            await self.repo.insert_model_usage(agent["id"], body.model_used, "review", review_id, "review")

        for c in body.comments:
            await self.repo.insert_review_comment(review_id, c.get("file_path"), c.get("line_number"), c.get("comment", ""), c.get("suggestion"))

        await self.repo.increment_reviews_done(agent["id"])

        issues_created = []
        if body.status in ("needs_changes", "rejected") and body.comments:
            project = await self.repo.get_project_for_review(project_id)
            repo_url = project["repo_url"] if project else None

            if repo_url:
                git = get_git_service()
                reviewer_name = agent.get("name", "ReviewerBot")
                reviewer_handle = agent.get("handle", "")
                reviewer_id = str(agent.get("id", ""))
                reviewer_ref = f"@{reviewer_handle}" if reviewer_handle else reviewer_name
                platform_url = "https://agentspore.com"

                for c in body.comments:
                    severity = c.get("severity", "medium").lower()
                    if severity not in ("high", "critical"):
                        continue

                    file_path = c.get("file_path", "unknown")
                    line_no = c.get("line_number", 0)
                    comment_text = c.get("comment", "")
                    suggestion = c.get("suggestion", "")

                    short_title = comment_text[:72].rstrip()
                    issue_title = f"[{severity.upper()}] {file_path}: {short_title}"

                    issue_body = (
                        f"## Code Review Issue\n\n"
                        f"**Reviewer:** [{reviewer_ref}]({platform_url}/agents/{reviewer_id})  \n"
                        f"**File:** `{file_path}`  \n"
                        f"**Line:** {line_no if line_no else 'N/A'}  \n"
                        f"**Severity:** {severity.upper()}\n\n"
                        f"### Problem\n{comment_text}\n\n"
                        f"### Suggestion\n{suggestion}\n\n"
                        f"---\n"
                        f"*Automated review by [{reviewer_ref}]({platform_url}/agents/{reviewer_id})"
                        f" · [AgentSpore]({platform_url})*"
                    )

                    label = "bug" if severity == "critical" else "enhancement"
                    issue = await git.create_issue(
                        project["title"],
                        issue_title,
                        issue_body,
                        labels=[label, f"severity:{severity}"],
                    )
                    if issue:
                        issues_created.append(issue)
                        logger.info(
                            "Created GitHub issue #%s for %s: %s",
                            issue["number"], project["title"], issue_title[:60],
                        )
                        owner_id = project.get("creator_agent_id")
                        if owner_id and str(owner_id) != str(agent["id"]):
                            await self.create_notification_task(
                                assigned_to_agent_id=owner_id,
                                task_type="respond_to_issue",
                                title=issue_title[:200],
                                project_id=project_id,
                                source_ref=issue["url"],
                                source_key=f"{project_id}:issue:{issue['number']}",
                                priority="urgent" if severity == "critical" else "high",
                                created_by_agent_id=agent["id"],
                            )

        await self.log_activity(
            agent["id"], "code_review",
            f"Code review ({body.status}): {len(body.comments)} comments, {len(issues_created)} GitHub issues",
            project_id=project_id,
            metadata={"issues_created": len(issues_created), "github_issues": [i["url"] for i in issues_created]},
        )
        return {
            "review_id": str(review_id),
            "status": body.status,
            "comments_count": len(body.comments),
            "github_issues_created": len(issues_created),
            "github_issues": [{"number": i["number"], "url": i["url"]} for i in issues_created],
        }

    async def deploy_project(self, project_id: UUID, agent: dict) -> dict:
        """Deploy a project (Render if configured, fallback URL otherwise)."""
        project = await self.repo.get_project_basic(project_id, "id, title, repo_url")
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")

        settings = get_settings()
        deploy_url = f"https://preview.agentspore.com/{project_id}"

        if settings.render_api_key and project["repo_url"]:
            try:
                from app.services.render_service import RenderService
                render = RenderService(settings.render_api_key, settings.render_owner_id)
                deploy_result = await render.deploy_project(
                    repo_url=project["repo_url"], title=project["title"],
                )
                deploy_url = deploy_result["deploy_url"]
                logger.info("Render deploy: %s → %s", project["title"], deploy_url)
            except Exception as e:
                logger.warning("Render deploy failed for '%s': %s (using fallback URL)", project["title"], e)

        await self.repo.update_project_deployed(project_id, deploy_url)
        await self.log_activity(agent["id"], "deploy", f"Deployed to {deploy_url}", project_id=project_id)
        return {"status": "deployed", "deploy_url": deploy_url, "preview_url": deploy_url}

    async def delete_project(self, project_id: UUID, agent: dict) -> dict:
        """Delete a project and its GitHub repo."""
        from app.services.git_service import get_git_service

        project = await self.repo.get_project_basic(project_id, "title, creator_agent_id, repo_url, vcs_provider")
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")

        if str(project["creator_agent_id"]) != str(agent["id"]):
            raise HTTPException(status_code=403, detail="Only project creator can delete projects")

        await self.repo.delete_project_and_related(project_id)
        await self.db.commit()

        deleted_repo = False
        if project["vcs_provider"] == "github" and project.get("repo_url"):
            try:
                git = get_git_service()
                repo_name = git._sanitize_repo_name(project["title"])
                ok = await git.github.delete_repository(repo_name)
                deleted_repo = ok
            except Exception:
                pass

        await self.repo.recount_agent_projects(agent["id"])
        await self.db.commit()

        return {
            "status": "deleted",
            "project_id": str(project_id),
            "title": project["title"],
            "github_repo_deleted": deleted_repo,
        }

    async def merge_project_pr(self, project_id: UUID, agent: dict, body: dict) -> dict:
        """Merge a PR in the project's repository."""
        from app.services.git_service import get_git_service

        pr_number = body.get("pr_number")
        if not pr_number or not isinstance(pr_number, int):
            raise HTTPException(status_code=422, detail="pr_number (int) is required")

        project = await self.repo.get_project_basic(project_id, "title, creator_agent_id, vcs_provider")
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")

        if str(project["creator_agent_id"]) != str(agent["id"]):
            raise HTTPException(status_code=403, detail="Only project creator can merge PRs")

        if project["vcs_provider"] != "github":
            raise HTTPException(status_code=400, detail="Only GitHub projects support PR merge")

        git = get_git_service()
        commit_message = body.get("commit_message", "")
        ok = await git.merge_pull_request(project["title"], pr_number, commit_message)
        if not ok:
            raise HTTPException(status_code=502, detail="Failed to merge PR on GitHub")

        return {"status": "merged", "pr_number": pr_number, "project_id": str(project_id)}

    # ── Issues API ────────────────────────────────────────────────────

    async def list_my_issues(self, agent: dict, state: str, limit: int) -> dict:
        """All GitHub Issues across all agent's projects."""
        from app.services.git_service import get_git_service

        projects = await self.repo.get_agent_project_ids(agent["id"], limit)
        git = get_git_service()
        all_issues = []
        for project in projects:
            issues = await git.list_issues(project["title"], state=state)
            repo_slug = git._sanitize_repo_name(project["title"])
            repo_url = f"https://github.com/{git.org}/{repo_slug}"
            for issue in issues:
                all_issues.append({
                    **issue,
                    "project_id": str(project["id"]),
                    "project_title": project["title"],
                    "project_repo_url": repo_url,
                })

        return {
            "issues": all_issues,
            "total": len(all_issues),
            "projects_checked": len(projects),
            "state": state,
        }

    async def list_issue_comments(self, project_id: UUID, issue_number: int) -> dict:
        """List comments for a specific issue."""
        from app.services.git_service import get_git_service

        project = await self.repo.get_project_basic(project_id, "title")
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")

        git = get_git_service()
        comments = await git.list_issue_comments(project["title"], issue_number)
        return {
            "comments": comments,
            "count": len(comments),
            "issue_number": issue_number,
            "issue_url": f"https://github.com/{git.org}/{git._sanitize_repo_name(project['title'])}/issues/{issue_number}",
        }

    async def post_issue_comment(self, project_id: UUID, issue_number: int, body_text: str, agent: dict) -> dict:
        """Post a comment on a GitHub/GitLab issue."""
        from app.services.git_service import get_git_service

        project = await self.repo.get_project_basic(project_id, "title, repo_url")
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")

        oauth_token = await self.ensure_github_token(agent)
        git = get_git_service()
        result = await git.comment_issue(project["title"], issue_number, body_text, user_token=oauth_token)
        if not result:
            raise HTTPException(status_code=502, detail="Failed to post comment on VCS")

        return {"status": "ok", "comment_id": result.get("id"), "url": result.get("url")}

    async def get_project_git_token(self, project_id: UUID, agent: dict) -> dict:
        """Get git token for a project repository."""
        from app.services.git_service import get_git_service

        project = await self.repo.get_project_basic(
            project_id, "title, repo_url, vcs_provider, creator_agent_id, team_id",
        )
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")

        agent_id = str(agent["id"])
        is_admin = agent.get("is_admin_agent", False)
        is_creator = str(project["creator_agent_id"]) == agent_id

        is_member = False
        if not is_creator and not is_admin and project.get("team_id"):
            from app.repositories import hackathon_repo
            is_member = await hackathon_repo.is_team_member(self.db, project["team_id"], agent["id"])

        if not is_creator and not is_member and not is_admin:
            raise HTTPException(
                status_code=403,
                detail="Only the project creator or a team member can get push access. "
                       "Use fork + pull request to contribute to this project.",
            )

        if project["vcs_provider"] != "github":
            raise HTTPException(status_code=400, detail="Only GitHub projects support git tokens")

        # Get owner email for GitHub credit
        owner_email = None
        if agent.get("owner_user_id"):
            owner_row = await self.db.execute(
                text("SELECT email FROM users WHERE id = :uid"),
                {"uid": agent["owner_user_id"]},
            )
            owner = owner_row.mappings().first()
            if owner:
                owner_email = owner["email"]
        committer = {
            "name": agent["name"],
            "email": owner_email or f"{agent.get('handle', 'agent')}@agents.agentspore.dev",
        }

        oauth_token = await self.ensure_github_token(agent)
        if oauth_token:
            return {"token": oauth_token, "repo_url": project["repo_url"], "committer": committer, "expires_in": 3600}

        git = get_git_service()
        repo_name = git._sanitize_repo_name(project["title"])
        scoped = await git.github.get_scoped_installation_token(repo_name)
        if not scoped:
            raise HTTPException(status_code=503, detail="Failed to generate git credentials")

        return {"token": scoped["token"], "repo_url": project["repo_url"], "committer": committer, "expires_in": 3600}

    async def push_project_files(
        self,
        project_id: UUID,
        agent: dict,
        files: list[dict],
        commit_message: str,
        branch: str = "main",
    ) -> dict:
        """Push files to project repo with guaranteed agent attribution."""
        from app.services.git_service import get_git_service

        project = await self.repo.get_project_basic(
            project_id, "title, repo_url, vcs_provider, creator_agent_id, team_id",
        )
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")

        # Access control: same as get_project_git_token
        agent_id = str(agent["id"])
        is_admin = agent.get("is_admin_agent", False)
        is_creator = str(project["creator_agent_id"]) == agent_id

        is_member = False
        if not is_creator and not is_admin and project.get("team_id"):
            from app.repositories import hackathon_repo
            is_member = await hackathon_repo.is_team_member(self.db, project["team_id"], agent["id"])

        if not is_creator and not is_member and not is_admin:
            raise HTTPException(
                status_code=403,
                detail="Only the project creator or a team member can push files. "
                       "Use fork + pull request to contribute to this project.",
            )

        if project["vcs_provider"] != "github":
            raise HTTPException(status_code=400, detail="Only GitHub projects support atomic push")

        # Always use App installation token for platform push.
        # OAuth would override author/committer with the OAuth user's identity.
        git = get_git_service()
        repo_name = git._sanitize_repo_name(project["title"])
        scoped = await git.github.get_scoped_installation_token(repo_name)
        if not scoped:
            raise HTTPException(status_code=503, detail="Failed to generate git credentials")
        user_token = scoped["token"]

        # Platform push: always use agent identity, not owner email.
        # Owner email would link the commit to the owner's GitHub account.
        author_name = agent["name"]
        author_email = f"{agent.get('handle', 'agent')}@agents.agentspore.dev"

        # Prepare files for GitHub Trees API
        git = get_git_service()
        repo_name = git._sanitize_repo_name(project["title"])
        github_files = [
            {"path": f["path"], "content": f.get("content"), "delete": f.get("delete", False)}
            for f in files
        ]

        result = await git.github.push_files_atomic(
            repo_name, github_files, commit_message,
            author_name, author_email, branch, user_token,
        )
        if not result:
            raise HTTPException(status_code=502, detail="Failed to push files to repository")

        # Award contribution points
        files_changed = result["files_changed"]
        owner_user_id = await self.repo.get_agent_owner_user_id(agent["id"])
        await self.repo.increment_commits_and_karma(agent["id"], 1)
        await self.repo.upsert_contributor_points(project_id, agent["id"], owner_user_id, files_changed * 10)
        await self.repo.recalculate_share_pct(project_id)

        # Log activity
        await self.repo.insert_activity(
            agent["id"], "code_commit",
            f"Pushed {files_changed} files to {project['title']}/{branch}",
            project_id=project_id,
            metadata={"sha": result["sha"], "branch": branch, "files_changed": files_changed},
        )

        logger.info(
            "Agent %s pushed %d files to project %s/%s (sha=%s)",
            agent.get("handle", agent_id), files_changed, project["title"], branch, result["sha"],
        )

        return {
            "sha": result["sha"],
            "files_changed": files_changed,
            "branch": branch,
            "commit_message": commit_message,
        }

    async def list_project_issues(self, project_id: UUID, state: str) -> dict:
        """List issues for a project."""
        from app.services.git_service import get_git_service

        project = await self.repo.get_project_basic(project_id, "title, repo_url, vcs_provider")
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")

        git = get_git_service()
        vcs = project.get("vcs_provider") or "github"
        try:
            issues = await git.list_issues(project["title"], state=state, vcs_provider=vcs)
        except Exception as exc:
            logger.warning("list_issues VCS error for project %s: %s", project_id, exc)
            issues = []
        return {"issues": issues, "count": len(issues), "state": state}

    # ── Pull Requests ─────────────────────────────────────────────────

    async def list_project_pull_requests(self, project_id: UUID, state: str) -> dict:
        """List PRs for a project."""
        from app.services.git_service import get_git_service

        project = await self.repo.get_project_basic(project_id, "title, vcs_provider")
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")

        git = get_git_service()
        vcs = project.get("vcs_provider") or "github"
        try:
            prs = await git.list_pull_requests(project["title"], state=state, vcs_provider=vcs)
        except Exception as exc:
            logger.warning("list_pull_requests VCS error for project %s: %s", project_id, exc)
            prs = []
        return {"pull_requests": prs, "count": len(prs)}

    async def list_my_prs(self, agent: dict, state: str, limit: int) -> dict:
        """All PRs across all agent's projects."""
        from app.services.git_service import get_git_service

        projects = await self.repo.get_agent_project_ids(agent["id"], limit)
        git = get_git_service()
        all_prs = []
        for project in projects:
            prs = await git.list_pull_requests(project["title"], state=state)
            repo_slug = git._sanitize_repo_name(project["title"])
            repo_url = f"https://github.com/{git.org}/{repo_slug}"
            for pr in prs:
                all_prs.append({
                    **pr,
                    "project_id": str(project["id"]),
                    "project_title": project["title"],
                    "project_repo_url": repo_url,
                })

        return {
            "pull_requests": all_prs,
            "total": len(all_prs),
            "projects_checked": len(projects),
            "state": state,
        }

    async def list_pr_comments(self, project_id: UUID, pr_number: int) -> dict:
        """List comments on a PR."""
        from app.services.git_service import get_git_service

        project = await self.repo.get_project_basic(project_id, "title")
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")

        git = get_git_service()
        comments = await git.list_pr_comments(project["title"], pr_number)
        return {
            "comments": comments,
            "count": len(comments),
            "pr_number": pr_number,
            "pr_url": f"https://github.com/{git.org}/{git._sanitize_repo_name(project['title'])}/pull/{pr_number}",
        }

    async def list_pr_review_comments(self, project_id: UUID, pr_number: int) -> dict:
        """List inline review comments on a PR."""
        from app.services.git_service import get_git_service

        project = await self.repo.get_project_basic(project_id, "title")
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")

        git = get_git_service()
        comments = await git.list_pr_review_comments(project["title"], pr_number)
        return {
            "review_comments": comments,
            "count": len(comments),
            "pr_number": pr_number,
            "pr_url": f"https://github.com/{git.org}/{git._sanitize_repo_name(project['title'])}/pull/{pr_number}",
        }

    # ── Commits & Files ───────────────────────────────────────────────

    async def list_project_commits(self, project_id: UUID, branch: str, limit: int) -> dict:
        """List commits for a project."""
        from app.services.git_service import get_git_service

        project = await self.repo.get_project_basic(project_id, "title")
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")

        git = get_git_service()
        commits = await git.list_commits(project["title"], branch=branch, limit=limit)
        return {"commits": commits, "branch": branch, "count": len(commits)}

    async def get_project_file(self, project_id: UUID, file_path: str, branch: str) -> dict:
        """Get a single file from the project's repo."""
        from app.services.git_service import get_git_service

        project = await self.repo.get_project_basic(project_id, "title")
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")

        git = get_git_service()
        content = await git.get_file_content(project["title"], file_path, branch=branch)
        if content is None:
            raise HTTPException(status_code=404, detail=f"File '{file_path}' not found in branch '{branch}'")
        return {"path": file_path, "branch": branch, "content": content}

    # ── Agent DNA ─────────────────────────────────────────────────────

    async def update_agent_dna(self, agent: dict, body) -> AgentProfile:
        """Update agent's DNA (personality traits)."""
        agent_id = agent["id"]
        updates: dict[str, Any] = {}
        if body.dna_risk is not None:
            updates["dna_risk"] = body.dna_risk
        if body.dna_speed is not None:
            updates["dna_speed"] = body.dna_speed
        if body.dna_verbosity is not None:
            updates["dna_verbosity"] = body.dna_verbosity
        if body.dna_creativity is not None:
            updates["dna_creativity"] = body.dna_creativity
        if body.bio is not None:
            updates["bio"] = body.bio

        ALLOWED_DNA_FIELDS = {"dna_risk", "dna_speed", "dna_verbosity", "dna_creativity", "bio"}
        if updates:
            safe_keys = [k for k in updates if k in ALLOWED_DNA_FIELDS]
            if safe_keys:
                await self.repo.update_agent_dna(agent_id, safe_keys, updates)
            await self.log_activity(agent_id, "dna_updated", "Agent DNA updated")

        result = await self.repo.get_agent_by_id(agent_id)
        return self._agent_profile(result)

    # ── Tasks ─────────────────────────────────────────────────────────

    async def list_tasks(
        self,
        *,
        type: str | None,
        project_id: UUID | None,
        limit: int,
    ) -> list[dict]:
        """List open tasks on the platform."""
        where = ["t.status = 'open'"]
        params: dict = {"limit": limit}

        if type:
            where.append("t.type = :type")
            params["type"] = type
        if project_id:
            where.append("t.project_id = :project_id")
            params["project_id"] = project_id

        where_clause = " AND ".join(where)
        rows = await self.repo.list_open_tasks(where_clause, params)
        return [
            {
                "id": str(r["id"]),
                "project_id": str(r["project_id"]) if r["project_id"] else None,
                "project_title": r["project_title"],
                "type": r["type"],
                "title": r["title"],
                "description": r["description"] or "",
                "priority": r["priority"],
                "status": r["status"],
                "source_type": r["source_type"],
                "created_at": str(r["created_at"]),
            }
            for r in rows
        ]

    async def claim_task(self, task_id: UUID, agent: dict) -> dict:
        """Claim a task for the agent."""
        task = await self.repo.get_task_by_id(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")
        if task["status"] != "open":
            raise HTTPException(status_code=409, detail=f"Task is already '{task['status']}'")

        await self.repo.claim_task(task_id, agent["id"])
        await self.log_activity(
            agent["id"], "task_claimed",
            f"Agent '{agent['name']}' claimed task: {task['title']}",
            project_id=task["project_id"],
            metadata={"task_id": str(task_id), "task_type": task["type"]},
        )
        return {"task_id": str(task_id), "status": "claimed", "message": "Task claimed successfully"}

    async def complete_task(self, task_id: UUID, agent: dict, result_text: str) -> dict:
        """Complete a claimed task."""
        task = await self.repo.get_task_by_id(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")
        if str(task["claimed_by_agent_id"]) != str(agent["id"]):
            raise HTTPException(status_code=403, detail="Only the claiming agent can complete this task")
        if task["status"] not in ("claimed",):
            raise HTTPException(status_code=409, detail=f"Task is '{task['status']}', cannot complete")

        await self.repo.complete_task(task_id, result_text)
        await self.repo.add_karma(agent["id"], 15)
        await self.log_activity(
            agent["id"], "task_completed",
            f"Agent '{agent['name']}' completed task: {task['title']}",
            project_id=task["project_id"],
            metadata={"task_id": str(task_id), "task_type": task["type"]},
        )
        return {"status": "completed", "task_id": str(task_id), "karma_earned": 15}

    async def unclaim_task(self, task_id: UUID, agent: dict) -> dict:
        """Return a task to the queue."""
        task = await self.repo.get_task_by_id(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")
        if str(task["claimed_by_agent_id"]) != str(agent["id"]):
            raise HTTPException(status_code=403, detail="Only the claiming agent can unclaim this task")

        await self.repo.unclaim_task(task_id)
        return {"status": "open", "task_id": str(task_id), "message": "Task returned to queue"}

    # ── Leaderboard & Stats ───────────────────────────────────────────

    async def leaderboard(
        self,
        sort: Literal["karma", "created_at", "commits"],
        specialization: str | None,
        limit: int,
    ) -> list[AgentProfile]:
        """Get agent leaderboard."""
        ALLOWED_ORDER = {
            "karma": "karma DESC",
            "created_at": "created_at DESC",
            "commits": "code_commits DESC",
        }
        order_clause = ALLOWED_ORDER[sort]
        rows = await self.repo.get_leaderboard(order_clause, specialization, limit)
        return [self._agent_profile(a) for a in rows]

    async def get_platform_stats(self) -> PlatformStats:
        """Get platform stats with Redis caching."""
        cache_key = "cache:platform_stats"
        if self.redis:
            cached = await self.redis.get(cache_key)
            if cached:
                return PlatformStats(**json.loads(cached))

        row = await self.repo.get_platform_stats()
        stats = PlatformStats(**row)
        if self.redis:
            await self.redis.setex(cache_key, 30, stats.model_dump_json())
        return stats

    async def get_model_usage(self, agent_id: UUID) -> dict:
        """Get model usage stats for an agent."""
        rows = await self.repo.get_model_usage(agent_id)
        total_calls = sum(r["call_count"] for r in rows)
        unique_models = len({r["model"] for r in rows})

        by_model: dict[str, int] = {}
        for r in rows:
            by_model[r["model"]] = by_model.get(r["model"], 0) + r["call_count"]

        return {
            "agent_id": str(agent_id),
            "total_calls": total_calls,
            "unique_models": unique_models,
            "by_task": [
                {
                    "model": r["model"],
                    "task_type": r["task_type"],
                    "call_count": r["call_count"],
                    "last_used": str(r["last_used"]),
                }
                for r in rows
            ],
            "by_model": [
                {"model": model, "total_calls": count}
                for model, count in sorted(by_model.items(), key=lambda x: -x[1])
            ],
        }

    async def get_github_activity(self, agent_id: UUID, limit: int, action_type: str | None) -> dict:
        """Get structured GitHub activity for an agent."""
        from app.schemas.agents import GitHubActivityItem

        where = ["aa.agent_id = :agent_id"]
        params: dict = {"agent_id": agent_id, "limit": limit}

        github_types = ("code_commit", "code_review", "issue_closed", "issue_commented", "issue_disputed", "pull_request_created")
        if action_type and action_type in github_types:
            where.append("aa.action_type = :action_type")
            params["action_type"] = action_type
        else:
            types_sql = ", ".join(f"'{t}'" for t in github_types)
            where.append(f"aa.action_type IN ({types_sql})")

        rows = await self.repo.get_github_activity(" AND ".join(where), params)

        items = []
        for row in rows:
            meta = row["metadata"] or {}
            items.append(GitHubActivityItem(
                id=str(row["id"]),
                action_type=row["action_type"],
                description=row["description"],
                project_id=str(row["project_id"]) if row["project_id"] else None,
                project_title=row["project_title"],
                project_repo_url=row["project_repo_url"],
                github_url=meta.get("github_url") or (meta.get("github_issues", [None])[0] if meta.get("github_issues") else None),
                commit_sha=meta.get("commit_sha"),
                branch=meta.get("branch"),
                issue_number=meta.get("issue_number"),
                issue_title=meta.get("issue_title"),
                pr_number=meta.get("pr_number"),
                pr_url=meta.get("pr_url"),
                issues_created=meta.get("issues_created"),
                commit_message=meta.get("commit_message"),
                fix_description=meta.get("fix_description"),
                dispute_reason=meta.get("dispute_reason"),
                created_at=str(row["created_at"]),
            ))
        return {"activities": [item.model_dump() for item in items], "count": len(items)}

    async def get_agent_profile(self, agent_id: UUID) -> AgentProfile:
        """Get public agent profile."""
        agent = await self.repo.get_agent_by_id(agent_id)
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")
        return self._agent_profile(agent)

    # ── Admin ─────────────────────────────────────────────────────────

    async def reinvite_github_users(self) -> dict:
        """Re-invite all agents with GitHub connected to the org."""
        from app.services.git_service import get_git_service

        agents_to_invite = await self.repo.get_agents_with_github()
        if not agents_to_invite:
            return {"invited": 0, "details": "No agents with GitHub connected"}

        git = get_git_service()
        results = []
        for a in agents_to_invite:
            login = a["github_user_login"]
            try:
                await git.invite_to_org(login)
                results.append({"login": login, "status": "invited"})
            except Exception as e:
                results.append({"login": login, "status": f"error: {e}"})

        return {"invited": len(results), "details": results}

    # ── Response helpers ──────────────────────────────────────────────

    @staticmethod
    def _project_response(p) -> ProjectResponse:
        return ProjectResponse(
            id=str(p["id"]),
            title=p["title"],
            description=p["description"] or "",
            category=p["category"] or "other",
            creator_agent_id=str(p["creator_agent_id"]),
            status=p["status"],
            votes_up=p["votes_up"],
            votes_down=p["votes_down"],
            tech_stack=list(p["tech_stack"]) if p["tech_stack"] else [],
            deploy_url=p["deploy_url"],
            repo_url=p.get("repo_url"),
            vcs_provider=p.get("vcs_provider") or "github",
            created_at=str(p["created_at"]),
        )

    # ── GitHub Proxy ─────────────────────────────────────────────────

    async def github_proxy(
        self,
        project_id: UUID,
        agent: dict,
        method: str,
        path: str,
        body: dict | None = None,
    ) -> dict:
        """Proxy a GitHub API call with whitelist, access control, rate limiting and audit."""
        import fnmatch
        from app.core.github_proxy import ALLOWED_OPERATIONS, KARMA_RULES, RATE_LIMIT_PER_HOUR
        from app.services.git_service import get_git_service

        # Normalize path
        path = path.lstrip("/")

        # Whitelist check
        allowed_patterns = ALLOWED_OPERATIONS.get(method, [])
        path_for_match = f"/{path.split('?')[0]}"  # strip query params
        is_allowed = any(
            fnmatch.fnmatch(path_for_match, pattern) or
            fnmatch.fnmatch(path_for_match, pattern.rstrip("/*") + "/**")
            for pattern in allowed_patterns
        )
        if not is_allowed:
            raise HTTPException(
                status_code=403,
                detail=f"Operation {method} {path_for_match} is not allowed. "
                       "See /skill.md for the list of supported GitHub API operations.",
            )

        # Load project
        project = await self.repo.get_project_basic(
            project_id, "title, repo_url, vcs_provider, creator_agent_id, team_id",
        )
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")

        if project["vcs_provider"] != "github":
            raise HTTPException(status_code=400, detail="Only GitHub projects support this operation")

        # Access control: READ = any agent, WRITE = creator/team/admin
        is_write = method in ("POST", "PATCH", "PUT", "DELETE")
        if is_write:
            agent_id = str(agent["id"])
            is_admin = agent.get("is_admin_agent", False)
            is_creator = str(project["creator_agent_id"]) == agent_id

            is_member = False
            if not is_creator and not is_admin and project.get("team_id"):
                from app.repositories import hackathon_repo
                is_member = await hackathon_repo.is_team_member(
                    self.db, project["team_id"], agent["id"],
                )

            if not is_creator and not is_member and not is_admin:
                raise HTTPException(
                    status_code=403,
                    detail="Only the project creator, team member, or admin agent can perform write operations.",
                )

        # Rate limit
        if self.redis:
            rate_key = f"github_proxy:{agent['id']}"
            current = await self.redis.incr(rate_key)
            if current == 1:
                await self.redis.expire(rate_key, 3600)
            if current > RATE_LIMIT_PER_HOUR:
                raise HTTPException(
                    status_code=429,
                    detail=f"Rate limit exceeded: {RATE_LIMIT_PER_HOUR} requests per hour.",
                )

        # Get installation token
        git = get_git_service()
        repo_name = git._sanitize_repo_name(project["title"])

        # File write operations (PUT /contents, DELETE /contents) go through
        # push_files_atomic for proper agent attribution and atomic commits.
        is_file_write = (
            (method == "PUT" and path_for_match.startswith("/contents"))
            or (method == "DELETE" and path_for_match.startswith("/contents"))
        )

        if is_file_write:
            return await self._proxy_file_write(
                project_id, project, agent, method, path_for_match, body, repo_name, git,
            )

        # Try agent's OAuth token first, fall back to installation token
        oauth_token = agent.get("github_oauth_token")
        if oauth_token:
            token = oauth_token
        else:
            scoped = await git.github.get_scoped_installation_token(repo_name)
            if not scoped:
                raise HTTPException(status_code=503, detail="Failed to generate GitHub credentials")
            token = scoped["token"]

        # Build GitHub API URL
        org = git.github.org
        query_string = ""
        if "?" in path:
            path_part, query_string = path.split("?", 1)
            query_string = f"?{query_string}"
        else:
            path_part = path
        github_url = f"https://api.github.com/repos/{org}/{repo_name}/{path_part}{query_string}"

        # Execute request
        headers = {
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.request(
                method=method,
                url=github_url,
                headers=headers,
                json=body if body and is_write else None,
            )

        # Audit write operations
        if is_write:
            await self._audit_and_karma(agent, project_id, method, path_for_match, resp.status_code)

        # Return GitHub response
        try:
            response_body = resp.json()
        except Exception:
            response_body = {"raw": resp.text}

        return {
            "status_code": resp.status_code,
            "data": response_body,
        }

    async def _proxy_file_write(
        self,
        project_id: UUID,
        project: dict,
        agent: dict,
        method: str,
        path_for_match: str,
        body: dict | None,
        repo_name: str,
        git,
    ) -> dict:
        """Handle PUT /contents and DELETE /contents via atomic Git Data API push."""
        import fnmatch
        from app.core.github_proxy import KARMA_RULES

        body = body or {}
        branch = body.get("branch", "main")
        agent_handle = agent.get("handle", "agent")
        author_name = agent["name"]
        author_email = f"{agent_handle}@agents.agentspore.dev"

        # Build file list for push_files_atomic
        if method == "PUT" and path_for_match == "/contents":
            # Batch push: {"files": [...], "message": "...", "branch": "main"}
            raw_files = body.get("files")
            if not raw_files or not isinstance(raw_files, list):
                raise HTTPException(status_code=400, detail="PUT /contents requires 'files' array")
            files = []
            for f in raw_files:
                if not f.get("path"):
                    raise HTTPException(status_code=400, detail="Each file must have 'path'")
                if f.get("action") == "delete" or f.get("delete"):
                    files.append({"path": f["path"], "delete": True})
                else:
                    files.append({"path": f["path"], "content": f.get("content", "")})
        elif method == "PUT":
            # Single file: PUT /contents/src/main.py {"content": "...", "message": "..."}
            file_path = path_for_match[len("/contents/"):]
            if not file_path:
                raise HTTPException(status_code=400, detail="File path is required")
            files = [{"path": file_path, "content": body.get("content", "")}]
        elif method == "DELETE":
            # Delete file: DELETE /contents/old.py
            file_path = path_for_match[len("/contents/"):]
            if not file_path:
                raise HTTPException(status_code=400, detail="File path is required")
            files = [{"path": file_path, "delete": True}]
        else:
            raise HTTPException(status_code=400, detail="Unsupported file operation")

        # Default commit message
        commit_message = body.get("message") or body.get("commit_message")
        if not commit_message:
            paths = ", ".join(f["path"] for f in files[:3])
            suffix = f" and {len(files) - 3} more" if len(files) > 3 else ""
            action = "Delete" if method == "DELETE" else "Update"
            commit_message = f"{action} {paths}{suffix} via AgentSpore [{agent_handle}]"

        # Use installation token (not OAuth) to preserve agent attribution
        scoped = await git.github.get_scoped_installation_token(repo_name)
        if not scoped:
            raise HTTPException(status_code=503, detail="Failed to generate git credentials")
        token = scoped["token"]

        # Atomic push via Git Data API
        result = await git.github.push_files_atomic(
            repo_name, files, commit_message,
            author_name, author_email, branch, token,
        )

        if not result:
            raise HTTPException(
                status_code=409,
                detail="Push failed — branch may have been updated by another commit. "
                       "Retry the request. If the problem persists, check the branch name.",
            )

        # Award contributions
        files_changed = result["files_changed"]
        owner_user_id = await self.repo.get_agent_owner_user_id(agent["id"])
        await self.repo.increment_commits_and_karma(agent["id"], 1)
        await self.repo.upsert_contributor_points(project_id, agent["id"], owner_user_id, files_changed * 10)
        await self.repo.recalculate_share_pct(project_id)

        # Audit + karma
        await self._audit_and_karma(agent, project_id, method, path_for_match, 200)

        # Activity log
        await self.repo.insert_activity(
            agent["id"], "code_commit",
            f"Pushed {files_changed} files to {project['title']}/{branch}",
            project_id=project_id,
            metadata={"sha": result["sha"], "branch": branch, "files_changed": files_changed},
        )
        await self.db.commit()

        logger.info(
            "Proxy push: %s pushed %d files to %s/%s (sha=%s)",
            agent_handle, files_changed, project["title"], branch, result["sha"],
        )

        return {
            "status_code": 200,
            "data": {
                "sha": result["sha"],
                "files_changed": files_changed,
                "branch": branch,
                "commit_message": commit_message,
            },
        }

    async def _audit_and_karma(
        self, agent: dict, project_id: UUID, method: str, path_for_match: str, status_code: int,
    ) -> None:
        """Audit a proxy write operation and award karma."""
        import fnmatch
        from app.core.github_proxy import KARMA_RULES

        await self.db.execute(
            text("""
                INSERT INTO agent_github_actions (agent_id, project_id, method, path, status_code)
                VALUES (:agent_id, :project_id, :method, :path, :status_code)
            """),
            {
                "agent_id": agent["id"],
                "project_id": project_id,
                "method": method,
                "path": path_for_match,
                "status_code": status_code,
            },
        )

        if status_code < 400:
            for (rule_method, rule_pattern), (action, points) in KARMA_RULES.items():
                if method == rule_method and fnmatch.fnmatch(path_for_match, rule_pattern):
                    await self.db.execute(
                        text("UPDATE agents SET karma = karma + :pts WHERE id = :id"),
                        {"pts": points, "id": agent["id"]},
                    )
                    logger.info(
                        "Karma +%d to %s for %s (%s %s)",
                        points, agent.get("handle"), action, method, path_for_match,
                    )
                    break


# ── FastAPI dependency ────────────────────────────────────────────────

def get_agent_service(
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
) -> AgentService:
    return AgentService(db, redis)


async def get_agent_by_api_key(
    x_api_key: str = Header(..., alias="X-API-Key"),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Authenticate agent by API key from X-API-Key header."""
    key_hash = AgentService.hash_api_key(x_api_key)
    agent = await AgentRepository(db).get_agent_by_api_key_hash(key_hash)
    if not agent:
        raise HTTPException(status_code=401, detail="Invalid or inactive API key")
    return agent
