"""Verify optional applications through actual registration, OAuth and owner claims."""

import pytest
import test_agent_owner_claims
import test_agent_registration_limits
import test_oauth_claim_identity
import test_service_usage
from sqlalchemy import text
from test_agent_owner_claims import AGENT, HUMAN
from test_oauth_claim_identity import EMAIL, PASSWORD, callback

claims_api = test_agent_owner_claims.claims_api
a3_redis = test_agent_registration_limits.a3_redis
a3_redis_container = test_agent_registration_limits.a3_redis_container
usage_pg = test_service_usage.usage_pg
usage_world = test_service_usage.usage_world
identity_case = test_oauth_claim_identity.identity_case
provider_http = test_oauth_claim_identity.provider_http


@pytest.mark.parametrize("channel", ["password", "google", "github"])
@pytest.mark.parametrize("broken", [False, True])
async def test_new_registration_application_is_optional(identity_case, channel, broken):
    case = identity_case
    async with case.world.maker.begin() as db:
        await db.execute(
            text("UPDATE reward_programs SET enabled=true, admission_start='2020-01-01'")
        )
        if broken:
            await db.execute(text("ALTER TABLE welcome_reward_grants RENAME TO unavailable_grants"))
    if channel == "password":
        response = await case.client.post(
            "/api/v1/auth/register", json={"email": EMAIL, "password": PASSWORD, "name": "Welcome"}
        )
    else:
        response = await callback(case, channel)
    assert response.status_code == (202 if channel == "password" else 302)
    async with case.world.maker() as db:
        owner = await db.scalar(text("SELECT id FROM users WHERE email=:email"), {"email": EMAIL})
        assert owner is not None
        if not broken:
            rows = (
                await db.execute(
                    text(
                        "SELECT owner_user_id,source_event,state,amount_raw "
                        "FROM welcome_reward_grants"
                    )
                )
            ).all()
            assert rows == [(owner, owner, "application", None)]
        assert await db.scalar(text("SELECT count(*) FROM reward_funding_entries")) == 0
        assert await db.scalar(text("SELECT count(*) FROM reward_eligibility_decisions")) == 0


@pytest.mark.parametrize("channel", ["google", "github"])
async def test_existing_oauth_login_does_not_create_application(identity_case, channel):
    case = identity_case
    first = await callback(case, channel)
    assert first.status_code == 302
    async with case.world.maker.begin() as db:
        await db.execute(
            text("UPDATE reward_programs SET enabled=true, admission_start='2020-01-01'")
        )
    again = await callback(case, channel)
    assert again.status_code == 302
    async with case.world.maker() as db:
        assert await db.scalar(text("SELECT count(*) FROM welcome_reward_grants")) == 0


@pytest.mark.parametrize("broken", [False, True])
async def test_confirmed_claim_preserves_owner_and_creates_one_application(claims_api, broken):
    c = claims_api
    async with c.world.maker.begin() as db:
        await db.execute(
            text("UPDATE reward_programs SET enabled=true, admission_start='2020-01-01'")
        )
        if broken:
            await db.execute(text("ALTER TABLE welcome_reward_grants RENAME TO unavailable_grants"))
    intent = await c.client.post(HUMAN, json={"agent_id": str(c.world.agents[0])}, headers=c.human)
    assert intent.status_code == 200
    body = {"expected_user_id": str(c.world.owner), "decision": "approve"}
    route = AGENT + intent.json()["claim_id"]
    for _ in range(2):
        result = await c.client.post(route, json=body, headers={"X-API-Key": c.world.keys[0]})
        assert result.status_code == 200 and result.json()["status"] == "claimed"
    async with c.world.maker() as db:
        assert (
            await db.scalar(
                text("SELECT owner_user_id FROM agents WHERE id=:id"), {"id": c.world.agents[0]}
            )
            == c.world.owner
        )
        if not broken:
            assert await db.scalar(text("SELECT count(*) FROM welcome_reward_grants")) == 1
            assert (
                await db.scalar(
                    text("SELECT count(*) FROM welcome_reward_grants WHERE amount_raw IS NOT NULL")
                )
                == 0
            )


@pytest.mark.parametrize("historical", [False, True])
async def test_legacy_claim_retries_do_not_bypass_admission(claims_api, historical):
    c = claims_api
    async with c.world.maker.begin() as db:
        await db.execute(
            text("UPDATE reward_programs SET enabled=true, admission_start=clock_timestamp()")
        )
        if not historical:
            await db.execute(
                text("UPDATE users SET created_at=clock_timestamp() WHERE id=:id"),
                {"id": c.world.owner},
            )
    for _ in range(2):
        response = await c.client.post(
            "/api/v1/agents/link-owner",
            json={"agent_id": str(c.world.agents[0])},
            headers={**c.human, "X-API-Key": c.world.keys[0]},
        )
        assert response.status_code == 200
    async with c.world.maker() as db:
        assert await db.scalar(text("SELECT count(*) FROM welcome_reward_grants")) == int(
            not historical
        )


async def test_two_agents_share_one_owner_application(claims_api):
    c = claims_api
    async with c.world.maker.begin() as db:
        await db.execute(
            text("UPDATE reward_programs SET enabled=true, admission_start='2020-01-01'")
        )
        await db.execute(
            text("UPDATE agents SET owner_user_id=NULL WHERE id=:id"), {"id": c.world.agents[1]}
        )
    for agent_id, key in zip(c.world.agents, c.world.keys, strict=True):
        reply = await c.client.post(
            "/api/v1/agents/link-owner",
            json={"agent_id": str(agent_id)},
            headers={**c.human, "X-API-Key": key},
        )
        assert reply.status_code == 200
    async with c.world.maker() as db:
        assert await db.scalar(text("SELECT count(*) FROM welcome_reward_grants")) == 1
