"""Collect bounded, untrusted public growth evidence without credentials."""

import asyncio
import hashlib
import json
import re
from datetime import UTC, datetime
from uuid import UUID

import httpx

from .operator_digest import canonical
from .operator_snapshot import (
    GITHUB,
    PLATFORM,
    PublicCollector,
    bounded_text,
    repo_name,
)

MOLTBOOK = "https://www.moltbook.com"
QUERIES = (
    'is:issue is:open "looking for" "app"',
    'is:issue is:open "wish there was"',
)
ISSUE_PATH = re.compile(
    r"/[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}/issues/[1-9][0-9]{0,19}"
)
MAX_BYTES = 50 * 1024


async def _read_public(client: httpx.AsyncClient, url: httpx.URL):
    # The third public origin and wall-clock bound are absent from PublicCollector.
    request = httpx.Request(
        "GET",
        url,
        headers={"Accept": "application/json", "User-Agent": "AgentSpore-Operator/1.0"},
        extensions={"timeout": {k: 10.0 for k in ("connect", "read", "write", "pool")}},
    )
    try:
        async with asyncio.timeout(10):
            response = await client.send(
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
    except (httpx.HTTPError, ValueError, RecursionError, TimeoutError):
        return None


def _uuid(value) -> str | None:
    try:
        return str(UUID(value)) if isinstance(value, str) else None
    except ValueError:
        return None


def _project(row: dict):
    identity = _uuid(row.get("id"))
    if identity is None or not isinstance(row.get("title"), str):
        return None
    repo = repo_name(row.get("repo_url"))
    return (
        f"project.{identity}",
        f"{PLATFORM}/projects/{identity}",
        {
            "title": bounded_text(row["title"], 120),
            "description": bounded_text(row.get("description"), 500),
            "repo_url": f"https://github.com/{repo}" if repo else None,
        },
    )


def _demand(row: dict):
    identity, raw_url = row.get("id"), row.get("html_url")
    if type(identity) is not int or identity <= 0 or not isinstance(raw_url, str):
        return None
    url = httpx.URL(raw_url)
    if (
        url.scheme != "https"
        or url.host != "github.com"
        or url.port not in (None, 443)
        or url.username
        or url.password
        or url.query
        or url.fragment
        or not ISSUE_PATH.fullmatch(url.path)
        or "pull_request" in row
        or not isinstance(row.get("title"), str)
    ):
        return None
    return (
        f"demand.{identity}",
        str(url),
        {
            "title": bounded_text(row["title"], 120),
            "body": bounded_text(row.get("body"), 500),
            "evidence": "Public issue; demand and suitability require review.",
        },
    )


def _agent(row: dict):
    identity = _uuid(row.get("id"))
    if identity is None or not isinstance(row.get("title"), str):
        return None
    if row.get("is_deleted") or row.get("is_spam"):
        return None
    return (
        f"agent.{identity}",
        f"{MOLTBOOK}/post/{identity}",
        {
            "title": bounded_text(row["title"], 120),
            "content": bounded_text(row.get("content"), 500),
            "evidence": "Self-declared public jobs post; availability and willingness to join AgentSpore are unverified.",
        },
    )


def _append_source(bundle: dict, name: str, rows, limit: int, kind: str) -> None:
    coverage = bundle["coverage"]
    if not isinstance(rows, list):
        coverage[name] = {
            "status": "unavailable",
            "reason": "source_unavailable_or_invalid",
        }
        return
    invalid = False
    for row in rows[:limit]:
        try:
            parsed = (
                {"project": _project, "demand": _demand, "agent": _agent}[kind](row)
                if isinstance(row, dict)
                else None
            )
        except (ValueError, TypeError, httpx.InvalidURL):
            parsed = None
        if parsed is None:
            invalid = True
            continue
        identity, url, value = parsed
        bundle["facts"][identity] = json.dumps(
            value, ensure_ascii=False, allow_nan=False
        )[:1600]
        coverage["source_urls"][identity] = url
        coverage["source_kinds"][identity] = kind
        if kind == "project":
            coverage["project_titles"].append(value["title"])
    coverage[name] = {
        "status": "partial" if invalid or len(rows) >= limit else "complete",
        "reason": "bounded_public_feed"
        if len(rows) >= limit
        else "source_shape_invalid"
        if invalid
        else None,
    }


def _finish_bundle(bundle: dict) -> dict:
    while len(canonical(bundle)) + 100 > MAX_BYTES:
        identity = next(reversed(bundle["facts"]))
        del bundle["facts"][identity]
        del bundle["coverage"]["source_urls"][identity]
        del bundle["coverage"]["source_kinds"][identity]
        bundle["coverage"]["output"] = {"status": "partial", "reason": "output_budget"}
    bundle["snapshot_digest"] = hashlib.sha256(canonical(bundle)).hexdigest()
    return bundle


async def collect_growth(client: httpx.AsyncClient, now: datetime) -> dict:
    """Return bounded public leads as untrusted evidence, never outreach consent."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    bundle = {
        "purpose": "growth",
        "window": {"observed_at": now.astimezone(UTC).isoformat()},
        "facts": {},
        "coverage": {
            "source_urls": {},
            "source_kinds": {},
            "project_titles": [],
            "material": "untrusted_public_data",
        },
    }
    await _platform_context(bundle, client)
    projects = await _read_public(
        client, httpx.URL(PLATFORM + "/api/v1/projects", params={"limit": 20})
    )
    _append_source(bundle, "projects", projects, 20, "project")
    for index, query in enumerate(QUERIES):
        data = await _read_public(
            client,
            httpx.URL(
                GITHUB + "/search/issues",
                params={"q": query, "per_page": 5, "sort": "created", "order": "desc"},
            ),
        )
        rows = data.get("items") if isinstance(data, dict) else None
        _append_source(bundle, f"demand_search_{index + 1}", rows, 5, "demand")
        if isinstance(data, dict) and data.get("incomplete_results") is True:
            bundle["coverage"][f"demand_search_{index + 1}"] = {
                "status": "partial",
                "reason": "incomplete_search",
            }
    data = await _read_public(
        client,
        httpx.URL(
            MOLTBOOK + "/api/v1/posts",
            params={"submolt": "jobs", "sort": "new", "limit": 10},
        ),
    )
    rows = (
        data.get("posts")
        if isinstance(data, dict) and data.get("success") is True
        else None
    )
    _append_source(bundle, "agent_jobs", rows, 10, "agent")
    if isinstance(rows, list) and data.get("has_more") is True:
        bundle["coverage"]["agent_jobs"] = {
            "status": "partial",
            "reason": "bounded_public_feed",
        }
    return _finish_bundle(bundle)


async def _platform_context(bundle: dict, client: httpx.AsyncClient) -> None:
    """Share published platform context without granting demand or contact authority."""
    updates = await PublicCollector(client).platform_updates()
    bundle["coverage"]["platform_updates"] = {
        key: updates[key] for key in ("status", "reason")
    }
    for row in updates["value"] or []:
        identity = f"platform.{row['kind']}.{row['identity']}"
        bundle["facts"][identity] = json.dumps(
            {
                **row,
                "installed_versions": "unknown; published source is not proof of deployment",
                "source_branch": "main" if row["kind"] == "commit" else None,
            },
            ensure_ascii=False,
        )
        bundle["coverage"]["source_urls"][identity] = row["url"]
        bundle["coverage"]["source_kinds"][identity] = "platform"
