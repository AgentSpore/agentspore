"""SEC.CHAT ownership uses real HTTP, identities, migrations, PostgreSQL and Redis."""

import json
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

from app.api.v1.chat import router
from app.core.database import get_db
from app.core.redis_client import get_redis
from app.core.security import create_access_token

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest.fixture
async def chat_api(usage_world, a3_redis):
    """Keep authentication, service, repository and transactional writes real."""
    other = uuid4()
    async with usage_world.maker.begin() as db:
        await db.execute(
            text("UPDATE users SET name = 'Shared name', is_verified = TRUE WHERE id = :id"),
            {"id": usage_world.owner},
        )
        await db.execute(
            text(
                "INSERT INTO users (id, email, hashed_password, name, is_verified) "
                "VALUES (:id, 'other-chat@example.invalid', 'synthetic', 'Shared name', TRUE)"
            ),
            {"id": other},
        )
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")

    async def request_db():
        async with usage_world.maker.begin() as db:
            yield db

    app.dependency_overrides[get_db] = request_db
    app.dependency_overrides[get_redis] = lambda: a3_redis
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield SimpleNamespace(client=client, world=usage_world, other=other, redis=a3_redis)
    finally:
        app.dependency_overrides.clear()


def bearer(user_id):
    return {"Authorization": f"Bearer {create_access_token(str(user_id))}"}


def chat_routes(case, scope):
    if scope == "public":
        return dict(
            create="/api/v1/chat/human-message",
            mutation="/api/v1/chat/human-messages",
            agent_create="/api/v1/chat/message",
            agent_mutation="/api/v1/chat/messages",
            read="/api/v1/chat/messages",
            table="agent_messages",
        )
    base = f"/api/v1/chat/project/{case.world.projects[0]}"
    return dict(
        create=base + "/human-messages",
        mutation=base + "/human-messages",
        agent_create=base + "/messages",
        agent_mutation=base + "/messages",
        read=base + "/messages",
        table="project_messages",
    )


async def post_human(case, scope, extra=None):
    routes = chat_routes(case, scope)
    body = {"name": "Ignored client display name", "content": "Owner original", **(extra or {})}
    response = await case.client.post(routes["create"], headers=bearer(case.world.owner), json=body)
    assert response.status_code == 200
    return response.json()["message_id"]


async def snapshot(case, scope, message_id):
    table = chat_routes(case, scope)["table"]
    async with case.world.maker() as db:
        row = await db.execute(
            text(f"SELECT content, is_deleted, edited_at, human_name FROM {table} WHERE id = :id"),
            {"id": message_id},
        )
        return dict(row.mappings().one())


async def mutate(case, scope, message_id, method, headers):
    url = chat_routes(case, scope)["mutation"] + "/" + message_id
    return await case.client.request(
        method, url, headers=headers, json={"content": "Changed content"}
    )


@pytest.mark.parametrize("scope", ["public", "project"])
@pytest.mark.parametrize("method", ["PATCH", "DELETE"])
async def test_same_name_foreign_mutation_is_denied(chat_api, scope, method):
    """The baseline name-only guard must fail these four SEC.CHAT assertions."""
    message_id = await post_human(chat_api, scope)
    before = await snapshot(chat_api, scope, message_id)
    async with chat_api.redis.pubsub() as events:
        await events.subscribe("agentspore:chat")
        acknowledgement = await events.get_message(timeout=1)
        assert acknowledgement is not None
        assert acknowledgement["type"] == "subscribe"
        assert acknowledgement["channel"] == "agentspore:chat"
        response = await mutate(chat_api, scope, message_id, method, bearer(chat_api.other))
        assert response.status_code == 403
        assert await snapshot(chat_api, scope, message_id) == before
        assert await events.get_message(ignore_subscribe_messages=True, timeout=0.2) is None


@pytest.mark.parametrize("scope", ["public", "project"])
@pytest.mark.parametrize("method", ["PATCH", "DELETE"])
async def test_owner_can_mutate_own_message(chat_api, scope, method):
    message_id = await post_human(chat_api, scope)
    response = await mutate(chat_api, scope, message_id, method, bearer(chat_api.world.owner))
    assert response.status_code == 200
    row = await snapshot(chat_api, scope, message_id)
    assert row["content"] == ("Changed content" if method == "PATCH" else "[deleted]")
    assert row["is_deleted"] is (method == "DELETE")


@pytest.mark.parametrize("scope", ["public", "project"])
@pytest.mark.parametrize("method", ["PATCH", "DELETE"])
async def test_renamed_owner_keeps_ownership(chat_api, scope, method):
    message_id = await post_human(chat_api, scope)
    async with chat_api.world.maker.begin() as db:
        await db.execute(
            text("UPDATE users SET name = 'Renamed owner' WHERE id = :id"),
            {"id": chat_api.world.owner},
        )
    response = await mutate(chat_api, scope, message_id, method, bearer(chat_api.world.owner))
    assert response.status_code == 200
    assert (await snapshot(chat_api, scope, message_id))["human_name"] == "Shared name"


@pytest.mark.parametrize("scope", ["public", "project"])
@pytest.mark.parametrize("method", ["PATCH", "DELETE"])
async def test_legacy_unattributed_message_denies_matching_name(chat_api, scope, method):
    routes, message_id = chat_routes(chat_api, scope), str(uuid4())
    columns, values = (
        "id, agent_id, sender_type, human_name, content",
        ":id, NULL, 'user', 'Shared name', 'Legacy content'",
    )
    params = {"id": message_id}
    if scope == "project":
        columns += ", project_id"
        values += ", :project"
        params["project"] = chat_api.world.projects[0]
    async with chat_api.world.maker.begin() as db:
        await db.execute(
            text(f"INSERT INTO {routes['table']} ({columns}) VALUES ({values})"), params
        )
    before = await snapshot(chat_api, scope, message_id)
    response = await mutate(chat_api, scope, message_id, method, bearer(chat_api.world.owner))
    assert response.status_code == 403
    assert await snapshot(chat_api, scope, message_id) == before


@pytest.mark.parametrize("scope", ["public", "project"])
async def test_create_ignores_spoofed_owner_and_serializes_authenticated_id(chat_api, scope):
    async with chat_api.redis.pubsub() as events:
        await events.subscribe("agentspore:chat")
        acknowledgement = await events.get_message(timeout=1)
        assert acknowledgement is not None
        assert acknowledgement["type"] == "subscribe"
        assert acknowledgement["channel"] == "agentspore:chat"
        message_id = await post_human(chat_api, scope, {"sender_user_id": str(chat_api.other)})
        response = await chat_api.client.get(
            chat_routes(chat_api, scope)["read"], headers=bearer(chat_api.world.owner)
        )
        assert response.status_code == 200
        message = next(row for row in response.json() if row["id"] == message_id)
        assert message["sender_user_id"] == str(chat_api.world.owner)
        table = chat_routes(chat_api, scope)["table"]
        async with chat_api.world.maker() as db:
            stored = await db.scalar(
                text(f"SELECT sender_user_id FROM {table} WHERE id = :id"), {"id": message_id}
            )
        assert str(stored) == str(chat_api.world.owner)
        assert (await snapshot(chat_api, scope, message_id))["human_name"] == "Shared name"
        if scope == "public":
            event = await events.get_message(ignore_subscribe_messages=True, timeout=1)
            assert event is not None
            payload = json.loads(event["data"])
            assert payload["id"] == message_id
            assert payload["sender_user_id"] == str(chat_api.world.owner)


@pytest.mark.parametrize("scope", ["public", "project"])
@pytest.mark.parametrize("method", ["PATCH", "DELETE"])
@pytest.mark.parametrize("actor", ["owner_agent", "other_agent", "human"])
async def test_agent_ownership_guard_remains(chat_api, scope, method, actor):
    routes = chat_routes(chat_api, scope)
    owner = {"X-API-Key": chat_api.world.keys[0]}
    response = await chat_api.client.post(
        routes["agent_create"],
        headers=owner,
        json={"content": "Agent original", "message_type": "text"},
    )
    assert response.status_code == 200
    message_id = response.json()["message_id"]
    if actor == "human":
        response = await mutate(chat_api, scope, message_id, method, bearer(chat_api.world.owner))
    else:
        key = chat_api.world.keys[int(actor == "other_agent")]
        response = await chat_api.client.request(
            method,
            routes["agent_mutation"] + "/" + message_id,
            headers={"X-API-Key": key},
            json={"content": "Changed content"},
        )
    assert response.status_code == (200 if actor == "owner_agent" else 403)
    if actor != "owner_agent":
        assert (await snapshot(chat_api, scope, message_id))["content"] == "Agent original"


@pytest.mark.parametrize("scope", ["public", "project"])
@pytest.mark.parametrize("method", ["PATCH", "DELETE"])
@pytest.mark.parametrize("identity", ["missing", "invalid"])
async def test_human_mutation_requires_valid_bearer(chat_api, scope, method, identity):
    message_id = await post_human(chat_api, scope)
    headers = {} if identity == "missing" else {"Authorization": "Bearer synthetic-invalid-token"}
    response = await mutate(chat_api, scope, message_id, method, headers)
    assert response.status_code in ({401, 403} if identity == "missing" else {401})
    assert (await snapshot(chat_api, scope, message_id))["content"] == "Owner original"
