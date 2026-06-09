"""OpenRouterService — fetches and caches free models with tool support from OpenRouter API.

Extra providers (Cerebras, Groq, Mistral, Nebius, Z.AI, Cloudflare Workers AI,
…) are fetched dynamically from their /models endpoints and cached alongside
OpenRouter models. Providers may declare a `static_models` list to skip the
/models fetch entirely (e.g. Z.AI's free Flash models are hidden by its /models
endpoint but work via chat/completions) and an `account_id_field` whose value
is substituted into a `{account_id}` placeholder in the base_url (Cloudflare).
"""

import time

import httpx
from loguru import logger

from app.core.config import get_settings

# Keywords that indicate a non-chat model (embedding, audio, guard, etc.)
_CHAT_MODEL_SKIP: frozenset[str] = frozenset({
    "embed",
    "whisper",
    "guard",
    "moderation",
    "ocr",
    "transcribe",
    "tts",
    "realtime",
    "voxtral",
    "pixtral",
    "prompt-guard",
})

_PROVIDER_DISPLAY: dict[str, str] = {
    "cerebras": "Cerebras",
    "groq": "Groq",
    "mistral": "Mistral",
    "nebius": "Nebius",
    "sambanova": "SambaNova",
    "nvidia": "NVIDIA NIM",
    "together": "Together AI",
    "zai": "Z.AI",
    "cloudflare": "Cloudflare Workers AI",
}


def _is_chat_model(model_id: str, context_window: int) -> bool:
    """Return True if model looks like a chat-capable LLM (not embed/audio/guard)."""
    if context_window < 4096:
        return False
    low = model_id.lower()
    return not any(kw in low for kw in _CHAT_MODEL_SKIP)


def _model_label(provider: str, model_id: str, ctx: int) -> str:
    """Build human-readable label: '<short-id> (Provider) — free, <N>K ctx'."""
    ctx_label = f"{ctx // 1024}K" if ctx >= 1024 else str(ctx)
    short = model_id.split("/")[-1]
    return f"{short} ({_PROVIDER_DISPLAY.get(provider, provider)}) — free, {ctx_label} ctx"


class OpenRouterService:
    """Manages model discovery across OpenRouter and extra LLM providers.

    OpenRouter: only free models with tool use support — zero cost for the platform.
    Extra providers (Cerebras, Groq, Mistral, Nebius): fetched dynamically from their
    /models APIs, filtered to chat-capable models only.
    All results are cached for 1 hour to avoid hitting APIs on every request.
    """

    API_URL = "https://openrouter.ai/api/v1/models"
    CACHE_TTL = 3600  # 1 hour

    # Models removed from the exposed list due to runtime unreachability.
    # OpenRouter still catalogues them but the actual /chat/completions call
    # fails for reasons unrelated to the request. Two classes of failure:
    #
    # 1. Upstream deprecated — free tier pulled by the original provider
    # 2. "All providers have been ignored" — every provider serving the
    #    model is blocked by this account's privacy setting (data-collecting
    #    providers disabled at https://openrouter.ai/settings/privacy)
    #
    # Refresh periodically. Probe with `scripts/probe_openrouter_models.py`.
    BLOCKED_MODELS: frozenset[str] = frozenset({
        # Deprecated upstream (2026-04-17 probe)
        "qwen/qwen3.6-plus:free",
        # Account privacy blocks all providers (2026-04-17 probe)
        # Error: "All providers have been ignored"
        "qwen/qwen3-coder:free",
        "qwen/qwen3-next-80b-a3b-instruct:free",
        "meta-llama/llama-3.2-3b-instruct:free",
        "meta-llama/llama-3.3-70b-instruct:free",
        "nousresearch/hermes-3-llama-3.1-405b:free",
        "cognitivecomputations/dolphin-mistral-24b-venice-edition:free",
    })

    # Preferred fallback when current selection is unavailable.
    # Verified responsive as of 2026-04-17.
    FALLBACK_MODEL = "nvidia/nemotron-3-super-120b-a12b:free"

    # Extra providers: models fetched dynamically via /models API.
    # Gemini does not expose a standard /models endpoint — keep static.
    EXTRA_PROVIDERS: dict = {
        "cerebras": {
            "base_url": "https://api.cerebras.ai/v1",
            "api_key_field": "cerebras_api_key",
        },
        "groq": {
            "base_url": "https://api.groq.com/openai/v1",
            "api_key_field": "groq_api_key",
        },
        "mistral": {
            "base_url": "https://api.mistral.ai/v1",
            "api_key_field": "mistral_api_key",
        },
        "nebius": {
            "base_url": "https://api.studio.nebius.ai/v1",
            "api_key_field": "nebius_api_key",
        },
        "sambanova": {
            "base_url": "https://api.sambanova.ai/v1",
            "api_key_field": "sambanova_api_key",
        },
        "nvidia": {
            "base_url": "https://integrate.api.nvidia.com/v1",
            "api_key_field": "nvidia_api_key",
        },
        "together": {
            "base_url": "https://api.together.xyz/v1",
            "api_key_field": "together_api_key",
        },
        "zai": {
            "base_url": "https://api.z.ai/api/paas/v4",
            "api_key_field": "zai_api_key",
            # Z.AI's /models endpoint lists ONLY paid models (glm-4.5, glm-4.6,
            # glm-5, …); the free Flash family is hidden there but works via
            # chat/completions (verified live 2026-06-09). Serve a static list.
            "static_models": ["glm-4.7-flash", "glm-4.5-flash"],
        },
        "cloudflare": {
            # {account_id} is substituted from `account_id_field` at consume time.
            "base_url": "https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/v1",
            "api_key_field": "cloudflare_api_key",
            "account_id_field": "cloudflare_account_id",
        },
    }

    # Gemini kept static — no standard /models endpoint.
    GEMINI_MODELS: list[dict] = [
        {
            "id": "gemini/gemini-2.0-flash",
            "name": "Gemini 2.0 Flash — free, 1M ctx",
            "context_length": 1048576,
            "provider": "gemini",
        },
        {
            "id": "gemini/gemini-2.5-flash-preview-05-20",
            "name": "Gemini 2.5 Flash Preview — free, 1M ctx",
            "context_length": 1048576,
            "provider": "gemini",
        },
    ]

    GEMINI_CFG: dict = {
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "api_key_field": "gemini_api_key",
    }

    @staticmethod
    def _resolve_provider_cfg(cfg: dict, settings) -> tuple[str, str] | None:
        """Resolve (base_url, api_key) for an extra-provider config entry.

        Returns None when the provider is not fully configured (missing api key,
        or a required account_id placeholder cannot be substituted). For entries
        with an `account_id_field`, the `{account_id}` placeholder in `base_url`
        is filled and the provider is active only when BOTH the key and the
        account id are set.
        """
        api_key = getattr(settings, cfg["api_key_field"], "")
        if not api_key:
            return None
        base_url = cfg["base_url"]
        account_field = cfg.get("account_id_field")
        if account_field:
            account_id = getattr(settings, account_field, "")
            if not account_id:
                return None
            base_url = base_url.format(account_id=account_id)
        return base_url, api_key

    def __init__(self) -> None:
        self._cache: list[dict] = []
        self._cache_ts: float = 0
        self._extra_cache: list[dict] = []
        self._extra_cache_ts: float = 0

    async def _extra_provider_models(self) -> list[dict]:
        """Fetch and cache models from all configured extra providers."""
        if self._extra_cache and (time.time() - self._extra_cache_ts) < self.CACHE_TTL:
            return self._extra_cache

        settings = get_settings()
        result: list[dict] = []

        for provider_name, cfg in self.EXTRA_PROVIDERS.items():
            resolved = self._resolve_provider_cfg(cfg, settings)
            if resolved is None:
                continue
            base_url, api_key = resolved

            # Static-model providers: emit declared IDs without hitting /models
            # (the provider's /models endpoint hides these free models).
            static_models = cfg.get("static_models")
            if static_models:
                for mid in static_models:
                    ctx = 131072
                    result.append({
                        "id": f"{provider_name}/{mid}",
                        "name": _model_label(provider_name, mid, ctx),
                        "context_length": ctx,
                        "provider": provider_name,
                    })
                continue

            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    resp = await client.get(
                        f"{base_url}/models",
                        headers={"Authorization": f"Bearer {api_key}"},
                    )
                    resp.raise_for_status()
                    data = resp.json()
            except Exception as e:
                logger.warning("Failed to fetch {} models: {}", provider_name, e)
                continue
            if not isinstance(data, dict):
                logger.warning(
                    "Unexpected {} /models payload shape: {}",
                    provider_name,
                    type(data).__name__,
                )
                continue

            for m in data.get("data", []):
                mid = m.get("id", "")
                ctx = int(m.get("context_window") or m.get("max_tokens") or 32768)
                if not _is_chat_model(mid, ctx):
                    continue
                platform_id = f"{provider_name}/{mid}"
                result.append({
                    "id": platform_id,
                    "name": _model_label(provider_name, mid, ctx),
                    "context_length": ctx,
                    "provider": provider_name,
                })

        # Gemini: static list, include when key is set
        gemini_key = getattr(settings, self.GEMINI_CFG["api_key_field"], "")
        if gemini_key:
            result.extend(self.GEMINI_MODELS)

        self._extra_cache = result
        self._extra_cache_ts = time.time()
        logger.info("Extra providers: loaded {} models", len(result))
        return result

    async def get_models(self) -> list[dict]:
        """Get available models: OpenRouter free + extra provider models. Cached 1h."""
        extra = await self._extra_provider_models()

        if self._cache and (time.time() - self._cache_ts) < self.CACHE_TTL:
            return self._cache + extra

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(self.API_URL)
                resp.raise_for_status()
                data = resp.json()
        except Exception as e:
            logger.warning("Failed to fetch OpenRouter models: {}", e)
            fallback = self._cache or [{"id": self.FALLBACK_MODEL, "name": "Nemotron 3 Super 120B — free", "provider": "openrouter"}]
            return fallback + extra

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

            models.append({
                "id": mid,
                "name": label,
                "context_length": ctx,
                "provider": "openrouter",
            })

        models.sort(key=lambda m: -m["context_length"])
        self._cache = models
        self._cache_ts = time.time()
        logger.info("OpenRouter: loaded {} free models with tools", len(self._cache))
        return self._cache + extra

    async def is_allowed(self, model_id: str) -> bool:
        """Check if a model ID is in the allowed list."""
        if model_id in self.BLOCKED_MODELS:
            return False
        # Prefix-based check for extra providers — avoids full model fetch
        for provider_name, cfg in self.EXTRA_PROVIDERS.items():
            if model_id.startswith(f"{provider_name}/"):
                settings = get_settings()
                return self._resolve_provider_cfg(cfg, settings) is not None
        if model_id.startswith("gemini/"):
            settings = get_settings()
            return bool(getattr(settings, self.GEMINI_CFG["api_key_field"], ""))
        models = await self.get_models()
        return any(m["id"] == model_id for m in models)

    def resolve_provider(self, model_id: str) -> dict | None:
        """Return provider credentials for non-OpenRouter models, None for OpenRouter.

        Used by hosted_agent_service to pass per-provider base_url and api_key
        to the runner when starting an agent with an extra-provider model.
        """
        settings = get_settings()
        for provider_name, cfg in self.EXTRA_PROVIDERS.items():
            if model_id.startswith(f"{provider_name}/"):
                resolved = self._resolve_provider_cfg(cfg, settings)
                if resolved is not None:
                    base_url, api_key = resolved
                    return {"base_url": base_url, "api_key": api_key}
        if model_id.startswith("gemini/"):
            api_key = getattr(settings, self.GEMINI_CFG["api_key_field"], "")
            if api_key:
                return {"base_url": self.GEMINI_CFG["base_url"], "api_key": api_key}
        return None

    async def resolve_model(self, model_id: str) -> str:
        """Return `model_id` if it is still runtime-reachable, otherwise the fallback.

        Extra provider models pass through unchanged (prefix-routed).
        OpenRouter blocked models fall back to FALLBACK_MODEL.
        """
        # Extra provider models — pass through unchanged, prefix-routed at call time
        for provider_name in self.EXTRA_PROVIDERS:
            if model_id.startswith(f"{provider_name}/"):
                return model_id
        if model_id.startswith("gemini/"):
            return model_id
        if model_id in self.BLOCKED_MODELS:
            logger.warning("Model {} blocked; falling back to {}", model_id, self.FALLBACK_MODEL)
            return self.FALLBACK_MODEL
        return model_id

    async def get_context_length(self, model_id: str) -> int:
        """Get context window size for a model (default 128K)."""
        extra = await self._extra_provider_models()
        for m in extra:
            if m["id"] == model_id:
                return m.get("context_length", 128_000)
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
