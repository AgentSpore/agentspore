"""Acquisition funnel without personal data.

Only (UTC day, step, source) counters are stored. Visitor uniqueness uses
HMAC(IP + UA) under a salt kept in Redis for one UTC day; the raw IP and UA are
never logged, stored or used as a Redis key.
"""

import hashlib
import hmac
import secrets
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

from loguru import logger
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import get_settings
from app.core.database import async_session_maker
from app.core.domains import match_domain
from app.repositories.funnel_repo import FunnelRepository
from app.schemas.funnel import FunnelReport, FunnelRow

SOURCES = frozenset(
    {"x", "substack", "telegram", "moltbook", "mcpservers", "github", "direct", "other"}
)
REFERRER_HOSTS = {
    "x.com": "x",
    "twitter.com": "x",
    "t.co": "x",
    "substack.com": "substack",
    "t.me": "telegram",
    "telegram.org": "telegram",
    "moltbook.com": "moltbook",
    "mcpservers.org": "mcpservers",
    "github.com": "github",
}
PAGE_PREFIXES = (
    "/agents",
    "/projects",
    "/blog",
    "/docs",
    "/showcase",
    "/teams",
    "/hosted-agents",
    "/signup",
    "/login",
    "/privacy",
    "/chat",
    "/start",
)
DEDUP_TTL = 48 * 3600
RATE_WINDOW = 60


class FunnelService:
    def __init__(self, redis: Redis, session_factory: Callable = async_session_maker) -> None:
        self.redis = redis
        self.session_factory = session_factory

    @staticmethod
    def source_for(utm_source: str | None, referrer: str | None) -> str:
        utm = (utm_source or "").strip().lower()
        if utm:
            return utm if utm in SOURCES else "other"
        if not referrer:
            return "direct"
        try:
            host = (urlsplit(referrer).hostname or "").removeprefix("www.")
        except ValueError:
            return "other"
        if not host:
            return "other"
        if match_domain(host, get_settings().domains):
            return "direct"
        return REFERRER_HOSTS.get(host, "other")

    @staticmethod
    def page_counted(path: str) -> bool:
        return path == "/" or any(path == p or path.startswith(p + "/") for p in PAGE_PREFIXES)

    async def record(self, step: str, source: str = "direct") -> None:
        """Count one event. Own session: a failure never touches the caller's transaction."""
        try:
            day = datetime.now(timezone.utc).date()
            async with self.session_factory() as db:
                await FunnelRepository(db).increment(day, step, source)
                await db.commit()
        except (SQLAlchemyError, OSError) as exc:
            logger.warning("funnel record failed: step={} err={}", step, type(exc).__name__)

    async def _visitor_hash(self, ip: str, ua: str) -> tuple[str, str] | None:
        """Return (day tag, HMAC) under today's salt; rotate (delete) yesterday's."""
        now = datetime.now(timezone.utc)
        tag = f"{now:%Y%m%d}"
        salt_key = f"funnel:salt:{tag}"
        await self.redis.set(salt_key, secrets.token_hex(16), ex=DEDUP_TTL, nx=True)
        await self.redis.delete(f"funnel:salt:{now - timedelta(days=1):%Y%m%d}")
        salt = await self.redis.get(salt_key)
        if not salt:
            return None
        digest = hmac.new(salt.encode(), f"{ip}|{ua}".encode(), hashlib.sha256).hexdigest()
        return tag, digest

    async def allow(self, ip: str, ua: str, limit: int) -> bool:
        """Per-visitor rate limit keyed by the salted hash. Redis trouble: fail open."""
        try:
            hashed = await self._visitor_hash(ip, ua)
            if hashed is None:
                return True
            key = f"funnel:rl:{hashed[1]}"
            hits = await self.redis.incr(key)
            if hits == 1:
                await self.redis.expire(key, RATE_WINDOW)
            return hits <= limit
        except (RedisError, OSError):
            return True

    async def record_unique(self, step: str, source: str, ip: str, ua: str) -> bool:
        """Count once per visitor per UTC day. Redis down: not counted."""
        try:
            hashed = await self._visitor_hash(ip, ua)
            if hashed is None:
                return False
            tag, digest = hashed
            seen_key = f"funnel:seen:{tag}:{step}"
            # redis-py stubs type sadd as a sync/async union
            added = await self.redis.sadd(seen_key, digest)  # ty: ignore[invalid-await]
            await self.redis.expire(seen_key, DEDUP_TTL)
        except (RedisError, OSError) as exc:
            logger.warning("funnel dedup unavailable: {}", type(exc).__name__)
            return False
        if not added:
            return False
        await self.record(step, source)
        return True

    async def report(self, db, days: int) -> FunnelReport:
        since = datetime.now(timezone.utc).date() - timedelta(days=days - 1)
        rows = await FunnelRepository(db).rows_since(since)
        totals: dict[str, int] = {}
        for row in rows:
            totals[row["step"]] = totals.get(row["step"], 0) + row["count"]
        return FunnelReport(days=days, rows=[FunnelRow(**r) for r in rows], totals=totals)
