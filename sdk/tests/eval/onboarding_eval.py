"""Onboarding eval runner: fresh local stand per attempt, models x attempts, JSON per attempt plus summary.md.

Never targets production: the only reachable origin is the stand started here. Run::

    cd sdk && uv run --no-project --with httpx python tests/eval/onboarding_eval.py \\
      --source <repo> --backend-python <repo>/backend/.venv/bin/python \\
      --docker-host unix://$HOME/.colima/default/docker.sock \\
      --key-file <path-to-openrouter-key-file> --out-dir <results>/onboarding-<date> \\
      --models google/gemini-3-flash-preview,google/gemini-2.5-flash-lite
"""

import argparse
import asyncio
import json
import subprocess
import sys
import tempfile
from collections import Counter
from functools import partial
from pathlib import Path
from typing import Any

import httpx
from onboarding_agent import OPENROUTER, chat, run_attempt
from onboarding_metrics import STEPS, summarize

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "integration"))
from user_stand import OwnedStand  # noqa: E402 (sys.path entry for tests/integration is added on the line above)  # ty: ignore[import-not-found]


async def credits(llm: httpx.AsyncClient) -> float:
    """OpenRouter account usage so far, in USD."""
    data = (await llm.get(OPENROUTER + "/credits")).json()["data"]
    return float(data["total_usage"])


def stand_error_result(model: str, error: Exception) -> dict[str, Any]:
    """An attempt that never started: infrastructure, not an agent outcome."""
    steps = dict.fromkeys(STEPS, False)
    return {
        "model": model, "outcome": "infra_error", "ended_by": "stand_error", "infra_detail": f"stand: {error}",
        "first_heartbeat": None, "steps": steps, "turns": 0, "cost": 0.0, "errors": [], "off_origin": 0,
        "prod_url_rewritten": 0, "statuses": {}, "final_message": "", "transcript": [],
    }  # fmt: skip


async def close_stand(stand: Any) -> str | None:
    """Remove the stand's own resources; a failing cleanup must not lose the attempt result."""
    try:
        await stand.close()
    except (RuntimeError, OSError) as error:  # shared Docker: a foreign container vanishing fails the baseline check
        return str(error)
    return None


async def one_attempt(
    options: argparse.Namespace, model: str, llm: httpx.AsyncClient, stand_factory: Any = OwnedStand
) -> dict[str, Any]:
    """Start a fresh stand, run one agent against it, always remove the stand's own resources."""
    stand = stand_factory(options.source, options.backend_python, options.docker_host, options.recovery_dir)
    try:
        try:
            await stand.start()
        except (RuntimeError, OSError, subprocess.TimeoutExpired) as error:
            result = stand_error_result(model, error)
        else:
            async with httpx.AsyncClient(timeout=30, trust_env=False) as stand_http:
                result = await run_attempt(model, stand.origin, partial(chat, llm), stand_http)
    finally:
        cleanup_error = await close_stand(stand)
    return {**result, "cleanup_error": cleanup_error}


def render_summary(
    attempts: list[dict[str, Any]], spend: float, stopped: str | None, spend_label: str = "Total spend"
) -> str:
    """Markdown report: per-model table, ceilings, error responses the agents hit."""
    lines = [
        "| model | success | median turns | median tokens | median s | infra_error | spend USD |",
        "|---|---|---|---|---|---|---|",
    ]
    for model, s in summarize(attempts).items():
        lines.append(
            f"| {model} | {s['success']}/{s['counted']} | {s['median_turns']} | {s['median_tokens']} "
            f"| {s['median_seconds'] and round(s['median_seconds'])} | {s['infra_error']} | {s['spend']:.3f} |"
        )
    ends = Counter((a["model"], a["outcome"], a["ended_by"]) for a in attempts)
    errors = Counter(f"{e['method']} {e['path']} -> {e['status']}" for a in attempts for e in a["errors"])
    lines += ["", f"{spend_label}: {spend:.3f} USD" + (f" (stopped: {stopped})" if stopped else ""), "", "Endings:"]
    lines += [f"- {m} / {o} / {b}: {n}" for (m, o, b), n in sorted(ends.items())]
    lines += ["", f"Off-origin attempts: {sum(a['off_origin'] for a in attempts)}"]
    lines += [
        f"prod_url_rewritten (informational): {sum(a['prod_url_rewritten'] for a in attempts)}",
        "",
        "Top error responses:",
    ]
    return "\n".join(lines + [f"- {k}: {n}" for k, n in errors.most_common(10)]) + "\n"


async def spent_so_far(llm: httpx.AsyncClient, begin: float, local: float) -> float:
    """Account spend since the start; when /credits is unreachable, the measured attempt costs."""
    try:
        return max(await credits(llm) - begin, local)
    except (httpx.HTTPError, KeyError, ValueError) as error:
        print("credits unavailable, budget check uses measured costs:", type(error).__name__, flush=True)
        return local


async def run_all(
    options: argparse.Namespace, llm: httpx.AsyncClient, attempts: list[dict], begin: float
) -> str | None:
    """Run models x attempts sequentially; return the reason when the budget ends the whole run."""
    for model in options.models:
        for n in range(1, options.attempts + 1):
            reserve = max([a["cost"] for a in attempts] + [options.reserve])
            local = sum(a["cost"] for a in attempts)
            if await spent_so_far(llm, begin, local) + reserve > options.budget:
                return f"budget {options.budget} USD would be exceeded before {model} #{n}"
            result = await one_attempt(options, model, llm)
            attempts.append(result)
            name = f"{model.replace('/', '_')}-{n}.json"
            (options.out_dir / name).write_text(json.dumps(result, indent=2, ensure_ascii=False))
            print(model, n, result["outcome"], result["turns"], round(result["cost"], 4), flush=True)
    return None


async def main_async(options: argparse.Namespace) -> None:
    key = options.key_file.expanduser().read_text().strip()
    options.out_dir.mkdir(parents=True, exist_ok=True)
    attempts: list[dict[str, Any]] = []
    stopped = None
    async with httpx.AsyncClient(headers={"Authorization": "Bearer " + key}, timeout=120, trust_env=False) as llm:
        begin = await credits(llm)
        try:
            stopped = await run_all(options, llm, attempts, begin)
        except BaseException as error:
            stopped = f"aborted: {type(error).__name__}"
            raise
        finally:
            label = "Total spend"
            try:
                spend = await credits(llm) - begin
            except (httpx.HTTPError, KeyError, ValueError):
                spend = sum(a["cost"] for a in attempts)
                label = "Total spend (sum of measured attempt costs; /credits unavailable)"
            (options.out_dir / "summary.md").write_text(render_summary(attempts, spend, stopped, label))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--backend-python", type=Path, required=True)
    parser.add_argument("--docker-host", required=True)
    parser.add_argument("--key-file", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--models", type=lambda v: v.split(","), required=True)
    parser.add_argument("--attempts", type=int, default=3)
    parser.add_argument("--budget", type=float, default=2.0)
    parser.add_argument("--reserve", type=float, default=0.15, help="USD assumed per attempt before any is measured")
    options = parser.parse_args()
    options.out_dir.mkdir(parents=True, exist_ok=True)
    # The stand bind-mounts migrations from here; Colima/Docker Desktop share only the
    # home tree by default, so a system temp dir mounts empty and flyway migrates nothing.
    options.recovery_dir = Path(tempfile.mkdtemp(prefix="stand-recovery-", dir=options.out_dir))
    asyncio.run(main_async(options))


if __name__ == "__main__":
    main()
