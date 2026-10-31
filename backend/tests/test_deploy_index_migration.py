"""A4 P2a: V108 index migration runs outside a transaction and is idempotent."""

from pathlib import Path

import pytest
from sqlalchemy import text
from test_deployment_control_plane import deployment_engine as deployment_engine
from test_deployment_control_plane import deployment_postgres as deployment_postgres

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]
MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "db/migrations/V108__project_deployments_creator_index.sql"
)


async def test_creator_index_created_concurrently_and_idempotent(deployment_engine):
    sql = MIGRATION.read_text()
    async with deployment_engine.connect() as connection:
        autocommit = await connection.execution_options(isolation_level="AUTOCOMMIT")
        await autocommit.execute(text(sql))
        await autocommit.execute(text(sql))
        row = (
            await autocommit.execute(
                text(
                    "SELECT indexdef, (SELECT indisvalid FROM pg_index WHERE indexrelid = "
                    "'idx_project_deployments_creator_created'::regclass) AS valid "
                    "FROM pg_indexes WHERE indexname = 'idx_project_deployments_creator_created'"
                )
            )
        ).one()
    assert "(creator_agent_id, created_at)" in row.indexdef
    assert row.valid is True
