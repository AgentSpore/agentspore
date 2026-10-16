"""A4 schema seam: real PostgreSQL must contain the durable queue relation."""

import pytest
from sqlalchemy import text
from test_deployment_control_plane import deployment_engine as deployment_engine
from test_deployment_control_plane import deployment_postgres as deployment_postgres

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]


async def test_deployment_queue_migration_creates_relation(deployment_engine):
    """The pending V94 must create the actual persistent PostgreSQL relation."""
    async with deployment_engine.connect() as connection:
        relation = await connection.scalar(text("SELECT to_regclass('public.project_deployments')"))
    assert relation is not None
