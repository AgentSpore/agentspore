"""LLM provider registry for agent-runner.

Defines all supported LLM providers (OpenRouter, NVIDIA NIM, Groq,
Cerebras, Together) with their free-tier models and capabilities.
Consumed by llm_fallback.py for multi-provider fallback chains.

Provider selection:
  - A provider is active when its api_key_env var is set (non-empty).
  - NVIDIA NIM: set NVIDIA_API_KEY. No key → provider skipped.
  - Groq: set GROQ_API_KEY.
  - Cerebras: set CEREBRAS_API_KEY.
  - Together: set TOGETHER_API_KEY.
  - OpenRouter: set OPENAI_API_KEY (existing config, always present).

Free-tier notes (verified 2026-05):
  - OpenRouter: free models suffixed :free, rate-limited by model.
  - NVIDIA NIM: NVIDIA_API_KEY free at build.nvidia.com, no credit card.
    ~1000 requests/day on most models. Supports tool_use (OpenAI compat).
  - Groq: free tier ~14400 req/day (Llama 3.3 70B), tool_use supported.
  - Cerebras: free tier ~1M tokens/day on Llama 3.1 70B, ~3x faster than
    GPU inference, tool_use supported.
  - Together: free tier for select models, tool_use on Llama/Mistral.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ModelSpec:
    """Metadata for a single model on a provider.

    Args:
        model_id:       Model identifier as sent in the API request body.
        tool_use:       Whether the model supports OpenAI-style tool_calls.
        context_window: Context window in tokens.
        priority:       Lower = preferred. Used to sort within a provider.
        notes:          Human-readable info (rate limits, caveats).
    """

    model_id: str
    tool_use: bool = True
    context_window: int = 128_000
    priority: int = 10
    notes: str = ""


@dataclass
class Provider:
    """Describes an LLM provider accessible via an OpenAI-compatible API.

    Args:
        name:        Short identifier used in config (e.g. ``nvidia``).
        base_url:    Base URL for chat completions.
        api_key_env: Environment variable name holding the API key.
                     If the variable is unset or empty, the provider is
                     inactive and excluded from the fallback chain.
        models:      Ordered list of free/cheap models, lowest priority first.
    """

    name: str
    base_url: str
    api_key_env: str
    models: list[ModelSpec] = field(default_factory=list)

    @property
    def api_key(self) -> str:
        """Return the API key from the environment. Empty string if unset."""
        return os.environ.get(self.api_key_env, "")

    @property
    def is_active(self) -> bool:
        """True when the provider's API key environment variable is set."""
        return bool(self.api_key)

    def get_model(self, model_id: str) -> ModelSpec | None:
        """Look up a ModelSpec by model_id."""
        for m in self.models:
            if m.model_id == model_id:
                return m
        return None


# ---------------------------------------------------------------------------
# Built-in provider definitions
# ---------------------------------------------------------------------------

OPENROUTER = Provider(
    name="openrouter",
    base_url="https://openrouter.ai/api/v1",
    api_key_env="OPENAI_API_KEY",
    models=[
        ModelSpec(
            model_id="nvidia/nemotron-3-super-120b-a12b:free",
            tool_use=True,
            context_window=262_144,
            priority=1,
            notes="Production default. Verified working 2026-05.",
        ),
        ModelSpec(
            model_id="openai/gpt-oss-120b:free",
            tool_use=True,
            context_window=131_072,
            priority=2,
            notes="OpenAI oss 120B via OpenRouter. Reliable tool_use.",
        ),
        ModelSpec(
            model_id="google/gemma-4-31b-it:free",
            tool_use=True,
            context_window=262_144,
            priority=3,
            notes="Gemma 4 31B instruct, Google-hosted.",
        ),
        ModelSpec(
            model_id="google/gemma-4-26b-a4b-it:free",
            tool_use=True,
            context_window=262_144,
            priority=4,
            notes="Gemma 4 MoE variant.",
        ),
        ModelSpec(
            model_id="nvidia/nemotron-3-nano-30b-a3b:free",
            tool_use=True,
            context_window=256_000,
            priority=5,
            notes="Lighter NVIDIA fallback.",
        ),
        ModelSpec(
            model_id="openai/gpt-oss-20b:free",
            tool_use=True,
            context_window=131_072,
            priority=6,
            notes="Lighter OpenAI oss fallback.",
        ),
    ],
)

# NVIDIA NIM — OpenAI-compatible endpoint, requires NVIDIA_API_KEY.
# Free signup: https://build.nvidia.com  (no credit card).
# Rate limit: ~1000 req/day per model on the free tier.
# Tool use: full OpenAI tool_calls format, same as OpenAI API.
# Note: NVIDIA NIM does NOT have a public no-auth demo endpoint for
# production models. NVIDIA_API_KEY is required for all listed models.
NVIDIA_NIM = Provider(
    name="nvidia",
    base_url="https://integrate.api.nvidia.com/v1",
    api_key_env="NVIDIA_API_KEY",
    models=[
        ModelSpec(
            model_id="nvidia/llama-3.1-nemotron-70b-instruct",
            tool_use=True,
            context_window=128_000,
            priority=1,
            notes="Best NVIDIA NIM free model. Verified tool_use 2026-05.",
        ),
        ModelSpec(
            model_id="meta/llama-3.3-70b-instruct",
            tool_use=True,
            context_window=128_000,
            priority=2,
            notes="Meta Llama 3.3 70B on NVIDIA infra.",
        ),
        ModelSpec(
            model_id="mistralai/mixtral-8x22b-instruct-v0.1",
            tool_use=True,
            context_window=65_536,
            priority=3,
            notes="Mixtral MoE, good coding tasks.",
        ),
        ModelSpec(
            model_id="qwen/qwen2.5-coder-32b-instruct",
            tool_use=True,
            context_window=32_768,
            priority=4,
            notes="Qwen 2.5 Coder, strong for code gen.",
        ),
        ModelSpec(
            model_id="nvidia/llama-3.2-nv-embedqa-1b-v2",
            tool_use=False,
            context_window=512,
            priority=99,
            notes="Embeddings only — NOT a chat model. Excluded from fallback.",
        ),
    ],
)

# Groq — ultra-fast inference, free tier ~14,400 req/day on Llama 3.3 70B.
# Key: https://console.groq.com  (no credit card on free plan).
# Tool use: supported on Llama 3.x and Gemma models.
GROQ = Provider(
    name="groq",
    base_url="https://api.groq.com/openai/v1",
    api_key_env="GROQ_API_KEY",
    models=[
        ModelSpec(
            model_id="llama-3.3-70b-versatile",
            tool_use=True,
            context_window=128_000,
            priority=1,
            notes="Groq flagship. ~14400 req/day free. Very fast (~300 t/s).",
        ),
        ModelSpec(
            model_id="llama-3.1-70b-versatile",
            tool_use=True,
            context_window=128_000,
            priority=2,
            notes="Llama 3.1 70B on Groq.",
        ),
        ModelSpec(
            model_id="gemma2-9b-it",
            tool_use=True,
            context_window=8_192,
            priority=3,
            notes="Gemma 2 9B. Fast, small ctx.",
        ),
        ModelSpec(
            model_id="llama-3.2-90b-vision-preview",
            tool_use=True,
            context_window=8_192,
            priority=4,
            notes="Llama 3.2 90B vision, limited ctx on free.",
        ),
    ],
)

# Cerebras — wafer-scale inference, free tier ~1M tokens/day.
# Fastest raw token throughput available (~2000+ t/s on small models).
# Key: https://cloud.cerebras.ai  (no credit card).
# Tool use: supported on Llama 3.1.
CEREBRAS = Provider(
    name="cerebras",
    base_url="https://api.cerebras.ai/v1",
    api_key_env="CEREBRAS_API_KEY",
    models=[
        ModelSpec(
            model_id="llama3.1-70b",
            tool_use=True,
            context_window=128_000,
            priority=1,
            notes="Cerebras Llama 3.1 70B. ~1M tokens/day free. Fastest inference.",
        ),
        ModelSpec(
            model_id="llama3.1-8b",
            tool_use=True,
            context_window=128_000,
            priority=2,
            notes="Llama 3.1 8B. Even faster, lighter.",
        ),
    ],
)

# Together AI — free tier on select models, good for large ctx.
# Key: https://api.together.xyz  ($1 free credit on signup).
# Tool use: supported on Llama 3.x and Mistral models.
TOGETHER = Provider(
    name="together",
    base_url="https://api.together.xyz/v1",
    api_key_env="TOGETHER_API_KEY",
    models=[
        ModelSpec(
            model_id="meta-llama/Llama-3.3-70B-Instruct-Turbo",
            tool_use=True,
            context_window=128_000,
            priority=1,
            notes="Together Llama 3.3 70B turbo. $1 credit = hundreds of calls.",
        ),
        ModelSpec(
            model_id="mistralai/Mixtral-8x22B-Instruct-v0.1",
            tool_use=True,
            context_window=65_536,
            priority=2,
            notes="Mixtral 8x22B on Together.",
        ),
        ModelSpec(
            model_id="Qwen/Qwen2.5-Coder-32B-Instruct",
            tool_use=True,
            context_window=32_768,
            priority=3,
            notes="Qwen 2.5 Coder on Together.",
        ),
    ],
)

# Canonical registry — order matters: used for default chain construction.
ALL_PROVIDERS: list[Provider] = [
    OPENROUTER,
    NVIDIA_NIM,
    GROQ,
    CEREBRAS,
    TOGETHER,
]

# Map name → Provider for O(1) lookup.
PROVIDER_BY_NAME: dict[str, Provider] = {p.name: p for p in ALL_PROVIDERS}


# ---------------------------------------------------------------------------
# Chain parsing helpers
# ---------------------------------------------------------------------------

def parse_chain_entry(entry: str) -> tuple[str, str]:
    """Parse a ``provider:model`` or bare ``model`` chain entry.

    Bare model IDs (no colon, or a model ID that itself contains colons
    like ``nvidia/nemotron-3-super-120b-a12b:free``) are treated as OpenRouter
    models for backward compatibility with the old single-provider format.

    Returns:
        (provider_name, model_id) tuple.
    """
    # Detect explicit provider prefix: first segment before the first colon,
    # if it matches a known provider name.
    if ":" in entry:
        candidate_provider, rest = entry.split(":", 1)
        if candidate_provider in PROVIDER_BY_NAME:
            return candidate_provider, rest
    # Legacy bare model id (e.g. "nvidia/nemotron-3-super-120b-a12b:free")
    return "openrouter", entry


def build_default_chain() -> list[tuple[str, str]]:
    """Build the default multi-provider fallback chain.

    Priority:
      1. NVIDIA NIM primary (if key available) — direct, no OpenRouter margin
      2. OpenRouter free tier — existing production chain
      3. Groq (if key available) — ultra-fast fallback
      4. Cerebras (if key available) — highest raw throughput
      5. Together (if key available) — extra coverage

    Only includes chat-capable models (tool_use check excluded embeddings).
    """
    chain: list[tuple[str, str]] = []

    # NVIDIA NIM first if key present
    nvidia = PROVIDER_BY_NAME["nvidia"]
    if nvidia.is_active:
        for m in sorted(nvidia.models, key=lambda x: x.priority):
            if m.tool_use and m.priority < 90:  # skip embeddings
                chain.append(("nvidia", m.model_id))

    # OpenRouter free models
    for m in sorted(OPENROUTER.models, key=lambda x: x.priority):
        chain.append(("openrouter", m.model_id))

    # Groq
    groq = PROVIDER_BY_NAME["groq"]
    if groq.is_active:
        for m in sorted(groq.models, key=lambda x: x.priority):
            if m.tool_use:
                chain.append(("groq", m.model_id))

    # Cerebras
    cerebras = PROVIDER_BY_NAME["cerebras"]
    if cerebras.is_active:
        for m in sorted(cerebras.models, key=lambda x: x.priority):
            if m.tool_use:
                chain.append(("cerebras", m.model_id))

    # Together
    together = PROVIDER_BY_NAME["together"]
    if together.is_active:
        for m in sorted(together.models, key=lambda x: x.priority):
            if m.tool_use:
                chain.append(("together", m.model_id))

    return chain


def active_providers() -> list[Provider]:
    """Return all providers that have a key set in the environment."""
    return [p for p in ALL_PROVIDERS if p.is_active]
