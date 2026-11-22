"""Acquisition funnel without personal data.

Only (UTC day, step, source) counters are stored. Visitor uniqueness uses
HMAC(IP + UA) under a salt kept in Redis for one UTC day; the raw IP and UA are
never logged, stored or used as a Redis key.
"""

import hashlib
import hmac
import ipaddress
import re
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
from app.schemas.funnel import FunnelPageRow, FunnelReport, FunnelRow

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
AUTOMATION_UA = re.compile(
    r"bot|crawl|spider|slurp|preview|curl|wget|python|httpx|go-http|node|axios"
    r"|headless|lighthouse|playwright|monitor|uptime",
    re.IGNORECASE,
)
# Set once at key creation (expire nx=True, Redis >= 7), never extended: hashes die within 48 h.
DEDUP_TTL = 48 * 3600
RATE_WINDOW = 60
SALT_GRACE = 3600
IP_DAILY_CAP = 20


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
    def is_automated(ua: str) -> bool:
        return AUTOMATION_UA.search(ua) is not None

    @staticmethod
    def page_for(path: str) -> str | None:
        """Site section of a counted path (its first segment; "/" is home), else None."""
        if path == "/":
            return "home"
        for prefix in PAGE_PREFIXES:
            if path == prefix or path.startswith(prefix + "/"):
                return prefix[1:]
        return None

    @classmethod
    def page_counted(cls, path: str) -> bool:
        return cls.page_for(path) is not None

    async def record(self, step: str, source: str = "direct") -> None:
        """Count one event. Own session: a failure never touches the caller's transaction."""
        try:
            day = datetime.now(timezone.utc).date()
            async with self.session_factory() as db:
                await FunnelRepository(db).increment(day, step, source)
                await db.commit()
        except (SQLAlchemyError, OSError) as exc:
            logger.warning("funnel record failed: step={} err={}", step, type(exc).__name__)

    @staticmethod
    def _rate_subject(ip: str) -> str:
        """IPv6 clients own a /64, so limit and cap key on the network, not the address."""
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            return ip
        if addr.version == 6:
            if addr.ipv4_mapped:
                return str(addr.ipv4_mapped)
            return str(ipaddress.ip_network(f"{ip}/64", strict=False).network_address)
        return ip

    async def _salted(self, ip: str, ua: str) -> tuple[str, str, str] | None:
        """Return (day tag, HMAC(rate subject), HMAC(full IP+UA)) under today's salt.

        The salt lives until the next UTC midnight plus a grace hour and then expires on its own.
        """
        now = datetime.now(timezone.utc)
        tag = f"{now:%Y%m%d}"
        salt_key = f"funnel:salt:{tag}"
        midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        ttl = int((midnight - now).total_seconds()) + SALT_GRACE
        await self.redis.set(salt_key, secrets.token_hex(16), ex=ttl, nx=True)
        salt = await self.redis.get(salt_key)
        if not salt:
            return None
        key = salt.encode()
        ip_hash = hmac.new(key, self._rate_subject(ip).encode(), hashlib.sha256).hexdigest()
        visitor_hash = hmac.new(key, f"{ip}|{ua}".encode(), hashlib.sha256).hexdigest()
        return tag, ip_hash, visitor_hash

    async def allow(self, ip: str, ua: str, limit: int) -> bool:
        """Per-IP rate limit keyed by the salted IP hash. Redis trouble: fail open."""
        try:
            salted = await self._salted(ip, ua)
            if salted is None:
                return True
            key = f"funnel:rl:{salted[1]}"
            hits = await self.redis.incr(key)
            if hits == 1:
                await self.redis.expire(key, RATE_WINDOW)
            return hits <= limit
        except (RedisError, OSError):
            return True

    async def record_unique(self, step: str, source: str, ip: str, ua: str) -> bool:
        """Count once per visitor per day, at most IP_DAILY_CAP per IP. Redis down: not counted."""
        if not await self._first_today(step, ip, ua):
            return False
        await self.record(step, source)
        return True

    async def record_page(self, page: str, ip: str, ua: str) -> bool:
        """Count a human visitor once per day and site section, same dedup and caps as a step."""
        if not await self._first_today(f"page:{page}", ip, ua):
            return False
        try:
            async with self.session_factory() as db:
                await FunnelRepository(db).increment_page(datetime.now(timezone.utc).date(), page)
                await db.commit()
        except (SQLAlchemyError, OSError) as exc:
            logger.warning("funnel page record failed: err={}", type(exc).__name__)
        return True

    async def _first_today(self, scope: str, ip: str, ua: str) -> bool:
        """True once per (visitor, scope, UTC day) within the per-IP cap. Redis down: False."""
        try:
            salted = await self._salted(ip, ua)
            if salted is None:
                return False
            tag, ip_hash, digest = salted
            seen_key = f"funnel:seen:{tag}:{scope}"
            # redis-py stubs type sadd as a sync/async union
            added = await self.redis.sadd(seen_key, digest)  # ty: ignore[invalid-await]
            await self.redis.expire(seen_key, DEDUP_TTL, nx=True)
            if not added:
                return False
            cap_key = f"funnel:ipcap:{tag}:{scope}:{ip_hash}"
            used = await self.redis.incr(cap_key)
            await self.redis.expire(cap_key, DEDUP_TTL, nx=True)
            return used <= IP_DAILY_CAP
        except (RedisError, OSError) as exc:
            logger.warning("funnel dedup unavailable: {}", type(exc).__name__)
            return False

    async def report(self, db, days: int) -> FunnelReport:
        since = datetime.now(timezone.utc).date() - timedelta(days=days - 1)
        repo = FunnelRepository(db)
        rows = await repo.rows_since(since)
        pages = await repo.pages_since(since)
        totals: dict[str, int] = {}
        for row in rows:
            totals[row["step"]] = totals.get(row["step"], 0) + row["count"]
        page_totals: dict[str, int] = {}
        for row in pages:
            page_totals[row["page"]] = page_totals.get(row["page"], 0) + row["visitors"]
        return FunnelReport(
            days=days,
            rows=[FunnelRow(**r) for r in rows],
            totals=totals,
            pages=[FunnelPageRow(**r) for r in pages],
            page_totals=page_totals,
        )
