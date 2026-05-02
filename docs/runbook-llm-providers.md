# Runbook: LLM Provider Configuration

Agent runner supports multiple LLM providers via an OpenAI-compatible interface.
Providers are registered in `agent-runner/providers.py` and consumed by
`agent-runner/llm_fallback.py`. Pydantic-deep agent instances use OpenRouter as
the primary base URL at startup; `call_with_fallback` and the health checker use
all configured providers.

---

## Supported providers (2026-05)

| Provider | Env var | Free tier | Tool use | Signup |
|---|---|---|---|---|
| **openrouter** | `OPENAI_API_KEY` | Yes — `:free` suffix models | Yes | openrouter.ai |
| **nvidia** | `NVIDIA_API_KEY` | Yes — ~1000 req/day/model | Yes | build.nvidia.com |
| **groq** | `GROQ_API_KEY` | Yes — ~14400 req/day | Yes | console.groq.com |
| **cerebras** | `CEREBRAS_API_KEY` | Yes — ~1M tokens/day | Yes | cloud.cerebras.ai |
| **together** | `TOGETHER_API_KEY` | $1 credit on signup | Yes | api.together.xyz |

All providers use the OpenAI tool_calls format (JSON schema functions).
No credit card is required for any provider on the free tier.

### NVIDIA NIM — key requirement

NVIDIA NIM does NOT have a public no-auth demo endpoint for production models.
`NVIDIA_API_KEY` is required for all listed models. Free signup at
`https://build.nvidia.com` (no credit card, no expiry on free credits as of 2026-05).
Free tier: approximately 1000 requests/day per model.

---

## Free-tier model limits (2026-05 verified)

| Provider | Model | Context | Rate limit | Notes |
|---|---|---|---|---|
| openrouter | nvidia/nemotron-3-super-120b-a12b:free | 262K | ~20 req/min | Production default |
| openrouter | openai/gpt-oss-120b:free | 131K | ~20 req/min | Reliable tool_use |
| openrouter | google/gemma-4-31b-it:free | 262K | ~10 req/min | Google-hosted |
| nvidia | nvidia/llama-3.1-nemotron-70b-instruct | 128K | ~1000 req/day | Best NIM model |
| nvidia | meta/llama-3.3-70b-instruct | 128K | ~1000 req/day | Meta on NVIDIA |
| nvidia | mistralai/mixtral-8x22b-instruct-v0.1 | 65K | ~1000 req/day | MoE |
| groq | llama-3.3-70b-versatile | 128K | 14400 req/day | ~300 t/s |
| groq | llama-3.1-70b-versatile | 128K | 14400 req/day | |
| cerebras | llama3.1-70b | 128K | ~1M tokens/day | Fastest inference |
| cerebras | llama3.1-8b | 128K | ~1M tokens/day | |
| together | meta-llama/Llama-3.3-70B-Instruct-Turbo | 128K | $1 credit | |

---

## Default fallback chain

When `LLM_FALLBACK_CHAIN` is unset, the chain is built automatically based on
which provider keys are present. Order:

1. NVIDIA NIM (if `NVIDIA_API_KEY` set) — direct inference, no OpenRouter margin
2. OpenRouter free models — production fallback (always included)
3. Groq (if `GROQ_API_KEY` set) — ultra-fast inference
4. Cerebras (if `CEREBRAS_API_KEY` set) — highest raw throughput
5. Together (if `TOGETHER_API_KEY` set) — extra coverage

With only `OPENAI_API_KEY` set (minimal config), the chain is the 6-model
OpenRouter chain that was in production before this change.

---

## How to switch the default chain

Set `LLM_FALLBACK_CHAIN` as a comma-separated list of `provider:model_id` entries.
Bare model IDs (no provider prefix) are treated as OpenRouter for backward compat.

Examples:

```bash
# NVIDIA first, then OpenRouter:
LLM_FALLBACK_CHAIN="nvidia:nvidia/llama-3.1-nemotron-70b-instruct,openrouter:nvidia/nemotron-3-super-120b-a12b:free,openrouter:openai/gpt-oss-120b:free"

# Groq only (fast):
LLM_FALLBACK_CHAIN="groq:llama-3.3-70b-versatile,groq:llama-3.1-70b-versatile"

# Legacy bare model IDs (OpenRouter, backward compat):
LLM_FALLBACK_CHAIN="nvidia/nemotron-3-super-120b-a12b:free,openai/gpt-oss-120b:free"
```

The agent startup model (`pydantic-deep create_deep_agent`) always uses OpenRouter
via `OPENAI_BASE_URL`; `LLM_FALLBACK_CHAIN` controls which model is selected from
that provider's list. Direct NVIDIA/Groq/Cerebras inference is used by
`call_with_fallback` (health checks, raw LLM calls outside pydantic-deep).

---

## How to add a new provider

1. Add a `Provider` instance to `agent-runner/providers.py`:

```python
MY_PROVIDER = Provider(
    name="myprovider",               # short key used in chain entries
    base_url="https://api.example.com/v1",
    api_key_env="MY_PROVIDER_KEY",   # env var name, no default
    models=[
        ModelSpec(
            model_id="org/model-name",
            tool_use=True,
            context_window=128_000,
            priority=1,
            notes="Free tier, 10K req/day.",
        ),
    ],
)
```

2. Add it to `ALL_PROVIDERS` list in `providers.py`:

```python
ALL_PROVIDERS: list[Provider] = [
    OPENROUTER,
    NVIDIA_NIM,
    GROQ,
    CEREBRAS,
    TOGETHER,
    MY_PROVIDER,   # add here
]
```

3. If you want it in the auto-built default chain, add a block in
   `build_default_chain()` in `providers.py` following the existing pattern.

4. Add `MY_PROVIDER_KEY` to `.env` and `docker-compose.yml` environment section.

5. Run `GET /admin/llm-health` to verify the new provider responds.

---

## How to verify providers are live

```bash
# From inside the server (requires RUNNER_KEY):
curl -H "X-Runner-Key: $RUNNER_KEY" http://localhost:8100/admin/llm-health | jq .
```

Sample response:
```json
{
  "chain_length": 8,
  "ok_count": 6,
  "results": [
    {"provider": "nvidia", "model": "nvidia/llama-3.1-nemotron-70b-instruct", "status": "ok", "latency_ms": 1234, "error": null},
    {"provider": "openrouter", "model": "nvidia/nemotron-3-super-120b-a12b:free", "status": "ok", "latency_ms": 800, "error": null},
    {"provider": "groq", "model": "llama-3.3-70b-versatile", "status": "skipped", "latency_ms": 0, "error": "No API key (GROQ_API_KEY not set)"},
    ...
  ]
}
```

`status` values:
- `ok` — model responded successfully
- `error` — provider returned an error (see `error` field)
- `timeout` — request timed out (default 20s probe timeout)
- `skipped` — provider's API key env var is not set

---

## Env vars reference

| Var | Required | Description |
|---|---|---|
| `OPENAI_API_KEY` | Yes | OpenRouter API key. Primary provider. |
| `OPENAI_BASE_URL` | No | Defaults to `https://openrouter.ai/api/v1`. |
| `NVIDIA_API_KEY` | No | NVIDIA NIM key. Activates nvidia provider. |
| `GROQ_API_KEY` | No | Groq key. Activates groq provider. |
| `CEREBRAS_API_KEY` | No | Cerebras key. Activates cerebras provider. |
| `TOGETHER_API_KEY` | No | Together AI key. Activates together provider. |
| `LLM_FALLBACK_CHAIN` | No | Override auto-built chain. Comma-separated `provider:model` entries. |

API keys are never logged. The health endpoint reports `skipped` (not the key
value) when a key is absent.
