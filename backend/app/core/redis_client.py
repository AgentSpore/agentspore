"""Redis client — singleton, переиспользуется через FastAPI Depends."""

from __future__ import annotations

import redis.asyncio as aioredis

from app.core.config import get_settings

from loguru import logger

_redis: aioredis.Redis | None = None


async def init_redis() -> aioredis.Redis:
    """Инициализировать Redis-клиент при старте приложения."""
    global _redis
    settings = get_settings()
    _redis = aioredis.from_url(
        settings.redis_url,
        decode_responses=True,
        encoding="utf-8",
    )
    # Проверяем соединение
    await _redis.ping()
    logger.info("✅ Redis connected: %s", settings.redis_url)
    return _redis


async def close_redis() -> None:
    """Закрыть Redis-клиент при остановке приложения."""
    global _redis
    if _redis is not None:
        await _redis.aclose()
        _redis = None
        logger.info("👋 Redis connection closed")


async def get_redis() -> aioredis.Redis:
    """FastAPI dependency — возвращает готовый Redis-клиент."""
    if _redis is None:
        raise RuntimeError("Redis not initialized. Call init_redis() first.")
    return _redis
