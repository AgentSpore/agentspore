"""HostedAgentService — business logic for hosted agents on platform infrastructure."""

import asyncio
import contextvars
import json
import time
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncGenerator
import httpx
import logfire
from croniter import croniter
from fastapi import Depends, HTTPException
from loguru import logger
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.core.config import get_settings
from app.core.database import async_session_maker
from app.observability import use_agent_context
from app.repositories.hosted_agent_repo import (
    HostedAgentRepository,
    get_hosted_agent_repo,
)
from app.schemas.hosted_agents import DEFAULT_RUNTIME
from app.services.agent_service import AgentService, get_agent_service
from app.services.connection_manager import deliver_user_event
from app.services.openrouter_service import OpenRouterService, get_openrouter_service
from app.services.openviking_service import OpenVikingService, get_openviking_service
from app.services.runner_client import RunnerFileClient


class HostedAgentRunnerUnavailable(Exception):
    """Raised when the agent runner is unreachable or returns 503."""


class HostedAgentTooManyFailures(Exception):
    """Raised when an agent has failed to auto-start 3+ times in 5 minutes."""


# File-path validation: blocks traversal, absolute paths, NULs.
# Mirrors the runner-side check so the BE rejects bad input early
# without a wasted HTTP round-trip to the runner.
def _validate_file_path(file_path: str) -> str:
    if not file_path or "\x00" in file_path:
        raise HTTPException(400, "Invalid file path")
    if file_path.startswith("/"):
        raise HTTPException(400, "Absolute paths are not allowed")
    parts = file_path.split("/")
    if any(p == ".." for p in parts):
        raise HTTPException(400, "Path traversal is not allowed")
    if len(file_path) > 500:
        raise HTTPException(400, "Path too long")
    return file_path


def _file_type_for(path: str) -> str:
    """Default file_type categorisation for workspace files served from the runner."""
    if ".deep/skills/" in path or path.startswith("skills/"):
        return "skill"
    if ".deep/memory" in path or "memory" in path.lower():
        return "memory"
    if path == "AGENT.md":
        return "config"
    return "text"

# ── Platform skill.md loader ──

_skill_md_cache: str = ""
_skill_md_ts: float = 0
_SKILL_MD_TTL = 300  # 5 min


def _load_skill_md() -> str:
    """Load platform skill.md from disk (volume-mounted in container)."""
    global _skill_md_cache, _skill_md_ts
    if _skill_md_cache and (time.time() - _skill_md_ts) < _SKILL_MD_TTL:
        return _skill_md_cache
    for path in [Path("/app/skill.md"), Path("skill.md"), Path("../skill.md")]:
        if path.exists():
            _skill_md_cache = path.read_text(encoding="utf-8")
            _skill_md_ts = time.time()
            return _skill_md_cache
    return ""


class HostedAgentService:
    """Manages hosted agent lifecycle: create, start, stop, chat, files.

    Hosted agents run on platform infrastructure (infra server) inside
    Docker containers via pydantic-deepagents runtime. This service
    handles the platform side — registration, state tracking, files,
    and communication with the Agent Runner Service.
    """

    # Per-instance in-memory lock map: hosted_id → asyncio.Event (set when start completes).
    # Bounded to 1024 entries (LRU eviction) so long-lived workers don't leak unboundedly.
    _starting_locks: OrderedDict[str, asyncio.Event]

    def __init__(
        self,
        repo: HostedAgentRepository,
        agent_service: AgentService,
        openrouter: OpenRouterService,
        openviking: OpenVikingService | None = None,
    ):
        self.settings = get_settings()
        self.repo = repo
        self.agent_svc = agent_service
        self.openrouter = openrouter
        self.openviking = openviking or OpenVikingService()
        self.runner_url = self.settings.agent_runner_url
        self._starting_locks = OrderedDict()
        self._rc = RunnerFileClient(
            runner_url=self.settings.agent_runner_url,
            runner_key=self.settings.agent_runner_key,
        )

    # ── CRUD ──

    async def _rollback_creation(
        self,
        agent_id: str,
        hosted_id: str,
        reason: str,
        cause: BaseException | None = None,
    ) -> None:
        """Deactivate the platform agent and delete the hosted row, then raise 503.

        Called when agent creation fails after the DB rows have been committed —
        runner is unavailable or import failed.  Logs but does not re-raise
        the cleanup failure so the primary error always surfaces.

        Args:
            agent_id: Platform agent UUID (agents.id).
            hosted_id: Hosted-agents record UUID (hosted_agents.id).
            reason: Human-readable cause for the 503 detail string.
            cause: Original exception to chain onto the 503 for traceback context.

        Raises:
            HTTPException(503): always.
        """
        await self.agent_svc.db.execute(
            text("UPDATE agents SET is_active = FALSE WHERE id = :id"),
            {"id": agent_id},
        )
        await self.agent_svc.db.commit()
        try:
            await self.repo.delete(hosted_id)
        except Exception as del_exc:
            logger.error(
                "Rollback delete failed for orphaned hosted agent {}"
                " after creation failure: {}",
                hosted_id,
                del_exc,
            )
        raise HTTPException(503, reason) from cause

    async def create_hosted_agent(
        self,
        *,
        user_id: str,
        user_email: str,
        name: str,
        description: str = "",
        specialization: str = "programmer",
        system_prompt: str,
        model: str = "qwen/qwen3-coder:free",
        skills: list[str] | None = None,
    ) -> dict:
        """Create a hosted agent.

        Registers the agent on the platform via AgentService, marks it as
        hosted, creates a hosted_agents record, and initializes default
        files (AGENT.md, MEMORY.md, platform SKILL.md).
        Returns the hosted agent dict with api_key for initial setup.
        """
        max_hosted = self.settings.max_hosted_agents_per_user
        existing = await self.repo.count_by_owner(user_id)
        if existing >= max_hosted:
            raise HTTPException(
                409, f"You can create up to {max_hosted} hosted agent(s). "
                     "Delete an existing agent to create a new one."
            )

        if not await self.openrouter.is_allowed(model):
            raise HTTPException(400, "Model not available")

        try:
            reg = await self.agent_svc.register_agent(
                name=name,
                model_provider="openrouter",
                model_name=model,
                specialization=specialization,
                skills=skills or [],
                description=description,
                owner_email=user_email,
            )
        except IntegrityError as e:
            await self.agent_svc.db.rollback()
            if "uq_agents_name" in str(e.orig):
                raise HTTPException(
                    409,
                    f"Agent name '{name}' is already taken. Pick a different name.",
                )
            raise

        await self.agent_svc.db.execute(
            text("UPDATE agents SET is_hosted = TRUE WHERE id = :id"),
            {"id": reg["agent_id"]},
        )
        await self.agent_svc.db.commit()

        hosted = await self.repo.create({
            "agent_id": reg["agent_id"],
            "owner_user_id": user_id,
            "system_prompt": system_prompt,
            "model": model,
            "runtime": DEFAULT_RUNTIME,
            "agent_api_key": reg["api_key"],
        })

        hosted_id = str(hosted["id"])

        # Seed initial workspace on the runner via import.
        # Includes: AGENT.md, agent.yaml, platform SKILL.md, and custom.md if skills given.
        # Runner creates the persistent workspace dir on first import — no DB agent_files writes.
        # Fail-fast: if runner is down at creation time, abort and clean up the dangling row.
        if not self.runner_url:
            # repo.create() committed above intentionally (before runner call) so that
            # rollback-delete operates on a real committed row, and a retry after failure
            # finds a clean state without orphan rows.
            await self._rollback_creation(
                reg["agent_id"],
                hosted_id,
                "Agent runner unavailable — required to create workspace",
            )

        import_items: list[dict] = [
            {"file_path": "AGENT.md", "content": system_prompt, "file_type": "config"},
            {
                "file_path": "agent.yaml",
                "content": self._default_agent_yaml(),
                "file_type": "config",
            },
        ]
        platform_skill = _load_skill_md()
        if platform_skill:
            import_items.append(
                {"file_path": ".deep/skills/SKILL.md", "content": platform_skill, "file_type": "skill"}  # noqa: E501 — pre-existing line-length limit in file
            )
        if skills:
            skill_content = "\n\n".join(f"## {s}\n{s} skill." for s in skills)
            import_items.append(
                {"file_path": ".deep/skills/custom.md", "content": skill_content, "file_type": "skill"}  # noqa: E501
            )

        try:
            await self._rc.post_import(hosted_id, import_items, timeout=20)
            logger.info(
                "create_hosted_agent: seeded {} files into runner workspace for {}",
                len(import_items),
                hosted_id,
            )
        except Exception as exc:
            logger.error(
                "create_hosted_agent: runner import failed for {}: {} — rolling back",
                hosted_id,
                exc,
            )
            # repo.create() committed above intentionally (before runner call).
            await self._rollback_creation(
                reg["agent_id"],
                hosted_id,
                "Agent runner unavailable — required to create workspace",
                cause=exc,
            )

        return {
            **hosted,
            "agent_name": name,
            "agent_handle": reg["handle"],
            "api_key": reg["api_key"],
        }

    async def fork_hosted_agent(
        self,
        *,
        source_hosted_id: str,
        user_id: str,
        user_email: str,
        new_name: str | None = None,
        new_system_prompt: str | None = None,
    ) -> dict:
        """Fork a public hosted agent — copies config, files, memory.

        Creates a new agent registration and hosted_agents record. Copies all
        workspace files from the source. Increments fork_count on the source agent.
        """
        source = await self.repo.get_public_by_id(source_hosted_id)
        if not source:
            raise HTTPException(404, "Agent not found or not public")

        if str(source["owner_user_id"]) == user_id:
            raise HTTPException(400, "Cannot fork your own agent")

        max_hosted = self.settings.max_hosted_agents_per_user
        existing = await self.repo.count_by_owner(user_id)
        if existing >= max_hosted:
            raise HTTPException(
                409, f"You can create up to {max_hosted} hosted agent(s). "
                     "Delete an existing agent to create a new one."
            )

        source_name = source["agent_name"]
        name = new_name or f"{source_name} (fork)"
        system_prompt = new_system_prompt or source["system_prompt"]
        model = source["model"]

        if not await self.openrouter.is_allowed(model):
            raise HTTPException(400, "Source model no longer available")

        reg = await self.agent_svc.register_agent(
            name=name,
            model_provider="openrouter",
            model_name=model,
            specialization=source.get("specialization", "programmer"),
            skills=source.get("skills") or [],
            description=source.get("description", ""),
            owner_email=user_email,
        )

        await self.agent_svc.db.execute(
            text("UPDATE agents SET is_hosted = TRUE WHERE id = :id"),
            {"id": reg["agent_id"]},
        )
        await self.agent_svc.db.commit()

        hosted = await self.repo.create({
            "agent_id": reg["agent_id"],
            "owner_user_id": user_id,
            "system_prompt": system_prompt,
            "model": model,
            "runtime": DEFAULT_RUNTIME,
            "agent_api_key": reg["api_key"],
        })

        hosted_id = str(hosted["id"])

        # Set fork lineage
        await self.repo.db.execute(
            text("""
                UPDATE hosted_agents
                SET forked_from_hosted_id = :source_id, forked_from_agent_name = :source_name
                WHERE id = :id
            """),
            {"id": hosted_id, "source_id": source_hosted_id, "source_name": source_name},
        )
        await self.repo.db.commit()

        # Copy all files from source workspace dir → new agent workspace dir.
        # Runner dir is the sole source of truth (P5 complete, no DB fallback).
        source_files = await self._fork_read_source_files(
            source_hosted_id=source_hosted_id,
            source_name=source_name,
        )
        await self._fork_seed_new_agent(
            hosted_id=hosted_id,
            source_files=source_files,
            source_name=source_name,
            source_handle=source["agent_handle"],
            system_prompt=system_prompt,
        )

        # Increment fork count on source
        await self.repo.increment_fork_count(str(source["agent_id"]))

        return {
            **hosted,
            "agent_name": name,
            "agent_handle": reg["handle"],
            "forked_from_agent_name": source_name,
        }

    async def _fork_read_source_files(
        self,
        *,
        source_hosted_id: str,
        source_name: str,
    ) -> list[dict]:
        """Return source workspace files for fork — runner-only (P5a removes DB fallback).

        GET runner /agents/{source_id}/files: dir is persistent (no-clobber on start),
        so it exists even when the source agent is stopped. P4c creation seeds the dir
        unconditionally, so every source agent has a populated workspace directory.
        If runner is unavailable or dir is empty, log + return empty list (no DB fallback).

        Returns:
            List of dicts with at least ``file_path``, ``content``, ``file_type`` keys.
        """
        if not self.runner_url:
            logger.warning("fork source {}: runner not configured — forking with empty workspace", source_hosted_id)
            return []
        runner_files = await self._rc.soft_list_files(source_hosted_id, timeout=15)
        if runner_files:
            return [
                {
                    "file_path": f["file_path"],
                    "content": f.get("content") or "",
                    "file_type": _file_type_for(f["file_path"]),
                }
                for f in runner_files
            ]
        # Empty dir — source never populated; fork proceeds with empty workspace.
        logger.warning(
            "fork source {} runner dir empty — forking with empty workspace (P5a: no DB fallback)",
            source_hosted_id,
        )
        return []

    async def _fork_seed_new_agent(
        self,
        *,
        hosted_id: str,
        source_files: list[dict],
        source_name: str,
        source_handle: str,
        system_prompt: str,
    ) -> None:
        """Apply fork transformations and seed new agent workspace via runner import.

        Transforms:
        - ``AGENT.md``: replaced with fork system_prompt + fork lineage header.
        - ``.deep/memory/MEMORY.md``: prepend "Forked from <source_name>." note.

        Seeds via ``POST /agents/{hosted_id}/files/import`` (creates dir if absent).
        """
        import_items: list[dict] = []
        for f in source_files:
            raw_path: str = f.get("file_path", "") or ""
            # Defense-in-depth: validate each path before forwarding to the runner.
            # Malformed paths (traversal, absolute, NUL) are skipped with a warning
            # so a poisoned source workspace cannot escape the sandbox.
            try:
                _validate_file_path(raw_path)
            except HTTPException:
                logger.warning(
                    "fork: skipping invalid file path {} from source {} — traversal/absolute/NUL",
                    raw_path,
                    hosted_id,
                )
                continue
            content: str = f.get("content") or ""
            if raw_path == "AGENT.md":
                content = (
                    f"{system_prompt}\n\n"
                    f"## Fork Info\n\nForked from **{source_name}** (@{source_handle})\n"
                )
            elif raw_path == ".deep/memory/MEMORY.md":
                content = f"# Memory\n\nForked from {source_name}.\n\n{content}"
            import_items.append({"file_path": raw_path, "content": content})

        if not import_items:
            logger.warning(
                "fork: no files to seed for new hosted agent {} (source had 0 files)",
                hosted_id,
            )
            return

        if not self.runner_url:
            logger.warning(
                "fork: runner_url not configured — new agent {} workspace not seeded",
                hosted_id,
            )
            return

        imported = await self._rc.soft_post_import(hosted_id, import_items, timeout=20)
        if imported >= 0:
            logger.info(
                "fork: seeded {} files into runner workspace for {}",
                imported,
                hosted_id,
            )
        else:
            logger.warning(
                "fork: runner import failed for {} — workspace not seeded",
                hosted_id,
            )

    async def list_forkable_agents(self) -> list[dict]:
        """List all public agents available for forking."""
        return await self.repo.list_forkable()

    async def get_hosted_agent(self, hosted_id: str, user_id: str) -> dict:
        """Get a hosted agent by ID, verifying the caller owns it.

        If DB says 'running' but runner doesn't have the agent, auto-correct to 'stopped'.
        """
        hosted = await self.repo.get_by_id(hosted_id)
        if not hosted:
            raise HTTPException(404, "Hosted agent not found")
        if str(hosted["owner_user_id"]) != user_id:
            raise HTTPException(403, "Not your agent")
        # Auto-detect dead agents: DB says running but runner lost them (e.g. runner restart).
        # soft_get_status returns None when runner is unreachable (network error) — in that
        # case we leave DB status unchanged so a transient runner blip doesn't flip all agents.
        if hosted["status"] == "running" and self.runner_url:
            status_data = await self._rc.soft_get_status(hosted_id, timeout=3)
            if status_data is not None and status_data.get("status") != "running":
                await self.repo.update_status(hosted_id, "stopped")
                hosted["status"] = "stopped"
                await self._notify_status(hosted, "stopped")
                logger.warning("Agent {} was dead on runner — auto-corrected to stopped", hosted_id)
        return hosted

    async def list_my_agents(self, user_id: str) -> list[dict]:
        """List all hosted agents owned by the given user."""
        return await self.repo.list_by_owner(user_id)

    async def list_running_agents(self) -> list[dict]:
        """List all agents with status=running for runner restore on restart.

        P5a: ``files`` is always empty. The runner's workspace dir is persistent
        (no-clobber on start) so file payload is redundant on restore. P4c creation
        already seeds the dir; the runner reads files live from disk, not from this
        payload. DB list_files/get_file reads are removed here.
        """
        agents = await self.repo.list_running()
        result = []
        for a in agents:
            hid = str(a["id"])
            result.append({
                "id": hid,
                "agent_id": str(a["agent_id"]),
                "system_prompt": a["system_prompt"],
                "model": a["model"],
                "runtime": a["runtime"],
                "agent_api_key": a.get("agent_api_key", ""),
                "platform_url": self.settings.oauth_redirect_base_url or "https://agentspore.com",
                "heartbeat_seconds": a.get("heartbeat_seconds", 3600),
                "files": [],
            })
        return result

    async def update_agent(self, hosted_id: str, user_id: str, updates: dict) -> dict | None:
        """Update hosted agent settings (system_prompt, model, budget, heartbeat).

        If system_prompt changes, also updates the AGENT.md file.
        Auto-restarts the agent if it's running so changes take effect immediately.
        """
        hosted = await self.get_hosted_agent(hosted_id, user_id)
        clean = {k: v for k, v in updates.items() if v is not None}
        if "model" in clean and not await self.openrouter.is_allowed(clean["model"]):
            raise HTTPException(400, "Model not available")
        if "system_prompt" in clean:
            # P5a: write AGENT.md directly to runner (best-effort); no DB upsert.
            # If runner is down/dir absent the next agent start re-writes from system_prompt.
            if self.runner_url:
                try:
                    await self._rc.put_file(
                        hosted_id, "AGENT.md", clean["system_prompt"], timeout=10
                    )
                except Exception as exc:
                    logger.warning(
                        "update_agent: runner PUT AGENT.md failed for {} — will re-write on next start: {}",
                        hosted_id,
                        exc,
                    )
        result = await self.repo.update(hosted_id, clean)

        # Auto-restart if running so new settings take effect
        if hosted["status"] == "running":
            try:
                await self._save_runner_history(hosted_id)
                await self._call_runner("stop", hosted_id)
                refreshed = await self.repo.get_by_id(hosted_id)
                if refreshed:
                    await self._start_agent_internal(refreshed)
                    logger.info("Auto-restarted agent {} after settings update", hosted_id)
            except Exception as e:
                logger.warning("Auto-restart failed for {}: {}", hosted_id, e)

        return result

    async def delete_agent(self, hosted_id: str, user_id: str) -> None:
        """Delete a hosted agent. Stops container (fire-and-forget), deactivates platform agent, soft-deletes hosted record."""
        hosted = await self.get_hosted_agent(hosted_id, user_id)
        if hosted["status"] == "running":
            # Fire-and-forget runner stop — don't block the HTTP worker on a 60s
            # runner call (Caddy upstream timeout returns 502 before it completes).
            async def _bg_stop(hid: str) -> None:
                try:
                    await self._call_runner("stop", hid)
                except Exception as e:
                    logger.warning("Background stop on delete failed for {}: {}", hid, e)
            asyncio.create_task(_bg_stop(hosted_id))
        # Deactivate platform agent (keep for karma/payout history)
        await self.agent_svc.db.execute(
            text("UPDATE agents SET is_active = FALSE WHERE id = :id"),
            {"id": hosted["agent_id"]},
        )
        await self.agent_svc.db.commit()
        await self.repo.delete(hosted_id)

    # ── Container control ──

    async def start_agent(self, hosted_id: str, user_id: str) -> dict:
        """Start the agent container on the infra server."""
        hosted = await self.get_hosted_agent(hosted_id, user_id)
        if hosted["status"] == "running":
            raise HTTPException(400, "Agent is already running")
        async with use_agent_context(
            agent_id=str(hosted["id"]),
            agent_handle=hosted.get("agent_handle"),
            model=hosted.get("model"),
        ):
            return await self._start_agent_internal(hosted)

    async def stop_agent(self, hosted_id: str, user_id: str) -> dict:
        """Stop the agent container on the infra server.

        Before stopping: persist session history and ask agent to summarize
        the session into memory/ for mid-term persistence.
        """
        hosted = await self.get_hosted_agent(hosted_id, user_id)
        if hosted["status"] != "running":
            raise HTTPException(400, "Agent is not running")

        hid = str(hosted["id"])

        # Save session history before stop (short-term memory)
        await self._save_runner_history(hid)

        # Ask agent to summarize session → memory/ (mid-term memory)
        try:
            summary_msg = (
                "You are about to be stopped. Before shutdown, update your memory file "
                ".deep/memory/MEMORY.md with key learnings, decisions, and context "
                "from this session that you'll need in the next session. Be concise."
            )
            await self._call_runner("chat", hid, {"content": summary_msg})
            # Save updated history including summary (P5a: no DB file sync)
            await self._save_runner_history(hid)
            logger.info("Session summary saved for {}", hid)
        except Exception as e:
            logger.warning("Session summary failed for {}: {}", hid, e)

        await self._call_runner("stop", hid)
        await self.repo.update_status(hid, "stopped")
        await self._notify_status(hosted, "stopped")
        return {"status": "stopped", "message": "Agent stopped"}

    async def restart_agent(self, hosted_id: str, user_id: str) -> dict:
        """Restart the agent: quick stop, then start. No session summary."""
        hosted = await self.get_hosted_agent(hosted_id, user_id)
        hid = str(hosted["id"])
        async with use_agent_context(
            agent_id=hid,
            agent_handle=hosted.get("agent_handle"),
            model=hosted.get("model"),
        ):
            if hosted["status"] == "running":
                try:
                    # P5a: no DB file sync on restart; runner dir is persistent
                    await self._save_runner_history(hid)
                except Exception:
                    pass
                try:
                    await self._call_runner("stop", hid)
                except Exception:
                    pass
            await self.repo.update_status(hid, "stopped")
            refreshed = await self.repo.get_by_id(hid)
            return await self._start_agent_internal(refreshed or hosted)

    async def _notify_status(self, hosted: dict, status: str) -> None:
        """Push hosted-agent status change to the owner's browser tabs."""
        owner = hosted.get("owner_user_id")
        if not owner:
            return
        await deliver_user_event(str(owner), {
            "type": "hosted_agent_status",
            "hosted_id": str(hosted.get("id")),
            "agent_id": str(hosted.get("agent_id")) if hosted.get("agent_id") else None,
            "status": status,
        })

    async def ensure_running(self, hosted_id: str, *, source: str) -> bool:
        """Guarantee the hosted agent is running. Returns True if already running.

        Performs a two-level concurrency guard:
        1. In-process asyncio.Event: collapses parallel callers in the same
           uvicorn worker so _start_agent_internal fires exactly once.
        2. PG advisory lock via pg_try_advisory_xact_lock: prevents two
           separate worker processes from racing the same start.

        If the advisory lock is held by another worker the method polls DB
        status every 1 s for up to 60 s.

        source: "chat" | "ws_event" | "cron" | "force_restart" — for logs.

        Raises:
            HostedAgentRunnerUnavailable: runner is down or returned 503/timeout.
            HostedAgentTooManyFailures: agent failed to auto-start 3+ times in 5 min.
        """
        _MAX_START_WAIT_S = 60
        _MAX_AUTOSTART_FAILURES = 3
        _AUTOSTART_FAILURE_TTL_S = 300  # 5 minutes

        # ── Fast path: already running ───────────────────────────────────────
        hosted = await self.repo.get_by_id(hosted_id)
        if not hosted:
            raise HTTPException(404, "Hosted agent not found")
        if hosted["status"] == "running":
            return True

        # Treat "error" the same as "stopped" for auto-start purposes.
        # The failure counter below will gate escalation after 3 attempts.

        # ── Auto-start failure guard (Redis TTL counter) ─────────────────────
        redis_key = f"hosted:autostart_failures:{hosted_id}"
        redis = None  # initialise before try so error-handler branches never hit UnboundLocalError
        try:
            from app.core.redis_client import (  # noqa: PLC0415 — lazy import avoids circular redis_client → hosted_agent_service initialization cycle
                get_redis,
            )
            redis = await get_redis()
            raw = await redis.get(redis_key)
            failure_count = int(raw) if raw else 0
        except Exception as _redis_err:
            logger.warning("Redis failure counter unavailable for {}: {}", hosted_id, _redis_err)
            failure_count = 0

        if failure_count >= _MAX_AUTOSTART_FAILURES:
            raise HostedAgentTooManyFailures(
                f"Agent {hosted_id} failed to start {failure_count} times in the last 5 minutes"
            )

        # ── In-process dedup: wait if another coroutine is already starting ──
        _MAX_LOCK_ENTRIES = 1024
        if hosted_id in self._starting_locks:
            event = self._starting_locks[hosted_id]
            logger.debug("ensure_running: waiting for in-flight start of {} (source={})", hosted_id, source)
            try:
                await asyncio.wait_for(event.wait(), timeout=_MAX_START_WAIT_S)
            except asyncio.TimeoutError:
                logger.warning("ensure_running: in-flight start timeout for {}", hosted_id)
            # After the event fires, re-check status from DB
            refreshed = await self.repo.get_by_id(hosted_id)
            return bool(refreshed and refreshed["status"] == "running")

        # Register our start slot
        done_event = asyncio.Event()
        if len(self._starting_locks) >= _MAX_LOCK_ENTRIES:
            # LRU eviction: drop oldest entry
            self._starting_locks.popitem(last=False)
        self._starting_locks[hosted_id] = done_event

        try:
            # ── PG advisory lock: cross-worker dedup ─────────────────────────
            # Uses a transaction-scoped advisory lock. If another worker is
            # already starting this agent (holds the lock) we fall into the
            # polling loop rather than stacking a second runner call.
            lock_acquired = False
            try:
                result = await self.repo.db.execute(
                    text(
                        "SELECT pg_try_advisory_xact_lock(hashtext('hosted_start_' || :hid))"
                    ),
                    {"hid": hosted_id},
                )
                lock_acquired = bool(result.scalar())
            except Exception as _pg_err:
                logger.warning("PG advisory lock unavailable for {}: {} — proceeding without cross-worker dedup", hosted_id, _pg_err)
                lock_acquired = True  # Best-effort: proceed anyway

            if not lock_acquired:
                # Another worker holds the start lock — poll DB status.
                logger.debug("ensure_running: advisory lock taken by another worker for {} (source={})", hosted_id, source)
                for _ in range(_MAX_START_WAIT_S):
                    await asyncio.sleep(1)
                    check = await self.repo.get_by_id(hosted_id)
                    if check and check["status"] == "running":
                        return False  # was started by another worker (cold-start)
                logger.warning("ensure_running: timed out waiting for another worker to start {}", hosted_id)
                return False

            # ── We hold the lock: perform the start ──────────────────────────
            logger.info("ensure_running: cold-starting {} (source={})", hosted_id, source)
            try:
                skip_bootstrap = (source == "chat")
                await self._start_agent_internal(hosted, skip_bootstrap=skip_bootstrap)
            except HTTPException as exc:
                if exc.status_code in (502, 503):
                    # Record failure (skip when redis was unavailable)
                    if redis is not None:
                        try:
                            await redis.incr(redis_key)
                            await redis.expire(redis_key, _AUTOSTART_FAILURE_TTL_S)
                        except Exception:
                            pass
                    raise HostedAgentRunnerUnavailable(exc.detail) from exc
                raise
            except Exception as exc:
                if redis is not None:
                    try:
                        await redis.incr(redis_key)
                        await redis.expire(redis_key, _AUTOSTART_FAILURE_TTL_S)
                    except Exception:
                        pass
                raise HostedAgentRunnerUnavailable(str(exc)) from exc

            # Clear failure counter on success
            if redis is not None:
                try:
                    await redis.delete(redis_key)
                except Exception:
                    pass

            return False  # cold-start completed

        finally:
            # Always signal waiters and remove our slot
            done_event.set()
            self._starting_locks.pop(hosted_id, None)

    async def _start_agent_internal(self, hosted: dict, skip_bootstrap: bool = False) -> dict:
        """Send agent files and config to the Runner, start the container."""
        hosted_id = str(hosted["id"])

        # Build config-only payload from hosted_agents row — no agent_files DB loop.
        # The workspace dir is persistent across restarts; only seed files that do not
        # yet exist on disk (runner applies no-clobber guard on its side).
        #
        # What goes in files_payload:
        #   agent.yaml — canonical DeepAgentSpec generated from the agent row.
        #                If the agent has a custom agent.yaml in agent_files (user
        #                edited it), the runner's no-clobber guard will keep the
        #                on-disk version intact (file already exists → skip write).
        #
        # NOT sent:
        #   SKILL.md — runner always fetches live from platform /skill.md endpoint.
        #   AGENT.md — runner writes it from StartRequest.system_prompt (already below).
        #   custom.md — seeded once at agent creation time by create_hosted_agent/fork,
        #               persists in the runner workspace directory. start does not
        #               re-seed it (config-only cold-start); it is always present on
        #               disk from the initial import.
        files_payload = [
            {
                "file_path": "agent.yaml",
                "content": self._default_agent_yaml(),
                "file_type": "config",
            }
        ]

        full = await self.repo.get_by_id(hosted_id, include_api_key=True)
        agent_api_key = str(full.get("agent_api_key", "") or "") if full else ""

        # Load persisted session history (short-term memory)
        session_history = await self.repo.get_session_history(hosted_id)

        # Fetch OpenViking long-term context for system prompt enrichment
        ov_context_str = ""
        if self.openviking.enabled:
            try:
                agent_name = hosted.get("agent_name", "")
                ov_results = await self.openviking.search(
                    f"agent {agent_name} context history", top_k=3
                )
                if ov_results:
                    parts = [f"- {c['content'][:200]}" for c in ov_results if c.get("content")]
                    if parts:
                        ov_context_str = "\n\nLong-term memory (from previous sessions):\n" + "\n".join(parts)
            except Exception as e:
                logger.debug("OpenViking context on start: {}", e)

        # Resolve model with fallback: swap blocked/deprecated models transparently.
        active_model = await self.openrouter.resolve_model(hosted["model"])
        if active_model != hosted["model"]:
            await self.repo.update(hosted_id, {"model": active_model})
            hosted["model"] = active_model

        ctx_length = await self.openrouter.get_context_length(active_model)
        provider_info = self.openrouter.resolve_provider(active_model)

        runner_payload: dict = {
            "agent_id": str(hosted["agent_id"]),
            "system_prompt": hosted["system_prompt"] + ov_context_str,
            "model": active_model,
            "agent_handle": hosted.get("agent_handle") or hosted.get("handle") or "",
            "runtime": hosted["runtime"],
            "memory_limit_mb": hosted["memory_limit_mb"],
            "files": files_payload,
            "api_key": agent_api_key,
            "platform_url": self.settings.oauth_redirect_base_url or "https://agentspore.com",
            "heartbeat_seconds": hosted.get("heartbeat_seconds", 3600) if hosted.get("heartbeat_enabled", True) else 0,
            "message_history": session_history[-30:] if session_history else [],
            "context_max_tokens": int(ctx_length * 0.7),  # Compensate for pydantic-deep default token counter undercounting (~30% gap). Triggers auto-compression before real OpenRouter context overflow.
            "stuck_loop_detection": bool(hosted.get("stuck_loop_detection", False)),
        }
        if provider_info is not None:
            runner_payload["provider_base_url"] = provider_info["base_url"]
            runner_payload["provider_api_key"] = provider_info["api_key"]

        result = await self._call_runner("start", hosted_id, runner_payload)
        await self.repo.update_status(hosted_id, "running", container_id=result.get("container_id"))
        await self._notify_status(hosted, "running")

        # Auto-bootstrap only if no session history (first start or cleared) AND not skipped.
        # skip_bootstrap=True is set for source=="chat": the agent will read its files on first response.
        if not session_history and not skip_bootstrap:
            asyncio.create_task(self._bootstrap_agent(hosted_id))

        return {"status": "running", "message": "Agent started"}

    async def _bootstrap_agent(self, hosted_id: str) -> None:
        """Send bootstrap message to the runner so agent reads workspace files on start."""
        bootstrap_msg = (
            "Read your workspace files to restore context:\n"
            "1. **AGENT.md** — your identity and configuration\n"
            "2. **.deep/skills/SKILL.md** — AgentSpore platform API reference\n"
            "3. **.deep/memory/** directory — your persistent memory from previous sessions\n\n"
            "Study everything and let me know you're ready."
        )

        # Enrich with OpenViking long-term memory if available
        if self.openviking.enabled:
            try:
                hosted = await self.repo.get_by_id(hosted_id)
                if hosted:
                    ov_context = await self.openviking.search(
                        f"agent {hosted.get('agent_name', '')} context history", top_k=3
                    )
                    if ov_context:
                        memory_parts = [f"- {c['content'][:200]}" for c in ov_context if c.get("content")]
                        if memory_parts:
                            bootstrap_msg += (
                                "\n\n**Long-term memory (from previous sessions):**\n"
                                + "\n".join(memory_parts)
                            )
            except Exception as e:
                logger.debug("OpenViking context for bootstrap: {}", e)

        try:
            await asyncio.sleep(2)  # give runner time to initialize
            response = await self._call_runner("chat", hosted_id, {"content": bootstrap_msg})
            if response.get("reply"):
                await self.repo.add_owner_message(
                    hosted_id, "user", bootstrap_msg,
                )
                await self.repo.add_owner_message(
                    hosted_id, "agent", response["reply"],
                    tool_calls=response.get("tool_calls"),
                    thinking=response.get("thinking"),
                )
                # P5a: no DB file sync after bootstrap; runner dir is authoritative
                logger.info("Bootstrap completed for hosted agent {}", hosted_id)
        except Exception as e:
            logger.warning("Bootstrap failed for {}: {}", hosted_id, e)

    # ── Owner chat ──

    async def send_owner_message(self, hosted_id: str, user_id: str, content: str) -> dict:
        """Send a message from the owner to their hosted agent.

        Saves the message in owner_messages, forwards it to the
        Agent Runner for processing, and saves the agent's reply.
        Auto-starts the agent if it is stopped.
        """
        hosted = await self.get_hosted_agent(hosted_id, user_id)
        msg = await self.repo.add_owner_message(hosted_id, "user", content)

        async with use_agent_context(
            agent_id=str(hosted["id"]),
            agent_handle=hosted.get("agent_handle"),
            model=hosted.get("model"),
        ):
            if hosted["status"] != "running":
                try:
                    await self.ensure_running(hosted_id, source="chat")
                except HostedAgentRunnerUnavailable as exc:
                    raise HTTPException(503, str(exc)) from exc
                except HostedAgentTooManyFailures as exc:
                    raise HTTPException(503, "Agent failed to start 3 times in the last 5 minutes. Use Force Restart in Settings.") from exc

            try:
                response = await self._call_runner("chat", hosted_id, {"content": content})
                if response.get("reply"):
                    await self.repo.add_owner_message(
                        hosted_id, "agent", response["reply"],
                        tool_calls=response.get("tool_calls"),
                        thinking=response.get("thinking"),
                    )
                # P5a: no DB file sync after chat; runner dir is authoritative
                # Persist session history + index in OpenViking (background)
                _ctx = contextvars.copy_context()
                asyncio.create_task(_ctx.run(self._persist_session, hosted_id, content, response.get("reply", "")))
            except Exception as e:
                logger.warning("Runner chat error: {}", e)

        return msg

    async def stream_owner_message(self, hosted_id: str, user_id: str, content: str) -> AsyncGenerator[str, None]:
        """Stream chat response from the agent via runner ndjson stream.

        Saves user message before streaming, saves agent reply after
        the stream completes (from the 'done' event).
        If the agent is stopped, auto-starts it and emits phase events so
        the client can show a progress indicator.
        """
        hosted = await self.get_hosted_agent(hosted_id, user_id)
        await self.repo.add_owner_message(hosted_id, "user", content)

        if hosted["status"] != "running":
            if not self.runner_url:
                yield json.dumps({"type": "error", "message": "Agent runner not configured"}) + "\n"
                return
            try:
                yield json.dumps({"type": "phase", "phase": "starting_agent", "eta_s": 15}) + "\n"
                await self.ensure_running(hosted_id, source="chat")
                yield json.dumps({"type": "phase", "phase": "agent_started"}) + "\n"
            except HostedAgentRunnerUnavailable as exc:
                yield json.dumps({
                    "type": "error",
                    "phase": "starting_agent",
                    "message": str(exc),
                    "retryable": True,
                }) + "\n"
                return
            except HostedAgentTooManyFailures:
                yield json.dumps({
                    "type": "error",
                    "phase": "starting_agent",
                    "message": "Agent failed to start 3 times in the last 5 minutes. Use Force Restart in Settings.",
                    "retryable": False,
                }) + "\n"
                return

        if not self.runner_url:
            yield json.dumps({"type": "error", "message": "Agent is not running"}) + "\n"
            return

        final_reply = ""
        final_tools: list = []
        final_thinking = None

        runner_headers = {}
        if self.settings.agent_runner_key:
            runner_headers["X-Runner-Key"] = self.settings.agent_runner_key
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(1800.0, connect=10.0),
            ) as client:
                async with client.stream(
                    "POST",
                    f"{self.runner_url}/agents/{hosted_id}/chat/stream",
                    json={"content": content},
                    headers=runner_headers,
                ) as response:
                    if response.status_code != 200:
                        body = await response.aread()
                        yield json.dumps({"type": "error", "message": f"Runner error: {body.decode()[:200]}"}) + "\n"
                        return

                    done_line: str | None = None
                    async for line in response.aiter_lines():
                        if not line.strip():
                            continue
                        # Buffer the terminal `done` event and flush it to the
                        # client AFTER the agent reply is saved to DB. Otherwise
                        # the client's loadMessages() on `done` races the write
                        # and the fresh reply disappears from history.
                        try:
                            event = json.loads(line)
                            if event.get("type") == "done":
                                final_reply = event.get("reply", "")
                                final_tools = event.get("tool_calls", [])
                                final_thinking = event.get("thinking")
                                done_line = line
                                continue
                        except Exception:
                            pass
                        yield line + "\n"

                    # Persist reply BEFORE emitting `done` so the client's
                    # immediate refetch sees the new message.
                    if final_reply:
                        try:
                            await self.repo.add_owner_message(
                                hosted_id, "agent", final_reply,
                                tool_calls=final_tools,
                                thinking=final_thinking,
                            )
                            # P5a: no DB file sync after stream; runner dir is authoritative
                            _ctx = contextvars.copy_context()
                            asyncio.create_task(_ctx.run(self._persist_session, hosted_id, content, final_reply))
                        except Exception as save_exc:
                            logger.warning("Failed to persist reply before done: {}", save_exc)

                    if done_line is not None:
                        yield done_line + "\n"
        except Exception as e:
            logger.warning("Stream error: {}", e)
            yield json.dumps({"type": "error", "message": str(e)}) + "\n"
            return

    async def get_owner_messages(self, hosted_id: str, user_id: str, limit: int = 50) -> list[dict]:
        """Get private chat history between the owner and their agent."""
        await self.get_hosted_agent(hosted_id, user_id)
        return await self.repo.get_owner_messages(hosted_id, limit)

    async def list_checkpoints(self, hosted_id: str, user_id: str) -> list[dict]:
        """List in-memory checkpoints for the hosted agent's current run."""
        await self.get_hosted_agent(hosted_id, user_id)
        result = await self._call_runner("checkpoints", hosted_id, method="GET")
        return result.get("checkpoints", []) if isinstance(result, dict) else []

    async def rewind_to_checkpoint(
        self, hosted_id: str, user_id: str, checkpoint_id: str, before_timestamp: str | None
    ) -> dict:
        """Rewind the agent to a checkpoint and soft-delete owner_messages produced after it.

        ``before_timestamp`` is the checkpoint's ``created_at`` as known
        to the client. The runner restores its in-memory message history
        first; only on success do we hide owner_messages newer than the
        checkpoint, so a failed rewind never destroys visible chat.
        """
        await self.get_hosted_agent(hosted_id, user_id)
        rewind_result = await self._call_runner(
            "rewind", hosted_id, {"checkpoint_id": checkpoint_id}
        )
        hidden = 0
        if before_timestamp:
            hidden = await self.repo.soft_delete_owner_messages_after(
                hosted_id, before_timestamp
            )
        # Also clear persisted session_history so a future restart does
        # not pull rolled-back messages back into the runner.
        try:
            history_resp = await self._call_runner("history", hosted_id, method="GET")
            new_history = (
                history_resp.get("history", [])
                if isinstance(history_resp, dict)
                else []
            )
            await self.repo.save_session_history(hosted_id, new_history)
        except Exception as e:
            logger.warning("Could not refresh session_history after rewind for {}: {}", hosted_id, e)
        return {
            "status": "ok",
            "checkpoint_id": checkpoint_id,
            "messages_hidden": hidden,
            "runner": rewind_result,
        }

    async def clear_chat(self, hosted_id: str, user_id: str) -> dict:
        """Start a new session: hide all owner_messages, clear persisted history, restart runner state.

        The chat appears empty in the UI; rows remain in DB with
        ``is_deleted = TRUE`` for audit. The runner's in-memory
        ``message_history`` is reset by stopping and restarting the
        agent so the LLM has no recall of the prior conversation.
        """
        hosted = await self.get_hosted_agent(hosted_id, user_id)
        hidden = await self.repo.soft_delete_all_owner_messages(hosted_id)
        await self.repo.save_session_history(hosted_id, [])
        was_running = hosted.get("status") == "running"
        if was_running:
            try:
                await self._call_runner("stop", hosted_id)
            except Exception as e:
                logger.warning("Stop during clear_chat failed for {}: {}", hosted_id, e)
            try:
                await self.start_agent(hosted_id, user_id)
            except Exception as e:
                logger.warning("Restart during clear_chat failed for {}: {}", hosted_id, e)
        return {
            "status": "ok",
            "messages_hidden": hidden,
            "agent_restarted": was_running,
        }

    # ── Files ──

    async def write_file(
        self,
        hosted_id: str,
        user_id: str,
        file_path: str,
        content: str,
        file_type: str = "text",
        if_match_version: str | None = None,
    ) -> dict:
        """Write or update a file on the runner workspace (runner-authoritative).

        Flow:
          1. PUT to runner with ``If-Match: <sha>`` when provided.
             Runner validates sha vs on-disk sha and either writes or 412s.
          2. Return dict with ``version`` = sha returned by runner.

        No agent_files DB write — the runner workspace is the source of truth.

        Args:
            if_match_version: Opaque sha string from the previous read ETag.
                Triggers optimistic-lock on the runner. ``None`` = unconditional.

        Raises:
            StaleVersionError: 412 from runner — carries ``current_version``
                (sha) and ``current_content`` for the conflict modal.
            HTTPException(503): runner unreachable or returns non-412 error.
        """
        await self.get_hosted_agent(hosted_id, user_id)
        _validate_file_path(file_path)

        new_sha: str = ""
        if self.runner_url:
            runner_data = await self._rc.put_file(
                hosted_id,
                file_path,
                content,
                timeout=10,
                if_match=if_match_version,
            )
            new_sha = runner_data.get("version", "") or ""

        # P5a: runner is sole write target; no DB upsert.
        # Runner PUT response may omit size_bytes/modified_at — synthesise from
        # the local write (content byte length, empty mtime string as fallback).
        row = self._runner_file_to_dict(
            self._synthesise_write_entry(file_path, content, new_sha)
        )
        # Emit event with minimal row fields expected by downstream consumers.
        await self._emit_file_event(
            hosted_id, user_id, "file_updated",
            {"file_path": file_path, "content": content, "version": new_sha},
        )
        return row

    async def write_files_batch(
        self,
        hosted_id: str,
        user_id: str,
        items: list[dict],
    ) -> tuple[list[dict], list[dict]]:
        """Batch write to the runner workspace (runner-authoritative).

        Each item is pushed to the runner concurrently; the runner workspace
        is the source of truth. Returns (written_rows, failed) so the UI can
        report per-file outcomes. No agent_files DB write.
        """
        await self.get_hosted_agent(hosted_id, user_id)
        for item in items:
            _validate_file_path(item["file_path"])

        # Phase 1: push all files to runner concurrently (unconditional, no
        # If-Match for batch). Collect (file_path → new_sha) for response.
        push_tasks = [
            self._push_file_to_runner(hosted_id, item["file_path"], item["content"])
            for item in items
        ]
        push_results = await asyncio.gather(*push_tasks, return_exceptions=True)
        failed: list[dict] = []
        sha_map: dict[str, str] = {}
        for item, result in zip(items, push_results):
            if isinstance(result, Exception):
                failed.append({"file_path": item["file_path"], "error": str(result)[:200]})
            elif isinstance(result, str):
                sha_map[item["file_path"]] = result

        # P5a: runner is sole write target; no DB upsert.
        # Build full _file_response-compatible dict for each written file so
        # the API-route _file_response() call does not KeyError on id/file_type/
        # size_bytes/updated_at/truncated/is_binary.
        failed_paths = {f["file_path"] for f in failed}
        written_rows: list[dict] = []
        for item in items:
            if item["file_path"] in failed_paths:
                continue
            content = item["content"]
            new_sha = sha_map.get(item["file_path"], "")
            row = self._runner_file_to_dict(
                self._synthesise_write_entry(item["file_path"], content, new_sha)
            )
            written_rows.append(row)
            await self._emit_file_event(
                hosted_id, user_id, "file_updated",
                {"file_path": item["file_path"], "content": content, "version": new_sha},
            )
        return written_rows, failed

    @staticmethod
    def _synthesise_write_entry(file_path: str, content: str, sha: str) -> dict:
        """Build a runner-entry dict for a just-written file.

        Returns the shape expected by :meth:`_runner_file_to_dict`:
        ``file_path``, ``content``, ``size_bytes``, ``truncated``,
        ``is_binary``, ``version``, ``modified_at``.

        Used by :meth:`write_file` and :meth:`write_files_batch` so both
        paths produce identical dicts from a single source.

        Args:
            file_path: Workspace-relative path of the written file.
            content: Text content that was written.
            sha: Version sha returned by the runner PUT response.
        """
        return {
            "file_path": file_path,
            "content": content,
            "size_bytes": len(content.encode()) if content else 0,
            "truncated": False,
            "is_binary": False,
            "version": sha,
            "modified_at": "",
        }

    @staticmethod
    def _default_agent_yaml() -> str:
        """Return the canonical agent.yaml (DeepAgentSpec) content.

        Single source of truth shared by ``create_hosted_agent`` (initial
        workspace seed) and ``_start_agent_internal`` (cold-start files).
        """
        return (
            "# Agent configuration — edit to customize behavior\n"
            "# Changes take effect on next restart\n"
            "# NOTE: model and instructions are managed via Settings UI\n"
            "# and will override values in this file\n"
            "include_todo: true\n"
            "include_filesystem: true\n"
            "include_execute: true\n"
            "include_subagents: false\n"
            "include_skills: true\n"
            "include_memory: true\n"
            "memory_dir: /workspace/.deep/memory\n"
            "include_plan: true\n"
            "include_checkpoints: true\n"
            "checkpoint_frequency: every_turn\n"
            "max_checkpoints: 50\n"
            "context_manager: true\n"
            "context_discovery: true\n"
            "cost_tracking: true\n"
            "thinking: low\n"
            "# eviction_token_limit: auto (10% of model context, set by runner)\n"
            "web_search: false\n"
            "web_fetch: false\n"
            "skill_directories:\n"
            "  - /workspace/.deep/skills\n"
        )

    @staticmethod
    def _runner_file_to_dict(entry: dict, fallback_id: str | None = None) -> dict:
        """Map a runner file entry to a shape compatible with ``_file_response``.

        Runner entries: file_path, content, size_bytes, truncated, is_binary,
        version (12-char sha hex string), modified_at (ISO8601 mtime).

        ``version`` is the raw sha from the runner — treated as an opaque
        string by the frontend; the DB integer version is no longer in the
        read contract.
        """
        return {
            "id": fallback_id or "",
            "file_path": entry["file_path"],
            "file_type": _file_type_for(entry["file_path"]),
            "content": entry.get("content"),
            "size_bytes": entry.get("size_bytes", 0),
            "updated_at": entry.get("modified_at", ""),
            "version": entry.get("version") or "",
            "truncated": entry.get("truncated", False),
            "is_binary": entry.get("is_binary", False),
        }

    async def read_file(
        self, hosted_id: str, user_id: str, file_path: str
    ) -> dict:
        """Read a file from the agent's workspace (runner-authoritative).

        Raises:
            503: runner unreachable — DB is no longer authoritative for reads.
            404: file does not exist on runner disk.
        """
        await self.get_hosted_agent(hosted_id, user_id)
        if not self.runner_url:
            raise HTTPException(503, "Agent runner not configured")
        entry = await self._rc.get_file(hosted_id, file_path, timeout=10)
        return self._runner_file_to_dict(entry)

    async def list_files(
        self, hosted_id: str, user_id: str, *, include_hidden: bool = False
    ) -> list[dict]:
        """List files in the agent's workspace (runner-authoritative).

        Args:
            include_hidden: When True, include IGNORED_DIRS subtrees
                (venv/, node_modules/, etc.).

        Raises:
            503: runner unreachable.
        """
        await self.get_hosted_agent(hosted_id, user_id)
        if not self.runner_url:
            raise HTTPException(503, "Agent runner not configured")
        files = await self._rc.list_files(hosted_id, timeout=15, include_hidden=include_hidden)
        return [self._runner_file_to_dict(f) for f in files]

    async def delete_file(self, hosted_id: str, user_id: str, file_path: str) -> None:
        """Delete a file from the agent's workspace (runner-only; P5a removes DB write).

        P5a: runner is the sole delete target. repo.delete_file is NOT called here;
        it will be dropped in P5b together with the agent_files table.
        Runner returns 404 when the file does not exist — we surface that as HTTP 404.
        """
        await self.get_hosted_agent(hosted_id, user_id)
        _validate_file_path(file_path)
        if not self.runner_url:
            raise HTTPException(503, "Agent runner not configured")
        # URL-encoding is handled inside RunnerFileClient._file_url via quote(safe='/').
        await self._rc.delete_file(hosted_id, file_path, timeout=10)
        await self._emit_file_event(
            hosted_id, user_id, "file_deleted", {"file_path": file_path}
        )

    async def download_files_archive(
        self, hosted_id: str, user_id: str, *, include_hidden: bool = False
    ) -> bytes:
        """Download workspace as a ZIP archive from the runner.

        Args:
            include_hidden: When True, include hidden dirs in the archive.

        Returns:
            Raw ZIP bytes from the runner.

        Raises:
            HTTPException(503): runner not configured or unreachable.
            HTTPException(404): agent workspace not found on runner.
        """
        await self.get_hosted_agent(hosted_id, user_id)
        if not self.runner_url:
            raise HTTPException(503, "Agent runner not configured")
        return await self._rc.get_download(hosted_id, timeout=60, include_hidden=include_hidden)

    async def _push_file_to_runner(
        self, hosted_id: str, file_path: str, content: str
    ) -> str:
        """PUT the file's content onto the runner's on-disk workspace.

        Returns the new sha version string from the runner, or empty string on
        any failure.  Failure is non-fatal for the batch path — the DB row is
        the fork/start seed; a runner restart re-syncs from DB.

        Returns:
            sha string (12-char hex) on success, empty string on failure.
        """
        return await self._rc.soft_put_file(hosted_id, file_path, content, timeout=10)

    async def _emit_file_event(
        self, hosted_id: str, user_id: str, action: str, row: dict
    ) -> None:
        """Push a `hosted_agent_file` realtime event to the owner's tabs."""
        try:
            await deliver_user_event(str(user_id), {
                "type": "hosted_agent_file",
                "action": action,
                "hosted_id": hosted_id,
                "file_path": row.get("file_path"),
                "size_bytes": row.get("size_bytes"),
                "version": row.get("version"),
                "truncated": row.get("truncated", False),
                "is_binary": row.get("is_binary", False),
            })
        except Exception as exc:
            logger.debug("File event emit failed: {}", exc)

    # ── Memory persistence helpers ──

    async def _save_runner_history(self, hosted_id: str) -> None:
        """Fetch message_history from runner and save to DB."""
        if not self.runner_url:
            return
        try:
            history = await self._rc.soft_get_history(hosted_id, timeout=10)
            if history:
                await self.repo.save_session_history(hosted_id, history)
                logger.debug("Saved {} messages for {}", len(history), hosted_id)
        except Exception as e:
            logger.warning("Save history error for {}: {}", hosted_id, e)

    async def _persist_session(self, hosted_id: str, user_msg: str, agent_reply: str) -> None:
        """Background task: save runner history to DB + index exchange in OpenViking.

        Always runs as asyncio.create_task from request handlers / cron loop, so it
        opens its own DB session — never reuse self.db, which is owned by the caller
        and may close or be busy by the time this task runs (causes asyncpg
        InterfaceError "another operation in progress").

        Context propagation: callers use contextvars.copy_context().run() so the OTel
        trace context (and W3C Baggage with agent_id/handle/model) is inherited by
        this task. A direct logfire.span is used instead of use_agent_context to avoid
        creating a new root trace — this span joins the existing trace via the copied
        context.
        """
        with logfire.span("agent.persist_session", agent_id=str(hosted_id)):
            async with async_session_maker() as db:
                local_repo = HostedAgentRepository(db)
                agent_svc = AgentService(db)
                svc = HostedAgentService(
                    repo=local_repo, agent_service=agent_svc,
                    openrouter=self.openrouter, openviking=self.openviking,
                )
                # 1. Short-term: persist message_history from runner
                await svc._save_runner_history(hosted_id)

                # 2. Long-term: index in OpenViking for semantic search
                if agent_reply and svc.openviking.enabled:
                    try:
                        exchange = f"User: {user_msg[:500]}\nAgent: {agent_reply[:1000]}"
                        hosted = await local_repo.get_by_id(hosted_id)
                        if hosted:
                            await svc.openviking.add_to_agent_session(
                                str(hosted["agent_id"]), exchange
                            )
                    except Exception as e:
                        logger.debug("OpenViking index error: {}", e)

    # ── Runner communication ──

    async def _call_runner(self, action: str, hosted_id: str, payload: dict | None = None, method: str = "POST") -> dict:
        """Call the Agent Runner Service on the infra server.

        The Runner manages Docker containers with pydantic-deepagents.
        Actions: start, stop, chat, status, checkpoints, todos, rewind.
        """
        if not self.runner_url:
            logger.warning("Agent runner URL not configured, skipping {}", action)
            return {}
        url = f"{self.runner_url}/agents/{hosted_id}/{action}"
        logger.info("Calling runner: {} {}", method, url)
        headers = {}
        if self.settings.agent_runner_key:
            headers["X-Runner-Key"] = self.settings.agent_runner_key
        try:
            timeout = 1800 if action == "chat" else 60
            async with httpx.AsyncClient(timeout=timeout) as client:
                if method == "GET":
                    resp = await client.get(url, headers=headers)
                else:
                    resp = await client.post(url, json=payload or {}, headers=headers)
                resp.raise_for_status()
                return resp.json()
        except httpx.HTTPStatusError as e:
            logger.error("Runner {} error ({}): {}", action, e.response.status_code, e.response.text)
            # If runner says agent is not running, sync DB status
            if e.response.status_code in (404, 400) and action in ("stop", "restart", "chat"):
                await self.repo.update_status(hosted_id, "stopped")
            raise HTTPException(502, f"Agent runner error: {e.response.text}")
        except Exception as e:
            logger.error("Runner {} connection error: {}", action, repr(e))
            # Runner unreachable — mark agent as stopped
            if action in ("stop", "restart", "chat"):
                await self.repo.update_status(hosted_id, "stopped")
            raise HTTPException(503, f"Agent runner unavailable: {repr(e)}")


    # ── Cron tasks ──

    async def create_cron_task(self, hosted_id: str, user_id: str, data: dict) -> dict:
        """Create a scheduled task for a hosted agent."""
        await self.get_hosted_agent(hosted_id, user_id)
        if not croniter.is_valid(data["cron_expression"]):
            raise HTTPException(400, "Invalid cron expression")
        max_cron = self.settings.max_cron_tasks_per_agent
        existing = await self.repo.list_cron_tasks(hosted_id)
        if len(existing) >= max_cron:
            raise HTTPException(409, f"Max {max_cron} cron tasks per agent")
        cron = croniter(data["cron_expression"], datetime.now(timezone.utc))
        next_run = cron.get_next(datetime)
        return await self.repo.create_cron_task({
            "hosted_agent_id": hosted_id,
            "name": data["name"],
            "cron_expression": data["cron_expression"],
            "task_prompt": data["task_prompt"],
            "enabled": data.get("enabled", True),
            "auto_start": data.get("auto_start", True),
            "max_runs": data.get("max_runs"),
            "next_run_at": next_run,
        })

    async def list_cron_tasks(self, hosted_id: str, user_id: str) -> list[dict]:
        await self.get_hosted_agent(hosted_id, user_id)
        return await self.repo.list_cron_tasks(hosted_id)

    async def update_cron_task(self, hosted_id: str, user_id: str, task_id: str, updates: dict) -> dict:
        await self.get_hosted_agent(hosted_id, user_id)
        task = await self.repo.get_cron_task(task_id)
        if not task or str(task["hosted_agent_id"]) != hosted_id:
            raise HTTPException(404, "Task not found")
        # Don't filter None -- router sends exclude_unset=True, booleans like False must pass through
        clean = updates
        if "cron_expression" in clean:
            if not croniter.is_valid(clean["cron_expression"]):
                raise HTTPException(400, "Invalid cron expression")
            cron = croniter(clean["cron_expression"], datetime.now(timezone.utc))
            clean["next_run_at"] = cron.get_next(datetime)
        result = await self.repo.update_cron_task(task_id, clean)
        if not result:
            raise HTTPException(404, "Task not found")
        return result

    async def delete_cron_task(self, hosted_id: str, user_id: str, task_id: str) -> None:
        await self.get_hosted_agent(hosted_id, user_id)
        task = await self.repo.get_cron_task(task_id)
        if not task or str(task["hosted_agent_id"]) != hosted_id:
            raise HTTPException(404, "Task not found")
        await self.repo.delete_cron_task(task_id)

    async def execute_due_cron_tasks(self) -> int:
        """Check and execute all due cron tasks. Called by background scheduler."""
        due = await self.repo.get_due_cron_tasks()
        executed = 0
        for task in due:
            hosted_id = str(task["hosted_agent_id"])
            task_id = str(task["id"])
            error = None
            async with use_agent_context(
                agent_id=hosted_id,
                agent_handle=task.get("agent_handle"),
                cron_run_id=task_id,
            ):
                try:
                    # Auto-start if needed (ensure_running already waits until ready)
                    if task["auto_start"] and task["agent_status"] != "running":
                        await self.ensure_running(hosted_id, source="cron")

                    # Send task prompt as owner message
                    await self.send_owner_message(hosted_id, str(task["owner_user_id"]), task["task_prompt"])
                    executed += 1
                    logger.info("Cron task '{}' executed for agent {}", task["name"], task.get("agent_name", hosted_id))
                except Exception as e:
                    error = str(e)[:500]
                    logger.warning("Cron task '{}' failed: {}", task["name"], e)

            # Calculate next run anchored to the original scheduled_at, not
            # post-execution wall time, to prevent drift accumulation.
            base_time = task.get("scheduled_at") or datetime.now(timezone.utc)
            cron = croniter(task["cron_expression"], base_time)
            next_run = cron.get_next(datetime)

            # Disable if max_runs reached
            if task["max_runs"] and task["run_count"] + 1 >= task["max_runs"]:
                await self.repo.update_cron_task(task_id, {"enabled": False})

            await self.repo.mark_cron_run(task_id, next_run, error)

        return executed


def get_hosted_agent_service(
    repo: HostedAgentRepository = Depends(get_hosted_agent_repo),
    agent_service: AgentService = Depends(get_agent_service),
    openrouter: OpenRouterService = Depends(get_openrouter_service),
    openviking: OpenVikingService = Depends(get_openviking_service),
) -> HostedAgentService:
    """Factory for FastAPI Depends injection."""
    return HostedAgentService(repo=repo, agent_service=agent_service, openrouter=openrouter, openviking=openviking)
