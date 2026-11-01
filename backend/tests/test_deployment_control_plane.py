"""A4 admission contract through the real router, service and PostgreSQL.

These tests do not exercise builds, sandbox startup or tenant isolation.
The coordinator runs them separately under the local resource/time budget.
"""

import re
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from testcontainers.postgres import PostgresContainer

from app.api.v1.agents import router
from app.core.database import get_db
from app.services.agent_service import AgentService, get_agent_service

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]
MIGRATIONS = Path(__file__).resolve().parents[2] / "db" / "migrations"


@pytest.fixture(scope="module")
def deployment_postgres() -> Iterator[PostgresContainer]:
    """Own one bounded test database; never reuse or stop another container."""
    container = PostgresContainer("postgres:16-alpine").with_kwargs(
        mem_limit="192m",
        memswap_limit="192m",
        nano_cpus=500_000_000,
        pids_limit=128,
    )
    with container as postgres:
        yield postgres


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def deployment_engine(deployment_postgres) -> AsyncIterator[AsyncEngine]:
    """Use the baseline versioned/repeatable migrations, as the A2 harness does."""
    url = deployment_postgres.get_connection_url().replace("psycopg2", "asyncpg")
    engine = create_async_engine(url, pool_size=2, max_overflow=0)
    versioned = sorted(
        (path for path in MIGRATIONS.glob("V*.sql") if re.match(r"V\d+__", path.name)),
        key=lambda path: int(path.name.split("__")[0][1:]),
    )
    migrations = [path for path in versioned if int(path.name.split("__")[0][1:]) <= 90]
    migrations += [
        MIGRATIONS / name
        for name in (
            "V92__service_usage.sql",
            "V94__project_deployments.sql",
            "V107__project_deployments_quota_identity.sql",
            "V110__project_deployments_lifecycle.sql",
        )
        if (MIGRATIONS / name).is_file()
    ]
    migrations += sorted(MIGRATIONS.glob("R__*.sql"))
    try:
        async with engine.begin() as connection:
            raw = await connection.get_raw_connection()
            driver = raw.driver_connection
            assert driver is not None
            for migration in migrations:
                await driver.execute(migration.read_text())
        yield engine
    finally:
        await engine.dispose()


@pytest_asyncio.fixture(loop_scope="module")
async def deployment_session(deployment_engine) -> AsyncIterator[AsyncSession]:
    async with AsyncSession(deployment_engine, expire_on_commit=False) as session:
        yield session


@pytest_asyncio.fixture(loop_scope="module")
async def deployment_project(deployment_session) -> dict[str, str]:
    """Seed two real API identities and an already available project version."""
    project_id = str(uuid4())
    owner_id = str(uuid4())
    keys = {"owner": uuid4().hex, "outsider": uuid4().hex}
    for role, key in keys.items():
        agent_id = owner_id if role == "owner" else str(uuid4())
        await deployment_session.execute(
            text(
                "INSERT INTO agents (id, name, handle, api_key_hash) "
                "VALUES (:id, :name, :handle, :hash)"
            ),
            {
                "id": agent_id,
                "name": f"A4-{agent_id}",
                "handle": f"a4-{agent_id}",
                "hash": AgentService.hash_api_key(key),
            },
        )
    await deployment_session.execute(
        text(
            "INSERT INTO projects "
            "(id, title, creator_agent_id, repo_url, deploy_url, status) "
            "VALUES (:id, 'A4 fixture', :owner, :repo, :url, 'deployed')"
        ),
        {
            "id": project_id,
            "owner": owner_id,
            "repo": "https://github.com/AgentSpore/a4-trusted-fixture",
            "url": f"https://a4-{project_id}.agentspore.com",
        },
    )
    await deployment_session.commit()
    return {"id": project_id, **keys, "invalid": uuid4().hex}


@pytest_asyncio.fixture(loop_scope="module")
async def deployment_client(deployment_engine) -> AsyncIterator[AsyncClient]:
    """Keep real authentication and producers; Redis is unused by admission."""
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    sessions = async_sessionmaker(deployment_engine, expire_on_commit=False)

    async def database() -> AsyncIterator[AsyncSession]:
        async with sessions() as session:
            yield session

    def service(db: AsyncSession = Depends(get_db)) -> AgentService:
        return AgentService(db)

    app.dependency_overrides[get_db] = database
    app.dependency_overrides[get_agent_service] = service
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


@pytest.mark.parametrize(
    "admission",
    [
        pytest.param(("owner", "a" * 40, 503), id="owner-flag-off-fails-closed"),
        pytest.param(("outsider", "a" * 40, 403), id="non-owner-before-isolation"),
        pytest.param(("owner", "refs/heads/main", 422), id="branch-is-not-a-pinned-sha"),
        pytest.param(("owner", "a" * 39, 422), id="truncated-sha-rejected"),
        pytest.param(("invalid", "a" * 40, 401), id="invalid-key-before-isolation"),
    ],
)
async def test_deploy_admission_preserves_active_project(
    deployment_client,
    deployment_project,
    deployment_session,
    admission: tuple[str, str, int],
) -> None:
    """Reject unsafe admission without announcing or replacing a deployment."""
    identity, commit_sha, expected_status = admission
    project_id = deployment_project["id"]
    before = (
        (
            await deployment_session.execute(
                text("SELECT deploy_url, status FROM projects WHERE id = :id"),
                {"id": project_id},
            )
        )
        .mappings()
        .one()
    )
    response = await deployment_client.post(
        f"/api/v1/agents/projects/{project_id}/deploy",
        headers={"X-API-Key": deployment_project[identity], "Idempotency-Key": str(uuid4())},
        json={"commit_sha": commit_sha},
    )
    assert response.status_code == expected_status
    body = response.json()
    assert body.get("status") != "deployed"
    assert "deployment_id" not in body
    assert "deploy_url" not in body
    after = (
        (
            await deployment_session.execute(
                text("SELECT deploy_url, status FROM projects WHERE id = :id"),
                {"id": project_id},
            )
        )
        .mappings()
        .one()
    )
    assert dict(after) == dict(before)
