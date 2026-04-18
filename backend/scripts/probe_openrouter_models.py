"""Probe all OpenRouter :free models for actual tool-use availability.

Usage:
    OPENROUTER_API_KEY=sk-or-v1-... python scripts/probe_openrouter_models.py

Why: OpenRouter `supported_parameters` metadata can be wrong or incomplete,
and some models are blocked at the account level ("All providers have been
ignored"). This script issues a minimal tool-use call against every free
model and reports which actually respond with a tool_call. The output is
used to maintain `BLOCKED_MODELS` in `backend/app/services/openrouter_service.py`.
"""

import asyncio
import os
import sys

import httpx


PING_TOOL = [{
    "type": "function",
    "function": {
        "name": "ping",
        "description": "Say pong.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}]


async def fetch_free_models(client: httpx.AsyncClient) -> list[str]:
    r = await client.get("https://openrouter.ai/api/v1/models", timeout=20)
    r.raise_for_status()
    return [m["id"] for m in r.json().get("data", []) if ":free" in m.get("id", "")]


async def probe(client: httpx.AsyncClient, model: str, api_key: str) -> tuple[str, int, str, bool]:
    """Probe with tool_choice='auto' — most permissive setting that still exercises tool-use."""
    try:
        r = await client.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": model,
                "messages": [{"role": "user", "content": "Please call the ping tool."}],
                "tools": PING_TOOL,
                "tool_choice": "auto",
                "max_tokens": 80,
            },
            timeout=45,
        )
        if r.status_code != 200:
            try:
                err = r.json().get("error", {}).get("message", r.text)[:140]
            except Exception:
                err = r.text[:140]
            return (model, r.status_code, err, False)
        data = r.json()
        msg = data.get("choices", [{}])[0].get("message", {})
        tc = msg.get("tool_calls") or []
        # Provider accepted the tool-use request = "working" for our purposes,
        # even if the model declined to call the tool this turn.
        return (model, 200, f"tool_calls={len(tc)} OK", True)
    except Exception as e:
        return (model, 0, f"exc: {e}", False)


async def main() -> int:
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        print("Set OPENROUTER_API_KEY", file=sys.stderr)
        return 2
    async with httpx.AsyncClient() as client:
        models = await fetch_free_models(client)
        print(f"Probing {len(models)} free models…\n")
        results = await asyncio.gather(*(probe(client, m, api_key) for m in models))

    print(f"{'MODEL':<60} {'HTTP':>4}  TOOL  NOTES")
    working, broken = [], []
    for m, s, note, has in sorted(results, key=lambda x: (not x[3], x[0])):
        mark = "YES" if has else "no "
        print(f"{m:<60} {s:>4}  {mark:<4}  {note}")
        (working if has else broken).append(m)

    print(f"\n=== {len(working)} WORKING ===")
    for m in working:
        print(f"  {m}")
    print(f"\n=== {len(broken)} BROKEN (candidates for BLOCKED_MODELS) ===")
    for m in broken:
        print(f"  {m}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
