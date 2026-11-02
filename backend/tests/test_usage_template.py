"""Real PostgreSQL checks for the migrated usage template and clone lifecycle."""

import pytest
import test_service_usage as usage_fixtures
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine
from test_service_usage import usage_database

usage_pg = usage_fixtures.usage_pg


def maintenance(pg):
    url = make_url(pg.get_connection_url()).set(
        drivername="postgresql+asyncpg", database="postgres"
    )
    return create_async_engine(url, isolation_level="AUTOCOMMIT")


async def clones(admin):
    async with admin.connect() as conn:
        return set(
            (
                await conn.execute(
                    text("SELECT datname FROM pg_database WHERE starts_with(datname, 'usage_')")
                )
            ).scalars()
        )


async def test_committed_schema_and_rows_do_not_escape_clone(usage_pg):
    admin = maintenance(usage_pg)
    try:
        before = await clones(admin)
        async with usage_database(usage_pg) as first, usage_database(usage_pg) as second:
            async with first.begin() as conn:
                await conn.execute(text("CREATE TABLE isolation_marker (value integer)"))
                await conn.execute(text("INSERT INTO isolation_marker VALUES (7)"))
                await conn.execute(text("DROP TABLE service_visits CASCADE"))
            async with second.connect() as conn:
                assert await conn.scalar(text("SELECT to_regclass('isolation_marker')")) is None
                assert await conn.scalar(text("SELECT to_regclass('service_visits')")) is not None
            assert len(await clones(admin) - before) == 2
        async with usage_database(usage_pg) as third:
            async with third.connect() as conn:
                assert await conn.scalar(text("SELECT to_regclass('isolation_marker')")) is None
                assert await conn.scalar(text("SELECT to_regclass('service_visits')")) is not None
        assert await clones(admin) == before
        async with admin.connect() as conn:
            template = make_url(usage_pg.get_connection_url()).database
            assert (
                await conn.scalar(
                    text("SELECT count(*) FROM pg_stat_activity WHERE datname = :name"),
                    {"name": template},
                )
                == 0
            )
            assert (
                await conn.scalar(
                    text("SELECT datallowconn FROM pg_database WHERE datname = :name"),
                    {"name": template},
                )
                is False
            )
    finally:
        await admin.dispose()


async def test_body_failure_still_drops_clone(usage_pg):
    admin = maintenance(usage_pg)
    try:
        before = await clones(admin)
        with pytest.raises(ValueError, match="synthetic body failure"):
            async with usage_database(usage_pg) as engine:
                async with engine.begin() as conn:
                    await conn.execute(text("CREATE TABLE body_failure_marker (value integer)"))
                raise ValueError("synthetic body failure")
        assert await clones(admin) == before
    finally:
        await admin.dispose()


async def test_leaked_connection_fails_cleanup_without_terminating_it(usage_pg):
    admin = maintenance(usage_pg)
    leaked = None
    database = None
    try:
        before = await clones(admin)
        with pytest.raises(DBAPIError):
            async with usage_database(usage_pg) as engine:
                database = engine.url.database
                leaked = await engine.connect()
                assert await leaked.scalar(text("SELECT 1")) == 1
        assert leaked is not None
        assert await leaked.scalar(text("SELECT 1")) == 1
        assert database in await clones(admin)
        await leaked.invalidate()
        await leaked.close()
        leaked = None
        async with admin.connect() as conn:
            name = conn.dialect.identifier_preparer.quote_identifier(database)
            await conn.execute(text(f"DROP DATABASE {name}"))
        assert await clones(admin) == before
    finally:
        if leaked is not None:
            await leaked.invalidate()
            await leaked.close()
        await admin.dispose()
