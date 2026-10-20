"""SEC.CHAT storage upgrades and revocation races use real PostgreSQL and HTTP."""

import asyncio
import re
from contextlib import suppress
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.schema import CreateSchema, DropSchema
from test_chat_ownership import a3_redis as a3_redis
from test_chat_ownership import a3_redis_container as a3_redis_container
from test_chat_ownership import bearer, chat_routes, mutate, post_human, snapshot
from test_chat_ownership import chat_api as chat_api
from test_chat_ownership import usage_pg as usage_pg
from test_chat_ownership import usage_world as usage_world

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
MIGRATIONS = Path(__file__).resolve().parents[2] / "db/migrations"


@pytest.fixture
async def legacy_chat(usage_pg):
    """Upgrade an owned private schema from the actual pre-V100 migration chain."""
    url = usage_pg.get_connection_url().replace("psycopg2", "asyncpg")
    admin, schema = create_async_engine(url), "chat_ownership_" + uuid4().hex
    async with admin.begin() as db:
        await db.execute(text('CREATE EXTENSION IF NOT EXISTS "uuid-ossp" WITH SCHEMA public'))
        await db.execute(CreateSchema(schema))
    engine = create_async_engine(
        url, connect_args={"server_settings": {"search_path": f"{schema},public"}}
    )
    try:
        paths = sorted(
            (p for p in MIGRATIONS.glob("V*.sql") if re.match(r"V\d+__", p.name)),
            key=lambda p: int(p.name.split("__")[0][1:]),
        )
        async with engine.begin() as db:
            raw = await db.get_raw_connection()
            assert raw.driver_connection is not None
            for path in paths:
                if int(path.name.split("__")[0][1:]) < 100:
                    await raw.driver_connection.execute(path.read_text())
            agent = await db.scalar(
                text(
                    "INSERT INTO agents (name, handle) "
                    "VALUES ('Legacy agent', 'legacy-chat') RETURNING id"
                )
            )
            project = await db.scalar(
                text(
                    "INSERT INTO projects (title, creator_agent_id) "
                    "VALUES ('Legacy project', :agent) RETURNING id"
                ),
                {"agent": agent},
            )
            for table in ("agent_messages", "project_messages"):
                columns, values = (
                    "sender_type, human_name, content",
                    "'human', 'Legacy', 'Kept text'",
                )
                if table == "project_messages":
                    columns, values = columns + ", project_id", values + ", :project"
                await db.execute(
                    text(f"INSERT INTO {table} ({columns}) VALUES ({values})"), {"project": project}
                )
        yield SimpleNamespace(maker=async_sessionmaker(engine), schema=schema)
    finally:
        await engine.dispose()
        async with admin.begin() as db:
            await db.execute(DropSchema(schema, cascade=True))
        await admin.dispose()


async def legacy_rows(maker):
    async with maker() as db:
        return {
            table: list(
                (
                    await db.execute(
                        text(
                            f"SELECT id, content, human_name, created_at, edited_at, is_deleted "
                            f"FROM {table} ORDER BY id"
                        )
                    )
                ).mappings()
            )
            for table in ("agent_messages", "project_messages")
        }


async def test_actual_upgrade_is_idempotent_preserves_legacy_and_enforces_fk(legacy_chat):
    before = await legacy_rows(legacy_chat.maker)
    async with legacy_chat.maker.begin() as db:
        raw = await db.connection()
        driver = (await raw.get_raw_connection()).driver_connection
        assert driver is not None
        migration = (MIGRATIONS / "V100__add_chat_sender_user_id.sql").read_text()
        await driver.execute(migration)
        await driver.execute(migration)
    assert await legacy_rows(legacy_chat.maker) == before
    for table in before:
        async with legacy_chat.maker() as db:
            owners = (await db.execute(text(f"SELECT sender_user_id FROM {table}"))).scalars().all()
            assert owners == [None]
            constraint = (
                (
                    await db.execute(
                        text(
                            "SELECT format_type(a.atttypid, a.atttypmod) AS type, "
                            "a.attnotnull AS required, c.confdeltype::text AS delete, "
                            "c.confrelid = 'users'::regclass AS references_users "
                            "FROM pg_attribute a JOIN pg_constraint c ON c.conrelid = a.attrelid "
                            "AND a.attnum = ANY(c.conkey) AND c.contype = 'f' "
                            "WHERE a.attrelid = CAST(:table AS regclass) "
                            "AND a.attname = 'sender_user_id'"
                        ),
                        {"table": table},
                    )
                )
                .mappings()
                .one()
            )
            assert dict(constraint) == {
                "type": "uuid",
                "required": False,
                "delete": "n",
                "references_users": True,
            }
        with pytest.raises(IntegrityError):
            async with legacy_chat.maker.begin() as db:
                await db.execute(text(f"UPDATE {table} SET sender_user_id = :id"), {"id": uuid4()})
        async with legacy_chat.maker() as db:
            assert await db.scalar(text(f"SELECT sender_user_id FROM {table}")) is None


@pytest.mark.parametrize("scope", ["public", "project"])
@pytest.mark.parametrize("method", ["PATCH", "DELETE"])
async def test_agent_cannot_mutate_human_message(chat_api, scope, method):
    message_id = await post_human(chat_api, scope)
    before = await snapshot(chat_api, scope, message_id)
    async with chat_api.redis.pubsub() as events:
        await events.subscribe("agentspore:chat")
        acknowledgement = await events.get_message(timeout=1)
        assert acknowledgement is not None
        assert acknowledgement["type"] == "subscribe"
        assert acknowledgement["channel"] == "agentspore:chat"
        response = await chat_api.client.request(
            method,
            chat_routes(chat_api, scope)["agent_mutation"] + "/" + message_id,
            headers={"X-API-Key": chat_api.world.keys[0]},
            json={"content": "Agent overwrite"},
        )
        assert response.status_code == 403
        assert await snapshot(chat_api, scope, message_id) == before
        assert await events.get_message(ignore_subscribe_messages=True, timeout=0.2) is None


@pytest.mark.parametrize("scope", ["public", "project"])
async def test_cursor_history_keeps_authenticated_owner(chat_api, scope):
    older = await post_human(chat_api, scope)
    newer = await post_human(chat_api, scope)
    routes = chat_routes(chat_api, scope)
    async with chat_api.world.maker.begin() as db:
        await db.execute(
            text(
                f"UPDATE {routes['table']} SET created_at = "
                "TIMESTAMPTZ '2026-01-01 00:00:00+00' WHERE id = :id"
            ),
            {"id": older},
        )
    response = await chat_api.client.get(
        routes["read"],
        params={"limit": 1, "before": newer},
        headers=bearer(chat_api.world.owner),
    )
    assert response.status_code == 200
    assert len(response.json()) == 1
    message = response.json()[0]
    assert message["id"] == older
    assert message["sender_user_id"] == str(chat_api.world.owner)
    assert message["content"] == "Owner original"


async def isolated_author_message(case, scope):
    user_id = uuid4()
    async with case.world.maker.begin() as db:
        await db.execute(
            text(
                "INSERT INTO users (id, email, hashed_password, name, is_verified) "
                "VALUES (:id, :email, 'synthetic', 'Isolated author', TRUE)"
            ),
            {"id": user_id, "email": f"{user_id}@example.invalid"},
        )
    response = await case.client.post(
        chat_routes(case, scope)["create"],
        headers=bearer(user_id),
        json={"content": "Isolated original", "name": "Ignored name"},
    )
    assert response.status_code == 200
    return user_id, response.json()["message_id"]


@pytest.mark.parametrize("scope", ["public", "project"])
async def test_deleted_user_keeps_message_with_null_owner(chat_api, scope):
    user_id, message_id = await isolated_author_message(chat_api, scope)
    before = await snapshot(chat_api, scope, message_id)
    async with chat_api.world.maker.begin() as db:
        await db.execute(text("DELETE FROM users WHERE id = :id"), {"id": user_id})
    assert await snapshot(chat_api, scope, message_id) == before
    table = chat_routes(chat_api, scope)["table"]
    async with chat_api.world.maker() as db:
        assert (
            await db.scalar(
                text(f"SELECT sender_user_id FROM {table} WHERE id = :id"),
                {"id": message_id},
            )
            is None
        )


async def wait_for_blocked_update(maker, blocker_pid, table):
    async with asyncio.timeout(15):
        async with maker() as db:
            while True:
                blocked = await db.scalar(
                    text(
                        "SELECT pid FROM pg_stat_activity WHERE datname = current_database() "
                        "AND state = 'active' AND wait_event_type = 'Lock' "
                        "AND :blocker = ANY(pg_blocking_pids(pid)) "
                        "AND query ~* :pattern"
                    ),
                    {"blocker": blocker_pid, "pattern": rf"UPDATE\s+{table}\s"},
                )
                if blocked is not None:
                    return
                await db.execute(text("SELECT pg_stat_clear_snapshot()"))
                await asyncio.sleep(0)


@pytest.mark.parametrize("scope", ["public", "project"])
@pytest.mark.parametrize("method", ["PATCH", "DELETE"])
async def test_revoked_owner_cannot_complete_queued_mutation(chat_api, scope, method):
    user_id, message_id = await isolated_author_message(chat_api, scope)
    before, table = (
        await snapshot(chat_api, scope, message_id),
        chat_routes(chat_api, scope)["table"],
    )
    pending = None
    async with chat_api.redis.pubsub() as events:
        await events.subscribe("agentspore:chat")
        acknowledgement = await events.get_message(timeout=1)
        assert acknowledgement is not None
        assert acknowledgement["type"] == "subscribe"
        assert acknowledgement["channel"] == "agentspore:chat"
        try:
            async with chat_api.world.maker.begin() as locker:
                await locker.execute(
                    text(f"SELECT id FROM {table} WHERE id = :id FOR UPDATE"), {"id": message_id}
                )
                blocker_pid = await locker.scalar(text("SELECT pg_backend_pid()"))
                pending = asyncio.create_task(
                    mutate(chat_api, scope, message_id, method, bearer(user_id))
                )
                await wait_for_blocked_update(chat_api.world.maker, blocker_pid, table)
                await locker.execute(text("DELETE FROM users WHERE id = :id"), {"id": user_id})
            async with asyncio.timeout(15):
                response = await pending
            assert response.status_code == 403
            assert await snapshot(chat_api, scope, message_id) == before
            async with chat_api.world.maker() as db:
                assert (
                    await db.scalar(
                        text(f"SELECT sender_user_id FROM {table} WHERE id = :id"),
                        {"id": message_id},
                    )
                    is None
                )
            assert await events.get_message(ignore_subscribe_messages=True, timeout=0.2) is None
        finally:
            if pending is not None:
                pending.cancel()
                with suppress(asyncio.CancelledError):
                    await pending
