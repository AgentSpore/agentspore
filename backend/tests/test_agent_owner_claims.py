"""Exercise owner claims through actual HTTP authentication and PostgreSQL."""

import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from test_agent_registration_limits import a3_redis as a3_redis
from test_agent_registration_limits import a3_redis_container as a3_redis_container
from test_service_usage import usage_pg as usage_pg
from test_service_usage import usage_world as usage_world

from app.api.v1.agents import router as agents_router
from app.api.v1.ownership import router
from app.core.database import get_db
from app.core.redis_client import get_redis
from app.core.security import create_access_token
from app.models import User
from app.repositories.agent_repo import AgentRepository
from app.repositories.webhook_repo import WebhookRepository
from app.services.agent_service import AgentService

HUMAN = "/api/v1/users/me/agent-claims"
AGENT = "/api/v1/agents/owner-claims/"


@pytest.fixture
async def claims_api(usage_world):
    world = usage_world
    other = uuid4()
    async with world.maker.begin() as db:
        await db.execute(
            text("UPDATE users SET is_verified=TRUE WHERE id=:id"), {"id": world.owner}
        )
        db.add(User(id=other, email=f"{other}@example.invalid", name="Other", is_verified=True))
        await db.flush()
        await db.execute(
            text("UPDATE agents SET owner_user_id=NULL, is_active=TRUE WHERE id=:id"),
            {"id": world.agents[0]},
        )
        await db.execute(
            text(
                "INSERT INTO project_contributors(project_id,agent_id,owner_user_id) "
                "VALUES (:p,:a,NULL)"
            ),
            {"p": world.projects[0], "a": world.agents[0]},
        )
        await db.execute(
            text(
                "INSERT INTO project_contributors(project_id,agent_id,owner_user_id) VALUES "
                "(:p,:a,:u)"
            ),
            {"p": world.projects[1], "a": world.agents[0], "u": other},
        )
    app = FastAPI()
    pids = []
    app.include_router(router, prefix="/api/v1")

    async def request_db():
        async with world.maker() as db:
            pids.append(await db.scalar(text("SELECT pg_backend_pid()")))
            yield db
            await db.commit()

    app.dependency_overrides[get_db] = request_db
    headers = {"Authorization": "Bearer " + create_access_token(str(world.owner))}
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test"
    ) as client:
        yield SimpleNamespace(
            client=client, world=world, human=headers, other=other, app=app, pids=pids
        )


async def test_verified_human_intent_requires_independent_agent_approval(claims_api):
    c = claims_api
    response = await c.client.post(
        HUMAN, json={"agent_id": str(c.world.agents[0])}, headers=c.human
    )
    assert response.status_code == 200
    claim = response.json()
    assert claim["status"] == "pending"
    async with c.world.maker() as db:
        assert (
            await db.scalar(
                text("SELECT owner_user_id FROM agents WHERE id=:id"), {"id": c.world.agents[0]}
            )
            is None
        )
    decision = {"expected_user_id": str(c.world.owner), "decision": "approve"}
    missing = await c.client.post(AGENT + claim["claim_id"], json=decision, headers=c.human)
    assert missing.status_code == 422
    result = await c.client.post(
        AGENT + claim["claim_id"], json=decision, headers={"X-API-Key": c.world.keys[0]}
    )
    assert result.status_code == 200
    assert result.json()["status"] == "claimed"
    async with c.world.maker() as db:
        assert (
            await db.scalar(
                text("SELECT owner_user_id FROM agents WHERE id=:id"), {"id": c.world.agents[0]}
            )
            == c.world.owner
        )


async def test_legacy_owner_link_never_rebinds_an_owned_agent(claims_api):
    c = claims_api
    async with c.world.maker.begin() as db:
        await db.execute(
            text("UPDATE agents SET owner_user_id=:owner WHERE id=:id"),
            {"owner": c.other, "id": c.world.agents[0]},
        )
    response = await c.client.post(
        "/api/v1/agents/link-owner",
        json={"agent_id": str(c.world.agents[0])},
        headers={**c.human, "X-API-Key": c.world.keys[0]},
    )
    assert response.status_code == 409
    async with c.world.maker() as db:
        assert (
            await db.scalar(
                text("SELECT owner_user_id FROM agents WHERE id=:id"), {"id": c.world.agents[0]}
            )
            == c.other
        )


@pytest.fixture
async def pending_claim(claims_api):
    c = claims_api
    response = await c.client.post(
        HUMAN, json={"agent_id": str(c.world.agents[0])}, headers=c.human
    )
    assert response.status_code == 200
    c.claim = response.json()
    return c


@pytest.fixture
def agent_headers(claims_api):
    return {"X-API-Key": claims_api.world.keys[0]}


@pytest.fixture
def decision(claims_api):
    return {"expected_user_id": str(claims_api.world.owner), "decision": "approve"}


@pytest.mark.parametrize("mode,expected", [("unverified", 403), ("missing", 401)])
async def test_human_consent_requires_verified_identity(claims_api, mode, expected):
    c = claims_api
    if mode == "unverified":
        async with c.world.maker.begin() as db:
            await db.execute(
                text("UPDATE users SET is_verified=FALSE WHERE id=:id"), {"id": c.world.owner}
            )
    response = await c.client.post(
        HUMAN,
        json={"agent_id": str(c.world.agents[0])},
        headers=c.human if mode == "unverified" else {},
    )
    assert response.status_code == expected


async def test_human_cannot_read_or_cancel_another_intent(pending_claim):
    c = pending_claim
    headers = {"Authorization": "Bearer " + create_access_token(str(c.other))}
    for method in ("GET", "DELETE"):
        response = await c.client.request(
            method, HUMAN + "/" + c.claim["claim_id"], headers=headers
        )
        assert response.status_code == 404


@pytest.mark.parametrize(
    "mode,expected",
    [
        ("wrong_agent", 404),
        ("unverified", 403),
        ("wrong_human", 409),
        ("disabled", 401),
        ("rotated", 409),
        ("expired", 410),
        ("canceled", 409),
        ("denied", 409),
    ],
)
async def test_invalid_agent_approval_never_assigns(
    pending_claim, agent_headers, decision, mode, expected
):
    c = pending_claim
    url = AGENT + c.claim["claim_id"]
    if mode == "wrong_agent":
        agent_headers = {"X-API-Key": c.world.keys[1]}
    elif mode == "wrong_human":
        decision["expected_user_id"] = str(c.other)
    elif mode == "canceled":
        assert (
            await c.client.delete(HUMAN + "/" + c.claim["claim_id"], headers=c.human)
        ).status_code == 200
    elif mode == "denied":
        assert (
            await c.client.post(url, json={**decision, "decision": "deny"}, headers=agent_headers)
        ).status_code == 200
    else:
        async with c.world.maker.begin() as db:
            if mode == "unverified":
                await db.execute(
                    text("UPDATE users SET is_verified=FALSE WHERE id=:id"), {"id": c.world.owner}
                )
            elif mode == "expired":
                await db.execute(
                    text(
                        "UPDATE agent_owner_claims SET created_at=clock_timestamp()-INTERVAL "
                        "'2 minutes',expires_at=clock_timestamp()-INTERVAL '1 second'"
                    )
                )
            elif mode == "disabled":
                await db.execute(
                    text("UPDATE agents SET is_active=FALSE WHERE id=:id"),
                    {"id": c.world.agents[0]},
                )
            elif mode == "rotated":
                new_key = str(uuid4())
                await db.execute(
                    text("UPDATE agents SET api_key_hash=:h WHERE id=:id"),
                    {"id": c.world.agents[0], "h": AgentService.hash_api_key(new_key)},
                )
                agent_headers = {"X-API-Key": new_key}
    response = await c.client.post(url, json=decision, headers=agent_headers)
    assert response.status_code == expected
    async with c.world.maker() as db:
        assert (
            await db.scalar(
                text("SELECT owner_user_id FROM agents WHERE id=:id"), {"id": c.world.agents[0]}
            )
            is None
        )


async def test_two_humans_have_one_winner_and_preserve_history(
    pending_claim, agent_headers, decision
):
    c = pending_claim
    other = await c.client.post(
        HUMAN,
        json={"agent_id": str(c.world.agents[0])},
        headers={"Authorization": "Bearer " + create_access_token(str(c.other))},
    )
    assert other.status_code == 200
    c.pids.clear()
    responses = await asyncio.gather(
        c.client.post(AGENT + c.claim["claim_id"], json=decision, headers=agent_headers),
        c.client.post(
            AGENT + other.json()["claim_id"],
            json={**decision, "expected_user_id": str(c.other)},
            headers=agent_headers,
        ),
    )
    assert sorted(response.status_code for response in responses) == [200, 409]
    assert len(set(c.pids)) == 2
    winner = next(
        response.json()["user_id"] for response in responses if response.status_code == 200
    )
    async with c.world.maker() as db:
        owner = await db.scalar(
            text("SELECT owner_user_id FROM agents WHERE id=:id"), {"id": c.world.agents[0]}
        )
        rows = (
            await db.execute(
                text(
                    "SELECT project_id,owner_user_id FROM project_contributors WHERE agent_id=:id"
                ),
                {"id": c.world.agents[0]},
            )
        ).all()
        assert str(owner) == winner
        assert {str(p): str(u) for p, u in rows} == {
            str(c.world.projects[0]): winner,
            str(c.world.projects[1]): str(c.other),
        }


async def test_cancel_and_approval_are_serialized(pending_claim, agent_headers, decision):
    c = pending_claim
    c.pids.clear()
    canceled, approved = await asyncio.gather(
        c.client.delete(HUMAN + "/" + c.claim["claim_id"], headers=c.human),
        c.client.post(AGENT + c.claim["claim_id"], json=decision, headers=agent_headers),
    )
    assert len(set(c.pids)) == 2
    async with c.world.maker() as db:
        owner = await db.scalar(
            text("SELECT owner_user_id FROM agents WHERE id=:id"), {"id": c.world.agents[0]}
        )
    assert approved.status_code == (200 if owner is not None else 409)
    assert canceled.json()["status"] == ("claimed" if owner is not None else "canceled")


async def test_database_failure_rolls_back_owner_and_contributors(
    pending_claim, agent_headers, decision
):
    c = pending_claim
    async with c.world.maker.begin() as db:
        await db.execute(
            text(
                "CREATE FUNCTION reject_claim() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN "
                "RAISE EXCEPTION 'synthetic rollback'; END $$"
            )
        )
        await db.execute(
            text(
                "CREATE TRIGGER reject_claim BEFORE UPDATE ON agent_owner_claims FOR EACH ROW "
                "WHEN (NEW.status='claimed') EXECUTE FUNCTION reject_claim()"
            )
        )
    response = await c.client.post(
        AGENT + c.claim["claim_id"], json=decision, headers=agent_headers
    )
    assert response.status_code == 500
    async with c.world.maker() as db:
        assert (
            await db.scalar(
                text("SELECT owner_user_id FROM agents WHERE id=:id"), {"id": c.world.agents[0]}
            )
            is None
        )
        assert (
            await db.scalar(
                text(
                    "SELECT count(*) FROM project_contributors WHERE agent_id=:id AND "
                    "owner_user_id IS NULL"
                ),
                {"id": c.world.agents[0]},
            )
            == 1
        )
        assert (
            await db.scalar(
                text("SELECT status FROM agent_owner_claims WHERE id=:id"),
                {"id": c.claim["claim_id"]},
            )
            == "pending"
        )


@pytest.mark.parametrize("race", ["expiry", "rotation"])
async def test_approval_rechecks_proof_after_lock_wait(
    pending_claim, agent_headers, decision, race
):
    c = pending_claim
    waiting = False
    async with c.world.maker.begin() as blocker:
        await blocker.execute(
            text("SELECT id FROM agents WHERE id=:id FOR UPDATE"), {"id": c.world.agents[0]}
        )
        await blocker.execute(
            text(
                "UPDATE agent_owner_claims SET expires_at=clock_timestamp()+INTERVAL '1 "
                "second' WHERE id=:id"
            ),
            {"id": c.claim["claim_id"]},
        )
        task = asyncio.create_task(
            c.client.post(AGENT + c.claim["claim_id"], json=decision, headers=agent_headers)
        )
        for _ in range(100):
            await blocker.execute(text("SELECT pg_stat_clear_snapshot()"))
            waiting = await blocker.scalar(
                text(
                    "SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE "
                    "pg_backend_pid()=ANY(pg_blocking_pids(pid)))"
                )
            )
            if waiting:
                break
        if race == "expiry":
            await blocker.execute(
                text(
                    "SELECT pg_sleep(GREATEST(0,EXTRACT(EPOCH FROM "
                    "(expires_at-clock_timestamp())))+0.05) FROM agent_owner_claims WHERE "
                    "id=:id"
                ),
                {"id": c.claim["claim_id"]},
            )
        else:
            await blocker.execute(
                text("UPDATE agents SET api_key_hash=:h WHERE id=:id"),
                {"id": c.world.agents[0], "h": AgentService.hash_api_key(str(uuid4()))},
            )
    response = await asyncio.wait_for(task, 10)
    assert waiting
    assert response.status_code == (410 if race == "expiry" else 403)
    async with c.world.maker() as db:
        assert (
            await db.scalar(
                text("SELECT owner_user_id FROM agents WHERE id=:id"), {"id": c.world.agents[0]}
            )
            is None
        )


async def test_same_claim_and_human_reload_are_idempotent(pending_claim, agent_headers, decision):
    c = pending_claim
    first = await c.client.post(AGENT + c.claim["claim_id"], json=decision, headers=agent_headers)
    repeated = await c.client.post(
        AGENT + c.claim["claim_id"], json=decision, headers=agent_headers
    )
    reloaded = await c.client.post(
        HUMAN, json={"agent_id": str(c.world.agents[0])}, headers=c.human
    )
    assert first.status_code == repeated.status_code == reloaded.status_code == 200
    assert first.json() == repeated.json() == reloaded.json()


async def test_unclaimed_registration_key_can_heartbeat(claims_api, a3_redis):
    c = claims_api
    c.app.include_router(agents_router, prefix="/api/v1")
    c.app.dependency_overrides[get_redis] = lambda: a3_redis
    response = await c.client.post(
        "/api/v1/agents/register",
        json={"name": "Fresh-" + uuid4().hex, "model_provider": "test", "model_name": "synthetic"},
    )
    assert response.status_code == 200
    data = response.json()
    async with AsyncClient(transport=ASGITransport(app=c.app), base_url="http://test") as client:
        heartbeat = await client.post(
            "/api/v1/agents/heartbeat",
            json={"status": "idle"},
            headers={"X-API-Key": data["api_key"]},
        )
    assert heartbeat.status_code == 200


@pytest.mark.parametrize("scope", ["pending", "hourly"])
async def test_concurrent_admission_enforces_both_limits(claims_api, scope):
    c = claims_api
    async with c.world.maker.begin() as db:
        if scope == "pending":
            agents = await db.execute(
                text(
                    "INSERT INTO agents(id,name,handle,api_key_hash) "
                    "SELECT uuid_generate_v4(),"
                    "'quota-'||n,'quota-'||n,md5(n::text)||md5(n::text) "
                    "FROM generate_series(1,4) n "
                    "RETURNING id,api_key_hash"
                )
            )
            for agent, key_hash in agents:
                await db.execute(
                    text(
                        "INSERT INTO agent_owner_claims(agent_id,user_id,agent_key_hash_snapshot) "
                        "VALUES (:a,:u,:h)"
                    ),
                    {"a": agent, "u": c.world.owner, "h": key_hash},
                )
        else:
            await db.execute(
                text(
                    "INSERT INTO "
                    "agent_owner_claims(agent_id,user_id,agent_key_hash_snapshot,status) "
                    "SELECT id,:u,api_key_hash,'denied' FROM agents CROSS JOIN "
                    "generate_series(1,9) WHERE id=:a"
                ),
                {"a": c.world.agents[0], "u": c.world.owner},
            )
    c.pids.clear()
    responses = await asyncio.gather(
        *(
            c.client.post(HUMAN, json={"agent_id": str(agent)}, headers=c.human)
            for agent in c.world.agents
        )
    )
    assert sorted(response.status_code for response in responses) == [200, 429]
    assert len(set(c.pids)) == 2
    async with c.world.maker() as db:
        query = "SELECT count(*) FROM agent_owner_claims WHERE user_id=:u"
        if scope == "pending":
            query += " AND status='pending'"
        assert await db.scalar(text(query), {"u": c.world.owner}) == (
            5 if scope == "pending" else 10
        )


async def test_foreign_pending_intents_cannot_block_legitimate_owner(
    claims_api, agent_headers, decision
):
    c = claims_api
    async with c.world.maker.begin() as db:
        users = (
            (
                await db.execute(
                    text(
                        "INSERT INTO users(id,email,name,is_verified) SELECT uuid_generate_v4(),"
                        "'foreign-'||n||'@example.invalid','Foreign',TRUE "
                        "FROM generate_series(1,5) n RETURNING id"
                    )
                )
            )
            .scalars()
            .all()
        )
    for user in users:
        headers = {"Authorization": "Bearer " + create_access_token(str(user))}
        response = await c.client.post(
            HUMAN, json={"agent_id": str(c.world.agents[0])}, headers=headers
        )
        assert response.status_code == 200
    intent = await c.client.post(HUMAN, json={"agent_id": str(c.world.agents[0])}, headers=c.human)
    assert intent.status_code == 200
    approved = await c.client.post(
        AGENT + intent.json()["claim_id"], json=decision, headers=agent_headers
    )
    assert approved.status_code == 200
    async with c.world.maker() as db:
        assert (
            await db.scalar(
                text("SELECT owner_user_id FROM agents WHERE id=:a"), {"a": c.world.agents[0]}
            )
            == c.world.owner
        )


@pytest.mark.parametrize("mode", ["unverified", "inactive", "key", "repeat"])
async def test_legacy_requires_both_current_proofs(claims_api, mode):
    c = claims_api
    async with c.world.maker.begin() as db:
        if mode == "unverified":
            await db.execute(
                text("UPDATE users SET is_verified=FALSE WHERE id=:id"), {"id": c.world.owner}
            )
        elif mode == "inactive":
            await db.execute(
                text("UPDATE agents SET is_active=FALSE WHERE id=:id"), {"id": c.world.agents[0]}
            )
    headers = {**c.human, "X-API-Key": str(uuid4()) if mode == "key" else c.world.keys[0]}
    for _ in range(2):
        response = await c.client.post(
            "/api/v1/agents/link-owner", json={"agent_id": str(c.world.agents[0])}, headers=headers
        )
        assert response.status_code == (200 if mode == "repeat" else 403)


async def test_human_or_agent_cannot_substitute_immutable_target(
    pending_claim, agent_headers, decision
):
    c = pending_claim
    forged = await c.client.post(
        HUMAN, json={"agent_id": str(c.world.agents[0]), "user_id": str(c.other)}, headers=c.human
    )
    assert forged.status_code == 422
    forged = await c.client.post(
        AGENT + c.claim["claim_id"],
        json={**decision, "agent_id": str(c.world.agents[1])},
        headers=agent_headers,
    )
    assert forged.status_code == 422


@pytest.mark.parametrize("repository", [AgentRepository, WebhookRepository])
async def test_later_contributions_preserve_historical_owner(
    pending_claim, agent_headers, decision, repository
):
    c = pending_claim
    approved = await c.client.post(
        AGENT + c.claim["claim_id"], json=decision, headers=agent_headers
    )
    assert approved.status_code == 200
    async with c.world.maker.begin() as db:
        await db.execute(
            text("INSERT INTO project_contributors(project_id,agent_id) VALUES (:p,:a)"),
            {"p": c.world.projects[2], "a": c.world.agents[0]},
        )
        for project in c.world.projects[1:]:
            await repository(db).upsert_contributor_points(
                project, c.world.agents[0], c.world.owner, 5
            )
        rows = (
            await db.execute(
                text(
                    "SELECT project_id,owner_user_id,contribution_points FROM project_contributors "
                    "WHERE agent_id=:a AND project_id=ANY(:p)"
                ),
                {"a": c.world.agents[0], "p": list(c.world.projects[1:])},
            )
        ).all()
        assert {row[0]: (row[1], row[2]) for row in rows} == {
            c.world.projects[1]: (c.other, 5),
            c.world.projects[2]: (c.world.owner, 5),
        }
