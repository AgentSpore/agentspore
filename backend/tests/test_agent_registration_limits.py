"""A3 admission acceptance tests exercise real HTTP, PostgreSQL and Redis."""

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.schema import CreateSchema, DropSchema
from testcontainers.postgres import PostgresContainer
from testcontainers.redis import RedisContainer

from app.api.v1.agents import router
from app.core.config import get_settings
from app.core.database import get_db
from app.core.redis_client import get_redis
from app.services import agent_admission

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
MIGRATIONS = (
    "V1__init_schema.sql",
    "V2__agent_platform.sql",
    "V3__gitea_integration.sql",
    "V4__agent_oauth.sql",
    "V5__hackathons_and_agent_dna.sql",
    "V6__remove_gitea.sql",
    "V7__web3_ownership.sql",
    "V12__agent_handle_github_activity.sql",
    "V14__project_governance.sql",
    "V15__unique_agent_name.sql",
    "V16__gitlab_vcs_provider.sql",
    "V20__agent_teams.sql",
    "V28__agent_owner_email.sql",
    "V37__github_proxy_audit.sql",
    "V61__email_verification.sql",
    "V81__commit_sha_dedup.sql",
    "V93__agent_code_karma_limits.sql",
)


@pytest.fixture(scope="session")
def a3_pg():
    """Own one capped PostgreSQL container for these acceptance tests."""
    with PostgresContainer(
        "postgres:16-alpine",
        mem_limit="384m",
        nano_cpus=500000000,
        labels={"a3.owner": "a3-acceptance-20261016"},
    ) as container:
        yield container


@pytest_asyncio.fixture
async def a3_db(a3_pg):
    """Apply real migrations in a private schema; never duplicate award SQL."""
    url = a3_pg.get_connection_url().replace("psycopg2", "asyncpg")
    admin = create_async_engine(url)
    schema = "a3_" + uuid4().hex
    async with admin.begin() as db:
        await db.execute(text('CREATE EXTENSION IF NOT EXISTS "uuid-ossp" WITH SCHEMA public'))
        await db.execute(CreateSchema(schema))
    engine = create_async_engine(
        url,
        pool_size=12,
        max_overflow=0,
        connect_args={"server_settings": {"search_path": f"{schema},public"}},
    )
    try:
        async with engine.begin() as db:
            connection = await db.get_raw_connection()
            driver = connection.driver_connection
            assert driver is not None
            for name in MIGRATIONS:
                path = Path(__file__).resolve().parents[2] / "db/migrations" / name
                await driver.execute(path.read_text())
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()
        async with admin.begin() as db:
            await db.execute(DropSchema(schema, cascade=True))
        await admin.dispose()


@pytest.fixture(scope="session")
def a3_redis_container():
    """Own Redis separately; admission counters must not be mocked."""
    with RedisContainer(
        "redis:7-alpine",
        mem_limit="64m",
        nano_cpus=100000000,
        labels={"a3.owner": "a3-acceptance-20261016"},
    ) as container:
        yield container


@pytest_asyncio.fixture
async def a3_redis(a3_redis_container):
    """Use only the fresh, fixture-owned Redis database."""
    client = Redis(
        host=a3_redis_container.get_container_host_ip(),
        port=int(a3_redis_container.get_exposed_port(6379)),
        decode_responses=True,
    )
    try:
        await client.flushdb()
        yield client
    finally:
        await client.aclose()


@pytest_asyncio.fixture
async def registration_app(a3_db, a3_redis):
    """Keep route/service/repository DI real; replace infrastructure locations."""
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.state.registration_barrier = None
    app.state.registration_pids = []

    async def request_db():
        async with a3_db.begin() as db:
            if app.state.registration_barrier is not None:
                app.state.registration_pids.append(await db.scalar(text("SELECT pg_backend_pid()")))
                await asyncio.wait_for(app.state.registration_barrier.wait(), timeout=20)
            yield db

    app.dependency_overrides[get_db] = request_db
    app.dependency_overrides[get_redis] = lambda: a3_redis
    yield app
    app.dependency_overrides.clear()


@pytest.fixture
def registration_body():
    """Match AgentRegisterRequest without triggering outbound skill registration."""
    return {
        "model_provider": "test",
        "model_name": "synthetic",
        "skills": [],
        "owner_email": "a3-owner@example.test",
    }


async def test_fifty_registrations_same_address_admits_only_first_five(
    registration_app,
    registration_body,
    a3_db,
) -> None:
    """A3: distinct valid names must not bypass the five-registration limit."""
    transport = ASGITransport(app=registration_app, client=("203.0.113.10", 40000))
    statuses = []
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        for index in range(50):
            response = await client.post(
                "/api/v1/agents/register",
                json={**registration_body, "name": f"A3-valid-agent-{index}"},
            )
            statuses.append(response.status_code)
    assert statuses[:5] == [200] * 5
    assert statuses[5:] == [429] * 45
    async with a3_db() as db:
        assert await db.scalar(text("SELECT count(*) FROM agents")) == 5


async def test_exhausted_address_does_not_consume_other_address_quota(
    registration_app,
    registration_body,
) -> None:
    """A second direct peer gets five admissions after the first is exhausted."""
    results = []
    for address in ("203.0.113.10", "203.0.113.11"):
        statuses = []
        transport = ASGITransport(app=registration_app, client=(address, 40000))
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            for index in range(6):
                response = await client.post(
                    "/api/v1/agents/register",
                    json={**registration_body, "name": f"A3-{address}-{index}"},
                )
                statuses.append(response.status_code)
        results.append(statuses)
    assert results == [[200] * 5 + [429], [200] * 5 + [429]]


async def test_concurrent_registration_connections_admit_at_most_five(
    registration_app,
    registration_body,
    a3_db,
) -> None:
    """All contenders reach separate DB connections before admission proceeds."""
    registration_app.state.registration_barrier = asyncio.Barrier(12)
    transport = ASGITransport(app=registration_app, client=("203.0.113.12", 40000))
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        async with asyncio.TaskGroup() as group:
            jobs = [
                group.create_task(
                    client.post(
                        "/api/v1/agents/register",
                        json={**registration_body, "name": f"A3-concurrent-{index}"},
                    )
                )
                for index in range(12)
            ]
    statuses = [job.result().status_code for job in jobs]
    assert len(set(registration_app.state.registration_pids)) == 12
    assert statuses.count(200) == 5
    assert statuses.count(429) == 7
    async with a3_db() as db:
        assert await db.scalar(text("SELECT count(*) FROM agents")) == 5


@pytest.mark.parametrize("second", ["2001:0db8:0:0:0:0:0:1", "2001:db8::1"])
async def test_equivalent_ipv6_addresses_share_admission(
    registration_app, registration_body, second
):
    """Expanded/compressed addresses identify the same caller."""
    statuses = []
    for address in ["2001:db8::1"] * 5 + [second]:
        transport = ASGITransport(app=registration_app, client=(address, 40000))
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/v1/agents/register",
                json={
                    **registration_body,
                    "name": f"A3-ipv6-{len(statuses)}",
                },
            )
            statuses.append(response.status_code)
    assert statuses == [200] * 5 + [429]


async def test_mapped_ipv4_and_spoofed_forwarding_share_quota(registration_app, registration_body):
    """Mapped IPv4 normalizes; untrusted XFF cannot create fresh budgets."""
    statuses = []
    for index in range(6):
        address = "203.0.113.10" if index < 5 else "::ffff:203.0.113.10"
        transport = ASGITransport(app=registration_app, client=(address, 40000))
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/v1/agents/register",
                json={
                    **registration_body,
                    "name": f"A3-mapped-{index}",
                },
                headers={"X-Forwarded-For": f"198.51.100.{index}"},
            )
            statuses.append(response.status_code)
    assert statuses == [200] * 5 + [429]
    assert 1 <= int(response.headers["Retry-After"]) <= 86400


@pytest.mark.parametrize("address", [None, ("unknown", 1), ("fe80::1%eth0", 1)])
async def test_missing_or_malformed_address_fails_closed(
    registration_app, registration_body, address
):
    """Unknown callers never share a permissive fallback bucket."""
    async with AsyncClient(
        transport=ASGITransport(app=registration_app, client=address), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/v1/agents/register",
            json={
                **registration_body,
                "name": "A3-invalid-address",
            },
        )
    assert response.status_code == 400


async def test_duplicate_attempts_consume_quota_but_validation_does_not(
    registration_app,
    registration_body,
):
    """Only validated requests enter admission; later DB rollback does not refund."""
    async with AsyncClient(
        transport=ASGITransport(app=registration_app, client=("203.0.113.10", 1)),
        base_url="http://test",
    ) as client:
        assert (await client.post("/api/v1/agents/register", json={})).status_code == 422
        statuses = [
            (
                await client.post(
                    "/api/v1/agents/register",
                    json={
                        **registration_body,
                        "name": "A3-duplicate",
                    },
                )
            ).status_code
            for _ in range(6)
        ]
    assert statuses == [200] + [409] * 4 + [429]


async def test_day_rollover_expires_private_counter(
    registration_app,
    registration_body,
    a3_redis,
    monkeypatch,
):
    """UTC midnight grants a fresh quota; Redis contains no raw address."""
    clock = [datetime.now(timezone.utc).replace(hour=23, minute=59, second=59, microsecond=0)]

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock[0]

    monkeypatch.setattr(agent_admission, "datetime", Clock)
    async with AsyncClient(
        transport=ASGITransport(app=registration_app, client=("203.0.113.10", 1)),
        base_url="http://test",
    ) as client:
        for index in range(6):
            response = await client.post(
                "/api/v1/agents/register",
                json={
                    **registration_body,
                    "name": f"A3-midnight-{index}",
                },
            )
        assert response.status_code == 429
        assert response.headers["Retry-After"] == "1"
        keys = await a3_redis.keys("agent-registration:*")
        assert len(keys) == 1 and "203.0.113.10" not in keys[0]
        assert 0 < await a3_redis.ttl(keys[0]) <= 86400
        clock[0] += timedelta(seconds=1)
        response = await client.post(
            "/api/v1/agents/register",
            json={
                **registration_body,
                "name": "A3-next-day",
            },
        )
        assert response.status_code == 200


async def test_admission_outage_returns_503(registration_app, registration_body):
    """An unavailable admission backend cannot permit registration."""
    unavailable = Redis(host="127.0.0.1", port=1, socket_connect_timeout=0.1)
    registration_app.dependency_overrides[get_redis] = lambda: unavailable
    try:
        async with AsyncClient(
            transport=ASGITransport(app=registration_app, client=("203.0.113.10", 1)),
            base_url="http://test",
        ) as client:
            response = await client.post(
                "/api/v1/agents/register",
                json={
                    **registration_body,
                    "name": "A3-outage",
                },
            )
        assert response.status_code == 503
    finally:
        await unavailable.aclose()


async def test_trusted_proxy_resolves_independent_forwarded_addresses(
    registration_app,
    registration_body,
    monkeypatch,
):
    """Trusted peers use the existing shared XFF policy before normalization."""
    monkeypatch.setattr(get_settings(), "trusted_proxy_ips", ["127.0.0.1"])
    async with AsyncClient(
        transport=ASGITransport(app=registration_app, client=("127.0.0.1", 1)),
        base_url="http://test",
    ) as client:
        statuses = []
        for index in range(6):
            response = await client.post(
                "/api/v1/agents/register",
                json={
                    **registration_body,
                    "name": f"A3-forwarded-{index}",
                },
                headers={"X-Forwarded-For": "203.0.113.10"},
            )
            statuses.append(response.status_code)
        assert statuses == [200] * 5 + [429]
        response = await client.post(
            "/api/v1/agents/register",
            json={
                **registration_body,
                "name": "A3-independent-forwarded",
            },
            headers={"X-Forwarded-For": "203.0.113.11"},
        )
        assert response.status_code == 200


@pytest.mark.parametrize("email", ["omitted", None, "legacy@example.invalid"])
async def test_agent_first_registration_is_active_and_unclaimed(registration_app, a3_db, email):
    payload = {"name": "Agent-" + uuid4().hex, "model_provider": "test", "model_name": "synthetic"}
    if email != "omitted":
        payload["owner_email"] = email
    if email is not None and email != "omitted":
        async with a3_db.begin() as db:
            await db.execute(
                text(
                    "INSERT INTO users(id,email,name,hashed_password,is_verified) VALUES "
                    "(:id,:email,'Synthetic','unused',TRUE)"
                ),
                {"id": uuid4(), "email": email},
            )
    async with AsyncClient(
        transport=ASGITransport(app=registration_app), base_url="http://test"
    ) as client:
        response = await client.post("/api/v1/agents/register", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["owner_status"] == "unclaimed"
    assert data["claim_url"] == "/agents/" + data["agent_id"] + "/claim"
    async with a3_db() as db:
        row = (
            await db.execute(
                text("SELECT is_active,owner_user_id FROM agents WHERE id=:id"),
                {"id": data["agent_id"]},
            )
        ).one()
        assert row.is_active and row.owner_user_id is None
