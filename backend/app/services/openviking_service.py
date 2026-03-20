"""OpenVikingService — shared agent memory via OpenViking context database."""

import asyncio
import time
from uuid import UUID

import httpx
from loguru import logger

from app.core.config import get_settings


class OpenVikingService:
    """Client for OpenViking API — stores agent insights, indexes projects, semantic search."""

    def __init__(self) -> None:
        s = get_settings()
        self.base_url = s.openviking_url.rstrip("/")
        self.api_key = s.openviking_api_key
        self.enabled = bool(self.base_url and self.api_key)
        self._headers = {"Authorization": f"Bearer {self.api_key}"}

    # ── Health ────────────────────────────────────────────────────────

    async def health(self) -> bool:
        """Check if OpenViking is reachable."""
        if not self.enabled:
            return False
        try:
            async with httpx.AsyncClient(timeout=5) as c:
                r = await c.get(f"{self.base_url}/health", headers=self._headers)
                return r.status_code == 200 and r.json().get("healthy", False)
        except Exception:
            return False

    # ── Store insight (shared) ────────────────────────────────────────

    async def store_insight(self, agent_id: str, agent_name: str, insight: str) -> bool:
        """Store an agent insight as a shared resource in viking://resources/insights/."""
        if not self.enabled:
            return False
        try:
            ts = int(time.time())
            filename = f"{agent_id}_{ts}.md"
            content = f"# Insight by {agent_name}\n\n{insight}"

            async with httpx.AsyncClient(timeout=15) as c:
                # Step 1: temp upload
                r = await c.post(
                    f"{self.base_url}/api/v1/resources/temp_upload",
                    headers=self._headers,
                    files={"file": (filename, content.encode())},
                )
                if r.status_code != 200:
                    logger.warning("OpenViking temp_upload failed: %d %s", r.status_code, r.text)
                    return False
                temp_path = r.json()["result"]["temp_path"]

                # Step 2: add as resource (fire-and-forget, don't block heartbeat)
                r = await c.post(
                    f"{self.base_url}/api/v1/resources",
                    headers=self._headers,
                    json={
                        "temp_path": temp_path,
                        "to": f"viking://resources/insights/{filename}",
                        "wait": False,
                    },
                )
                if r.status_code != 200:
                    logger.warning("OpenViking resource create failed: %d %s", r.status_code, r.text)
                    return False

            logger.info("OpenViking: stored insight from %s", agent_name)
            return True
        except Exception as e:
            logger.warning("OpenViking store_insight error: %s", e)
            return False

    # ── Store in agent session (private) ──────────────────────────────

    async def add_to_agent_session(self, agent_id: str, content: str) -> bool:
        """Add a message to agent's private session for long-term memory."""
        if not self.enabled:
            return False
        try:
            session_id = f"agent_{agent_id}"
            async with httpx.AsyncClient(timeout=10) as c:
                # Ensure session exists
                await c.post(
                    f"{self.base_url}/api/v1/sessions",
                    headers=self._headers,
                    json={"session_id": session_id},
                )
                # Add message
                r = await c.post(
                    f"{self.base_url}/api/v1/sessions/{session_id}/messages",
                    headers=self._headers,
                    json={"role": "user", "content": content},
                )
                return r.status_code == 200
        except Exception as e:
            logger.warning("OpenViking add_to_session error: %s", e)
            return False

    # ── Semantic search (shared knowledge) ────────────────────────────

    async def search(self, query: str, top_k: int = 5) -> list[dict]:
        """Semantic search across all shared resources (projects + insights)."""
        if not self.enabled:
            return []
        try:
            async with httpx.AsyncClient(timeout=15) as c:
                r = await c.post(
                    f"{self.base_url}/api/v1/search/search",
                    headers=self._headers,
                    json={"query": query, "top_k": top_k},
                )
                if r.status_code != 200:
                    return []
                result = r.json().get("result", {})
                # Flatten memories + resources into a single list
                items = []
                for key in ("memories", "resources", "skills"):
                    for item in result.get(key, []):
                        abstract = item.get("abstract", "")
                        items.append({
                            "uri": item.get("uri", ""),
                            "title": item.get("title", item.get("uri", "")),
                            "score": item.get("score", 0),
                            "source": key,
                            "content": abstract[:500] if abstract else "",
                        })
                # Sort by score descending, take top_k
                items.sort(key=lambda x: x.get("score", 0), reverse=True)
                items = items[:top_k]

                # Use abstract from search results (already available, no extra HTTP calls)
                # Fall back to parallel content/read only for items without abstract
                needs_fetch = [i for i in items if not i.get("content")]
                if needs_fetch:
                    async def _fetch(client: httpx.AsyncClient, item: dict) -> None:
                        uri = item.get("uri")
                        if not uri:
                            return
                        try:
                            cr = await client.get(
                                f"{self.base_url}/api/v1/content/read",
                                headers=self._headers,
                                params={"uri": uri},
                            )
                            if cr.status_code == 200:
                                raw = cr.json().get("result", "")
                                item["content"] = raw[:500] if isinstance(raw, str) else ""
                        except Exception:
                            pass

                    await asyncio.gather(*[_fetch(c, item) for item in needs_fetch])

                return items
        except Exception as e:
            logger.warning("OpenViking search error: %s", e)
            return []

    # ── Get agent context for heartbeat ───────────────────────────────

    async def get_agent_context(
        self, agent_id: str, project_titles: list[str], limit: int = 5
    ) -> list[dict]:
        """Get relevant memory context for an agent based on their projects."""
        if not self.enabled or not project_titles:
            return []
        query = "Agent working on: " + ", ".join(project_titles[:3])
        return await self.search(query, top_k=limit)

    # ── Index project as shared resource ──────────────────────────────

    async def index_project(
        self, project_id: str, title: str, description: str, tech_stack: list[str], category: str
    ) -> bool:
        """Index a project as a shared resource for semantic search."""
        if not self.enabled:
            return False
        try:
            content = (
                f"# {title}\n\n"
                f"**Category:** {category}\n"
                f"**Tech Stack:** {', '.join(tech_stack) if tech_stack else 'N/A'}\n\n"
                f"{description}"
            )
            filename = f"{project_id}.md"

            async with httpx.AsyncClient(timeout=15) as c:
                # Step 1: temp upload
                r = await c.post(
                    f"{self.base_url}/api/v1/resources/temp_upload",
                    headers=self._headers,
                    files={"file": (filename, content.encode())},
                )
                if r.status_code != 200:
                    return False
                temp_path = r.json()["result"]["temp_path"]

                # Step 2: add as resource (upsert via same path)
                r = await c.post(
                    f"{self.base_url}/api/v1/resources",
                    headers=self._headers,
                    json={
                        "temp_path": temp_path,
                        "to": f"viking://resources/projects/{filename}",
                        "wait": True,
                        "timeout": 30,
                    },
                )
                if r.status_code != 200:
                    logger.warning("OpenViking index_project failed for %s: %d", title, r.status_code)
                    return False

            logger.info("OpenViking: indexed project '%s'", title)
            return True
        except Exception as e:
            logger.warning("OpenViking index_project error: %s", e)
            return False

    # ── Check similar projects (deduplication) ────────────────────────

    async def find_similar_projects(self, title: str, description: str, threshold: float = 0.85) -> list[dict]:
        """Search for similar projects to prevent duplication."""
        if not self.enabled:
            return []
        query = f"{title}. {description[:200]}"
        results = await self.search(query, top_k=3)
        return [r for r in results if r.get("score", 0) >= threshold and r.get("source") == "resources"]

    # ── Session extract (auto-distill long-term memory) ─────────────

    async def extract_session_memory(self, agent_id: str) -> list[dict]:
        """Extract long-term memories from agent session via VLM analysis."""
        if not self.enabled:
            return []
        try:
            session_id = f"agent_{agent_id}"
            async with httpx.AsyncClient(timeout=30) as c:
                r = await c.post(
                    f"{self.base_url}/api/v1/sessions/{session_id}/extract",
                    headers=self._headers,
                )
                if r.status_code == 200:
                    memories = r.json().get("result", [])
                    if memories:
                        logger.info("OpenViking: extracted %d memories for agent %s", len(memories), agent_id)
                    return memories
                return []
        except Exception as e:
            logger.warning("OpenViking extract error: %s", e)
            return []

    # ── Session commit (compress + archive) ───────────────────────────

    async def commit_session(self, agent_id: str) -> dict:
        """Commit agent session — compresses history, extracts memories, archives."""
        if not self.enabled:
            return {}
        try:
            session_id = f"agent_{agent_id}"
            async with httpx.AsyncClient(timeout=30) as c:
                r = await c.post(
                    f"{self.base_url}/api/v1/sessions/{session_id}/commit",
                    headers=self._headers,
                )
                if r.status_code == 200:
                    result = r.json().get("result", {})
                    extracted = result.get("memories_extracted", 0)
                    if extracted:
                        logger.info("OpenViking: committed session for agent %s, extracted %d memories", agent_id, extracted)
                    return result
                return {}
        except Exception as e:
            logger.warning("OpenViking commit_session error: %s", e)
            return {}

    # ── Relations (knowledge graph) ───────────────────────────────────

    async def link_resources(self, from_uri: str, to_uris: list[str], relation: str = "related") -> bool:
        """Create relations between resources (e.g. project↔insight, agent↔project)."""
        if not self.enabled or not to_uris:
            return False
        try:
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.post(
                    f"{self.base_url}/api/v1/relations/link",
                    headers=self._headers,
                    json={"from_uri": from_uri, "to_uris": to_uris, "relation": relation},
                )
                return r.status_code == 200
        except Exception as e:
            logger.warning("OpenViking link error: %s", e)
            return False

    async def unlink_resources(self, from_uri: str, to_uris: list[str]) -> bool:
        """Remove relations between resources."""
        if not self.enabled or not to_uris:
            return False
        try:
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.post(
                    f"{self.base_url}/api/v1/relations/unlink",
                    headers=self._headers,
                    json={"from_uri": from_uri, "to_uris": to_uris},
                )
                return r.status_code == 200
        except Exception as e:
            logger.warning("OpenViking unlink error: %s", e)
            return False

    # ── Content abstract (VLM-generated summary) ──────────────────────

    async def get_directory_abstract(self, uri: str) -> str:
        """Get VLM-generated abstract of a resource directory."""
        if not self.enabled:
            return ""
        try:
            async with httpx.AsyncClient(timeout=20) as c:
                r = await c.get(
                    f"{self.base_url}/api/v1/content/abstract",
                    headers=self._headers,
                    params={"uri": uri},
                )
                if r.status_code == 200:
                    return r.json().get("result", "") or ""
                return ""
        except Exception as e:
            logger.warning("OpenViking abstract error: %s", e)
            return ""

    # ── Content overview (directory summary) ──────────────────────────

    async def get_directory_overview(self, uri: str) -> str:
        """Get overview of a resource directory (L0/L1 tiered context)."""
        if not self.enabled:
            return ""
        try:
            async with httpx.AsyncClient(timeout=20) as c:
                r = await c.get(
                    f"{self.base_url}/api/v1/content/overview",
                    headers=self._headers,
                    params={"uri": uri},
                )
                if r.status_code == 200:
                    return r.json().get("result", "") or ""
                return ""
        except Exception as e:
            logger.warning("OpenViking overview error: %s", e)
            return ""

    # ── Ask memory (RAG query via search + content) ───────────────────

    async def ask(self, question: str, top_k: int = 5) -> dict:
        """Answer a question using RAG — search + fetch content + build context."""
        if not self.enabled:
            return {"answer": "", "sources": []}
        results = await self.search(question, top_k=top_k)
        if not results:
            return {"answer": "", "sources": []}
        # Build context from search results
        context_parts = []
        sources = []
        for r in results:
            content = r.get("content", "")
            if content:
                context_parts.append(content)
                sources.append({
                    "uri": r.get("uri", ""),
                    "score": r.get("score", 0),
                    "source": r.get("source", ""),
                })
        return {
            "answer": "\n\n---\n\n".join(context_parts),
            "sources": sources,
            "query": question,
        }

    # ── Store insight with relation to project ────────────────────────

    async def store_insight_with_context(
        self, agent_id: str, agent_name: str, insight: str, project_id: str | None = None
    ) -> bool:
        """Store insight and link it to project if provided."""
        ok = await self.store_insight(agent_id, agent_name, insight)
        if ok and project_id:
            ts = int(time.time())
            insight_uri = f"viking://resources/insights/{agent_id}_{ts}.md"
            project_uri = f"viking://resources/projects/{project_id}.md"
            await self.link_resources(project_uri, [insight_uri], relation="has_insight")
        return ok

    # ── Skills (agent shared skills) ────────────────────────────────

    async def register_skill(self, name: str, description: str, content: str) -> bool:
        """Register an agent skill in OpenViking for discovery and sharing."""
        if not self.enabled:
            return False
        try:
            async with httpx.AsyncClient(timeout=60) as c:
                r = await c.post(
                    f"{self.base_url}/api/v1/skills",
                    headers=self._headers,
                    json={"data": {"name": name, "description": description, "content": content}},
                )
                if r.status_code == 200:
                    logger.info("OpenViking: registered skill '%s'", name)
                    return True
                logger.warning("OpenViking register_skill failed: %d %s", r.status_code, r.text[:200])
                return False
        except Exception as e:
            logger.warning("OpenViking register_skill error: %s", e)
            return False

    # ── Pack (backup / export) ────────────────────────────────────────

    async def export_backup(self, uri: str = "viking://resources", to: str = "viking://pack/backup.ovpack") -> str:
        """Export resources as a backup pack file."""
        if not self.enabled:
            return ""
        try:
            async with httpx.AsyncClient(timeout=30) as c:
                r = await c.post(
                    f"{self.base_url}/api/v1/pack/export",
                    headers=self._headers,
                    json={"uri": uri, "to": to},
                )
                if r.status_code == 200:
                    file_uri = r.json().get("result", {}).get("file", "")
                    logger.info("OpenViking: exported backup to %s", file_uri)
                    return file_uri
                return ""
        except Exception as e:
            logger.warning("OpenViking export_backup error: %s", e)
            return ""

    async def import_backup(self, uri: str, parent: str = "viking://resources") -> bool:
        """Import a previously exported backup pack."""
        if not self.enabled:
            return False
        try:
            async with httpx.AsyncClient(timeout=30) as c:
                r = await c.post(
                    f"{self.base_url}/api/v1/pack/import",
                    headers=self._headers,
                    json={"uri": uri, "parent": parent},
                )
                return r.status_code == 200
        except Exception as e:
            logger.warning("OpenViking import_backup error: %s", e)
            return False

    # ── Init shared directories ───────────────────────────────────────

    async def init_directories(self) -> None:
        """Create directory structure in OpenViking."""
        if not self.enabled:
            return
        try:
            async with httpx.AsyncClient(timeout=10) as c:
                for folder in ("insights", "projects"):
                    await c.post(
                        f"{self.base_url}/api/v1/fs/mkdir",
                        headers=self._headers,
                        json={"uri": f"viking://resources/{folder}"},
                    )
            logger.info("OpenViking: directories initialized")
        except Exception as e:
            logger.warning("OpenViking init_directories error: %s", e)


def get_openviking_service() -> OpenVikingService:
    """Factory function for OpenVikingService."""
    return OpenVikingService()
