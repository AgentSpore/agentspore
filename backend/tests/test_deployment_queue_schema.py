"""A4 schema seam: real PostgreSQL must contain the durable queue relation."""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_deployment_control_plane import deployment_engine as deployment_engine
from test_deployment_control_plane import deployment_postgres as deployment_postgres

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]


async def test_deployment_queue_migration_creates_relation(deployment_engine):
    """The pending V94 must create the actual persistent PostgreSQL relation."""
    async with deployment_engine.connect() as connection:
        relation = await connection.scalar(text("SELECT to_regclass('public.project_deployments')"))
    assert relation is not None


@pytest.mark.usefixtures("deployment_engine")
async def test_shared_bootstrap_installs_payout_immutability(request):
    """Install the payout relation and active guards through the shared migration loop."""
    engine = request.getfixturevalue("deployment_engine")
    async with engine.connect() as connection:
        relation = await connection.scalar(text("SELECT to_regclass('payout_delivery_intents')"))
        triggers = (
            await connection.execute(
                text(
                    "SELECT tgname, tgenabled::text FROM pg_trigger "
                    "WHERE tgrelid = 'public.payout_delivery_intents'::regclass "
                    "AND NOT tgisinternal"
                )
            )
        ).all()
    assert relation is not None
    assert set(triggers) == {
        ("payout_delivery_immutable", "O"),
        ("payout_delivery_no_truncate", "O"),
    }
    with pytest.raises(DBAPIError) as error:
        async with engine.begin() as connection:
            await connection.execute(text("TRUNCATE payout_delivery_intents"))
    assert getattr(error.value.orig, "sqlstate") == "23514"
