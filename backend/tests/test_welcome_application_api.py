"""Retry welcome applications through real auth and pre-response transactions."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import test_agent_registration_limits
import test_marketplace_tasks
import test_welcome_rewards
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from loguru import logger
from sqlalchemy import text

from app.api import deps
from app.api.v1.auth import router as auth_router
from app.api.v1.users import router
from app.core.database import get_db
from app.core.redis_client import get_redis
from app.core.security import create_access_token
from app.repositories.welcome_reward_repo import WelcomeRewardRepository
from app.services.email_service import get_email_service
from app.services.welcome_reward_service import WelcomeRewardService

a3_redis = test_agent_registration_limits.a3_redis
a3_redis_container = test_agent_registration_limits.a3_redis_container
marketplace_pg = test_marketplace_tasks.marketplace_pg
admitted = test_welcome_rewards.admitted
ledger = test_welcome_rewards.ledger

ROUTE = "/api/v1/users/me/rewards/welcome"


@pytest.fixture
async def application_api(ledger, monkeypatch):
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.include_router(auth_router, prefix="/api/v1")
    monkeypatch.setattr(deps, "async_session_maker", ledger.maker)

    async def database():
        async with ledger.maker.begin() as db:
            yield db

    app.dependency_overrides[get_db] = database
    headers = {"Authorization": "Bearer " + create_access_token(str(ledger.owner))}
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test"
    ) as client:
        yield SimpleNamespace(client=client, app=app, ledger=ledger, headers=headers)
    app.dependency_overrides.clear()


async def test_retries_use_current_owner_only_and_never_reserve(application_api):
    c = application_api
    async with c.ledger.maker.begin() as db:
        await db.execute(
            text("UPDATE reward_programs SET enabled=true, admission_start='2020-01-01'")
        )
    denied = await c.client.post(ROUTE + "/application")
    assert denied.status_code == 401
    replies = await asyncio.gather(
        *[
            c.client.post(
                ROUTE + "/application",
                headers=c.headers,
                params={"owner": str(c.ledger.other)},
                json={"owner_user_id": str(c.ledger.other), "source_event": str(c.ledger.other)},
            )
            for _ in range(2)
        ]
    )
    assert all(r.status_code == 200 and r.json()["state"] == "application" for r in replies)
    async with c.ledger.maker() as db:
        assert (
            await db.execute(
                text("SELECT owner_user_id,source_event,amount_raw FROM welcome_reward_grants")
            )
        ).all() == [(c.ledger.owner, c.ledger.owner, None)]
        assert await db.scalar(text("SELECT count(*) FROM reward_funding_entries")) == 0


@pytest.mark.parametrize("enabled", [False, True])
async def test_disabled_and_historical_retry_do_not_create_application(application_api, enabled):
    c = application_api
    async with c.ledger.maker.begin() as db:
        await db.execute(
            text("UPDATE reward_programs SET enabled=:enabled, admission_start=clock_timestamp()"),
            {"enabled": enabled},
        )
    for method, route in [("get", ROUTE), ("post", ROUTE + "/application")]:
        response = await c.client.request(method, route, headers=c.headers)
        assert response.status_code == 200
        assert response.json()["reason"] == ("not_eligible" if enabled else "program_disabled")
    async with c.ledger.maker() as db:
        assert await db.scalar(text("SELECT count(*) FROM welcome_reward_grants")) == 0


@pytest.mark.parametrize("deferred", [False, True])
async def test_application_database_failure_returns_sanitized_503(application_api, deferred):
    c = application_api
    async with c.ledger.maker.begin() as db:
        await db.execute(
            text("UPDATE reward_programs SET enabled=true, admission_start='2020-01-01'")
        )
        if deferred:
            raw = await (await db.connection()).get_raw_connection()
            driver = raw.driver_connection
            assert driver is not None
            await driver.execute("""
                CREATE FUNCTION reject_application_commit() RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN RAISE EXCEPTION 'synthetic-private-owner@example.com'; END; $$;
                CREATE CONSTRAINT TRIGGER reject_application_commit
                AFTER INSERT ON welcome_reward_grants
                DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
                EXECUTE FUNCTION reject_application_commit();
            """)
        else:
            await db.execute(text("ALTER TABLE welcome_reward_grants RENAME TO unavailable_grants"))
    logs = []
    sink = logger.add(logs.append, format="{message}")
    try:
        response = await c.client.post(ROUTE + "/application", headers=c.headers)
    finally:
        logger.remove(sink)
    assert response.status_code == 503
    assert response.json() == {"detail": "Welcome rewards are temporarily unavailable"}
    assert "synthetic-private-owner@example.com" not in "".join(logs)
    if deferred:
        async with c.ledger.maker() as db:
            assert await db.scalar(text("SELECT count(*) FROM welcome_reward_grants")) == 0


async def test_confirmed_email_waits_for_fresh_risk_decision(application_api, admitted, a3_redis):
    c = application_api
    mail = AsyncMock()
    mail.send_email_proof_code.return_value = True
    c.app.dependency_overrides[get_redis] = lambda: a3_redis
    c.app.dependency_overrides[get_email_service] = lambda: mail
    async with admitted.maker.begin() as db:
        await WelcomeRewardService(WelcomeRewardRepository(db)).advance(admitted.owner)
    requested = await c.client.post("/api/v1/auth/email-proof/request", headers=c.headers)
    assert requested.status_code == 202
    code = mail.send_email_proof_code.call_args.args[1]
    confirmed = await c.client.post(
        "/api/v1/auth/email-proof/confirm", headers=c.headers, json={"code": code}
    )
    assert confirmed.status_code == 200
    response = await c.client.get(ROUTE, headers=c.headers)
    assert response.json()["state"] == "frozen"
    assert response.json()["reason"] == "risk_review"
    assert response.json()["next_action"] == "await_review"
    async with admitted.maker() as db:
        assert await WelcomeRewardRepository(db).free_funding() == 0
