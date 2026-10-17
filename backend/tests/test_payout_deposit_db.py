"""Deposit persistence uses real V30 SQL and independent PostgreSQL transactions."""

from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from test_marketplace_tasks import marketplace_pg as marketplace_pg


@pytest.fixture
async def deposit_db(marketplace_pg):
    url = marketplace_pg.get_connection_url().replace("psycopg2", "asyncpg")
    schema = "deposit_" + uuid4().hex
    admin = create_async_engine(url)
    async with admin.begin() as db:
        await db.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(url, connect_args={"server_settings": {"search_path": schema}})
    owner, other = uuid4(), uuid4()
    async with engine.begin() as db:
        await db.execute(text("CREATE TABLE users (id UUID PRIMARY KEY, updated_at TIMESTAMPTZ)"))
        raw = await db.get_raw_connection()
        migration = Path(__file__).resolve().parents[2] / "db/migrations/V30__aspore_balance.sql"
        driver = raw.driver_connection
        assert driver is not None
        await driver.execute(migration.read_text())
        await db.execute(
            text("INSERT INTO users(id) VALUES (:owner), (:other)"),
            {"owner": owner, "other": other},
        )
    try:
        yield async_sessionmaker(engine, expire_on_commit=False), owner, other
    finally:
        await engine.dispose()
        async with admin.begin() as db:
            await db.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin.dispose()


@pytest.fixture
async def unique_deposit_db(deposit_db):
    maker, _, _ = deposit_db
    migration = (
        Path(__file__).resolve().parents[2] / "db/migrations/V91__unique_aspore_deposits.sql"
    )
    async with maker.begin() as db:
        await db.execute(text(migration.read_text()))
    return deposit_db


async def test_v91_enforces_global_signature_uniqueness(unique_deposit_db):
    maker, owner, other = unique_deposit_db
    sql = text(
        "INSERT INTO aspore_transactions(user_id, tx_type, amount, balance_after, solana_tx) "
        "VALUES (:user, 'deposit', 3, 3, :signature)"
    )
    async with maker.begin() as db:
        await db.execute(sql, {"user": owner, "signature": "Synthetic duplicate"})
        await db.execute(sql, {"user": owner, "signature": None})
        await db.execute(sql, {"user": other, "signature": None})
    with pytest.raises(DBAPIError):
        async with maker.begin() as db:
            await db.execute(sql, {"user": other, "signature": "Synthetic duplicate"})
    async with maker() as db:
        assert await db.scalar(text("SELECT count(*) FROM aspore_transactions")) == 3


async def test_v91_duplicate_history_stops_without_deleting_rows(deposit_db):
    maker, owner, _ = deposit_db
    sql = text(
        "INSERT INTO aspore_transactions(user_id, tx_type, amount, balance_after, solana_tx) "
        "VALUES (:user, 'deposit', 3, 3, 'Synthetic historical duplicate')"
    )
    async with maker.begin() as db:
        await db.execute(sql, {"user": owner})
        await db.execute(sql, {"user": owner})
    migration = (
        Path(__file__).resolve().parents[2] / "db/migrations/V91__unique_aspore_deposits.sql"
    )
    with pytest.raises(DBAPIError):
        async with maker.begin() as db:
            await db.execute(text(migration.read_text()))
    async with maker() as db:
        assert await db.scalar(text("SELECT count(*) FROM aspore_transactions")) == 2
        assert await db.scalar(text("SELECT sum(aspore_balance) FROM users")) == 0
