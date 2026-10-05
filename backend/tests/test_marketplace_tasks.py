"""Marketplace tests use real migrations and separate transactions."""

from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer

from app.services.agent_service import AgentService


@pytest.fixture(scope="session")
def marketplace_pg():
    with PostgresContainer("postgres:16-alpine") as pg:
        print(f"Owned marketplace container: {pg.get_wrapped_container().id}")
        yield pg


@pytest.fixture
async def marketplace_db(marketplace_pg):
    engine = create_async_engine(marketplace_pg.get_connection_url().replace("psycopg2", "asyncpg"))
    base = """
        DROP TABLE IF EXISTS tasks, agent_activity, projects, agents CASCADE;
        CREATE TABLE agents (id UUID PRIMARY KEY, name TEXT NOT NULL,
            api_key_hash VARCHAR(64), is_active BOOLEAN DEFAULT TRUE, karma INTEGER DEFAULT 0);
        CREATE TABLE projects (id UUID PRIMARY KEY, title TEXT,
            creator_agent_id UUID REFERENCES agents(id), status TEXT DEFAULT 'active');
        CREATE TABLE agent_activity (id UUID DEFAULT gen_random_uuid(),
            agent_id UUID REFERENCES agents(id), project_id UUID REFERENCES projects(id),
            action_type TEXT, description TEXT, metadata JSONB);
        CREATE OR REPLACE FUNCTION update_updated_at_column() RETURNS TRIGGER AS $$
        BEGIN NEW.updated_at = CURRENT_TIMESTAMP; RETURN NEW; END; $$ LANGUAGE plpgsql;
    """
    async with engine.begin() as conn:
        raw = await conn.get_raw_connection()
        driver = raw.driver_connection
        assert driver is not None
        await driver.execute(base)
        for name in ("V8__tasks.sql", "V13__agent_notifications.sql"):
            migration = Path(__file__).resolve().parents[2] / "db/migrations" / name
            await driver.execute(migration.read_text())
    maker = async_sessionmaker(engine, expire_on_commit=False)
    owner, other, project_id = uuid4(), uuid4(), uuid4()
    keys = [str(uuid4()), str(uuid4())]
    async with maker.begin() as db:
        for agent_id, key in zip((owner, other), keys, strict=True):
            await db.execute(
                text(
                    "INSERT INTO agents (id, name, api_key_hash) "
                    "VALUES (:id, 'Synthetic agent', :hash)"
                ),
                {"id": agent_id, "hash": AgentService.hash_api_key(key)},
            )
        await db.execute(
            text(
                "INSERT INTO projects (id, title, creator_agent_id) "
                "VALUES (:id, 'Synthetic project', :owner)"
            ),
            {"id": project_id, "owner": owner},
        )
    yield maker, project_id, owner, other, keys
    await engine.dispose()
