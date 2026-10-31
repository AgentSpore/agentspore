"""F1 funnel: aggregate-only counters over real PostgreSQL and Redis."""

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
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.schema import CreateSchema, DropSchema
from testcontainers.postgres import PostgresContainer
from testcontainers.redis import RedisContainer

from app.api.deps import get_admin_user
from app.api.v1.auth import router as auth_router
from app.api.v1.funnel import get_funnel_service, router
from app.core.config import get_settings
from app.core.database import get_db
from app.core.redis_client import get_redis
from app.repositories.agent_repo import AgentRepository
from app.services.email_service import get_email_service
from app.services.funnel_service import FunnelService

MIGRATION_DIR = Path(__file__).resolve().parents[2] / "db/migrations"
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
    "V19__admin_and_prize_pool.sql",
    "V20__agent_teams.sql",
    "V21__user_oauth.sql",
    "V28__agent_owner_email.sql",
    "V29__solana_wallet_payouts.sql",
    "V30__aspore_balance.sql",
    "V37__github_proxy_audit.sql",
    "V61__email_verification.sql",
    "V81__commit_sha_dedup.sql",
    "V93__agent_code_karma_limits.sql",
    "V109__funnel_daily.sql",
)


def utc_today():
    return datetime.now(timezone.utc).date()


@pytest.fixture(scope="module")
def pg():
    with PostgresContainer("postgres:16-alpine", mem_limit="384m", nano_cpus=500000000) as c:
        yield c


@pytest.fixture(scope="module")
def redis_container():
    with RedisContainer("redis:7-alpine", mem_limit="64m", nano_cpus=100000000) as c:
        yield c


@pytest_asyncio.fixture
async def sessions(pg):
    url = pg.get_connection_url().replace("psycopg2", "asyncpg")
    admin = create_async_engine(url)
    schema = "f1_" + uuid4().hex
    async with admin.begin() as db:
        await db.execute(text('CREATE EXTENSION IF NOT EXISTS "uuid-ossp" WITH SCHEMA public'))
        await db.execute(CreateSchema(schema))
    engine = create_async_engine(
        url, connect_args={"server_settings": {"search_path": f"{schema},public"}}
    )
    try:
        async with engine.begin() as db:
            driver = (await db.get_raw_connection()).driver_connection
            assert driver is not None
            for name in MIGRATIONS:
                path = MIGRATION_DIR / name
                await driver.execute(path.read_text())
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()
        async with admin.begin() as db:
            await db.execute(DropSchema(schema, cascade=True))
        await admin.dispose()


@pytest_asyncio.fixture
async def redis(redis_container):
    client = Redis(
        host=redis_container.get_container_host_ip(),
        port=int(redis_container.get_exposed_port(6379)),
        decode_responses=True,
    )
    await client.flushdb()
    yield client
    await client.aclose()


@pytest.fixture
def svc(sessions, redis):
    return FunnelService(redis, session_factory=sessions)


@pytest_asyncio.fixture
async def app(sessions, svc):
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")

    async def request_db():
        async with sessions() as db:
            yield db
            await db.commit()

    app.dependency_overrides[get_db] = request_db
    app.dependency_overrides[get_funnel_service] = lambda: svc
    yield app
    app.dependency_overrides.clear()


def visitor(app, ip="203.0.113.5"):
    return AsyncClient(transport=ASGITransport(app=app, client=(ip, 40000)), base_url="http://t")


async def rows(sessions):
    async with sessions() as db:
        r = await db.execute(text("SELECT step, source, count FROM funnel_daily ORDER BY 1, 2"))
        return [tuple(x) for x in r]


async def visit(client, **body):
    return await client.post(
        "/api/v1/funnel/visit", json={"path": "/", **body}, headers={"User-Agent": "ua-1"}
    )


async def test_funnel_counts_aggregate_only(app, sessions, redis):
    async with visitor(app) as c:
        assert (await visit(c, utm_source="x")).status_code == 204
    async with sessions() as db:
        cols = [
            r[0]
            for r in await db.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name='funnel_daily' AND table_schema=current_schema()"
                )
            )
        ]
        dump = str(await rows(sessions))
    assert sorted(cols) == ["count", "day", "source", "step"]
    assert "203.0.113.5" not in dump and "ua-1" not in dump
    keys = await redis.keys("*")
    assert keys and not any("203.0.113.5" in k or "ua-1" in k for k in keys)


async def test_visit_dedup_same_day_salt_rotation(app, sessions, redis):
    async with visitor(app) as c:
        await visit(c)
        await visit(c)
        assert await rows(sessions) == [("visit", "direct", 1)]
        # a different visitor counts
        async with visitor(app, ip="203.0.113.6") as c2:
            await visit(c2)
        assert await rows(sessions) == [("visit", "direct", 2)]
        # a new UTC day brings a new salt: the same visitor counts again
        today = utc_today()
        await redis.flushdb()
        await visit(c)
        assert await redis.exists(f"funnel:salt:{today:%Y%m%d}") == 1
        ttl = await redis.ttl(f"funnel:seen:{today:%Y%m%d}:visit")
        assert 0 < ttl <= 48 * 3600


async def test_salt_expires_at_next_utc_midnight_plus_grace(app, redis):
    async with visitor(app) as c:
        await visit(c)
    now = datetime.now(timezone.utc)
    midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    ttl = await redis.ttl(f"funnel:salt:{now:%Y%m%d}")
    assert 0 < ttl <= (midnight - now).total_seconds() + 3600 + 5
    assert ttl > (midnight - now).total_seconds() - 5


async def test_user_agent_rotation_cannot_exceed_daily_ip_cap(app, sessions, monkeypatch):
    monkeypatch.setattr(get_settings(), "funnel_visit_rate_limit", 1000)
    async with visitor(app) as c:
        for n in range(30):
            r = await c.post(
                "/api/v1/funnel/visit", json={"path": "/"}, headers={"User-Agent": f"ua-{n}"}
            )
            assert r.status_code == 204
    assert await rows(sessions) == [("visit", "direct", 20)]


async def test_rate_limit_is_per_ip_not_per_user_agent(app, monkeypatch):
    monkeypatch.setattr(get_settings(), "funnel_visit_rate_limit", 3)
    async with visitor(app) as c:
        codes = [
            (
                await c.post(
                    "/api/v1/funnel/visit", json={"path": "/"}, headers={"User-Agent": f"ua-{n}"}
                )
            ).status_code
            for n in range(5)
        ]
    assert codes == [204, 204, 204, 429, 429]


@pytest.mark.parametrize(
    "utm,referrer,expected",
    [
        ("x", "", "x"),
        ("TELEGRAM", "", "telegram"),
        ("evil<script>", "", "other"),
        ("", "", "direct"),
        ("", "https://substack.com/p/abc", "substack"),
        ("", "https://t.co/xyz", "x"),
        ("", "https://unknown.example/" + "a" * 3000, "other"),
        ("", "not a url", "other"),
    ],
)
async def test_source_whitelist_maps_unknown_to_other(app, sessions, utm, referrer, expected):
    async with visitor(app) as c:
        body = {}
        if utm:
            body["utm_source"] = utm
        if referrer:
            body["referrer"] = referrer
        assert (await visit(c, **body)).status_code == 204
    assert await rows(sessions) == [("visit", expected, 1)]


async def test_visit_path_allowlist_and_length(app, sessions):
    async with visitor(app) as c:
        assert (await visit(c, path="/secret-admin-page")).status_code == 204
        assert (await visit(c, path="/" + "a" * 300)).status_code == 422
    assert await rows(sessions) == []


async def seed_agent(sessions, agent_id):
    async with sessions.begin() as db:
        await AgentRepository(db).insert_agent(
            {
                "id": agent_id,
                "name": "f1-agent",
                "handle": "f1-agent",
                "provider": "t",
                "model": "m",
                "spec": "programmer",
                "skills": [],
                "desc": "",
                "api_key": "h",
                "oauth_state": "s",
                "dna_risk": 5,
                "dna_speed": 5,
                "dna_verbosity": 5,
                "dna_creativity": 5,
                "bio": None,
                "owner_email": None,
                "owner_user_id": None,
            }
        )


async def test_first_heartbeat_counted_once(sessions):
    agent_id = uuid4()
    await seed_agent(sessions, agent_id)
    for _ in range(3):
        async with sessions.begin() as db:
            await AgentRepository(db).update_heartbeat(agent_id)
    assert await rows(sessions) == [("agent_first_heartbeat", "direct", 1)]


async def test_project_first_push_counted_once(sessions):
    agent_id, project_id = uuid4(), uuid4()
    await seed_agent(sessions, agent_id)
    async with sessions.begin() as db:
        await db.execute(
            text("INSERT INTO projects (id, title, creator_agent_id) VALUES (:p, 't', :a)"),
            {"p": project_id, "a": agent_id},
        )
    for sha in ("sha1", "sha2", "sha1"):
        async with sessions.begin() as db:
            await AgentRepository(db).record_commit_shas(agent_id, [sha], project_id=project_id)
    assert await rows(sessions) == [("project_first_push", "direct", 1)]


async def seed_project(sessions, agent_id, project_id):
    async with sessions.begin() as db:
        await db.execute(
            text("INSERT INTO projects (id, title, creator_agent_id) VALUES (:p, 't', :a)"),
            {"p": project_id, "a": agent_id},
        )


async def heartbeat(sessions, agent_id):
    async with sessions.begin() as db:
        await AgentRepository(db).update_heartbeat(agent_id)


async def push(sessions, agent_id, project_id, sha):
    async with sessions.begin() as db:
        await AgentRepository(db).record_commit_shas(agent_id, [sha], project_id=project_id)


async def test_concurrent_first_heartbeats_count_once(sessions):
    """B starts while A's transaction still holds the agent row uncommitted."""
    agent_id = uuid4()
    await seed_agent(sessions, agent_id)
    async with sessions.begin() as first_tx:
        await AgentRepository(first_tx).update_heartbeat(agent_id)
        racer = asyncio.create_task(heartbeat(sessions, agent_id))
        await asyncio.sleep(0.5)
    await racer
    assert await rows(sessions) == [("agent_first_heartbeat", "direct", 1)]


async def test_concurrent_first_pushes_count_once(sessions):
    """B starts while A's transaction still holds the project row uncommitted."""
    agent_id, project_id = uuid4(), uuid4()
    await seed_agent(sessions, agent_id)
    await seed_project(sessions, agent_id, project_id)
    async with sessions.begin() as first_tx:
        await AgentRepository(first_tx).record_commit_shas(
            agent_id, ["sha-a"], project_id=project_id
        )
        racer = asyncio.create_task(push(sessions, agent_id, project_id, "sha-b"))
        await asyncio.sleep(0.5)
        assert not racer.done()
    await racer
    assert await rows(sessions) == [("project_first_push", "direct", 1)]


async def test_register_counts_after_email_send_not_before(app, sessions, svc, redis):
    """The counter row lock must not be held while the verification email is sent."""
    await svc.record("user_register")
    lock_free = []
    lock_sql = "SELECT 1 FROM funnel_daily WHERE step='user_register' FOR UPDATE NOWAIT"

    class ProbeEmail:
        async def send_verification_email(self, *args):
            try:
                async with sessions.begin() as other:
                    await other.execute(text(lock_sql))
                lock_free.append(True)
            except DBAPIError:
                lock_free.append(False)
            return True

    app.include_router(auth_router, prefix="/api/v1")
    app.dependency_overrides[get_redis] = lambda: redis
    app.dependency_overrides[get_email_service] = ProbeEmail
    async with visitor(app) as c:
        r = await c.post(
            "/api/v1/auth/register",
            json={"email": "new@example.com", "password": "Str0ng!Passw0rd", "name": "N"},
        )
    assert r.status_code == 202, r.text
    assert lock_free == [True]
    assert await rows(sessions) == [("user_register", "direct", 2)]


async def test_ipv6_addresses_in_one_64_share_rate_bucket(svc):
    assert await svc.allow("2001:db8:1:2::1", "ua", 1) is True
    assert await svc.allow("2001:db8:1:2:ffff::9", "ua", 1) is False
    assert await svc.allow("2001:db8:1:3::1", "ua", 1) is True


async def test_ipv6_addresses_in_one_64_stay_distinct_visitors(svc, sessions):
    assert await svc.record_unique("visit", "direct", "2001:db8:1:2::1", "ua") is True
    assert await svc.record_unique("visit", "direct", "2001:db8:1:2::2", "ua") is True
    assert await rows(sessions) == [("visit", "direct", 2)]


def test_ipv4_mapped_addresses_keep_their_own_subject():
    first = FunnelService._rate_subject("::ffff:192.0.2.1")
    assert first == "192.0.2.1"
    assert first != FunnelService._rate_subject("::ffff:192.0.2.2")


async def test_ipv6_addresses_in_one_64_share_daily_cap(svc, sessions):
    for n in range(30):
        await svc.record_unique("visit", "direct", f"2001:db8:1:2::{n + 1:x}", f"ua-{n}")
    assert await svc.record_unique("visit", "direct", "2001:db8:1:3::1", "ua-x") is True
    assert await rows(sessions) == [("visit", "direct", 21)]


async def test_funnel_write_failure_keeps_heartbeat_and_push(sessions):
    agent_id, project_id = uuid4(), uuid4()
    await seed_agent(sessions, agent_id)
    await seed_project(sessions, agent_id, project_id)
    async with sessions.begin() as db:
        await db.execute(text("DROP TABLE funnel_daily"))
    await heartbeat(sessions, agent_id)
    await push(sessions, agent_id, project_id, "sha1")
    async with sessions() as db:
        assert await db.scalar(text("SELECT last_heartbeat IS NOT NULL FROM agents")) is True
        assert (
            await db.scalar(
                text("SELECT count(*) FROM agent_activity WHERE action_type = 'code_commit'")
            )
            == 1
        )


async def test_backfill_keeps_preexisting_pushed_project_out_of_funnel(sessions):
    agent_id, project_id = uuid4(), uuid4()
    await seed_agent(sessions, agent_id)
    await seed_project(sessions, agent_id, project_id)
    async with sessions.begin() as db:
        await db.execute(text("DROP TABLE funnel_daily"))
        await db.execute(text("ALTER TABLE projects DROP COLUMN first_pushed_at"))
        await db.execute(
            text(
                "INSERT INTO agent_activity (agent_id, project_id, action_type, description, "
                "metadata, created_at) VALUES (:a, :p, 'code_commit', 'old', "
                "'{\"commit_sha\": \"old1\"}', NOW() - INTERVAL '30 days')"
            ),
            {"a": agent_id, "p": project_id},
        )
        driver = (await (await db.connection()).get_raw_connection()).driver_connection
        assert driver is not None
        await driver.execute((MIGRATION_DIR / "V109__funnel_daily.sql").read_text())
    await push(sessions, agent_id, project_id, "new1")
    assert await rows(sessions) == []


async def test_skill_fetch_deduped_per_visitor(svc, sessions):
    assert await svc.record_unique("skill_fetch", "direct", "203.0.113.5", "ua-1") is True
    assert await svc.record_unique("skill_fetch", "direct", "203.0.113.5", "ua-1") is False
    assert await rows(sessions) == [("skill_fetch", "direct", 1)]


async def test_admin_funnel_requires_admin(app, svc):
    async with visitor(app) as c:
        assert (await c.get("/api/v1/admin/funnel")).status_code in (401, 403)
    app.dependency_overrides[get_admin_user] = lambda: object()
    await svc.record("user_register")
    async with visitor(app) as c:
        ok = await c.get("/api/v1/admin/funnel?days=7")
        assert ok.status_code == 200
        body = ok.json()
        assert body["totals"] == {"user_register": 1}
        assert body["rows"][0]["step"] == "user_register"
        assert (await c.get("/api/v1/admin/funnel?days=91")).status_code == 422


async def test_funnel_endpoint_rate_limited(app, monkeypatch):
    monkeypatch.setattr(get_settings(), "funnel_visit_rate_limit", 3)
    async with visitor(app) as c:
        codes = [(await visit(c)).status_code for _ in range(5)]
    assert codes == [204, 204, 204, 429, 429]


async def test_redis_down_not_counted_but_204(sessions):
    dead = Redis(host="127.0.0.1", port=1, socket_connect_timeout=0.2)
    svc = FunnelService(dead, session_factory=sessions)
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_funnel_service] = lambda: svc
    async with visitor(app) as c:
        assert (await visit(c)).status_code == 204
    assert await rows(sessions) == []


async def test_record_never_raises_on_db_failure(redis):
    dead = create_async_engine("postgresql+asyncpg://u:p@127.0.0.1:1/db")
    await FunnelService(redis, session_factory=async_sessionmaker(dead)).record("user_register")
    await dead.dispose()
