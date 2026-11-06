"""A4 schema seam: real PostgreSQL must contain the durable queue relation."""

from uuid import uuid4

import pytest
from solders.pubkey import Pubkey
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
            await connection.execute(text("TRUNCATE payout_delivery_intents CASCADE"))
    assert getattr(error.value.orig, "sqlstate") == "23514"


@pytest.mark.usefixtures("deployment_engine")
async def test_shared_bootstrap_installs_claim_guards(request):
    """Require the shared migration loop to install both enabled claim guards."""
    engine = request.getfixturevalue("deployment_engine")
    async with engine.connect() as connection:
        rows = (
            await connection.execute(
                text(
                    "SELECT tgname, tgenabled::text FROM pg_trigger "
                    "WHERE tgrelid = 'public.payout_delivery_claims'::regclass AND NOT tgisinternal"
                )
            )
        ).all()
    assert set(rows) == {
        ("payout_claim_identity", "O"),
        ("payout_claim_no_truncate", "O"),
        ("payout_claim_attempt_pair", "O"),
    }
    with pytest.raises(DBAPIError) as error:
        async with engine.begin() as connection:
            await connection.execute(text("TRUNCATE payout_delivery_claims CASCADE"))
    assert getattr(error.value.orig, "sqlstate") == "23514"


@pytest.mark.usefixtures("deployment_engine")
async def test_shared_bootstrap_installs_attempt_barrier(request):
    """Require strict immutable-attempt and deferred pair guards in the real shared schema."""
    engine = request.getfixturevalue("deployment_engine")
    async with engine.connect() as connection:
        rows = (
            await connection.execute(
                text(
                    "SELECT tgname, tgenabled::text, tgdeferrable, tginitdeferred "
                    "FROM pg_trigger WHERE tgrelid = "
                    "'public.payout_delivery_attempts'::regclass AND NOT tgisinternal"
                )
            )
        ).all()
    assert set(rows) == {
        ("payout_attempt_immutable", "O", False, False),
        ("payout_attempt_no_truncate", "O", False, False),
        ("payout_attempt_claim_pair", "O", True, True),
    }
    with pytest.raises(DBAPIError) as error:
        async with engine.begin() as connection:
            await connection.execute(text("TRUNCATE payout_delivery_attempts"))
    assert getattr(error.value.orig, "sqlstate") == "23514"


async def seed_shared_claim(engine):
    """Commit valid bootstrap rows before testing the separate deferred failure transaction."""
    identity, worker = uuid4(), uuid4()
    values: dict[str, object] = {
        name: str(Pubkey.from_bytes(bytes([n]) * 32))
        for n, name in enumerate(
            ("mint", "source", "destination", "authority", "fee_payer", "recipient"), 1
        )
    }
    values |= {"id": identity, "allocation": identity.hex, "worker": worker}
    async with engine.begin() as db:
        await db.execute(
            text(
                "INSERT INTO payout_delivery_intents(intent_id, producer_namespace, "
                "allocation_id, genesis_hash, mint, "
                "program, source, destination, authority, fee_payer, "
                "recipient, decimals, raw_amount) VALUES "
                "(:id, 'bootstrap', :allocation, repeat('1', 32), "
                ":mint, 'TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb', :source, :destination, "
                ":authority, :fee_payer, :recipient, 6, '1')"
            ),
            values,
        )
        await db.execute(
            text(
                "INSERT INTO payout_delivery_claims VALUES "
                "(:id, 1, 'claimed', :worker, clock_timestamp() + interval '300 seconds')"
            ),
            values,
        )
    return values


@pytest.mark.usefixtures("deployment_engine")
@pytest.mark.parametrize("half", ["claim", "attempt"])
async def test_shared_bootstrap_rejects_incomplete_prepared_pair(request, half):
    """Exercise the actual commit-time coupling through the shared migration bootstrap."""
    engine = request.getfixturevalue("deployment_engine")
    values = await seed_shared_claim(engine)
    with pytest.raises(DBAPIError) as error:
        async with engine.begin() as db:
            if half == "claim":
                await db.execute(
                    text(
                        "UPDATE payout_delivery_claims SET state = 'prepared' WHERE intent_id = :id"
                    ),
                    values,
                )
            else:
                await db.execute(
                    text(
                        "INSERT INTO payout_delivery_attempts VALUES "
                        "(:id, 1, :worker, decode('00', 'hex'), decode(repeat('00', 64), 'hex'), "
                        "repeat('1', 32), '0')"
                    ),
                    values,
                )
    assert getattr(error.value.orig, "sqlstate") == "23514"
    async with engine.connect() as db:
        assert (
            await db.scalar(
                text("SELECT state FROM payout_delivery_claims WHERE intent_id = :id"), values
            )
            == "claimed"
        )
        assert (
            await db.scalar(
                text("SELECT count(*) FROM payout_delivery_attempts WHERE intent_id = :id"), values
            )
            == 0
        )
