"""RunnerFileClient — thin HTTP wrapper for agent-runner file endpoints.

Encapsulates:
- base runner URL
- X-Runner-Key auth header
- URL construction for /agents/{id}/... paths
- per-call timeout parameter
- error → HTTPException / StaleVersionError mapping

DOES NOT own generic runner actions (start/stop/chat/history) — those stay in
_call_runner / stream_owner_message in HostedAgentService.  This module handles
only file-namespace endpoints:
  PUT  /agents/{id}/files
  GET  /agents/{id}/files
  GET  /agents/{id}/files/{file_path}
  DELETE /agents/{id}/files/{file_path}
  POST /agents/{id}/files/import
  GET  /agents/{id}/files/download
  GET  /agents/{id}/status  (used by get_hosted_agent status probe)
  GET  /agents/{id}/history (used by _save_runner_history)
"""

from urllib.parse import quote

import httpx
from fastapi import HTTPException
from loguru import logger

from app.repositories.hosted_agent_repo import StaleVersionError


class RunnerFileClient:
    """Thin HTTP client for agent-runner file-namespace calls.

    Args:
        runner_url: Base URL of the runner service, e.g. ``http://runner:8000``.
            When empty / None the client is a no-op for soft-failure paths and
            raises 503 for hard-failure paths (same semantics as the inline code
            it replaces).
        runner_key: Optional secret for ``X-Runner-Key`` header.
    """

    def __init__(self, runner_url: str | None, runner_key: str | None) -> None:
        self._base = (runner_url or "").rstrip("/")
        self._key = runner_key or ""

    # ── public accessors ──────────────────────────────────────────────────────

    @property
    def configured(self) -> bool:
        """True when a runner URL is set."""
        return bool(self._base)

    def headers(self) -> dict[str, str]:
        """Return auth headers dict (may be empty if no key configured)."""
        if self._key:
            return {"X-Runner-Key": self._key}
        return {}

    # ── URL helpers ───────────────────────────────────────────────────────────

    def _url(self, agent_id: str, suffix: str) -> str:
        return f"{self._base}/agents/{agent_id}/{suffix}"

    def _file_url(self, agent_id: str, file_path: str) -> str:
        return f"{self._base}/agents/{agent_id}/files/{quote(file_path, safe='/')}"

    # ── Hard-failure calls (raise 503 / 404 on error) ─────────────────────────

    async def put_file(
        self,
        agent_id: str,
        file_path: str,
        content: str,
        *,
        timeout: float = 10,
        if_match: str | None = None,
    ) -> dict:
        """PUT a file to the runner workspace.

        Returns runner JSON response (contains ``version`` key on success).

        Raises:
            StaleVersionError: runner returned 412.
            HTTPException(503): runner unreachable or other non-2xx.
        """
        url = self._url(agent_id, "files")
        hdrs = dict(self.headers())
        if if_match:
            hdrs["If-Match"] = if_match
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.put(
                    url,
                    json={"file_path": file_path, "content": content},
                    headers=hdrs,
                )
        except httpx.RequestError as exc:
            logger.warning(
                "runner put_file unreachable for {}/{}: {}", agent_id, file_path, exc
            )
            raise HTTPException(503, "Agent runner unavailable") from exc

        if resp.status_code == 412:
            detail = resp.json() if resp.content else {}
            inner = detail.get("detail", detail) if isinstance(detail, dict) else {}
            if isinstance(inner, dict):
                cv = inner.get("current_version", "")
                cc = inner.get("current_content")
            else:
                cv = ""
                cc = None
            raise StaleVersionError(current_version=str(cv), current_content=cc)

        if resp.status_code not in (200, 201):
            logger.warning(
                "runner put_file {} returned {}", file_path, resp.status_code
            )
            raise HTTPException(503, "Agent runner error")

        return resp.json() if resp.content else {}

    async def get_file(
        self,
        agent_id: str,
        file_path: str,
        *,
        timeout: float = 10,
    ) -> dict:
        """GET a single file from the runner workspace.

        Raises:
            HTTPException(404): file not found on runner.
            HTTPException(503): runner unreachable or other error.
        """
        url = self._file_url(agent_id, file_path)
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.get(url, headers=self.headers())
        except Exception as exc:
            logger.warning("runner get_file unavailable for {}: {}", file_path, exc)
            raise HTTPException(503, "Agent runner unavailable") from exc

        if resp.status_code == 404:
            raise HTTPException(404, "File not found")
        if resp.status_code != 200:
            logger.warning(
                "runner get_file {} returned {}", file_path, resp.status_code
            )
            raise HTTPException(503, "Agent runner error")

        return resp.json()

    async def list_files(
        self,
        agent_id: str,
        *,
        timeout: float = 15,
        include_hidden: bool = False,
    ) -> list[dict]:
        """GET /agents/{id}/files — returns list of file-entry dicts.

        Raises:
            HTTPException(503): runner unreachable or non-200.
        """
        params = {"include_hidden": "true"} if include_hidden else {}
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.get(
                    self._url(agent_id, "files"),
                    headers=self.headers(),
                    params=params,
                )
        except Exception as exc:
            logger.warning(
                "runner list_files unavailable for {}: {}", agent_id, exc
            )
            raise HTTPException(503, "Agent runner unavailable") from exc

        if resp.status_code != 200:
            logger.warning(
                "runner list_files {} returned {}", agent_id, resp.status_code
            )
            raise HTTPException(503, "Agent runner error")

        data = resp.json()
        return data.get("files", []) if isinstance(data, dict) else data

    async def delete_file(
        self,
        agent_id: str,
        file_path: str,
        *,
        timeout: float = 10,
    ) -> None:
        """DELETE a file from the runner workspace.

        Raises:
            HTTPException(404): file not found.
            HTTPException(503): runner unreachable or other error.
        """
        url = self._file_url(agent_id, file_path)
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.delete(url, headers=self.headers())
        except Exception as exc:
            logger.warning(
                "runner delete_file unavailable for {}: {}", file_path, exc
            )
            raise HTTPException(503, "Agent runner unavailable") from exc

        if resp.status_code == 404:
            raise HTTPException(404, "File not found")
        if resp.status_code not in (200, 204):
            logger.warning(
                "runner delete_file {} returned {}", file_path, resp.status_code
            )
            raise HTTPException(503, "Agent runner error")

    async def post_import(
        self,
        agent_id: str,
        files: list[dict],
        *,
        timeout: float = 20,
    ) -> dict:
        """POST /agents/{id}/files/import — seed workspace.

        Raises:
            HTTPException(503): runner unreachable or non-2xx.
        """
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(
                    self._url(agent_id, "files/import"),
                    json={"files": files},
                    headers=self.headers(),
                )
        except httpx.RequestError as exc:
            raise HTTPException(503, "Agent runner unavailable") from exc
        if resp.status_code not in (200, 201):
            raise HTTPException(503, "Agent runner unavailable")
        return resp.json() if resp.content else {}

    async def get_download(
        self,
        agent_id: str,
        *,
        timeout: float = 60,
        include_hidden: bool = False,
    ) -> bytes:
        """GET /agents/{id}/files/download — stream ZIP archive.

        Returns raw bytes of the ZIP.

        Raises:
            HTTPException(404): workspace not found on runner.
            HTTPException(503): runner unreachable or other error.
        """
        params = {"include_hidden": "true"} if include_hidden else {}
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.get(
                    self._url(agent_id, "files/download"),
                    headers=self.headers(),
                    params=params,
                )
        except Exception as exc:
            logger.warning(
                "runner download unavailable for {}: {}", agent_id, exc
            )
            raise HTTPException(503, "Agent runner unavailable") from exc

        if resp.status_code == 404:
            raise HTTPException(404, "Agent workspace not found")
        if resp.status_code != 200:
            logger.warning(
                "runner download {} returned {}", agent_id, resp.status_code
            )
            raise HTTPException(503, "Agent runner error")

        return resp.content

    # ── Soft-failure calls (return default / log on error, no raise) ──────────

    async def soft_put_file(
        self,
        agent_id: str,
        file_path: str,
        content: str,
        *,
        timeout: float = 10,
    ) -> str:
        """PUT a file — returns new sha version string on success, '' on any failure.

        Used by batch push path: failures are non-fatal.
        """
        if not self._base:
            return ""
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.put(
                    self._url(agent_id, "files"),
                    json={"file_path": file_path, "content": content},
                    headers=self.headers(),
                )
            if resp.status_code in (200, 201):
                data = resp.json() if resp.content else {}
                return data.get("version", "") or ""
        except Exception as exc:
            logger.debug(
                "runner soft_put_file fallthrough for {}/{}: {}", agent_id, file_path, exc
            )
        return ""

    async def soft_list_files(
        self,
        agent_id: str,
        *,
        timeout: float = 15,
    ) -> list[dict]:
        """GET files — returns list or [] on any error (no raise).

        Used by fork source-read path where runner down = empty workspace.
        """
        if not self._base:
            return []
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.get(
                    self._url(agent_id, "files"),
                    headers=self.headers(),
                )
            if resp.status_code == 200:
                data = resp.json()
                return data.get("files", []) if isinstance(data, dict) else data
        except httpx.RequestError as exc:
            logger.warning(
                "runner soft_list_files unreachable for {}: {}", agent_id, exc
            )
            return []
        return []

    async def soft_post_import(
        self,
        agent_id: str,
        files: list[dict],
        *,
        timeout: float = 20,
    ) -> int:
        """POST import — returns imported count, logs warning on failure (no raise).

        Used by fork seed path where runner down = workspace not seeded.
        Returns -1 on failure.
        """
        if not self._base:
            return -1
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(
                    self._url(agent_id, "files/import"),
                    json={"files": files},
                    headers=self.headers(),
                )
            if resp.status_code == 200:
                result = resp.json()
                return result.get("imported", len(files))
            logger.warning(
                "runner soft_post_import returned {} for {}", resp.status_code, agent_id
            )
        except httpx.RequestError as exc:
            logger.warning(
                "runner soft_post_import unavailable for {}: {}", agent_id, exc
            )
        return -1

    async def soft_get_status(
        self,
        agent_id: str,
        *,
        timeout: float = 3,
    ) -> dict | None:
        """GET /agents/{id}/status probe.

        Returns the JSON dict on success, an empty dict on non-200, None on a
        network error. Used by get_hosted_agent dead-agent probe; must not raise.

        Return semantics:
            dict with ``status`` key  — runner responded 200; use ``status`` value.
            {}  (empty dict)          — runner responded non-200 (agent not registered / 404).
            None                      — network error; caller should NOT change DB status.
        """
        if not self._base:
            return None
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.get(
                    self._url(agent_id, "status"),
                    headers=self.headers(),
                )
            if resp.status_code == 200:
                return resp.json()
            # Non-200 (e.g. 404 = agent not running on runner): return empty dict.
            return {}
        except Exception:
            # Network error: return None so caller leaves DB status unchanged.
            return None

    async def soft_get_history(
        self,
        agent_id: str,
        *,
        timeout: float = 10,
    ) -> list[dict]:
        """GET /agents/{id}/history — returns history list or [] on any error."""
        if not self._base:
            return []
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.get(
                    self._url(agent_id, "history"),
                    headers=self.headers(),
                )
            if resp.status_code == 200:
                return resp.json().get("history", [])
        except Exception as exc:
            logger.warning(
                "runner soft_get_history error for {}: {}", agent_id, exc
            )
        return []
