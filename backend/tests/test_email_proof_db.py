"""Exercise email proof through HTTP and real PostgreSQL row locks/triggers."""

import asyncio
import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient, ConnectTimeout
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from test_marketplace_tasks import marketplace_pg as marketplace_pg

from app.api import deps
from app.api.deps import get_current_user
from app.core.database import get_db
from app.core.redis_client import get_redis
from app.main import app
from app.models.user import User
from app.services.email_proof_service import EmailProofService
from app.services.email_service import get_email_service

pytestmark = pytest.mark.integration


@pytest.fixture
async def proof_api(request, monkeypatch):
    pg = request.getfixturevalue("marketplace_pg")
    url = pg.get_connection_url().replace("psycopg2", "asyncpg")
    schema = "email_proof_" + uuid4().hex
    admin = create_async_engine(url)
    engine = create_async_engine(url, connect_args={"server_settings": {"search_path": schema}})
    maker = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(deps, "async_session_maker", maker)
    owner = uuid4()
    redis, mail = AsyncMock(), AsyncMock()
    redis.set.return_value = True
    redis.getdel.return_value = "1"
    mail.send_email_proof_code.return_value = True
    async with admin.begin() as conn:
        await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    async with engine.begin() as conn:
        raw = await conn.get_raw_connection()
        assert raw.driver_connection is not None
        await raw.driver_connection.execute("""
            CREATE TABLE users (id uuid PRIMARY KEY, email varchar(255) UNIQUE NOT NULL,
            hashed_password varchar(255), name varchar(100) NOT NULL, avatar_url varchar(500),
            is_admin boolean DEFAULT false, is_verified boolean DEFAULT true,
            verification_token text, verification_expires_at timestamptz,
            token_balance integer DEFAULT 0, oauth_provider varchar(50), oauth_id varchar(255),
            created_at timestamptz DEFAULT now(), updated_at timestamptz DEFAULT now(),
            solana_wallet varchar(44), aspore_balance bigint DEFAULT 0,
            oauth_account_created_at timestamptz);
        """)
        await conn.execute(
            text("INSERT INTO users (id,email,name) VALUES (:id,:email,'Owner')"),
            {"id": owner, "email": "owner@example.com"},
        )
        for name in ("V114__email_ownership_proof.sql", "R__email_ownership_proof.sql"):
            await raw.driver_connection.execute(
                (Path(__file__).resolve().parents[2] / "db/migrations" / name).read_text()
            )

    async def database():
        async with maker.begin() as db:
            yield db

    app.dependency_overrides[get_db] = database
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=owner)
    app.dependency_overrides[get_redis] = lambda: redis
    app.dependency_overrides[get_email_service] = lambda: mail
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield SimpleNamespace(client=client, maker=maker, owner=owner, redis=redis, mail=mail)
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()
        async with admin.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin.dispose()


async def test_proof_roundtrip_replay_and_balances(proof_api):
    ctx = proof_api
    async with ctx.maker() as db:
        assert not EmailProofService.has_current_proof(await db.get(User, ctx.owner))
    assert (await ctx.client.post("/api/v1/auth/email-proof/request")).status_code == 202
    code = ctx.mail.send_email_proof_code.call_args.args[1]
    assert len(code) == 43
    async with ctx.maker() as db:
        user = await db.get(User, ctx.owner)
        assert user.verification_token == hashlib.sha256(code.encode()).hexdigest()
        assert user.verification_token != code
    replies = await asyncio.gather(
        *[
            ctx.client.post("/api/v1/auth/email-proof/confirm", json={"code": code})
            for _ in range(4)
        ]
    )
    assert sorted(r.status_code for r in replies) == [200, 400, 400, 400]
    assert all("access_token" not in r.json() for r in replies)
    async with ctx.maker() as db:
        user = await db.get(User, ctx.owner)
        assert EmailProofService.has_current_proof(user)
        assert user.email_proof_source == "email_code"
        assert user.verification_token is None
        assert (user.aspore_balance, user.token_balance) == (0, 0)


async def test_email_change_and_change_back_invalidate(proof_api):
    ctx = proof_api
    assert (await ctx.client.post("/api/v1/auth/email-proof/request")).status_code == 202
    code = ctx.mail.send_email_proof_code.call_args.args[1]
    assert (
        await ctx.client.post("/api/v1/auth/email-proof/confirm", json={"code": code})
    ).status_code == 200
    assert (await ctx.client.post("/api/v1/auth/email-proof/request")).status_code == 202
    pending = ctx.mail.send_email_proof_code.call_args.args[1]
    async with ctx.maker.begin() as db:
        for email in ("changed@example.com", "owner@example.com"):
            await db.execute(
                text("UPDATE users SET email=:email WHERE id=:id"),
                {"email": email, "id": ctx.owner},
            )
    async with ctx.maker() as db:
        user = await db.get(User, ctx.owner)
        assert user.is_verified
        assert not EmailProofService.has_current_proof(user)
        assert user.email_proof_email is user.verification_token is None
    assert (
        await ctx.client.post("/api/v1/auth/email-proof/confirm", json={"code": pending})
    ).status_code == 400


@pytest.mark.parametrize("failure", ["expired", "purpose", "wrong", "foreign", "redis", "missing"])
async def test_confirmation_fails_closed(proof_api, failure):
    ctx = proof_api
    await ctx.client.post("/api/v1/auth/email-proof/request")
    code = ctx.mail.send_email_proof_code.call_args.args[1]
    async with ctx.maker.begin() as db:
        user = await db.get(User, ctx.owner)
        if failure == "expired":
            user.verification_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        if failure == "purpose":
            user.verification_purpose = "registration"
    if failure == "foreign":
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=uuid4())
    if failure == "wrong":
        code = "x" * 43
    if failure == "redis":
        ctx.redis.getdel.side_effect = RedisError("synthetic outage")
    if failure == "missing":
        ctx.redis.getdel.return_value = None
    reply = await ctx.client.post("/api/v1/auth/email-proof/confirm", json={"code": code})
    assert reply.status_code == (503 if failure == "redis" else 400)
    async with ctx.maker() as db:
        assert not EmailProofService.has_current_proof(await db.get(User, ctx.owner))


@pytest.mark.parametrize("failure", ["rate", "redis", "mail", "mail_timeout"])
async def test_request_failure_does_not_create_proof(proof_api, failure):
    ctx = proof_api
    if failure == "rate":
        ctx.redis.set.return_value = None
    if failure == "redis":
        ctx.redis.set.side_effect = RedisError("synthetic outage")
    if failure == "mail":
        ctx.mail.send_email_proof_code.return_value = False
    if failure == "mail_timeout":
        ctx.mail.send_email_proof_code.side_effect = ConnectTimeout("synthetic-private-value")
    reply = await ctx.client.post("/api/v1/auth/email-proof/request")
    assert reply.status_code == (429 if failure == "rate" else 503)
    async with ctx.maker() as db:
        user = await db.get(User, ctx.owner)
        assert not EmailProofService.has_current_proof(user)
        assert user.verification_token is None
    assert ctx.redis.set.call_args.kwargs["nx"] is True
    assert ctx.redis.set.call_args.kwargs["ex"] > 0


@pytest.mark.parametrize("bound", [True, False])
async def test_registration_link_only_proves_issued_email(proof_api, bound):
    ctx = proof_api
    code = "synthetic-registration-token"
    async with ctx.maker.begin() as db:
        user = await db.get(User, ctx.owner)
        user.is_verified = False
        user.verification_token = hashlib.sha256(code.encode()).hexdigest()
        user.verification_expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
        if bound:
            user.verification_email = user.email
            user.verification_purpose = "registration"
    ctx.redis.getdel.return_value = str(ctx.owner)
    reply = await ctx.client.get("/api/v1/auth/verify-email", params={"token": code})
    assert reply.status_code == 200
    assert "access_token" in reply.json()
    async with ctx.maker() as db:
        user = await db.get(User, ctx.owner)
        assert user.is_verified
        assert EmailProofService.has_current_proof(user) is bound
        if bound:
            assert user.email_proof_source == "email_link"


@pytest.mark.parametrize("phase", ["flush", "commit"])
async def test_database_failure_rolls_back_proof(proof_api, phase, caplog):
    ctx = proof_api
    await ctx.client.post("/api/v1/auth/email-proof/request")
    code = ctx.mail.send_email_proof_code.call_args.args[1]
    async with ctx.maker.begin() as db:
        if phase == "flush":
            await db.execute(
                text(
                    "ALTER TABLE users ADD CONSTRAINT synthetic_reject_proof "
                    "CHECK (email_proof_at IS NULL)"
                )
            )
        else:
            conn = await db.connection()
            raw = await conn.get_raw_connection()
            assert raw.driver_connection is not None
            await raw.driver_connection.execute("""
                CREATE FUNCTION reject_proof_commit() RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN RAISE EXCEPTION 'synthetic-private-proof-detail'; END; $$;
                CREATE CONSTRAINT TRIGGER reject_proof_commit AFTER UPDATE ON users
                DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
                WHEN (NEW.email_proof_at IS NOT NULL) EXECUTE FUNCTION reject_proof_commit();
            """)
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test"
    ) as client:
        reply = await client.post("/api/v1/auth/email-proof/confirm", json={"code": code})
    assert reply.status_code == 503
    assert reply.json() == {"detail": "Email verification is temporarily unavailable"}
    for private in (
        code,
        hashlib.sha256(code.encode()).hexdigest(),
        "owner@example.com",
        "synthetic-private-proof-detail",
    ):
        assert private not in caplog.text
    async with ctx.maker() as db:
        assert not EmailProofService.has_current_proof(await db.get(User, ctx.owner))
