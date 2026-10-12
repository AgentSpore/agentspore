"""Owner projections exercise real JWT authentication and PostgreSQL queries."""

import json
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from test_marketplace_tasks_api import marketplace_db as marketplace_db
from test_marketplace_tasks_api import marketplace_pg as marketplace_pg

from app.api.v1.users import router
from app.core.database import get_db
from app.core.security import create_access_token
from app.models import User

BASE = "/api/v1/users/me"


@pytest.fixture
async def workspace_api(marketplace_db):
    maker, project, requester, claimant, _ = marketplace_db
    owners = [uuid4(), uuid4()]
    async with maker.begin() as db:
        await db.execute(text("DROP TABLE IF EXISTS users CASCADE"))
        await db.execute(
            text("""
            CREATE TABLE users (id UUID PRIMARY KEY, email TEXT, hashed_password TEXT,
                name TEXT, avatar_url TEXT, is_admin BOOLEAN, is_verified BOOLEAN,
                verification_token TEXT, verification_expires_at TIMESTAMPTZ,
                token_balance INTEGER, oauth_provider TEXT, oauth_id TEXT,
                created_at TIMESTAMPTZ DEFAULT NOW(), solana_wallet TEXT,
                aspore_balance BIGINT, updated_at TIMESTAMPTZ DEFAULT NOW());
        """)
        )
        await db.execute(
            text("""
            ALTER TABLE agents ADD COLUMN handle TEXT, ADD COLUMN specialization TEXT,
                ADD COLUMN is_hosted BOOLEAN DEFAULT FALSE,
                ADD COLUMN created_at TIMESTAMPTZ DEFAULT NOW(),
                ADD COLUMN last_heartbeat TIMESTAMPTZ;
        """)
        )
        for index, owner in enumerate(owners):
            db.add(User(id=owner, name="Synthetic owner", email=f"owner{index}@example.invalid"))
        await db.execute(
            text("UPDATE agents SET owner_user_id = :owner, handle = 'synthetic'"),
            {"owner": owners[0]},
        )
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")

    async def request_db():
        async with maker.begin() as db:
            yield db

    app.dependency_overrides[get_db] = request_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        client.headers["Authorization"] = f"Bearer {create_access_token(str(owners[0]))}"
        yield client, maker, project, requester, claimant, owners


@pytest.fixture
async def workspace_tasks(workspace_api):
    _, maker, project, requester, claimant, _ = workspace_api
    ids = [uuid4() for _ in range(3)]
    async with maker.begin() as db:
        for index, task in enumerate(ids):
            await db.execute(
                text("""
                INSERT INTO tasks(id, project_id, type, title, created_by_agent_id,
                    claimed_by_agent_id, status, result)
                VALUES (:id, :project, 'write_docs', :title, :requester, :claimant,
                    'completed', 'Private synthetic result')
            """),
                {
                    "id": task,
                    "project": project,
                    "title": f"Task {index}",
                    "requester": requester,
                    "claimant": claimant,
                },
            )
        await db.execute(
            text("""
            INSERT INTO task_reviews(task_id, requester_agent_id, state, version,
                relationship, result) VALUES (:id, :requester, 'accepted', 3,
                'internal', 'Private reviewed result')
        """),
            {"id": ids[0], "requester": requester},
        )
    return ids


async def test_owner_lists_closed_legacy_and_reviewed_once(workspace_api, workspace_tasks):
    client, _, _, _, _, _ = workspace_api
    page = (await client.get(BASE + "/agent-tasks?limit=2")).json()
    assert page["total"] == 3 and len(page["items"]) == 2
    assert all(item["viewer_roles"] == ["requester", "claimant"] for item in page["items"])
    assert all("result" not in item and "history" not in item for item in page["items"])
    next_page = (await client.get(BASE + "/agent-tasks?limit=2&offset=2")).json()
    assert len(next_page["items"]) == 1
    ids = [item["id"] for item in page["items"] + next_page["items"]]
    assert len(set(ids)) == 3
    legacy = await client.get(BASE + f"/agent-tasks/{workspace_tasks[1]}")
    assert legacy.json()["review_state"] is None
    assert legacy.json()["result"] == "Private synthetic result"


@pytest.mark.parametrize("ownership", ["other", "unowned"])
async def test_foreign_or_unowned_details_are_indistinguishable_from_missing(
    workspace_api, workspace_tasks, ownership
):
    client, maker, _, _, _, owners = workspace_api
    async with maker.begin() as db:
        await db.execute(
            text("UPDATE agents SET owner_user_id = :id"),
            {"id": owners[1] if ownership == "other" else None},
        )
    foreign = await client.get(BASE + f"/agent-tasks/{workspace_tasks[0]}")
    missing = await client.get(BASE + f"/agent-tasks/{uuid4()}")
    assert foreign.status_code == missing.status_code == 404
    assert foreign.json() == missing.json()
    assert (await client.get(BASE + "/agent-tasks")).json()["total"] == 0
    assert (await client.get(BASE + "/agent-workspace")).json()["agents"] == []


async def test_reads_do_not_activate_and_budget_totals_ignore_event_count(
    workspace_api, workspace_tasks
):
    client, maker, _, requester, claimant, _ = workspace_api
    empty = (await client.get(BASE + "/agent-workspace")).json()
    assert all(agent["budget"]["available"] == 0 for agent in empty["agents"])
    async with maker.begin() as db:
        assert await db.scalar(text("SELECT count(*) FROM learning_budget_accounts")) == 0
        await db.execute(
            text("""
            INSERT INTO learning_budget_accounts(agent_id, available, initial_grant_applied)
            VALUES (:requester, 70, TRUE), (:claimant, 30, FALSE)
        """),
            {"requester": requester, "claimant": claimant},
        )
        await db.execute(
            text("""
            INSERT INTO learning_budget_reservations(task_id, requester_agent_id, amount, status)
            VALUES (:task, :requester, 30, 'captured')
        """),
            {"task": workspace_tasks[0], "requester": requester},
        )
        await db.execute(
            text("""
            INSERT INTO task_review_events(task_id, actor_agent_id, action, idempotency_key,
                expected_version, payload, response)
            SELECT :task, :actor, 'accept', gen_random_uuid(), 2,
                CAST(:payload AS jsonb), CAST(:response AS jsonb) FROM generate_series(1, 55)
        """),
            {
                "task": workspace_tasks[0],
                "actor": requester,
                "payload": json.dumps({"reason": "Synthetic reason", "hidden": "private"}),
                "response": json.dumps({"state": "accepted", "version": 3}),
            },
        )
    data = (await client.get(BASE + "/agent-workspace")).json()
    budgets = {agent["id"]: agent["budget"] for agent in data["agents"]}
    assert budgets[str(requester)]["transferred"] == 30
    assert budgets[str(claimant)]["received"] == 30
    assert data["review_counts"]["accepted"] == 1
    detail = (await client.get(BASE + f"/agent-tasks/{workspace_tasks[0]}")).json()
    assert detail["relationship"] == "internal" and len(detail["history"]) == 50
    assert detail["result"] == "Private reviewed result"
    assert all(
        event["state"] == "accepted" and event["version"] == 3 for event in detail["history"]
    )
    wire = str(data) + str(detail)
    assert all(
        field not in wire for field in ("api_key", "email", "idempotency", "hidden", "payload")
    )


@pytest.mark.parametrize("query", ["limit=0", "limit=101", "offset=-1", "offset=10001"])
async def test_pagination_rejects_unbounded_requests(workspace_api, query):
    client, *_ = workspace_api
    assert (await client.get(BASE + "/agent-tasks?" + query)).status_code == 422


async def test_reserved_and_refunded_units_are_not_transferred(workspace_api, workspace_tasks):
    client, maker, _, requester, _, _ = workspace_api
    async with maker.begin() as db:
        await db.execute(
            text(
                "INSERT INTO learning_budget_accounts(agent_id, available, reserved) "
                "VALUES (:id, 60, 30)"
            ),
            {"id": requester},
        )
        for task, state, status, amount in zip(
            workspace_tasks,
            ("accepted", "rejected", "awaiting_submission"),
            ("captured", "refunded", "reserved"),
            (10, 20, 30),
            strict=True,
        ):
            await db.execute(
                text(
                    "INSERT INTO task_reviews(task_id, requester_agent_id, state) "
                    "VALUES (:task, :actor, :state) ON CONFLICT(task_id) DO NOTHING"
                ),
                {"task": task, "actor": requester, "state": state},
            )
            await db.execute(
                text(
                    "INSERT INTO learning_budget_reservations "
                    "(task_id, requester_agent_id, amount, status) "
                    "VALUES (:task, :actor, :amount, :status)"
                ),
                {"task": task, "actor": requester, "amount": amount, "status": status},
            )
    data = (await client.get(BASE + "/agent-workspace")).json()
    budget = next(agent["budget"] for agent in data["agents"] if agent["id"] == str(requester))
    assert (
        budget["available"],
        budget["reserved"],
        budget["transferred"],
        budget["received"],
        budget["refunded"],
    ) == (60, 30, 10, 0, 20)


async def test_review_requester_overrides_legacy_creator_and_relationship_stays_saved(
    workspace_api, workspace_tasks
):
    client, maker, _, requester, claimant, owners = workspace_api
    async with maker.begin() as db:
        await db.execute(
            text("UPDATE agents SET owner_user_id = :other WHERE id = :claimant"),
            {"other": owners[1], "claimant": claimant},
        )
        await db.execute(
            text("UPDATE tasks SET created_by_agent_id = :claimant WHERE id = :task"),
            {"claimant": claimant, "task": workspace_tasks[0]},
        )
    detail = (await client.get(BASE + f"/agent-tasks/{workspace_tasks[0]}")).json()
    assert detail["requester"]["id"] == str(requester)
    assert detail["viewer_roles"] == ["requester"] and detail["relationship"] == "internal"
    client.headers["Authorization"] = f"Bearer {create_access_token(str(owners[1]))}"
    detail = (await client.get(BASE + f"/agent-tasks/{workspace_tasks[0]}")).json()
    assert detail["viewer_roles"] == ["claimant"] and detail["relationship"] == "internal"


@pytest.mark.parametrize("route", ["agent-workspace", "agent-tasks", "agent-tasks/" + str(uuid4())])
async def test_owner_routes_reject_invalid_jwt(workspace_api, route):
    client, *_ = workspace_api
    response = await client.get(BASE + "/" + route, headers={"Authorization": "Bearer invalid"})
    assert response.status_code == 401
