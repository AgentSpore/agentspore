"""OpenVikingService — shared agent memory via OpenViking context database."""

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

                # Step 2: add as resource
                r = await c.post(
                    f"{self.base_url}/api/v1/resources",
                    headers=self._headers,
                    json={
                        "temp_path": temp_path,
                        "to": f"viking://resources/insights/{filename}",
                        "wait": True,
                        "timeout": 30,
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
            async with httpx.AsyncClient(timeout=10) as c:
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
                        items.append({
                            "title": item.get("title", item.get("uri", "")),
                            "content": item.get("content", item.get("text", ""))[:500],
                            "score": item.get("score", 0),
                            "source": key,
                        })
                # Sort by score descending, take top_k
                items.sort(key=lambda x: x.get("score", 0), reverse=True)
                return items[:top_k]
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
