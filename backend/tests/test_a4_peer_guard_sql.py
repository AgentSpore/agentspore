"""The serve kill switch's internal-vs-A4 lookup against real PostgreSQL."""

from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from starlette.datastructures import State
from test_a4_peer_guard import inner_app, make_scope, run, status_of
from test_service_usage import usage_pg as usage_pg
from test_service_usage import usage_world as usage_world

from app.middleware.service_gateway import ServiceGatewayMiddleware
from app.schemas.usage import ServiceRoute


def route_of(project_id):
    return ServiceRoute(project_id=project_id, handle="svc", active_revision=uuid4())


def with_db(maker):
    resources = State()
    resources.service_gateway_db_factory = maker
    return ServiceGatewayMiddleware(
        inner_app, resources=resources, domains=["agentspore.com"], serve_enabled=False
    )


@pytest.fixture
def world(usage_world):  # noqa: F811 (pytest injects the imported fixture by parameter name)
    return usage_world


async def test_platform_service_lookup_matrix(world):
    internal_project = world.projects[0]
    async with world.maker.begin() as db:
        await db.execute(
            text("UPDATE agents SET agent_type = 'internal' WHERE id = :id"),
            {"id": world.agents[0]},
        )
        await db.execute(
            text("UPDATE agents SET agent_type = 'external' WHERE id = :id"),
            {"id": world.agents[1]},
        )
        orphan = uuid4()
        await db.execute(
            text("INSERT INTO projects (id, title) VALUES (:id, 'no creator')"), {"id": orphan}
        )
    mw = with_db(world.maker)
    assert await mw._is_platform_service(route_of(internal_project)) is True
    assert await mw._is_platform_service(route_of(world.projects[2])) is False
    assert await mw._is_platform_service(route_of(orphan)) is False
    assert await mw._is_platform_service(route_of(uuid4())) is False


class BrokenSession:
    async def __aenter__(self):
        raise SQLAlchemyError("db down")

    async def __aexit__(self, *exc):
        return False


async def test_db_failure_during_serve_check_is_503(monkeypatch):
    async def fake_resolve(self, handle):
        return route_of(uuid4())

    monkeypatch.setattr(ServiceGatewayMiddleware, "_resolve", fake_resolve)
    resources = State()
    resources.service_gateway_db_factory = BrokenSession
    mw = ServiceGatewayMiddleware(
        inner_app, resources=resources, domains=["agentspore.com"], serve_enabled=False
    )
    scope = make_scope("http", ("203.0.113.7", 1), "/", [("host", "svc.agentspore.com")])
    assert status_of(await run(mw, scope)) == 503
