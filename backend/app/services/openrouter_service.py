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

    # Models removed from the exposed list due to runtime unreachability.
    # OpenRouter still catalogues them but the actual /chat/completions call
    # fails for reasons unrelated to the request (upstream deprecation,
    # permanent 404 "providers ignored", etc.). Refresh periodically.
    BLOCKED_MODELS: frozenset[str] = frozenset({
        "qwen/qwen3.6-plus:free",          # 404 deprecated (2026-04)
        "qwen/qwen3-coder:free",           # 404 "All providers have been ignored"
    })

    # Preferred fallback when current selection is unavailable.
    # Verified responsive as of 2026-04-17.
    FALLBACK_MODEL = "nvidia/nemotron-3-super-120b-a12b:free"

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
            return self._cache or [{"id": self.FALLBACK_MODEL, "name": "Nemotron 3 Super 120B — free"}]

        models = []
        for m in data.get("data", []):
            mid = m.get("id", "")
            if ":free" not in mid:
                continue
            if mid in self.BLOCKED_MODELS:
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
        if model_id in self.BLOCKED_MODELS:
            return False
        models = await self.get_models()
        return any(m["id"] == model_id for m in models)

    async def resolve_model(self, model_id: str) -> str:
        """Return `model_id` if it is still runtime-reachable, otherwise the fallback.

        Used when starting a hosted agent: an owner may have picked a model months
        ago that has since been deprecated or blocked. Instead of surfacing a raw
        404 from OpenRouter, silently redirect to a known-working free model.
        """
        if model_id in self.BLOCKED_MODELS:
            logger.warning(
                "Model {} is in BLOCKED list; falling back to {}",
                model_id, self.FALLBACK_MODEL,
            )
            return self.FALLBACK_MODEL
        return model_id

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
