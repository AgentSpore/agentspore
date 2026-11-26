"""Pure parts of the onboarding eval: origin guard, step detection, key masking, aggregation."""

import re
import statistics
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit, urlunsplit

STEPS = ("skill_fetched", "registered", "key_used", "first_heartbeat_ok", "project_or_push_attempted")
SECRET = re.compile(r"\b(af_|sk-or-)[A-Za-z0-9_-]{20,}")
# Masking also hides fragments: a key cut by truncation is shorter than the learned shape.
SECRET_PART = re.compile(r"\b(af_|sk-or-)[A-Za-z0-9_-]+")


@dataclass
class Call:
    method: str
    path: str
    status: int | None
    authed: bool


def in_origin(url: str, origin: str) -> bool:
    """True for relative paths and URLs whose scheme, host and port equal the stand origin."""
    if url.startswith("/"):
        return True
    try:
        got, want = urlsplit(url), urlsplit(origin)
        return (got.scheme, got.hostname, got.port) == (want.scheme, want.hostname, want.port)
    except ValueError:
        return False


REGISTER = "/api/v1/agents/register"
PROD_HOSTS = ("agentspore.com", "www.agentspore.com")


def rewrite_prod_url(url: str, origin: str) -> str:
    """Point production-host URLs from skill.md at the stand (path and query kept); others unchanged."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    if parts.scheme in ("http", "https") and parts.hostname in PROD_HOSTS:
        return origin + urlunsplit(("", "", parts.path, parts.query, ""))
    return url


def _ok(call: Call, method: str, path: str) -> bool:
    return call.method == method and call.path == path and call.status is not None and 200 <= call.status < 300


def _key_used(calls: list[Call]) -> bool:
    """An authenticated agent-API call the platform did not reject, after a successful register."""
    registered = False
    for c in calls:
        if _ok(c, "POST", REGISTER):
            registered = True
        elif registered and c.authed and c.path.startswith("/api/v1/agents/") and c.status not in (None, 401):
            return True
    return False


def detect_steps(calls: list[Call]) -> dict[str, bool]:
    """Derive reached onboarding steps from the HTTP transcript; key storage is seen only as use."""
    return {
        "skill_fetched": any(_ok(c, "GET", "/skill.md") for c in calls),
        "registered": any(_ok(c, "POST", "/api/v1/agents/register") for c in calls),
        "key_used": _key_used(calls),
        "first_heartbeat_ok": any(_ok(c, "POST", "/api/v1/agents/heartbeat") for c in calls),
        "project_or_push_attempted": any(
            c.method == "POST" and c.path.startswith("/api/v1/agents/projects") for c in calls
        ),
    }


def mask(text: str, secrets: list[str]) -> str:
    """Hide stand-issued keys (by shape and by learned value) before anything is saved."""
    for secret in secrets:
        text = text.replace(secret, "***")
    return SECRET_PART.sub(lambda m: m.group(1) + "***", text)


def classify(steps: dict[str, bool], infra: bool) -> str:
    """Unknown is not negative: an infra-killed attempt without a heartbeat is excluded, not failed."""
    if steps["first_heartbeat_ok"]:
        return "success"
    return "infra_error" if infra else "agent_failure"


def summarize(attempts: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Per model: success over non-infra attempts, medians over successes, infra count, spend."""
    out: dict[str, dict[str, Any]] = {}
    for model in dict.fromkeys(a["model"] for a in attempts):
        rows = [a for a in attempts if a["model"] == model]
        wins = [a["first_heartbeat"] for a in rows if a["outcome"] == "success"]
        infra = sum(a["outcome"] == "infra_error" for a in rows)

        def med(key: str, wins: list[dict[str, Any]] = wins) -> float | None:
            return statistics.median(w[key] for w in wins) if wins else None

        out[model] = {
            "attempts": len(rows),
            "success": len(wins),
            "counted": len(rows) - infra,
            "infra_error": infra,
            "median_turns": med("turns"),
            "median_tokens": med("total_tokens"),
            "median_seconds": med("seconds"),
            "spend": sum(a["cost"] for a in rows),
        }
    return out
