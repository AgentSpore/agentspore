"""Scoped producer authentication and committed visits on real PostgreSQL/Redis."""

import asyncio
import time
from contextvars import ContextVar
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
import test_service_gateway as gateway_fixtures
import test_service_usage as usage_fixtures
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.api.v1 import api_router
from app.core.config import get_settings
from app.core.database import get_db
from app.core.redis_client import get_redis
from app.core.security import create_access_token, create_refresh_token

usage_pg = usage_fixtures.usage_pg
usage_world = usage_fixtures.usage_world
usage_clock = usage_fixtures.usage_clock
gateway_redis_container = gateway_fixtures.gateway_redis_container
gateway_redis = gateway_fixtures.gateway_redis
PATH = "/api/v1/_internal/usage/otkrytka/observe"
KEY = "synthetic-observer-key-not-a-platform-key"


@pytest.fixture
async def observer_app(usage_world, gateway_redis, monkeypatch):
    monkeypatch.setenv("OTKRYTKA_USAGE_OBSERVER_KEY", KEY)
    monkeypatch.setenv("OTKRYTKA_USAGE_PROJECT_ID", str(usage_world.projects[0]))
    get_settings.cache_clear()
    app, state = FastAPI(), {"opened": 0, "committed": 0}
    app.include_router(api_router, prefix="/api/v1")
    committed = ContextVar("observer_committed", default=False)

    async def transaction():
        state["opened"] += 1
        committed.set(False)
        async with usage_world.maker() as db:
            try:
                yield db
                await db.commit()
                state["committed"] += 1
                committed.set(True)
            except Exception:
                await db.rollback()
                raise

    app.dependency_overrides[get_db] = transaction
    app.dependency_overrides[get_redis] = lambda: gateway_redis

    async def checked_app(scope, receive, send):
        async def checked_send(message):
            if message["type"] == "http.response.start" and message["status"] == 204:
                assert committed.get(), "204 sent before transaction commit"
            await send(message)

        await app(scope, receive, checked_send)

    yield checked_app, state
    get_settings.cache_clear()


@pytest.fixture
async def observer(observer_app):
    app, state = observer_app
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="https://test"
    ) as client:
        yield client, state


@pytest.fixture
def metadata():
    return {
        "X-Usage-Observer-Key": KEY,
        "X-Usage-Kind": "visit",
        "X-Usage-Request-Id": str(uuid4()),
        "X-Usage-Visitor-Id": "",
        "X-Usage-User-Agent": usage_fixtures.BROWSER,
        "X-Usage-Accept": "text/html",
        "X-Usage-Method": "GET",
        "X-Usage-Status": "200",
        "X-Usage-Content-Type": "text/html; charset=utf-8",
        "X-Usage-Path-Class": "document",
        "X-Usage-Client-IP": "192.0.2.4",
    }


async def test_first_repeat_and_concurrent_retry_preserve_one_visitor(
    observer, metadata, usage_world, gateway_redis, usage_clock
):
    client, state = observer
    responses = await asyncio.gather(*(client.post(PATH, headers=metadata) for _ in range(5)))
    assert all(r.status_code == 204 for r in responses)
    visitors = {r.cookies["__Host-visitor"] for r in responses}
    assert len(visitors) == 1 and UUID(next(iter(visitors))).version == 4
    assert next(iter(visitors)) != metadata["X-Usage-Request-Id"]
    cookie = responses[0].headers["set-cookie"].lower()
    assert all(
        s in cookie for s in ("secure", "httponly", "samesite=lax", "path=/", "max-age=691200")
    )
    assert "domain=" not in cookie and all(not r.content for r in responses)
    metadata["X-Usage-Visitor-Id"] = next(iter(visitors))
    (ip_key,) = await gateway_redis.keys("service-ip:*")
    await gateway_redis.expire(ip_key, 60)
    repeat = await client.post(PATH, headers=metadata)
    assert repeat.status_code == 204 and "set-cookie" not in repeat.headers
    assert 0 < await gateway_redis.ttl(ip_key) <= 60
    async with usage_world.maker() as db:
        assert await db.scalar(text("SELECT count(*) FROM service_visits")) == 1
    assert state == {"opened": 6, "committed": 6}


@pytest.mark.parametrize("mode", ["missing", "wrong", "disabled", "no_project", "agent_key"])
async def test_producer_rejection_precedes_database(observer, metadata, monkeypatch, mode):
    client, state = observer
    if mode in {"missing", "agent_key"}:
        metadata.pop("X-Usage-Observer-Key")
    if mode == "wrong":
        metadata["X-Usage-Observer-Key"] = "wrong"
    if mode == "agent_key":
        metadata["X-API-Key"] = "af_synthetic"
    if mode in {"disabled", "no_project"}:
        monkeypatch.delenv(
            "OTKRYTKA_USAGE_OBSERVER_KEY" if mode == "disabled" else "OTKRYTKA_USAGE_PROJECT_ID"
        )
        get_settings.cache_clear()
    result = await client.post(PATH, headers=metadata)
    assert result.status_code in {403, 503} and state["opened"] == 0
    assert KEY not in result.text and "set-cookie" not in result.headers


@pytest.mark.parametrize(
    "change",
    [
        {"X-Usage-Status": "500"},
        {"X-Usage-Content-Type": "application/json"},
        {"X-Usage-Method": "POST"},
        {"X-Usage-User-Agent": "x" * 4097},
        {"X-Usage-Verified-User-Id": "private-marker"},
        {"X-Usage-Path-Class": "/boards/private"},
        {"X-Usage-Request-Id": str(UUID(int=1))},
        {"X-Usage-Client-IP": "private-marker"},
    ],
)
async def test_closed_metadata_is_generic_and_unstored(observer, metadata, change):
    client, state = observer
    result = await client.post(PATH, headers={**metadata, **change})
    assert result.status_code in {400, 431} and state["opened"] == 0
    assert "private-marker" not in result.text and KEY not in result.text


@pytest.mark.parametrize("mode", ["body", "query", "chunked", "duplicate", "oversized"])
async def test_empty_bounded_transport(observer, metadata, mode):
    client, state = observer
    path, body = PATH, b""
    if mode == "body":
        body = b"private-marker"
    if mode == "query":
        path += "?private=private-marker"
    if mode == "chunked":
        metadata["Transfer-Encoding"] = "chunked"
    if mode == "oversized":
        metadata["X-Unrelated"] = "x" * 20000
    headers = list(metadata.items())
    if mode == "duplicate":
        headers.append(("X-Usage-Status", "200"))
    result = await client.post(path, headers=headers, content=body)
    assert result.status_code in {400, 431} and state["opened"] == 0
    assert "private-marker" not in result.text


@pytest.mark.parametrize(
    "identity", ["owner", "stranger", "missing", "invalid", "refresh", "expired", "nonuuid"]
)
async def test_optional_user_uses_existing_lookup_and_excludes_owner(
    observer, metadata, usage_world, usage_clock, identity
):
    client, state = observer
    stranger = uuid4()
    async with usage_world.maker.begin() as db:
        await db.execute(
            text(
                "INSERT INTO users(id,email,hashed_password,name) "
                "VALUES(:id,'stranger@example.invalid','synthetic','Stranger')"
            ),
            {"id": stranger},
        )
    proof = {
        "owner": create_access_token(str(usage_world.owner)),
        "stranger": create_access_token(str(stranger)),
        "invalid": "invalid",
        "expired": create_access_token(str(usage_world.owner), timedelta(seconds=-1)),
        "nonuuid": create_access_token("private-marker"),
        "refresh": create_refresh_token(str(usage_world.owner)),
    }
    if identity != "missing":
        metadata["Authorization"] = "Bearer " + proof[identity]
    result = await client.post(PATH, headers=metadata)
    assert result.status_code == (503 if identity == "nonuuid" else 204)
    assert state == {"opened": 1, "committed": int(identity != "nonuuid")}
    assert "private-marker" not in result.text
    async with usage_world.maker() as db:
        assert await db.scalar(text("SELECT count(*) FROM service_visits")) == int(
            identity not in {"owner", "nonuuid"}
        )


@pytest.mark.usefixtures("usage_clock")
@pytest.mark.parametrize("mode", ["bot", "probe", "unknown_project"])
async def test_exclusions_and_missing_project(observer, metadata, usage_world, monkeypatch, mode):
    client, _ = observer
    if mode == "bot":
        metadata["X-Usage-User-Agent"] = "Googlebot"
    if mode == "probe":
        metadata["X-Usage-Path-Class"] = "probe"
    if mode == "unknown_project":
        monkeypatch.setenv("OTKRYTKA_USAGE_PROJECT_ID", str(uuid4()))
        get_settings.cache_clear()
    result = await client.post(PATH, headers=metadata)
    assert result.status_code == (404 if mode == "unknown_project" else 204)
    async with usage_world.maker() as db:
        assert await db.scalar(text("SELECT count(*) FROM service_visits")) == 0


async def test_replacement_is_scoped_and_private(
    observer, metadata, usage_world, monkeypatch, usage_clock
):
    client, _ = observer
    metadata["X-Usage-Visitor-Id"] = "malformed"
    first = await client.post(PATH, headers=metadata)
    monkeypatch.setenv("OTKRYTKA_USAGE_OBSERVER_KEY", KEY + "-rotated")
    get_settings.cache_clear()
    metadata["X-Usage-Observer-Key"] = KEY + "-rotated"
    second = await client.post(PATH, headers=metadata)
    metadata["X-Usage-Request-Id"] = str(uuid4())
    third = await client.post(PATH, headers=metadata)
    values = [r.cookies["__Host-visitor"] for r in (first, second, third)]
    assert len(set(values)) == 3
    async with usage_world.maker() as db:
        columns = await db.scalars(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name='service_visits' AND table_schema='public'"
            )
        )
        assert set(columns) == {"project_id", "visitor_id", "visit_day"}


async def test_deferred_commit_failure_is_sanitized(
    observer, metadata, usage_world, capsys, caplog
):
    client, state = observer
    metadata["X-Usage-Visitor-Id"] = str(uuid4())
    async with usage_world.maker.begin() as db:
        await db.execute(
            text(
                "CREATE FUNCTION observer_failure() RETURNS trigger LANGUAGE plpgsql AS $$ "
                "BEGIN RAISE EXCEPTION 'private-marker %', NEW.visitor_id; RETURN NEW; END $$"
            )
        )
        await db.execute(
            text(
                "CREATE CONSTRAINT TRIGGER observer_commit_failure AFTER INSERT ON service_visits "
                "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
                "EXECUTE FUNCTION observer_failure()"
            )
        )
    result = await client.post(PATH, headers=metadata)
    assert result.status_code == 503 and "set-cookie" not in result.headers
    assert state == {"opened": 1, "committed": 0}
    captured = capsys.readouterr()
    output = result.text + captured.err + captured.out + caplog.text
    assert all(
        value not in output
        for value in (
            "private-marker",
            metadata["X-Usage-Visitor-Id"],
            metadata["X-Usage-Request-Id"],
            KEY,
        )
    )
    async with usage_world.maker() as db:
        assert await db.scalar(text("SELECT count(*) FROM service_visits")) == 0


async def test_redis_outage_retains_committed_cookie(
    observer, metadata, usage_world, gateway_redis, monkeypatch
):
    async def unavailable(*args, **kwargs):
        raise OSError("private-marker")

    monkeypatch.setattr(gateway_redis, "set", unavailable)
    client, state = observer
    result = await client.post(PATH, headers=metadata)
    assert result.status_code == 204 and "__Host-visitor" in result.cookies
    assert state == {"opened": 1, "committed": 1}
    async with usage_world.maker() as db:
        assert await db.scalar(text("SELECT count(*) FROM service_visits")) == 1


async def test_slow_database_is_cancelled_without_committing(observer, metadata, usage_world):
    async with usage_world.maker.begin() as db:
        await db.execute(
            text(
                "CREATE FUNCTION observer_delay() RETURNS trigger LANGUAGE plpgsql AS $$ "
                "BEGIN PERFORM pg_sleep(2); RETURN NEW; END $$"
            )
        )
        await db.execute(
            text(
                "CREATE TRIGGER observer_slow BEFORE INSERT ON service_visits "
                "FOR EACH ROW EXECUTE FUNCTION observer_delay()"
            )
        )
    client, state = observer
    started = time.monotonic()
    result = await client.post(PATH, headers=metadata)
    assert result.status_code == 503 and time.monotonic() - started < 1.6
    assert state == {"opened": 1, "committed": 0}
    async with usage_world.maker() as db:
        assert await db.scalar(text("SELECT count(*) FROM service_visits")) == 0


async def test_late_send_failure_never_sends_second_response(observer_app, metadata, capsys):
    app, state = observer_app
    sent = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message["type"])
        raise OSError("private-marker")

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "scheme": "https",
        "method": "POST",
        "path": PATH,
        "raw_path": PATH.encode(),
        "query_string": b"",
        "headers": [(k.lower().encode(), v.encode()) for k, v in metadata.items()],
    }
    await app(scope, receive, send)
    assert sent == ["http.response.start"] and state == {"opened": 1, "committed": 1}
    captured = capsys.readouterr()
    assert "private-marker" not in captured.err + captured.out
