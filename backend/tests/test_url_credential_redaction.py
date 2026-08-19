"""A connection-string password must never reach a log sink.

Production had no Redis password until 2026-08-19. The moment one was set,
the very next startup wrote it in clear text: ``redis_client.py`` logged the
full ``settings.redis_url`` on connect. The URL used the empty-user Redis
form (``redis://:password@host``), which the existing ``redact_secrets``
query-string pattern does not match at all — there is no ``key=value`` pair,
the credential sits between ``://`` and ``@``.

No real value appears here; every credential below is a fabricated literal.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from loguru import logger

from app.core import redis_client
from app.core.config import get_settings
from app.core.logging import redact_secrets

FAKE_PASSWORD = "s3cr3t-not-real"
FAKE_USER = "app_user"


class TestUrlCredentialRedaction:
    def test_redis_empty_user_form_is_masked(self) -> None:
        """The exact production leak: redis://:password@host."""
        out = redact_secrets(f"redis://:{FAKE_PASSWORD}@redis:6379")
        assert FAKE_PASSWORD not in out
        assert "redis:6379" in out
        assert out.startswith("redis://")

    def test_postgres_user_and_password_form_is_masked(self) -> None:
        out = redact_secrets(
            f"postgresql+asyncpg://{FAKE_USER}:{FAKE_PASSWORD}@db:5432/agentspore"
        )
        assert FAKE_PASSWORD not in out
        assert FAKE_USER in out
        assert "db:5432/agentspore" in out

    def test_generic_scheme_user_password_form_is_masked(self) -> None:
        out = redact_secrets(f"amqp://{FAKE_USER}:{FAKE_PASSWORD}@broker:5672")
        assert FAKE_PASSWORD not in out
        assert FAKE_USER in out
        assert "broker:5672" in out

    def test_url_without_credentials_is_unchanged(self) -> None:
        line = "redis://redis:6379"
        assert redact_secrets(line) == line

    def test_url_without_credentials_in_a_sentence_is_unchanged(self) -> None:
        line = "connecting to postgresql+asyncpg://db:5432/agentspore"
        assert redact_secrets(line) == line


class TestRedisConnectLogLine:
    """The exact production leak, reproduced through the real connect path."""

    async def test_connect_log_has_host_and_port_but_not_the_password(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        settings = get_settings()
        monkeypatch.setattr(settings, "redis_url", f"redis://:{FAKE_PASSWORD}@redis:6379")
        monkeypatch.setattr(redis_client, "get_settings", lambda: settings)

        fake_client = AsyncMock()
        monkeypatch.setattr(redis_client.aioredis, "from_url", lambda *a, **kw: fake_client)

        written: list[str] = []
        logger.remove()
        sink_id = logger.add(written.append, format="{message}", level="DEBUG")
        try:
            await redis_client.init_redis()
        finally:
            logger.remove(sink_id)
            redis_client._redis = None

        blob = "\n".join(written)
        assert FAKE_PASSWORD not in blob, "the redis password reached a log sink"
        assert "redis:6379" in blob
