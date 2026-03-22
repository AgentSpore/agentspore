"""OpenRouterService — fetches and caches free models with tool support from OpenRouter API."""

import time

import httpx
from loguru import logger


class OpenRouterService:
    """Manages OpenRouter model discovery with caching.

    Only returns free models with tool use support — zero cost for the platform.
    Results are cached for 1 hour to avoid hitting the API on every request.
    """

    API_URL = "https://openrouter.ai/api/v1/models"
    CACHE_TTL = 3600  # 1 hour

    def __init__(self):
        self._cache: list[dict] = []
        self._cache_ts: float = 0

    async def get_models(self) -> list[dict]:
        """Get available free models with tool use support. Cached 1h."""
        if self._cache and (time.time() - self._cache_ts) < self.CACHE_TTL:
            return self._cache

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(self.API_URL)
                resp.raise_for_status()
                data = resp.json()
        except Exception as e:
            logger.warning("Failed to fetch OpenRouter models: {}", e)
            return self._cache or [{"id": "qwen/qwen3-coder:free", "name": "Qwen3 Coder 480B A35B — free"}]

        models = []
        for m in data.get("data", []):
            mid = m.get("id", "")
            if ":free" not in mid:
                continue

            name = m.get("name", mid)
            sp = m.get("supported_parameters", [])
            has_tools = "tools" in sp or "tool_choice" in sp

            # Skip routers, non-tool models
            if "auto" in mid or not has_tools:
                continue

            clean_name = name.replace(" (free)", "").strip()
            ctx = m.get("context_length", 0)
            ctx_label = f"{ctx // 1024}K" if ctx >= 1024 else str(ctx)
            label = f"{clean_name} — free, {ctx_label} ctx"

            models.append({"id": mid, "name": label, "context_length": ctx})

        models.sort(key=lambda m: -m["context_length"])
        self._cache = [{"id": m["id"], "name": m["name"], "context_length": m["context_length"]} for m in models]
        self._cache_ts = time.time()
        logger.info("OpenRouter: loaded {} free models with tools", len(self._cache))
        return self._cache

    async def is_allowed(self, model_id: str) -> bool:
        """Check if a model ID is in the allowed list."""
        models = await self.get_models()
        return any(m["id"] == model_id for m in models)

    async def get_context_length(self, model_id: str) -> int:
        """Get context window size for a model (default 128K)."""
        models = await self.get_models()
        for m in models:
            if m["id"] == model_id:
                return m.get("context_length", 128_000)
        return 128_000


# Singleton
_instance: OpenRouterService | None = None


def get_openrouter_service() -> OpenRouterService:
    """Get or create the OpenRouter service singleton."""
    global _instance
    if _instance is None:
        _instance = OpenRouterService()
    return _instance
