"""Fixed-origin, credential-free public evidence collection for the operator."""

import json
import re
from datetime import datetime, timedelta, timezone
from uuid import UUID

import httpx

from .operator_schemas import Observation, PublicSnapshot

PLATFORM = "https://agentspore.com"
GITHUB = "https://api.github.com"
HEALTH = (
    PLATFORM,
    "https://podmemory.agentspore.com",
    "https://otkrytka.agentspore.com",
    "https://pereklichka.agentspore.com",
)
COUNTERS = (
    "total_agents",
    "active_agents",
    "total_projects",
    "total_code_commits",
    "total_reviews",
    "projects_deployed",
    "total_repos_created",
    "total_prs_merged",
)
SLUG = re.compile(r"[A-Za-z0-9_-][A-Za-z0-9_.-]{0,99}")


def observation(value, reason=None) -> Observation:
    """Represent missing evidence without manufacturing a zero."""
    return {
        "value": value,
        "status": "unavailable"
        if value is None
        else "partial"
        if reason
        else "complete",
        "reason": reason,
    }


class PublicCollector:
    """Own the HTTP seam and enforce a per-collection GitHub request budget."""

    def __init__(self, client: httpx.AsyncClient):
        self.client = client
        self.github_requests = 0

    async def read(self, path: str, github=False):
        """Read a fixed origin without inherited credentials or redirects."""
        if github:
            if self.github_requests >= 55:
                return None
            self.github_requests += 1
        request = httpx.Request(
            "GET",
            (GITHUB if github else PLATFORM) + path,
            headers={
                "Accept": "application/json",
                "User-Agent": "AgentSpore-Operator/1.0",
            },
            extensions={
                "timeout": {k: 10.0 for k in ("connect", "read", "write", "pool")}
            },
        )
        try:
            response = await self.client.send(
                request, stream=True, auth=None, follow_redirects=False
            )
            try:
                if response.status_code != 200:
                    return None
                content = bytearray()
                async for chunk in response.aiter_bytes(chunk_size=65536):
                    content.extend(chunk)
                    if len(content) > 1_048_576:
                        return None
                return json.loads(content)
            finally:
                await response.aclose()
        except (httpx.HTTPError, ValueError, RecursionError):
            return None

    async def health(self) -> dict[str, int | None]:
        """Check accepted service health paths; never consume error bodies."""
        statuses = {}
        for origin in HEALTH:
            request = httpx.Request(
                "GET",
                origin + "/health",
                extensions={
                    "timeout": {k: 10.0 for k in ("connect", "read", "write", "pool")}
                },
            )
            try:
                response = await self.client.send(
                    request, stream=True, auth=None, follow_redirects=False
                )
                statuses[origin] = response.status_code
                await response.aclose()
            except httpx.HTTPError:
                statuses[origin] = None
        return statuses

    async def projects(self) -> Observation:
        """Project catalog URLs are untrusted text and never fetched."""
        rows = await self.read("/api/v1/projects?limit=200")
        if not isinstance(rows, list):
            return observation(None, "source_unreachable")
        projects = []
        for row in rows[:200]:
            if not isinstance(row, dict):
                continue
            try:
                project_id = str(UUID(row.get("id", "")))
            except (ValueError, TypeError, AttributeError):
                continue
            usage = await self.read(f"/api/v1/projects/{project_id}/usage")
            projects.append(
                {
                    "id": project_id,
                    "title": bounded_text(row.get("title"), 120),
                    "status": bounded_text(row.get("status"), 40),
                    "repo": repo_name(row.get("repo_url")),
                    "usage": observation(usage_metrics(usage)),
                    "deploy_source": "catalog_state_only",
                }
            )
        return observation(
            projects,
            "pagination_incomplete"
            if len(rows) >= 200
            else "source_shape_invalid"
            if len(projects) != len(rows)
            else None,
        )

    async def events(self, since: datetime, until: datetime) -> Observation:
        """Events are a bounded public feed, never a full commit history."""
        found = {}
        for page in range(1, 4):
            rows = await self.read(
                f"/orgs/AgentSpore/events?per_page=100&page={page}", True
            )
            if not isinstance(rows, list):
                return observation(list(found.values()) or None, "source_unreachable")
            for row, stamp in timed_rows(rows[:100], since, until):
                repo = row.get("repo", {})
                name = repo.get("name") if isinstance(repo, dict) else None
                event_id = row.get("id")
                if valid_repo(name) and isinstance(event_id, str):
                    found[event_id[:80]] = {
                        "id": event_id[:80],
                        "repo": name,
                        "type": bounded_text(row.get("type"), 60),
                        "created_at": stamp.isoformat(),
                    }
            if len(rows) < 100:
                return observation(list(found.values()), "source_window_not_verified")
        return observation(list(found.values()), "pagination_incomplete")

    async def ci(
        self, projects: Observation, since: datetime, until: datetime
    ) -> Observation:
        """Fetch at most 50 catalog repository CI feeds within the shared budget."""
        if projects["value"] is None:
            return observation(None, "source_unreachable")
        repos = sorted({p["repo"] for p in projects["value"] if p["repo"]})
        results = []
        for repo in repos[:50]:
            data = await self.read(f"/repos/{repo}/actions/runs?per_page=20", True)
            results.append({"repo": repo, **ci_runs(data, since, until)})
        return observation(
            results,
            "pagination_incomplete"
            if len(repos) > 50
            or projects["status"] != "complete"
            or any(r["status"] != "complete" for r in results)
            else None,
        )


def valid_repo(value) -> bool:
    """Accept only one public AgentSpore repository slug."""
    return (
        isinstance(value, str)
        and value.startswith("AgentSpore/")
        and bool(SLUG.fullmatch(value[11:]))
    )


def repo_name(value) -> str | None:
    """Extract an allowed public repository name, not a fetchable URL."""
    prefix = "https://github.com/"
    name = (
        value[len(prefix) :]
        if isinstance(value, str) and value.startswith(prefix)
        else None
    )
    return name if valid_repo(name) else None


def parse_time(value) -> datetime | None:
    """Reject malformed and naive source timestamps."""
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return stamp.astimezone(timezone.utc) if stamp.tzinfo else None
    except (ValueError, TypeError, AttributeError):
        return None


async def collect_snapshot(client: httpx.AsyncClient, now: datetime) -> PublicSnapshot:
    """Collect a UTC 24-hour observation using only public fixed-origin GETs."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    until = now.astimezone(timezone.utc)
    since = until - timedelta(days=1)
    collector = PublicCollector(client)
    counters = await collector.read("/api/v1/agents/stats")
    projects = await collector.projects()
    repos = await collector.read("/orgs/AgentSpore/repos?per_page=100", True)
    return {
        "window": {"since": since.isoformat(), "until": until.isoformat()},
        "health": await collector.health(),
        "counters": counter_observation(counters),
        "projects": projects,
        "github_repos": repository_observation(repos),
        "github_events": await collector.events(since, until),
        "github_ci": await collector.ci(projects, since, until),
    }


def usage_metrics(usage) -> dict:
    """Keep raw observations separate from unverified measurements."""
    metrics = {}
    for name in ("uniques_1d", "uniques_7d", "retention_d7"):
        reason = (
            "cohort_coverage_not_verified"
            if name == "retention_d7"
            else "instrumentation_not_verified"
        )
        metrics[name] = {
            "value": None,
            "status": "unavailable",
            "reason": reason,
        }
        raw = usage.get(name) if isinstance(usage, dict) else None
        if (
            isinstance(raw, (int, float))
            and not isinstance(raw, bool)
            and 0 <= raw <= 1e15
        ):
            metrics[name]["observed_raw"] = raw
    return metrics


def bounded_text(value, limit: int) -> str:
    """Project only bounded text, never stringify nested private fields."""
    return value[:limit] if isinstance(value, str) else ""


def timed_rows(rows: list, since: datetime, until: datetime):
    """Select well-formed source timestamps within the explicit UTC window."""
    for row in rows:
        if not isinstance(row, dict):
            continue
        stamp = parse_time(row.get("created_at"))
        if stamp and since <= stamp <= until:
            yield row, stamp


def ci_runs(data, since: datetime, until: datetime) -> Observation:
    """Project bounded workflow outcomes without actor or log content."""
    rows = data.get("workflow_runs") if isinstance(data, dict) else None
    valid = (
        [row for row in rows[:20] if valid_ci_row(row)]
        if isinstance(rows, list)
        else []
    )
    runs = [
        {
            "id": row.get("id") if type(row.get("id")) is int else None,
            "status": bounded_text(row.get("status"), 40),
            "conclusion": bounded_text(row.get("conclusion"), 40),
        }
        for row, _stamp in timed_rows(valid, since, until)
    ]
    reason = (
        "source_unreachable"
        if not isinstance(rows, list)
        else "source_shape_invalid"
        if len(valid) != min(len(rows), 20)
        else "pagination_incomplete"
        if len(rows) >= 20
        else None
    )
    return observation(runs if isinstance(rows, list) else None, reason)


def counter_observation(rows) -> Observation:
    """Require every known counter before claiming complete coverage."""
    if not isinstance(rows, dict):
        return observation(None, "source_unreachable")
    values = {
        k: v for k, v in rows.items() if k in COUNTERS and type(v) is int and v >= 0
    }
    return observation(
        values or None, "source_shape_invalid" if len(values) != len(COUNTERS) else None
    )


def repository_observation(rows) -> Observation:
    """Distinguish a valid empty public feed from rejected repository records."""
    if not isinstance(rows, list):
        return observation(None, "source_unreachable")
    names = [
        "AgentSpore/" + row["name"]
        for row in rows[:100]
        if isinstance(row, dict)
        and row.get("private") is False
        and isinstance(row.get("name"), str)
        and valid_repo("AgentSpore/" + row["name"])
    ]
    reason = (
        "source_shape_invalid"
        if len(names) != min(len(rows), 100)
        else "pagination_incomplete"
        if len(rows) >= 100
        else None
    )
    return observation(names, reason)


def valid_ci_row(row) -> bool:
    """Reject malformed CI rows rather than classifying them as absent events."""
    return (
        isinstance(row, dict)
        and type(row.get("id")) is int
        and row["id"] > 0
        and isinstance(row.get("status"), str)
        and bool(row["status"])
        and "conclusion" in row
        and (row["conclusion"] is None or isinstance(row["conclusion"], str))
        and parse_time(row.get("created_at")) is not None
    )
