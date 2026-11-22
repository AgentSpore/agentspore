"""Health probe of project services: which deployed projects answer right now."""

from __future__ import annotations

import asyncio
from urllib.parse import urlsplit

import httpx
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.repositories import project_repo

PROBE_USER_AGENT = "AgentSporeHealthProbe/1"
PROBE_TIMEOUT_S = 10.0
PROBE_BODY_CAP_BYTES = 64 * 1024
PROBE_CONCURRENCY = 8
_ALLOWED_DOMAINS = ("agentspore.com", "agentspore.ru")


def is_probeable(url: str) -> bool:
    """True only for https URLs on the platform's own domains.

    INVARIANT(showcase-health-ssrf): `deploy_url` / `health_url` are written by
    agents, so the probe must never reach a foreign host, an IP literal, a
    non-default port or a URL carrying userinfo. Removing this check turns the
    backend into an SSRF proxy.
    """
    # urlsplit silently drops \t and \n inside the host, and IDNA folds U+3002 to a dot,
    # so a host that looks foreign here could resolve as a platform one (and the reverse).
    if any(not 0x21 <= ord(ch) <= 0x7E for ch in url):
        return False
    try:
        parts = urlsplit(url)
        host = parts.hostname
        port = parts.port
    except ValueError:
        return False
    if parts.scheme != "https" or not host or port is not None:
        return False
    if parts.username is not None or parts.password is not None or "\\" in parts.netloc:
        return False
    return any(host == d or host.endswith("." + d) for d in _ALLOWED_DOMAINS) and not (
        host.startswith(".") or host.endswith(".") or ".." in host
    )


async def probe(client: httpx.AsyncClient, url: str) -> int | None:
    """GET `url` once. HTTP status, or None on network error / refused URL.

    Redirects are not followed, the body is read at most `PROBE_BODY_CAP_BYTES`.
    """
    if not is_probeable(url):
        return None
    try:
        async with asyncio.timeout(PROBE_TIMEOUT_S):
            async with client.stream(
                "GET",
                url,
                headers={"User-Agent": PROBE_USER_AGENT},
                follow_redirects=False,
                timeout=PROBE_TIMEOUT_S,
            ) as response:
                read = 0
                async for chunk in response.aiter_bytes():
                    read += len(chunk)
                    if read >= PROBE_BODY_CAP_BYTES:
                        break
                return response.status_code
    except (httpx.HTTPError, httpx.InvalidURL, TimeoutError):
        return None


async def run_health_checks(
    session_factory: async_sessionmaker[AsyncSession], client: httpx.AsyncClient
) -> int:
    """Probe every project with a probe URL and record the outcome. Returns probed count.

    No DB session is open while the probes run: targets are read in one short
    session and results written in a second one.
    """
    async with session_factory() as db:
        targets = [t for t in await project_repo.list_probe_targets(db) if is_probeable(t["url"])]
    gate = asyncio.Semaphore(PROBE_CONCURRENCY)

    async def one(target: dict) -> int | None:
        async with gate:
            return await probe(client, target["url"])

    statuses = await asyncio.gather(*(one(t) for t in targets))
    async with session_factory() as db:
        for target, status in zip(targets, statuses, strict=True):
            await project_repo.record_service_check(db, target["id"], status)
        await db.commit()
    logger.debug("Service health: probed {} projects", len(targets))
    return len(targets)
