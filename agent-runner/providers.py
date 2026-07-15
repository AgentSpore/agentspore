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
            notes="Production default. RS eval PASS 2026-05.",
        ),
        ModelSpec(
            model_id="openai/gpt-oss-120b:free",
            tool_use=True,
            context_window=131_072,
            priority=2,
            notes="RS eval PASS 2026-05. QA eval partial (PostsBlogPost fail).",
        ),
        ModelSpec(
            model_id="deepseek/deepseek-v4-flash:free",
            tool_use=True,
            context_window=256_000,
            priority=3,
            notes="DeepSeek V4 Flash free. 256K ctx. Strong reasoning.",
        ),
        ModelSpec(
            model_id="qwen/qwen3-coder:free",
            tool_use=True,
            context_window=262_000,
            priority=4,
            notes="Qwen 3 Coder free. Excellent code generation.",
        ),
        ModelSpec(
            model_id="meta-llama/llama-3.3-70b-instruct:free",
            tool_use=True,
            context_window=65_536,
            priority=5,
            notes="Llama 3.3 70B free via OpenRouter.",
        ),
        ModelSpec(
            model_id="minimax/minimax-m2.5:free",
            tool_use=True,
            context_window=196_608,
            priority=6,
            notes="MiniMax M2.5 free. Large ctx.",
        ),
        ModelSpec(
            model_id="google/gemma-4-31b-it:free",
            tool_use=True,
            context_window=262_144,
            priority=7,
            notes="Gemma 4 31B instruct, Google-hosted.",
        ),
        ModelSpec(
            model_id="nvidia/nemotron-3-nano-30b-a3b:free",
            tool_use=True,
            context_window=256_000,
            priority=8,
            notes="Lighter NVIDIA fallback.",
        ),
        ModelSpec(
            model_id="openai/gpt-oss-20b:free",
            tool_use=True,
            context_window=131_072,
            priority=9,
            notes="Lightweight OpenAI oss fallback.",
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
            model_id="openai/gpt-oss-120b",
            tool_use=True,
            context_window=131_072,
            priority=1,
            notes="OpenAI OSS 120B on Groq ultra-fast inference. Verified 2026-05.",
        ),
        ModelSpec(
            model_id="llama-3.3-70b-versatile",
            tool_use=True,
            context_window=128_000,
            priority=2,
            notes="Groq Llama 3.3 70B. ~14400 req/day free. ~300 t/s.",
        ),
        ModelSpec(
            model_id="qwen/qwen3-32b",
            tool_use=True,
            context_window=131_072,
            priority=3,
            notes="Qwen 3 32B on Groq. Strong reasoning + tool_use.",
        ),
        ModelSpec(
            model_id="meta-llama/llama-4-scout-17b-16e-instruct",
            tool_use=True,
            context_window=131_072,
            priority=4,
            notes="Llama 4 Scout 17B MoE on Groq. Fast, efficient.",
        ),
        ModelSpec(
            model_id="llama-3.1-8b-instant",
            tool_use=True,
            context_window=128_000,
            priority=5,
            notes="Llama 3.1 8B instant. Lightweight fallback.",
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
            model_id="qwen-3-235b-a22b-instruct-2507",
            tool_use=True,
            context_window=131_072,
            priority=1,
            notes="Qwen 3 235B MoE on Cerebras. Fast inference. Verified content=OK 2026-05.",
        ),
        ModelSpec(
            model_id="llama3.1-8b",
            tool_use=False,
            context_window=128_000,
            priority=2,
            notes="Llama 3.1 8B. Too small for tool_use in practice.",
        ),
        ModelSpec(
            model_id="gpt-oss-120b",
            tool_use=False,
            context_window=131_072,
            priority=99,
            notes="THINKING-ONLY: content=None, reasoning field only. Breaks agent. Excluded from chain.",
        ),
        ModelSpec(
            model_id="zai-glm-4.7",
            tool_use=False,
            context_window=131_072,
            priority=99,
            notes="THINKING-ONLY: content=None, reasoning field only. Breaks agent. Excluded from chain.",
        ),
    ],
)

# Mistral AI — free tier on open models, good instruction following.
# Key: https://console.mistral.ai  (free tier with rate limits).
# Tool use: supported on mistral-small and above.
MISTRAL = Provider(
    name="mistral",
    base_url="https://api.mistral.ai/v1",
    api_key_env="MISTRAL_API_KEY",
    models=[
        ModelSpec(
            model_id="mistral-small-latest",
            tool_use=True,
            context_window=32_000,
            priority=1,
            notes="Mistral Small, free tier rate-limited. Good tool_use.",
        ),
        ModelSpec(
            model_id="open-mixtral-8x22b",
            tool_use=True,
            context_window=65_536,
            priority=2,
            notes="Mixtral 8x22B open weights. Not in /models list but chat still works 2026-05.",
        ),
        ModelSpec(
            model_id="open-mistral-nemo",
            tool_use=False,
            context_window=128_000,
            priority=3,
            notes="Mistral Nemo 7B, 128K ctx. Replaces open-mistral-7b (removed from API 2026-05).",
        ),
    ],
)

# Nebius AI Studio — hosts open-source models, generous free tier.
# Key: https://studio.nebius.ai  (no credit card on free plan).
# Tool use: supported on Llama and Qwen models.
NEBIUS = Provider(
    name="nebius",
    base_url="https://api.studio.nebius.ai/v1",
    api_key_env="NEBIUS_API_KEY",
    models=[
        ModelSpec(
            model_id="NousResearch/Hermes-4-70B",
            tool_use=True,
            context_window=131_072,
            priority=1,
            notes="Hermes 4 70B — best-in-class tool_use. Verified 2026-05.",
        ),
        ModelSpec(
            model_id="NousResearch/Hermes-4-405B",
            tool_use=True,
            context_window=131_072,
            priority=2,
            notes="Hermes 4 405B — largest Hermes. Superior reasoning.",
        ),
        ModelSpec(
            model_id="deepseek-ai/DeepSeek-V3.2",
            tool_use=True,
            context_window=131_072,
            priority=3,
            notes="DeepSeek V3.2 on Nebius. content=OK verified 2026-05.",
        ),
        ModelSpec(
            model_id="nvidia/Llama-3_1-Nemotron-Ultra-253B-v1",
            tool_use=True,
            context_window=128_000,
            priority=4,
            notes="Nemotron Ultra 253B on Nebius. Very capable.",
        ),
        ModelSpec(
            model_id="Qwen/Qwen3-32B",
            tool_use=True,
            context_window=32_000,
            priority=5,
            notes="Qwen 3 32B on Nebius.",
        ),
        ModelSpec(
            model_id="meta-llama/Llama-3.3-70B-Instruct",
            tool_use=False,
            context_window=128_000,
            priority=6,
            notes="Llama 3.3 70B on Nebius. 0 tool calls observed 2026-05.",
        ),
        ModelSpec(
            model_id="meta-llama/Meta-Llama-3.1-8B-Instruct",
            tool_use=False,
            context_window=128_000,
            priority=7,
            notes="Llama 3.1 8B lightweight fallback. No reliable tool_use.",
        ),
        ModelSpec(
            model_id="openai/gpt-oss-120b",
            tool_use=False,
            context_window=131_072,
            priority=99,
            notes="THINKING-ONLY: content=None, reasoning_content only. Breaks agent. Excluded from chain.",
        ),
        ModelSpec(
            model_id="Qwen/Qwen3.5-397B-A17B",
            tool_use=False,
            context_window=131_072,
            priority=99,
            notes="THINKING-ONLY: content=None verified 2026-05. Excluded from chain.",
        ),
    ],
)

# SambaNova Cloud — high-throughput inference for open-source models.
# Key: https://cloud.sambanova.ai  (free tier available).
# Tool use: supported on Llama 3.3 70B and Llama 4; NOT on DeepSeek.
SAMBANOVA = Provider(
    name="sambanova",
    base_url="https://api.sambanova.ai/v1",
    api_key_env="SAMBANOVA_API_KEY",
    models=[
        ModelSpec(
            model_id="Meta-Llama-3.3-70B-Instruct",
            tool_use=True,
            context_window=131_072,
            priority=1,
            notes="Llama 3.3 70B on SambaNova. High-throughput, tool_use supported.",
        ),
        ModelSpec(
            model_id="Llama-4-Maverick-17B-128E-Instruct",
            tool_use=True,
            context_window=131_072,
            priority=2,
            notes="Llama 4 Maverick 17B MoE on SambaNova.",
        ),
        ModelSpec(
            model_id="MiniMax-M2.5",
            tool_use=True,
            context_window=131_072,
            priority=3,
            notes="MiniMax M2.5 on SambaNova. Listed in /models 2026-05, unverified tool_use.",
        ),
        ModelSpec(
            model_id="MiniMax-M2.7",
            tool_use=True,
            context_window=131_072,
            priority=4,
            notes="MiniMax M2.7 on SambaNova. Listed in /models 2026-05, unverified tool_use.",
        ),
        ModelSpec(
            model_id="gemma-3-12b-it",
            tool_use=True,
            context_window=131_072,
            priority=5,
            notes="Gemma 3 12B on SambaNova.",
        ),
        ModelSpec(
            model_id="DeepSeek-V3.1",
            tool_use=False,
            context_window=131_072,
            priority=6,
            notes="DeepSeek V3.1 on SambaNova. No tool_use.",
        ),
        ModelSpec(
            model_id="DeepSeek-V3.2",
            tool_use=False,
            context_window=131_072,
            priority=7,
            notes="DeepSeek V3.2 on SambaNova. Listed in /models 2026-05, no tool_use expected.",
        ),
        ModelSpec(
            model_id="gpt-oss-120b",
            tool_use=False,
            context_window=131_072,
            priority=99,
            notes="Likely thinking-only (pattern from Cerebras/Nebius). Verify before enabling.",
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

# Z.AI — first-party GLM API, OpenAI-compatible.
# Key: https://z.ai  (ZAI_API_KEY).
# The only provider still reachable from our hosts: OpenRouter, OpenAI, Groq,
# Anthropic, Gemini, Cerebras and Nebius all answer HTTP 403 "Access denied by
# security policy" to Russian ASNs (verified 2026-07-15).
#
# Free-tier reality, measured live with the production key on 2026-07-15 — this
# overrides Z.AI's published price list:
#   - glm-4.5-flash  → HTTP 200, 10/10 sequential calls, correct output.
#                      THE ONLY RELIABLY-FREE MODEL.
#   - glm-4.7-flash  → HTTP 429 code 1302 (request rate limit), then a timeout
#                      on retry. Free on paper, not dependable → priority 2.
#   - glm-4.6v-flash → HTTP 429 code 1305 (temporarily overloaded); vision-only.
#   - glm-4.5 / -air / 4.6 / 4.7 / 5 / 5-turbo / 5.1 / 5.2
#                    → HTTP 429 code 1113 "insufficient balance" = paid tier,
#                      account balance is zero → excluded entirely.
# Concurrency ceiling ~3 in-flight requests (6 parallel → 3×200, 3×429/1302).
ZAI = Provider(
    name="zai",
    base_url="https://api.z.ai/api/paas/v4",
    api_key_env="ZAI_API_KEY",
    models=[
        ModelSpec(
            model_id="glm-4.5-flash",
            tool_use=True,
            context_window=128_000,
            priority=1,
            notes="Only measured-reliable free GLM. ~3 concurrent max; 429/1302 above that.",
        ),
        ModelSpec(
            model_id="glm-4.7-flash",
            tool_use=True,
            context_window=128_000,
            priority=2,
            notes="Free per price list but rate-limits (429/1302) and times out on retry.",
        ),
    ],
)

# Canonical registry — order matters: used for default chain construction.
ALL_PROVIDERS: list[Provider] = [
    OPENROUTER,
    NVIDIA_NIM,
    GROQ,
    CEREBRAS,
    MISTRAL,
    NEBIUS,
    TOGETHER,
    SAMBANOVA,
    ZAI,
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
      1. Z.AI (if key available) — the only provider reachable from our hosts
      2. NVIDIA NIM (if key available) — direct, no OpenRouter margin
      3. OpenRouter free tier — existing production chain
      4. Groq (if key available) — ultra-fast fallback
      5. Cerebras (if key available) — highest raw throughput
      6. Together (if key available) — extra coverage

    Only includes chat-capable models (tool_use check excluded embeddings).
    """
    chain: list[tuple[str, str]] = []

    # Z.AI first if key present: every other provider below geo-blocks our hosts
    # with HTTP 403 (verified 2026-07-15), so a reachable model must lead.
    zai = PROVIDER_BY_NAME["zai"]
    if zai.is_active:
        for m in sorted(zai.models, key=lambda x: x.priority):
            if m.tool_use:
                chain.append(("zai", m.model_id))

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

    # Mistral
    mistral = PROVIDER_BY_NAME["mistral"]
    if mistral.is_active:
        for m in sorted(mistral.models, key=lambda x: x.priority):
            if m.tool_use:
                chain.append(("mistral", m.model_id))

    # Nebius
    nebius = PROVIDER_BY_NAME["nebius"]
    if nebius.is_active:
        for m in sorted(nebius.models, key=lambda x: x.priority):
            if m.tool_use:
                chain.append(("nebius", m.model_id))

    # Together
    together = PROVIDER_BY_NAME["together"]
    if together.is_active:
        for m in sorted(together.models, key=lambda x: x.priority):
            if m.tool_use:
                chain.append(("together", m.model_id))

    # SambaNova
    sambanova = PROVIDER_BY_NAME["sambanova"]
    if sambanova.is_active:
        for m in sorted(sambanova.models, key=lambda x: x.priority):
            if m.tool_use:
                chain.append(("sambanova", m.model_id))

    return chain


def active_providers() -> list[Provider]:
    """Return all providers that have a key set in the environment."""
    return [p for p in ALL_PROVIDERS if p.is_active]
