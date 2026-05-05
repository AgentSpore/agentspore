"""Admin endpoints: llm-health, disk-usage, quota."""

import subprocess

from fastapi import APIRouter

from config import get_settings
from llm_fallback import LLMHealthChecker

settings = get_settings()

router = APIRouter()

# disk_quota is injected by main.py after initialisation to avoid circular import.
disk_quota = None


@router.get("/admin/llm-health")
async def admin_llm_health():
    """Probe each provider/model in the fallback chain and return health status.

    Runs all probes concurrently. Returns one entry per chain slot:
      {provider, model, status, latency_ms, error}
    status: "ok" | "error" | "timeout" | "skipped" (no API key).

    Protected by the global X-Runner-Key middleware. Safe to call before
    deploying agents to a hackathon or demo — verifies all providers are live.
    """
    checker = LLMHealthChecker(timeout=20.0)
    results = await checker.check_all()
    ok_count = sum(1 for r in results if r["status"] == "ok")
    return {
        "chain_length": len(results),
        "ok_count": ok_count,
        "results": results,
    }


@router.get("/admin/disk-usage")
async def admin_disk_usage():
    """Per-agent disk usage for all workspace directories under workspace_root.

    Returns a map of {hosted_id: human_readable_size} for monitoring.
    Runs ``du -sh`` for each immediate subdirectory. Protected by the global
    X-Runner-Key middleware — only the backend can call this.
    """
    workspace_root = settings.workspace_root
    usage: dict[str, str] = {}

    if not workspace_root.exists():
        return {"usage": usage, "workspace_root": str(workspace_root), "error": "workspace_root missing"}

    for agent_dir in workspace_root.iterdir():
        if not agent_dir.is_dir():
            continue
        hosted_id = agent_dir.name
        try:
            result = subprocess.run(
                ["du", "-sh", str(agent_dir)],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0:
                size = result.stdout.split("\t")[0].strip()
                usage[hosted_id] = size
        except Exception as exc:
            usage[hosted_id] = f"error: {exc!r}"

    return {"usage": usage, "workspace_root": str(workspace_root)}


@router.get("/admin/quota/{hosted_id}")
async def admin_quota(hosted_id: str):
    """Per-agent disk quota status.

    Returns current usage alongside configured soft/hard limits.
    Runs du live (not cached) so the backend always gets fresh numbers.
    Protected by the global X-Runner-Key middleware.

    Response schema:
      {
        "hosted_id": str,
        "usage_mb": float,
        "soft_mb": int,
        "hard_mb": int,
        "soft_exceeded": bool,
        "hard_exceeded": bool,
        "quota_enabled": bool,
      }
    """
    soft_mb, hard_mb = disk_quota.get_limits()
    usage_mb = await disk_quota.measure_usage_mb_async(hosted_id)
    return {
        "hosted_id": hosted_id,
        "usage_mb": round(usage_mb, 2),
        "soft_mb": soft_mb,
        "hard_mb": hard_mb,
        "soft_exceeded": usage_mb >= soft_mb,
        "hard_exceeded": usage_mb >= hard_mb,
        "quota_enabled": disk_quota.is_enabled(),
    }
