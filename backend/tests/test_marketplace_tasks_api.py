"""Requests exercise the actual router, authentication and PostgreSQL path."""

import asyncio
from contextvars import ContextVar
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from test_marketplace_tasks import marketplace_db as marketplace_db
from test_marketplace_tasks import marketplace_pg as marketplace_pg

from app.api.v1.agents import router
from app.core.database import get_db
from app.core.redis_client import get_redis

REQUEST_PIDS: ContextVar[list[int] | None] = ContextVar("marketplace_request_pids", default=None)


@pytest.fixture
async def marketplace_api(marketplace_db):
    maker, project_id, owner, other, keys = marketplace_db
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")

    async def request_db():
        async with maker.begin() as db:
            pids = REQUEST_PIDS.get()
            if pids is not None:
                pid = await db.scalar(text("SELECT pg_backend_pid()"))
                assert isinstance(pid, int)
                pids.append(pid)
            yield db

    app.dependency_overrides[get_db] = request_db
    app.dependency_overrides[get_redis] = lambda: None
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client, marketplace_db


@pytest.fixture
def demo_body():
    return {
        "idempotency_key": str(uuid4()),
        "title": "OSS onboarding demo",
        "description": "Write onboarding.txt containing exactly: "
        "AgentSpore onboarding demo (with a trailing newline).",
    }


@pytest.mark.parametrize("identity,expected", [(None, 422), ("invalid", 401), (1, 403), (0, 200)])
async def test_create_route_auth(marketplace_api, demo_body, identity, expected):
    client, (maker, project_id, owner, other, keys) = marketplace_api
    headers = (
        {}
        if identity is None
        else {"X-API-Key": keys[identity] if isinstance(identity, int) else str(uuid4())}
    )
    response = await client.post(
        f"/api/v1/agents/projects/{project_id}/tasks", json=demo_body, headers=headers
    )
    assert response.status_code == expected
    async with maker() as db:
        assert await db.scalar(text("SELECT count(*) FROM tasks")) == int(expected == 200)


@pytest.mark.parametrize(
    "patch",
    [
        {"idempotency_key": None},
        {"idempotency_key": "invalid"},
        {"title": ""},
        {"title": "x" * 301},
        {"description": ""},
        {"description": "x" * 4001},
        {"type": "fix_bug"},
        {"status": "completed"},
        {"source_type": "manual"},
        {"created_by_agent_id": str(uuid4())},
    ],
)
async def test_create_validation(marketplace_api, demo_body, patch):
    client, (maker, project_id, owner, other, keys) = marketplace_api
    response = await client.post(
        f"/api/v1/agents/projects/{project_id}/tasks",
        json={**demo_body, **patch},
        headers={"X-API-Key": keys[0]},
    )
    assert response.status_code == 422
    async with maker() as db:
        assert await db.scalar(text("SELECT count(*) FROM tasks")) == 0


@pytest.mark.parametrize("archived,expected", [(False, 404), (True, 409)])
async def test_create_missing_or_archived(marketplace_api, demo_body, archived, expected):
    client, (maker, project_id, owner, other, keys) = marketplace_api
    if archived:
        async with maker.begin() as db:
            await db.execute(text("UPDATE projects SET status = 'archived'"))
    else:
        project_id = uuid4()
    response = await client.post(
        f"/api/v1/agents/projects/{project_id}/tasks",
        json=demo_body,
        headers={"X-API-Key": keys[0]},
    )
    assert response.status_code == expected
    async with maker() as db:
        assert await db.scalar(text("SELECT count(*) FROM tasks")) == 0


@pytest.mark.parametrize("state", ["open", "claimed", "completed", "cancelled"])
async def test_create_retry_preserves_all_states(marketplace_api, demo_body, state):
    client, (maker, project_id, owner, other, keys) = marketplace_api
    url, headers = f"/api/v1/agents/projects/{project_id}/tasks", {"X-API-Key": keys[0]}
    first = (await client.post(url, json=demo_body, headers=headers)).json()
    async with maker.begin() as db:
        await db.execute(text("UPDATE tasks SET status = :state"), {"state": state})
    repeat = await client.post(url, json=demo_body, headers=headers)
    assert repeat.json() == {"task_id": first["task_id"], "status": state, "created": False}
    changed = await client.post(url, json={**demo_body, "title": "Different"}, headers=headers)
    assert changed.status_code == 409
    async with maker() as db:
        task = (await db.execute(text("SELECT * FROM tasks"))).mappings().one()
        assert task["created_by_agent_id"] == owner
        assert task["priority"] == "medium" and task["source_type"] == "manual"
        assert task["title"] == demo_body["title"]


@pytest.fixture
async def created_task(marketplace_api, demo_body):
    client, (maker, project_id, owner, other, keys) = marketplace_api
    response = await client.post(
        f"/api/v1/agents/projects/{project_id}/tasks",
        json=demo_body,
        headers={"X-API-Key": keys[0]},
    )
    assert response.status_code == 200
    return response.json()["task_id"]


async def test_completed_task_cannot_be_unclaimed(marketplace_api, created_task):
    client, (maker, project_id, owner, other, keys) = marketplace_api
    url, headers = f"/api/v1/agents/tasks/{created_task}", {"X-API-Key": keys[1]}
    assert (await client.post(url + "/claim", headers=headers)).status_code == 200
    assert (
        await client.post(url + "/complete", json={"result": "done"}, headers=headers)
    ).status_code == 200
    response = await client.post(url + "/unclaim", headers=headers)
    assert response.status_code == 409
    async with maker() as db:
        assert await db.scalar(text("SELECT status FROM tasks")) == "completed"


@pytest.fixture
async def competing_requests(marketplace_api):
    client, (maker, project_id, owner, other, keys) = marketplace_api

    async def run_pair(requests, table, row_id):
        pids = []
        token = REQUEST_PIDS.set(pids)
        try:
            async with maker() as blocker, maker() as observer:
                await blocker.execute(
                    text(f"SELECT id FROM {table} WHERE id = :id FOR UPDATE"), {"id": row_id}
                )
                await observer.scalar(text("SELECT count(*) FROM pg_stat_activity"))
                async with asyncio.TaskGroup() as group:
                    jobs = [
                        group.create_task(client.post(url, json=body, headers=headers))
                        for url, body, headers in requests
                    ]
                    for _ in range(200):
                        await observer.execute(text("SELECT pg_stat_clear_snapshot()"))
                        blocked = await observer.scalar(
                            text(
                                "SELECT count(*) FROM pg_stat_activity WHERE "
                                "pid = ANY(CAST(:pids AS int[])) AND wait_event_type = 'Lock'"
                            ),
                            {"pids": pids},
                        )
                        if len(pids) == 2 and blocked == 2:
                            break
                        await asyncio.sleep(0.01)
                    else:
                        raise AssertionError("Both competing requests must reach the row lock")
                    await blocker.commit()
                return [job.result() for job in jobs]
        finally:
            REQUEST_PIDS.reset(token)

    return run_pair


async def test_concurrent_create_one_task(marketplace_api, demo_body, competing_requests):
    client, (maker, project_id, owner, other, keys) = marketplace_api
    request = (f"/api/v1/agents/projects/{project_id}/tasks", demo_body, {"X-API-Key": keys[0]})
    results = await competing_requests([request, request], "projects", project_id)
    assert [r.status_code for r in results] == [200, 200]
    assert len({r.json()["task_id"] for r in results}) == 1
    assert sorted(r.json()["created"] for r in results) == [False, True]
    async with maker() as db:
        assert await db.scalar(text("SELECT count(*) FROM tasks")) == 1


@pytest.mark.parametrize("operation", ["claim", "complete", "unclaim"])
async def test_competing_transition_one_winner(
    marketplace_api, created_task, competing_requests, operation
):
    client, (maker, project_id, owner, other, keys) = marketplace_api
    url = f"/api/v1/agents/tasks/{created_task}"
    if operation != "claim":
        assert (
            await client.post(url + "/claim", headers={"X-API-Key": keys[0]})
        ).status_code == 200
    requests = [
        (url + "/" + operation, {"result": "done"}, {"X-API-Key": key})
        for key in (keys if operation == "claim" else [keys[0], keys[0]])
    ]
    responses = await competing_requests(requests, "tasks", created_task)
    assert sorted(r.status_code for r in responses) == [200, 409]
    async with maker() as db:
        assert await db.scalar(text("SELECT sum(karma) FROM agents")) == (
            15 if operation == "complete" else 0
        )
        count = await db.scalar(
            text("SELECT count(*) FROM agent_activity WHERE action_type = :action"),
            {"action": "task_" + ("claimed" if operation == "claim" else "completed")},
        )
        assert count == (0 if operation == "unclaim" else 1)
        if operation == "unclaim":
            task = (
                (await db.execute(text("SELECT status, claimed_by_agent_id FROM tasks")))
                .mappings()
                .one()
            )
            assert task["status"] == "open" and task["claimed_by_agent_id"] is None


async def test_completion_owner_and_repeat(marketplace_api, created_task):
    client, (maker, project_id, owner, other, keys) = marketplace_api
    url, headers = f"/api/v1/agents/tasks/{created_task}", {"X-API-Key": keys[1]}
    assert (await client.post(url + "/claim", headers=headers)).status_code == 200
    foreign = await client.post(
        url + "/complete", json={"result": "done"}, headers={"X-API-Key": keys[0]}
    )
    assert foreign.status_code == 403
    assert (await client.post(url + "/unclaim", headers={"X-API-Key": keys[0]})).status_code == 403
    assert (
        await client.post(url + "/complete", json={"result": "done"}, headers=headers)
    ).status_code == 200
    assert (
        await client.post(url + "/complete", json={"result": "repeat"}, headers=headers)
    ).status_code == 409
    async with maker() as db:
        assert await db.scalar(text("SELECT sum(karma) FROM agents")) == 15
        assert await db.scalar(text("SELECT result FROM tasks")) == "done"


@pytest.mark.parametrize("operation", ["claim", "complete", "unclaim"])
async def test_missing_transition(marketplace_api, operation):
    client, (maker, project_id, owner, other, keys) = marketplace_api
    response = await client.post(
        f"/api/v1/agents/tasks/{uuid4()}/{operation}",
        json={"result": "done"},
        headers={"X-API-Key": keys[0]},
    )
    assert response.status_code == 404


async def test_create_key_namespaces(marketplace_api, demo_body):
    client, (maker, project_id, owner, other, keys) = marketplace_api
    second = uuid4()
    async with maker.begin() as db:
        await db.execute(
            text(
                "INSERT INTO projects (id, title, creator_agent_id) "
                "VALUES (:id, 'Second project', :owner)"
            ),
            {"id": second, "owner": owner},
        )
        await db.execute(
            text(
                "INSERT INTO tasks (project_id, type, title, status, source_type, "
                "source_key, assigned_to_agent_id) VALUES (:pid, 'notification', "
                "'Synthetic notification', 'pending', 'github', :key, :owner)"
            ),
            {"pid": project_id, "owner": owner, "key": "manual:" + demo_body["idempotency_key"]},
        )
    ids = []
    for pid in (project_id, second):
        response = await client.post(
            f"/api/v1/agents/projects/{pid}/tasks", json=demo_body, headers={"X-API-Key": keys[0]}
        )
        assert response.status_code == 200
        ids.append(response.json()["task_id"])
    assert ids[0] != ids[1]
    async with maker() as db:
        assert (
            await db.scalar(text("SELECT status FROM tasks WHERE type = 'notification'"))
            == "pending"
        )
