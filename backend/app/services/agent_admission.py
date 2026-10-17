"""Atomic daily admission for public agent registrations."""

import hashlib
import hmac
import math
from datetime import datetime, timedelta, timezone
from ipaddress import IPv6Address, ip_address

from fastapi import HTTPException
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.config import get_settings

_ADMIT = """
local count = tonumber(redis.call('GET', KEYS[1]) or '0')
if count >= 5 then return 0 end
redis.call('INCR', KEYS[1])
redis.call('EXPIREAT', KEYS[1], ARGV[1])
return 1
"""


async def admit_agent_registration(address: str, redis: Redis | None) -> None:
    """Consume one attempt per canonical address and UTC day, failing closed."""
    try:
        if "%" in address:
            raise ValueError("Scoped addresses are not public admission identities")
        canonical = ip_address(address)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid client address") from None
    if isinstance(canonical, IPv6Address) and canonical.ipv4_mapped is not None:
        canonical = canonical.ipv4_mapped
    now = datetime.now(timezone.utc)
    tomorrow = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    retry_after = str(max(1, math.ceil((tomorrow - now).total_seconds())))
    identity = f"agent-registration:{now.date().isoformat()}:{canonical}"
    digest = hmac.new(
        get_settings().secret_key.encode(), identity.encode(), hashlib.sha256
    ).hexdigest()
    key = f"agent-registration:{now.date().isoformat()}:{digest}"
    if redis is None:
        raise HTTPException(status_code=503, detail="Registration admission unavailable")
    try:
        admitted = await redis.execute_command("EVAL", _ADMIT, 1, key, int(tomorrow.timestamp()))
    except RedisError:
        raise HTTPException(status_code=503, detail="Registration admission unavailable") from None
    if not admitted:
        raise HTTPException(
            status_code=429,
            detail="Daily agent registration limit reached",
            headers={"Retry-After": retry_after},
        )
