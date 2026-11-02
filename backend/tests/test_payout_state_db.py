"""Exercise payout reference persistence through the service and real V29 SQL."""

import asyncio
from pathlib import Path
from uuid import uuid4

import pytest
import test_marketplace_tasks as marketplace
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.services.payout_service import PayoutService

payout_postgres = marketplace.marketplace_pg
PAYOUT_MIGRATION = (
    Path(__file__).resolve().parents[2] / "db/migrations/V29__solana_wallet_payouts.sql"
).read_text()
PAYOUT_STATE = text(
    "SELECT status, tx_signature, error_message, sent_at, confirmed_at FROM token_payouts"
)


@pytest.fixture
async def payout_db(payout_postgres):
    url = payout_postgres.get_connection_url().replace("psycopg2", "asyncpg")
    schema = "payout_" + uuid4().hex
    admin = create_async_engine(url)
    engine = create_async_engine(url, connect_args={"server_settings": {"search_path": schema}})
    owner, payout_id = uuid4(), uuid4()
    try:
        async with admin.begin() as db:
            await db.execute(text(f'CREATE SCHEMA "{schema}"'))
        async with engine.begin() as db:
            await db.execute(text("CREATE TABLE users (id UUID PRIMARY KEY)"))
            raw = await db.get_raw_connection()
            driver = raw.driver_connection
            assert driver is not None
            await driver.execute(PAYOUT_MIGRATION)
            await db.execute(text("INSERT INTO users(id) VALUES (:id)"), {"id": owner})
            await db.execute(
                text(
                    "INSERT INTO token_payouts "
                    "(id, user_id, solana_wallet, amount, pool_total, period_start, period_end) "
                    "VALUES (:id, :owner, 'Synthetic wallet', 1, 1, "
                    "DATE '2026-01-01', DATE '2026-01-02')"
                ),
                {"id": payout_id, "owner": owner},
            )
        yield async_sessionmaker(engine, expire_on_commit=False), str(payout_id)
    finally:
        await engine.dispose()
        async with admin.begin() as db:
            await db.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin.dispose()


@pytest.mark.parametrize("status", ["confirmed", "failed"])
async def test_sent_status_update_preserves_signature(payout_db, status):
    maker, payout_id = payout_db
    async with maker.begin() as db:
        await PayoutService(db).mark_payout_sent(payout_id, "Synthetic signature")
    async with maker.begin() as db:
        service = PayoutService(db)
        if status == "confirmed":
            await service.mark_payout_confirmed(payout_id)
        else:
            await service.mark_payout_failed(payout_id, "Synthetic failure")
    async with maker() as db:
        row = (await db.execute(PAYOUT_STATE)).mappings().one()
        assert row["status"] == status
        assert row["tx_signature"] == "Synthetic signature"
        assert row["sent_at"] is not None
        assert (row["confirmed_at"] is not None) == (status == "confirmed")
        assert row["error_message"] == ("Synthetic failure" if status == "failed" else None)


async def test_same_signature_repeats_without_replacement(payout_db):
    maker, payout_id = payout_db
    for _ in range(2):
        async with maker.begin() as db:
            await PayoutService(db).mark_payout_sent(payout_id, "Synthetic signature")
    async with maker() as db:
        row = (await db.execute(PAYOUT_STATE)).mappings().one()
        assert (row["status"], row["tx_signature"]) == ("sent", "Synthetic signature")


async def test_conflicting_signature_rejects_entire_update(payout_db):
    maker, payout_id = payout_db
    async with maker.begin() as db:
        await PayoutService(db).mark_payout_sent(payout_id, "Synthetic signature")
    async with maker() as db:
        before = (await db.execute(PAYOUT_STATE)).one()
    with pytest.raises(ValueError, match="Payout update not applied"):
        async with maker.begin() as db:
            await PayoutService(db).repo.update_payout_status(
                payout_id,
                status="failed",
                tx_signature="Conflicting signature",
                error_message="Rejected",
            )
    async with maker() as db:
        assert (await db.execute(PAYOUT_STATE)).one() == before


@pytest.mark.parametrize("operation", ["sent", "confirmed", "failed"])
async def test_missing_payout_is_observable(payout_db, operation):
    maker, _ = payout_db
    missing_id = str(uuid4())
    with pytest.raises(ValueError, match="Payout update not applied"):
        async with maker.begin() as db:
            service = PayoutService(db)
            if operation == "sent":
                await service.mark_payout_sent(missing_id, "Synthetic signature")
            elif operation == "confirmed":
                await service.mark_payout_confirmed(missing_id)
            else:
                await service.mark_payout_failed(missing_id, "Synthetic failure")


@pytest.mark.parametrize("signature", ["", "   "])
async def test_empty_reference_is_rejected_without_changing_row(payout_db, signature):
    maker, payout_id = payout_db
    with pytest.raises(ValueError, match="Payout transaction reference must not be empty"):
        async with maker.begin() as db:
            await PayoutService(db).mark_payout_sent(payout_id, signature)
    async with maker() as db:
        row = (await db.execute(PAYOUT_STATE)).mappings().one()
        assert (row["status"], row["tx_signature"], row["sent_at"]) == ("pending", None, None)


@pytest.fixture
async def competing_assignment(payout_db):
    maker, payout_id = payout_db
    barrier = asyncio.Barrier(2)
    backend_ids = set()

    async def assign(signature):
        try:
            async with maker.begin() as db:
                backend_ids.add(await db.scalar(text("SELECT pg_backend_pid()")))
                await barrier.wait()
                assert len(backend_ids) == 2
                await PayoutService(db).mark_payout_sent(payout_id, signature)
        except ValueError as error:
            assert str(error) == "Payout update not applied"
            return None
        return signature

    return assign


async def test_concurrent_first_assignments_have_one_winner(payout_db, competing_assignment):
    maker, _ = payout_db
    async with asyncio.timeout(10), asyncio.TaskGroup() as group:
        first = group.create_task(competing_assignment("Synthetic first"))
        second = group.create_task(competing_assignment("Synthetic second"))
    results = [first.result(), second.result()]
    assert results.count(None) == 1
    winner = next(result for result in results if result is not None)
    async with maker() as db:
        row = (await db.execute(PAYOUT_STATE)).mappings().one()
        assert (row["status"], row["tx_signature"]) == ("sent", winner)


async def test_caller_rollback_restores_signature_and_status(payout_db):
    maker, payout_id = payout_db
    async with maker() as db:
        await PayoutService(db).mark_payout_sent(payout_id, "Rolled back signature")
        async with maker() as observer:
            row = (await observer.execute(PAYOUT_STATE)).mappings().one()
            assert (row["status"], row["tx_signature"]) == ("pending", None)
        await db.rollback()
    async with maker.begin() as db:
        await PayoutService(db).mark_payout_sent(payout_id, "Committed signature")
    async with maker() as db:
        await PayoutService(db).mark_payout_confirmed(payout_id)
        await db.rollback()
    async with maker() as db:
        row = (await db.execute(PAYOUT_STATE)).mappings().one()
        assert (row["status"], row["tx_signature"]) == ("sent", "Committed signature")
        assert row["confirmed_at"] is None
