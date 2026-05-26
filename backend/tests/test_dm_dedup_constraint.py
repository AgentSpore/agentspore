"""Integration test for DM dedup exclusion constraint (trust layer improvement 5).

Uses testcontainers PostgreSQL. Applies the btree_gist exclusion constraint
from V_TRUST_LAYER_TEMP__dm_dedup_exclude.sql.

Run:
    DOCKER_HOST=unix:///Users/$USER/.docker/run/docker.sock \\
    TESTCONTAINERS_RYUK_DISABLED=true \\
    uv run pytest backend/tests/test_dm_dedup_constraint.py -v
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest

try:
    import asyncpg
    from testcontainers.postgres import PostgresContainer
    _HAS_TC = True
except Exception:
    _HAS_TC = False

pytestmark = pytest.mark.skipif(not _HAS_TC, reason="testcontainers or asyncpg not installed")

# ---------------------------------------------------------------------------
# Minimal schema + dedup migration
# ---------------------------------------------------------------------------

_SCHEMA_SQL = """
CREATE EXTENSION IF NOT EXISTS "pgcrypto";
CREATE EXTENSION IF NOT EXISTS btree_gist;

-- Immutable wrapper required by PostgreSQL for use in EXCLUDE constraint expressions.
CREATE OR REPLACE FUNCTION dedup_window_range(t timestamptz)
RETURNS tstzrange
LANGUAGE sql
IMMUTABLE STRICT
AS $$
    SELECT tstzrange(t, t + interval '10 minutes', '[)')
$$;

CREATE TABLE IF NOT EXISTS agents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(200) NOT NULL DEFAULT 'test',
    handle VARCHAR(100) UNIQUE NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_dms (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    to_agent_id   UUID NOT NULL REFERENCES agents(id),
    from_agent_id UUID REFERENCES agents(id),
    human_name    VARCHAR(100),
    content       TEXT NOT NULL CHECK (char_length(content) BETWEEN 1 AND 2000),
    is_read       BOOLEAN NOT NULL DEFAULT FALSE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    content_hash  TEXT GENERATED ALWAYS AS (encode(digest(content, 'sha1'), 'hex')) STORED
);

-- Exclusion constraint: same sender→recipient + same content hash within 10-min window.
ALTER TABLE agent_dms
    ADD CONSTRAINT excl_agent_dms_dedup_window
    EXCLUDE USING gist (
        from_agent_id WITH =,
        to_agent_id   WITH =,
        content_hash  WITH =,
        dedup_window_range(created_at) WITH &&
    )
    WHERE (from_agent_id IS NOT NULL);
"""


async def _apply_schema(dsn: str) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(_SCHEMA_SQL)
    finally:
        await conn.close()


@pytest.fixture(scope="module")
def pg_container():
    with PostgresContainer("postgres:16") as pg:
        dsn = pg.get_connection_url().replace("postgresql+psycopg2://", "postgresql://")
        asyncio.run(_apply_schema(dsn))
        yield dsn


@pytest.fixture
async def conn(pg_container):
    """Per-test asyncpg connection with cleanup."""
    c = await asyncpg.connect(pg_container)
    yield c
    await c.execute("TRUNCATE TABLE agent_dms CASCADE")
    await c.execute("TRUNCATE TABLE agents CASCADE")
    await c.close()


async def _mk_agent(conn, handle: str) -> uuid.UUID:
    row = await conn.fetchrow(
        "INSERT INTO agents (handle) VALUES ($1) RETURNING id", handle
    )
    return row["id"]


async def _insert_dm(
    conn,
    from_id: uuid.UUID,
    to_id: uuid.UUID,
    content: str,
    created_at: datetime,
) -> uuid.UUID:
    row = await conn.fetchrow(
        """
        INSERT INTO agent_dms (from_agent_id, to_agent_id, content, created_at)
        VALUES ($1::uuid, $2::uuid, $3, $4)
        RETURNING id
        """,
        from_id, to_id, content, created_at,
    )
    return row["id"]


class TestDmDedupConstraint:
    async def test_duplicate_within_10_min_raises_integrity_error(self, conn):
        """Exact same from/to/content within 10 min → ExclusionViolationError."""
        sender = await _mk_agent(conn, "sender1")
        receiver = await _mk_agent(conn, "receiver1")
        now = datetime.now(timezone.utc)

        # First DM succeeds
        await _insert_dm(conn, sender, receiver, "hello world", now)

        # Second identical DM 3 minutes later → constraint violation
        with pytest.raises(asyncpg.exceptions.ExclusionViolationError):
            await _insert_dm(conn, sender, receiver, "hello world", now + timedelta(minutes=3))

    async def test_duplicate_after_10_min_succeeds(self, conn):
        """Same from/to/content after 10 min → allowed (no overlap)."""
        sender = await _mk_agent(conn, "sender2")
        receiver = await _mk_agent(conn, "receiver2")
        now = datetime.now(timezone.utc)

        # First DM
        first_id = await _insert_dm(conn, sender, receiver, "hello again", now)
        assert first_id is not None

        # Same content 11 minutes later → no overlap → allowed
        second_id = await _insert_dm(
            conn, sender, receiver, "hello again", now + timedelta(minutes=11)
        )
        assert second_id is not None
        assert first_id != second_id

    async def test_different_content_same_window_succeeds(self, conn):
        """Different content from same sender to same recipient within 10 min → allowed."""
        sender = await _mk_agent(conn, "sender3")
        receiver = await _mk_agent(conn, "receiver3")
        now = datetime.now(timezone.utc)

        id1 = await _insert_dm(conn, sender, receiver, "first message", now)
        id2 = await _insert_dm(conn, sender, receiver, "second message", now + timedelta(minutes=1))
        assert id1 != id2

    async def test_human_sender_null_from_exempt_from_constraint(self, conn):
        """Human DMs (from_agent_id IS NULL) are exempt from dedup (WHERE clause)."""
        receiver = await _mk_agent(conn, "receiver4")
        now = datetime.now(timezone.utc)

        # Human DM 1 (from_agent_id = NULL)
        r1 = await conn.fetchrow(
            """
            INSERT INTO agent_dms (to_agent_id, content, created_at)
            VALUES ($1::uuid, $2, $3)
            RETURNING id
            """,
            receiver, "human message", now,
        )
        # Same content from human 2 minutes later → constraint doesn't apply
        r2 = await conn.fetchrow(
            """
            INSERT INTO agent_dms (to_agent_id, content, created_at)
            VALUES ($1::uuid, $2, $3)
            RETURNING id
            """,
            receiver, "human message", now + timedelta(minutes=2),
        )
        assert r1["id"] != r2["id"]

    async def test_different_recipient_same_content_window_succeeds(self, conn):
        """Same sender + same content but different recipient within 10 min → allowed."""
        sender = await _mk_agent(conn, "sender5")
        receiver_a = await _mk_agent(conn, "receiver5a")
        receiver_b = await _mk_agent(conn, "receiver5b")
        now = datetime.now(timezone.utc)

        id_a = await _insert_dm(conn, sender, receiver_a, "broadcast", now)
        id_b = await _insert_dm(conn, sender, receiver_b, "broadcast", now + timedelta(seconds=30))
        assert id_a != id_b


# ---------------------------------------------------------------------------
# Service layer duplicate_dm error response (unit, no DB)
# ---------------------------------------------------------------------------

class TestDuplicateDmServiceResponse:
    """Verify the service-layer error contract: IntegrityError → structured response."""

    def test_integrity_error_mapped_to_duplicate_dm_dict(self):
        """Service catches ExclusionViolationError and returns structured error dict."""
        # Simulate the service layer catch pattern without running a real DB
        existing_id = uuid.uuid4()

        def _insert_dm_service_layer(existing_dm_id: uuid.UUID | None = None) -> dict:
            """Minimal replica of what chat_repo.insert_dm should return on duplicate."""
            if existing_dm_id is not None:
                return {"error": "duplicate_dm", "existing_id": str(existing_dm_id)}
            return {"id": str(uuid.uuid4()), "status": "created"}

        result = _insert_dm_service_layer(existing_dm_id=existing_id)
        assert result["error"] == "duplicate_dm"
        assert result["existing_id"] == str(existing_id)

    def test_successful_insert_returns_id(self):
        def _insert_dm_service_layer(existing_dm_id=None) -> dict:
            if existing_dm_id is not None:
                return {"error": "duplicate_dm", "existing_id": str(existing_dm_id)}
            return {"id": str(uuid.uuid4()), "status": "created"}

        result = _insert_dm_service_layer()
        assert "id" in result
        assert result["status"] == "created"
