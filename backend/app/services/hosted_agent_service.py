"""HostedAgentService — business logic for hosted agents on platform infrastructure."""

import asyncio
import json
import time
from pathlib import Path
from typing import AsyncGenerator

import httpx
from fastapi import Depends, HTTPException
from sqlalchemy import text
from app.core.config import get_settings
from app.repositories.hosted_agent_repo import HostedAgentRepository, get_hosted_agent_repo
from app.services.agent_service import AgentService, get_agent_service
from app.services.openrouter_service import OpenRouterService, get_openrouter_service
from app.services.openviking_service import OpenVikingService, get_openviking_service
from app.schemas.hosted_agents import DEFAULT_RUNTIME

from loguru import logger

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

    # ── CRUD ──

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
        MAX_HOSTED_PER_USER = 1
        existing = await self.repo.count_by_owner(user_id)
        if existing >= MAX_HOSTED_PER_USER:
            raise HTTPException(
                409, f"You can create up to {MAX_HOSTED_PER_USER} hosted agent(s). "
                     "Delete an existing agent to create a new one."
            )

        if not await self.openrouter.is_allowed(model):
            raise HTTPException(400, "Model not available")

        reg = await self.agent_svc.register_agent(
            name=name,
            model_provider="openrouter",
            model_name=model,
            specialization=specialization,
            skills=skills or [],
            description=description,
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

        # Create pydantic-deepagents workspace structure:
        # /AGENT.md              — system prompt (context file, auto-injected)
        # /SKILL.md              — platform skill.md (SkillsToolset)
        # /.deep/memory/main/MEMORY.md — persistent memory (MemoryToolset, branch "main")
        # /skills/               — custom skills directory (SkillsToolset)
        agent_md = (
            f"{system_prompt}\n\n"
            "## Platform Credentials\n\n"
            f"- **Agent ID**: `{reg['agent_id']}`\n"
            f"- **API Key**: `{reg['api_key']}`\n"
            f"- **Auth Header**: `X-API-Key: {reg['api_key']}`\n"
            f"- **Platform URL**: `{self.settings.oauth_redirect_base_url}`\n\n"
            "Use these credentials for all AgentSpore API calls (heartbeat, projects, chat, etc).\n\n"
            "## Platform API\n\n"
            "You have access to the AgentSpore platform API described in your SKILL.md file. "
            "Study it carefully before starting any work — it contains all available endpoints "
            "for creating projects, pushing code, and interacting with the platform.\n"
            "Reference: https://agentspore.com/skill.md"
        )
        await self.repo.upsert_file(hosted_id, "AGENT.md", agent_md, "config")
        await self.repo.upsert_file(hosted_id, ".deep/memory/main/MEMORY.md", "", "memory")

        # Auto-load platform skill.md
        platform_skill = _load_skill_md()
        if platform_skill:
            await self.repo.upsert_file(hosted_id, "SKILL.md", platform_skill, "skill")

        # Add user skills as separate file if provided
        if skills:
            skill_content = "\n\n".join(f"## {s}\n{s} skill." for s in skills)
            await self.repo.upsert_file(hosted_id, "skills/custom.md", skill_content, "skill")

        return {
            **hosted,
            "agent_name": name,
            "agent_handle": reg["handle"],
            "api_key": reg["api_key"],
        }

    async def get_hosted_agent(self, hosted_id: str, user_id: str) -> dict:
        """Get a hosted agent by ID, verifying the caller owns it.

        If DB says 'running' but runner doesn't have the agent, auto-correct to 'stopped'.
        """
        hosted = await self.repo.get_by_id(hosted_id)
        if not hosted:
            raise HTTPException(404, "Hosted agent not found")
        if str(hosted["owner_user_id"]) != user_id:
            raise HTTPException(403, "Not your agent")
        # Auto-detect dead agents: DB says running but runner lost them (e.g. runner restart)
        if hosted["status"] == "running" and self.runner_url:
            try:
                rh = {"X-Runner-Key": self.settings.agent_runner_key} if self.settings.agent_runner_key else {}
                async with httpx.AsyncClient(timeout=3) as client:
                    resp = await client.get(f"{self.runner_url}/agents/{hosted_id}/status", headers=rh)
                    if resp.status_code != 200 or resp.json().get("status") != "running":
                        await self.repo.update_status(hosted_id, "stopped")
                        hosted["status"] = "stopped"
                        logger.warning("Agent {} was dead on runner — auto-corrected to stopped", hosted_id)
            except Exception:
                pass  # Runner unreachable — don't change status
        return hosted

    async def list_my_agents(self, user_id: str) -> list[dict]:
        """List all hosted agents owned by the given user."""
        return await self.repo.list_by_owner(user_id)

    async def list_running_agents(self) -> list[dict]:
        """List all agents with status=running, with files for runner restore."""
        agents = await self.repo.list_running()
        result = []
        for a in agents:
            hid = str(a["id"])
            raw_files = await self.repo.list_files(hid)
            files = []
            for f in raw_files:
                fd = await self.repo.get_file(hid, f["file_path"])
                files.append({"file_path": f["file_path"], "content": fd["content"] if fd else "", "file_type": f["file_type"]})
            result.append({
                "id": hid,
                "agent_id": str(a["agent_id"]),
                "system_prompt": a["system_prompt"],
                "model": a["model"],
                "runtime": a["runtime"],
                "agent_api_key": a.get("agent_api_key", ""),
                "heartbeat_seconds": a.get("heartbeat_seconds", 3600),
                "files": files,
            })
        return result

    async def update_agent(self, hosted_id: str, user_id: str, updates: dict) -> dict:
        """Update hosted agent settings (system_prompt, model, budget, heartbeat).

        If system_prompt changes, also updates the AGENT.md file.
        Auto-restarts the agent if it's running so changes take effect immediately.
        """
        hosted = await self.get_hosted_agent(hosted_id, user_id)
        clean = {k: v for k, v in updates.items() if v is not None}
        if "model" in clean and not await self.openrouter.is_allowed(clean["model"]):
            raise HTTPException(400, "Model not available")
        if "system_prompt" in clean:
            await self.repo.upsert_file(hosted_id, "AGENT.md", clean["system_prompt"], "config")
        result = await self.repo.update(hosted_id, clean)

        # Auto-restart if running so new settings take effect
        if hosted["status"] == "running":
            try:
                await self._save_runner_history(hosted_id)
                await self._sync_files_from_runner(hosted_id)
                await self._call_runner("stop", hosted_id)
                refreshed = await self.repo.get_by_id(hosted_id)
                if refreshed:
                    await self._start_agent_internal(refreshed)
                    logger.info("Auto-restarted agent {} after settings update", hosted_id)
            except Exception as e:
                logger.warning("Auto-restart failed for {}: {}", hosted_id, e)

        return result

    async def delete_agent(self, hosted_id: str, user_id: str) -> None:
        """Delete a hosted agent. Stops the container first if running."""
        hosted = await self.get_hosted_agent(hosted_id, user_id)
        if hosted["status"] == "running":
            await self._call_runner("stop", hosted_id)
        await self.repo.delete(hosted_id)

    # ── Container control ──

    async def start_agent(self, hosted_id: str, user_id: str) -> dict:
        """Start the agent container on the infra server."""
        hosted = await self.get_hosted_agent(hosted_id, user_id)
        if hosted["status"] == "running":
            raise HTTPException(400, "Agent is already running")
        return await self._start_agent_internal(hosted)

    async def stop_agent(self, hosted_id: str, user_id: str) -> dict:
        """Stop the agent container on the infra server.

        Before stopping: persist session history and ask agent to summarize
        the session into .deep/memory for mid-term persistence.
        """
        hosted = await self.get_hosted_agent(hosted_id, user_id)
        if hosted["status"] != "running":
            raise HTTPException(400, "Agent is not running")

        hid = str(hosted["id"])

        # Save session history before stop (short-term memory)
        await self._save_runner_history(hid)

        # Ask agent to summarize session → .deep/memory (mid-term memory)
        try:
            summary_msg = (
                "You are about to be stopped. Before shutdown, update your memory file "
                ".deep/memory/main/MEMORY.md with key learnings, decisions, and context "
                "from this session that you'll need in the next session. Be concise."
            )
            await self._call_runner("chat", hid, {"content": summary_msg})
            await self._sync_files_from_runner(hid)
            # Save updated history including summary
            await self._save_runner_history(hid)
            logger.info("Session summary saved for {}", hid)
        except Exception as e:
            logger.warning("Session summary failed for {}: {}", hid, e)

        await self._call_runner("stop", hid)
        await self.repo.update_status(hid, "stopped")
        return {"status": "stopped", "message": "Agent stopped"}

    async def restart_agent(self, hosted_id: str, user_id: str) -> dict:
        """Restart the agent: quick stop, then start. No session summary."""
        hosted = await self.get_hosted_agent(hosted_id, user_id)
        hid = str(hosted["id"])
        if hosted["status"] == "running":
            try:
                await self._save_runner_history(hid)
                await self._sync_files_from_runner(hid)
            except Exception as e:
                logger.warning("Pre-restart save failed for {}: {}", hid, e)
            await self._call_runner("stop", hid)
        refreshed = await self.repo.get_by_id(hid)
        return await self._start_agent_internal(refreshed or hosted)

    async def _start_agent_internal(self, hosted: dict) -> dict:
        """Send agent files and config to the Runner, start the container."""
        hosted_id = str(hosted["id"])

        # Ensure platform SKILL.md is present
        existing_skill = await self.repo.get_file(hosted_id, "SKILL.md")
        if not existing_skill:
            platform_skill = _load_skill_md()
            if platform_skill:
                await self.repo.upsert_file(hosted_id, "SKILL.md", platform_skill, "skill")

        raw_files = await self.repo.list_files(hosted_id)
        files_payload = []
        for f in raw_files:
            file_data = await self.repo.get_file(hosted_id, f["file_path"])
            files_payload.append({
                "file_path": f["file_path"],
                "content": file_data["content"] if file_data else "",
                "file_type": f["file_type"],
            })

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

        ctx_length = await self.openrouter.get_context_length(hosted["model"])

        result = await self._call_runner("start", hosted_id, {
            "agent_id": str(hosted["agent_id"]),
            "system_prompt": hosted["system_prompt"] + ov_context_str,
            "model": hosted["model"],
            "runtime": hosted["runtime"],
            "memory_limit_mb": hosted["memory_limit_mb"],
            "files": files_payload,
            "api_key": agent_api_key,
            "heartbeat_seconds": hosted.get("heartbeat_seconds", 3600) if hosted.get("heartbeat_enabled", True) else 0,
            "message_history": session_history[-30:] if session_history else [],
            "context_max_tokens": ctx_length,
        })
        await self.repo.update_status(hosted_id, "running", container_id=result.get("container_id"))

        # Auto-bootstrap only if no session history (first start or cleared)
        if not session_history:
            asyncio.create_task(self._bootstrap_agent(hosted_id))

        return {"status": "running", "message": "Agent started"}

    async def _bootstrap_agent(self, hosted_id: str) -> None:
        """Send bootstrap message to the runner so agent reads workspace files on start."""
        bootstrap_msg = (
            "Read your workspace files to restore context:\n"
            "1. **AGENT.md** — your identity and configuration\n"
            "2. **SKILL.md** — AgentSpore platform API reference\n"
            "3. **.deep/** directory — your persistent memory from previous sessions\n\n"
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
                if response.get("tool_calls"):
                    await self._sync_files_from_runner(hosted_id)
                logger.info("Bootstrap completed for hosted agent {}", hosted_id)
        except Exception as e:
            logger.warning("Bootstrap failed for {}: {}", hosted_id, e)

    # ── Owner chat ──

    async def send_owner_message(self, hosted_id: str, user_id: str, content: str) -> dict:
        """Send a message from the owner to their hosted agent.

        Saves the message in owner_messages, forwards it to the
        Agent Runner for processing, and saves the agent's reply.
        """
        hosted = await self.get_hosted_agent(hosted_id, user_id)
        msg = await self.repo.add_owner_message(hosted_id, "user", content)

        if hosted["status"] != "running":
            await self.repo.add_owner_message(hosted_id, "agent", "⚠ Agent is stopped. Press Start to restart.")
            return msg

        try:
            response = await self._call_runner("chat", hosted_id, {"content": content})
            if response.get("reply"):
                await self.repo.add_owner_message(
                    hosted_id, "agent", response["reply"],
                    tool_calls=response.get("tool_calls"),
                    thinking=response.get("thinking"),
                )
            # Sync files from runner workspace → DB after tool use
            if response.get("tool_calls"):
                await self._sync_files_from_runner(hosted_id)
            # Persist session history + index in OpenViking (background)
            asyncio.create_task(self._persist_session(hosted_id, content, response.get("reply", "")))
        except Exception as e:
            logger.warning("Runner chat error: {}", e)

        return msg

    async def stream_owner_message(self, hosted_id: str, user_id: str, content: str) -> AsyncGenerator[str, None]:
        """Stream chat response from the agent via runner ndjson stream.

        Saves user message before streaming, saves agent reply after
        the stream completes (from the 'done' event).
        """
        hosted = await self.get_hosted_agent(hosted_id, user_id)
        await self.repo.add_owner_message(hosted_id, "user", content)

        if hosted["status"] != "running" or not self.runner_url:
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
                timeout=httpx.Timeout(180.0, connect=10.0),
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

                    async for line in response.aiter_lines():
                        if not line.strip():
                            continue
                        yield line + "\n"
                        try:
                            event = json.loads(line)
                            if event.get("type") == "done":
                                final_reply = event.get("reply", "")
                                final_tools = event.get("tool_calls", [])
                                final_thinking = event.get("thinking")
                        except Exception:
                            pass
        except Exception as e:
            logger.warning("Stream error: {}", e)
            yield json.dumps({"type": "error", "message": str(e)}) + "\n"
            return

        # Save agent response to DB after stream completes
        if final_reply:
            await self.repo.add_owner_message(
                hosted_id, "agent", final_reply,
                tool_calls=final_tools,
                thinking=final_thinking,
            )
            if final_tools:
                await self._sync_files_from_runner(hosted_id)
            # Persist session history + index in OpenViking (background)
            asyncio.create_task(self._persist_session(hosted_id, content, final_reply))

    async def get_owner_messages(self, hosted_id: str, user_id: str, limit: int = 50) -> list[dict]:
        """Get private chat history between the owner and their agent."""
        await self.get_hosted_agent(hosted_id, user_id)
        return await self.repo.get_owner_messages(hosted_id, limit)

    # ── Files ──

    async def write_file(self, hosted_id: str, user_id: str, file_path: str, content: str, file_type: str = "text") -> dict:
        """Write or update a file in the agent's workspace."""
        await self.get_hosted_agent(hosted_id, user_id)
        return await self.repo.upsert_file(hosted_id, file_path, content, file_type)

    async def read_file(self, hosted_id: str, user_id: str, file_path: str) -> dict:
        """Read a file from the agent's workspace."""
        await self.get_hosted_agent(hosted_id, user_id)
        f = await self.repo.get_file(hosted_id, file_path)
        if not f:
            raise HTTPException(404, "File not found")
        return f

    async def list_files(self, hosted_id: str, user_id: str) -> list[dict]:
        """List all files in the agent's workspace."""
        await self.get_hosted_agent(hosted_id, user_id)
        return await self.repo.list_files(hosted_id)

    async def delete_file(self, hosted_id: str, user_id: str, file_path: str) -> None:
        """Delete a file from the agent's workspace (DB + runner disk)."""
        await self.get_hosted_agent(hosted_id, user_id)
        deleted = await self.repo.delete_file(hosted_id, file_path)
        if not deleted:
            raise HTTPException(404, "File not found")
        # Also delete from runner disk
        if self.runner_url:
            try:
                rh = {"X-Runner-Key": self.settings.agent_runner_key} if self.settings.agent_runner_key else {}
                async with httpx.AsyncClient(timeout=10) as client:
                    await client.delete(f"{self.runner_url}/agents/{hosted_id}/files/{file_path}", headers=rh)
            except Exception:
                pass

    # ── Memory persistence helpers ──

    async def _save_runner_history(self, hosted_id: str) -> None:
        """Fetch message_history from runner and save to DB."""
        try:
            if not self.runner_url:
                return
            rh = {"X-Runner-Key": self.settings.agent_runner_key} if self.settings.agent_runner_key else {}
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(f"{self.runner_url}/agents/{hosted_id}/history", headers=rh)
                if resp.status_code == 200:
                    history = resp.json().get("history", [])
                    if history:
                        await self.repo.save_session_history(hosted_id, history)
                        logger.debug("Saved {} messages for {}", len(history), hosted_id)
        except Exception as e:
            logger.warning("Save history error for {}: {}", hosted_id, e)

    async def _persist_session(self, hosted_id: str, user_msg: str, agent_reply: str) -> None:
        """Background task: save runner history to DB + index exchange in OpenViking."""
        # 1. Short-term: persist message_history from runner
        await self._save_runner_history(hosted_id)

        # 2. Long-term: index in OpenViking for semantic search
        if agent_reply and self.openviking.enabled:
            try:
                exchange = f"User: {user_msg[:500]}\nAgent: {agent_reply[:1000]}"
                # Get agent_id for OpenViking session
                hosted = await self.repo.get_by_id(hosted_id)
                if hosted:
                    await self.openviking.add_to_agent_session(
                        str(hosted["agent_id"]), exchange
                    )
            except Exception as e:
                logger.debug("OpenViking index error: {}", e)

    async def _sync_files_from_runner(self, hosted_id: str) -> None:
        """Sync files from runner workspace to DB after agent creates/modifies files.

        Only adds new files and updates existing ones. Does not re-add
        files that the user explicitly deleted from the UI.
        """
        try:
            if not self.runner_url:
                return
            rh = {"X-Runner-Key": self.settings.agent_runner_key} if self.settings.agent_runner_key else {}
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(f"{self.runner_url}/agents/{hosted_id}/files", headers=rh)
                if resp.status_code != 200:
                    return
                data = resp.json()

            for f in data.get("files", []):
                path = f.get("file_path", "")
                content = f.get("content")
                if not path or content is None:
                    continue
                if len(content) > 500_000:
                    logger.debug("Skipping large file {} ({} bytes)", path, len(content))
                    continue
                file_type = "skill" if "skills/" in path else ("memory" if "memory" in path.lower() else ("config" if path == "AGENT.md" else "text"))
                await self.repo.upsert_file(hosted_id, path, content, file_type)
        except Exception as e:
            logger.debug("File sync error: {}", e)

    # ── Runner communication ──

    async def _call_runner(self, action: str, hosted_id: str, payload: dict | None = None) -> dict:
        """Call the Agent Runner Service on the infra server.

        The Runner manages Docker containers with pydantic-deepagents.
        Actions: start, stop, chat, status.
        """
        if not self.runner_url:
            logger.warning("Agent runner URL not configured, skipping {}", action)
            return {}
        url = f"{self.runner_url}/agents/{hosted_id}/{action}"
        logger.info("Calling runner: POST {}", url)
        headers = {}
        if self.settings.agent_runner_key:
            headers["X-Runner-Key"] = self.settings.agent_runner_key
        try:
            timeout = 180 if action == "chat" else 60
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(url, json=payload or {}, headers=headers)
                resp.raise_for_status()
                return resp.json()
        except httpx.HTTPStatusError as e:
            logger.error("Runner {} error ({}): {}", action, e.response.status_code, e.response.text)
            raise HTTPException(502, f"Agent runner error: {e.response.text}")
        except Exception as e:
            logger.error("Runner {} connection error: {}", action, repr(e))
            raise HTTPException(503, f"Agent runner unavailable: {repr(e)}")


def get_hosted_agent_service(
    repo: HostedAgentRepository = Depends(get_hosted_agent_repo),
    agent_service: AgentService = Depends(get_agent_service),
    openrouter: OpenRouterService = Depends(get_openrouter_service),
    openviking: OpenVikingService = Depends(get_openviking_service),
) -> HostedAgentService:
    """Factory for FastAPI Depends injection."""
    return HostedAgentService(repo=repo, agent_service=agent_service, openrouter=openrouter, openviking=openviking)
