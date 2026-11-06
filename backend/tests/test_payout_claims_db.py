"""Prove exact-intent leases through actual CLI, services and PostgreSQL."""

import asyncio
import io
import json
import os
import sys
from pathlib import Path
from uuid import uuid4

import pytest
import test_payout_delivery_db as admission
from asyncpg import UndefinedTableError
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

from app.cli.payout_delivery import run_admission
from app.repositories.payout_delivery_repo import PayoutDeliveryRepository
from app.schemas.payout_delivery import ClaimFence, ClaimRequest, RenewRequest
from app.services.payout_delivery_service import admit_intent, manage_claim

allocation = admission.allocation
delivery_db = admission.delivery_db
marketplace_pg = admission.marketplace_pg
MIGRATIONS = Path(__file__).resolve().parents[2] / "db/migrations"


@pytest.fixture
async def claims_db(delivery_db, allocation):
    maker, url, schema = delivery_db
    async with maker.begin() as db:
        conn = await db.connection()
        raw = await conn.get_raw_connection()
        assert raw.driver_connection is not None
        for name in ("V112__payout_delivery_claims.sql", "R__payout_delivery_claim_identity.sql"):
            await raw.driver_connection.execute((MIGRATIONS / name).read_text())
        intent = await admit_intent(PayoutDeliveryRepository(db), allocation)
    return maker, url, schema, {"intent_id": intent["intent_id"], "worker_id": str(uuid4())}


async def invoke_claim(maker, command, payload):
    """Execute the CLI transaction boundary and parse its fixed JSON output."""
    out = io.StringIO()
    code = await run_admission(command, io.StringIO(json.dumps(payload)), out, maker)
    return code, json.loads(out.getvalue())


async def seed_claim(maker, key, expired):
    """Insert a permitted initial epoch using actual server time."""
    async with maker.begin() as db:
        await db.execute(
            text(
                "INSERT INTO payout_delivery_claims VALUES "
                "(:intent_id, 1, 'claimed', :worker_id, "
                "clock_timestamp() + :offset * interval '1 second')"
            ),
            key | {"offset": -1 if expired else 300},
        )


async def test_cli_lost_response_generations_and_nonshortening(claims_db):
    maker, _, _, key = claims_db
    code, first = await invoke_claim(maker, "claim", key | {"ttl_seconds": 300})
    assert code == 0 and first["epoch"] == 1
    assert (await invoke_claim(maker, "lease-show", {"intent_id": key["intent_id"]}))[
        1
    ] == first | {"verdict": "found"}
    assert (await invoke_claim(maker, "renew", key | {"epoch": 1, "ttl_seconds": 1}))[1][
        "expires_at"
    ] == first["expires_at"]
    assert await invoke_claim(maker, "claim", key | {"ttl_seconds": 1}) == (
        11,
        {"verdict": "unavailable"},
    )
    for epoch in (1, 2, 3):
        assert (await invoke_claim(maker, "release", key | {"epoch": epoch}))[1] == {
            "verdict": "released",
            "intent_id": key["intent_id"],
            "state": "ready",
            "epoch": epoch,
            "worker_id": None,
            "expires_at": None,
        }
        assert (await invoke_claim(maker, "claim", key | {"ttl_seconds": 300}))[1][
            "epoch"
        ] == epoch + 1
        for command in ("renew", "release", "recover"):
            payload = key | {"epoch": epoch} | ({"ttl_seconds": 1} if command == "renew" else {})
            before = await invoke_claim(maker, "lease-show", {"intent_id": key["intent_id"]})
            assert await invoke_claim(maker, command, payload) == (10, {"verdict": "stale"})
            assert (
                await invoke_claim(maker, "lease-show", {"intent_id": key["intent_id"]}) == before
            )


@pytest.mark.parametrize("command", ["renew", "release", "recover"])
@pytest.mark.parametrize("cause", ["worker", "epoch", "missing", "ready", "wrong_time"])
async def test_fenced_zero_rows_observable(claims_db, command, cause):
    maker, _, _, key = claims_db
    expired = (command == "recover") != (cause == "wrong_time")
    if cause != "missing":
        await seed_claim(maker, key, expired)
        if cause == "ready":
            await invoke_claim(maker, "recover" if expired else "release", key | {"epoch": 1})
    payload = key | {"epoch": 2 if cause == "epoch" else 1}
    if cause == "worker":
        payload["worker_id"] = str(uuid4())
    if command == "renew":
        payload["ttl_seconds"] = 30
    before = await invoke_claim(maker, "lease-show", {"intent_id": key["intent_id"]})
    assert await invoke_claim(maker, command, payload) == (10, {"verdict": "stale"})
    assert await invoke_claim(maker, "lease-show", {"intent_id": key["intent_id"]}) == before


async def test_server_clock_expiry_in_one_transaction(claims_db):
    maker, _, _, key = claims_db
    async with maker.begin() as db:
        start = await db.scalar(text("SELECT now()"))
        expiry = await db.scalar(
            text(
                "INSERT INTO payout_delivery_claims VALUES "
                "(:intent_id, 1, 'claimed', :worker_id, clock_timestamp()) RETURNING expires_at"
            ),
            key,
        )
        current = await db.scalar(text("SELECT clock_timestamp()"))
        assert start < expiry <= current
        repo = PayoutDeliveryRepository(db)
        request = ClaimFence.model_validate_json(json.dumps(key | {"epoch": 1}))
        assert (await manage_claim(repo, "release", request))["verdict"] == "stale"
        assert (await manage_claim(repo, "recover", request))["verdict"] == "recovered"
    assert (await invoke_claim(maker, "claim", key | {"ttl_seconds": 1}))[1]["epoch"] == 2


@pytest.mark.parametrize("command", ["claim", "renew", "release", "recover"])
async def test_caller_rollback_each_mutation(claims_db, command):
    maker, _, _, key = claims_db
    if command != "claim":
        await seed_claim(maker, key, command == "recover")
    before = await invoke_claim(maker, "lease-show", {"intent_id": key["intent_id"]})
    model = {"claim": ClaimRequest, "renew": RenewRequest}.get(command, ClaimFence)
    payload = key | ({"epoch": 1} if command != "claim" else {})
    if command in {"claim", "renew"}:
        payload["ttl_seconds"] = 300
    with pytest.raises(RuntimeError, match="caller abort"):
        async with maker.begin() as db:
            result = await manage_claim(
                PayoutDeliveryRepository(db),
                command,
                model.model_validate_json(json.dumps(payload)),
            )
            assert result["verdict"] in {"claimed", "renewed", "released", "recovered"}
            if command == "renew":
                assert result["expires_at"] > before[1]["expires_at"]
            raise RuntimeError("caller abort")
    assert await invoke_claim(maker, "lease-show", {"intent_id": key["intent_id"]}) == before


@pytest.mark.parametrize("rollback", [False, True])
async def test_parent_nowait_independent_intents(claims_db, allocation, rollback):
    maker, _, _, key = claims_db
    async with maker.begin() as db:
        other = await admit_intent(
            PayoutDeliveryRepository(db), allocation.model_copy(update={"allocation_id": "other"})
        )
    async with maker() as first, maker() as second:
        request = ClaimRequest.model_validate_json(json.dumps(key | {"ttl_seconds": 300}))
        await manage_claim(PayoutDeliveryRepository(first), "claim", request)
        pid1 = await first.scalar(text("SELECT pg_backend_pid()"))
        pid2 = await second.scalar(text("SELECT pg_backend_pid()"))
        assert pid1 != pid2
        with pytest.raises(DBAPIError) as error:
            await asyncio.wait_for(
                manage_claim(PayoutDeliveryRepository(second), "claim", request), 3
            )
        assert getattr(error.value.orig, "sqlstate") == "55P03"
        await second.rollback()
        assert await invoke_claim(maker, "claim", key | {"ttl_seconds": 300}) == (
            9,
            {"verdict": "busy"},
        )
        assert (
            await invoke_claim(
                maker, "claim", key | {"intent_id": other["intent_id"], "ttl_seconds": 300}
            )
        )[0] == 0
        await first.rollback() if rollback else await first.commit()
    code, result = await invoke_claim(maker, "claim", key | {"ttl_seconds": 300})
    assert code == (0 if rollback else 11)
    if rollback:
        assert result["epoch"] == 1


async def test_access_exclusive_lock_budget(claims_db):
    maker, _, _, key = claims_db
    async with maker() as holder, maker() as waiter:
        waiting_pid = await waiter.scalar(text("SELECT pg_backend_pid()"))
        await holder.execute(text("LOCK TABLE payout_delivery_intents IN ACCESS EXCLUSIVE MODE"))
        holding_pid = await holder.scalar(text("SELECT pg_backend_pid()"))
        assert waiting_pid != holding_pid
        request = ClaimRequest.model_validate_json(json.dumps(key | {"ttl_seconds": 300}))
        task = asyncio.create_task(manage_claim(PayoutDeliveryRepository(waiter), "claim", request))
        try:
            async with asyncio.timeout(3):
                while True:
                    blockers = await holder.scalar(
                        text("SELECT pg_blocking_pids(:pid)"), {"pid": waiting_pid}
                    )
                    if holding_pid in blockers:
                        break
                    await asyncio.sleep(0)
            assert (
                await holder.scalar(
                    text("SELECT count(*) FROM pg_locks WHERE pid = :pid AND NOT granted"),
                    {"pid": waiting_pid},
                )
                > 0
            )
            with pytest.raises(DBAPIError) as error:
                await asyncio.wait_for(task, 3)
            assert getattr(error.value.orig, "sqlstate") == "55P03"
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await waiter.rollback()
            await holder.rollback()
    async with maker.begin() as db:
        await PayoutDeliveryRepository(db).claim_budget()
        assert await db.scalar(text("SHOW lock_timeout")) == "250ms"
        assert await db.scalar(text("SHOW statement_timeout")) == "1s"
    async with maker() as db:
        assert await db.scalar(text("SHOW lock_timeout")) == "0"


@pytest.mark.parametrize(
    "statement",
    [
        "DELETE FROM payout_delivery_claims",
        "TRUNCATE payout_delivery_claims",
        "UPDATE payout_delivery_claims SET intent_id = gen_random_uuid()",
        "UPDATE payout_delivery_claims SET epoch = 0",
        "UPDATE payout_delivery_claims SET epoch = epoch + 1",
        "UPDATE payout_delivery_claims SET worker_id = gen_random_uuid()",
        "UPDATE payout_delivery_claims SET expires_at = expires_at - interval '1 second'",
    ],
)
async def test_claim_dml_guards(claims_db, statement):
    maker, _, _, key = claims_db
    await invoke_claim(maker, "claim", key | {"ttl_seconds": 300})
    with pytest.raises(DBAPIError):
        async with maker.begin() as db:
            await db.execute(text(statement))


async def test_ready_guards_and_repeatable_replay(claims_db):
    maker, _, _, key = claims_db
    _, claimed = await invoke_claim(maker, "claim", key | {"ttl_seconds": 300})
    async with maker.begin() as db:
        conn = await db.connection()
        raw = await conn.get_raw_connection()
        assert raw.driver_connection is not None
        for name in (
            "V112__payout_delivery_claims.sql",
            "R__payout_delivery_claim_identity.sql",
            "R__payout_delivery_claim_identity.sql",
        ):
            await raw.driver_connection.execute((MIGRATIONS / name).read_text())
    _, stored = await invoke_claim(maker, "lease-show", {"intent_id": key["intent_id"]})
    assert stored == claimed | {"verdict": "found"}
    await invoke_claim(maker, "release", key | {"epoch": 1})
    for change in (
        "state = state",
        "state = 'claimed', epoch = 3, worker_id = "
        "gen_random_uuid(), expires_at = clock_timestamp()",
    ):
        with pytest.raises(DBAPIError):
            async with maker.begin() as db:
                await db.execute(text("UPDATE payout_delivery_claims SET " + change))
    assert (await invoke_claim(maker, "claim", key | {"ttl_seconds": 300}))[1]["epoch"] == 2
    async with maker.begin() as db:
        await db.execute(text("DROP TABLE payout_delivery_claims"))
    assert await invoke_claim(maker, "lease-show", {"intent_id": key["intent_id"]}) == (
        8,
        {"verdict": "storage_error"},
    )
    with pytest.raises(UndefinedTableError):
        async with maker.begin() as db:
            conn = await db.connection()
            raw = await conn.get_raw_connection()
            assert raw.driver_connection is not None
            await raw.driver_connection.execute(
                (MIGRATIONS / "R__payout_delivery_claim_identity.sql").read_text()
            )


@pytest.mark.parametrize(
    "epoch,state", [(2, "claimed"), (9223372036854775807, "claimed"), (1, "ready")]
)
async def test_insert_generation_guards(claims_db, epoch, state):
    maker, _, _, key = claims_db
    with pytest.raises(DBAPIError):
        async with maker.begin() as db:
            await db.execute(
                text(
                    "INSERT INTO payout_delivery_claims VALUES "
                    "(:intent_id, :epoch, :state, :worker_id, clock_timestamp())"
                ),
                key | {"epoch": epoch, "state": state},
            )


@pytest.mark.parametrize(
    "sqlstate,expected",
    [("23514", (8, {"verdict": "storage_error"})), ("57014", (9, {"verdict": "busy"}))],
)
async def test_real_commit_failure_suppresses_fence(claims_db, sqlstate, expected, capsys):
    maker, _, _, key = claims_db
    async with maker.begin() as db:
        await db.execute(
            text(
                "CREATE FUNCTION fail_claim_commit() RETURNS trigger AS $$ "
                "BEGIN RAISE EXCEPTION 'private-marker' USING ERRCODE = '"
                + sqlstate
                + "'; END; $$ LANGUAGE plpgsql"
            )
        )
        await db.execute(
            text(
                "CREATE CONSTRAINT TRIGGER "
                "fail_claim_commit AFTER INSERT ON payout_delivery_claims "
                "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION fail_claim_commit()"
            )
        )
    assert await invoke_claim(maker, "claim", key | {"ttl_seconds": 1}) == expected
    assert await invoke_claim(maker, "lease-show", {"intent_id": key["intent_id"]}) == (
        6,
        {"verdict": "not_found"},
    )
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize(
    "command,change",
    [("claim", {"ttl_seconds": v}) for v in [True, 1.5, 0, 301]]
    + [("renew", {"epoch": v}) for v in [True, 1.5, 0, 9223372036854775808]]
    + [("claim", {"intent_id": "private-marker"}), ("claim", {"rpc_endpoint": "private-marker"})],
)
async def test_strict_private_input(command, change):
    key = {"intent_id": str(uuid4()), "worker_id": str(uuid4()), "ttl_seconds": 1}
    if command == "renew":
        key["epoch"] = 1
    assert await invoke_claim(None, command, key | change) == (5, {"verdict": "invalid_input"})


async def test_subprocess_all_commands(claims_db):
    maker, url, schema, key = claims_db
    await seed_claim(maker, key, True)
    scoped = make_url(url).update_query_dict({"dsn": "postgresql:///?search_path=" + schema})
    env = os.environ | {"DATABASE_URL": scoped.render_as_string(hide_password=False)}
    commands = [
        ("recover", key | {"epoch": 1}, "recovered"),
        ("claim", key | {"ttl_seconds": 300}, "claimed"),
        ("lease-show", {"intent_id": key["intent_id"]}, "found"),
        ("renew", key | {"epoch": 2, "ttl_seconds": 1}, "renewed"),
        ("release", key | {"epoch": 2}, "released"),
    ]
    for command, payload, verdict in commands:
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "app.cli.payout_delivery",
            command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        stdout, stderr = await asyncio.wait_for(
            process.communicate(json.dumps(payload).encode()), 20
        )
        assert process.returncode == 0 and stderr == b""
        result = json.loads(stdout)
        assert result["verdict"] == verdict and result["intent_id"] == key["intent_id"]
    assert (await invoke_claim(maker, "lease-show", {"intent_id": key["intent_id"]}))[1][
        "state"
    ] == "ready"


@pytest.mark.parametrize("command", ["claim", "renew", "release", "recover", "lease-show"])
async def test_missing_parent(claims_db, command):
    maker, _, _, key = claims_db
    payload = key | {"intent_id": str(uuid4())}
    if command != "claim":
        payload["epoch"] = 1
    if command in {"claim", "renew"}:
        payload["ttl_seconds"] = 1
    if command == "lease-show":
        payload = {"intent_id": payload["intent_id"]}
    assert await invoke_claim(maker, command, payload) == (6, {"verdict": "not_found"})


async def test_expired_claim_requires_explicit_recovery(claims_db):
    maker, _, _, key = claims_db
    await seed_claim(maker, key, True)
    assert await invoke_claim(maker, "claim", key | {"ttl_seconds": 1}) == (
        11,
        {"verdict": "unavailable"},
    )
    _, stored = await invoke_claim(maker, "lease-show", {"intent_id": key["intent_id"]})
    assert stored["state"] == "claimed" and stored["epoch"] == 1
    assert stored["worker_id"] == key["worker_id"]


@pytest.mark.parametrize("state", ["55P03", "57014"])
async def test_existing_admission_errors_keep_storage_verdict(delivery_db, allocation, state):
    """Preserve submit failure codes while claim contention gains its own busy result."""
    maker, _, _ = delivery_db
    async with maker.begin() as db:
        await db.execute(
            text(
                "CREATE FUNCTION fail_existing_intake_commit() RETURNS trigger AS $$ "
                "BEGIN RAISE EXCEPTION 'synthetic-private-marker' USING ERRCODE = '"
                + state
                + "'; END; $$ LANGUAGE plpgsql"
            )
        )
        await db.execute(
            text(
                "CREATE CONSTRAINT TRIGGER fail_existing_intake_commit "
                "AFTER INSERT ON payout_delivery_intents DEFERRABLE INITIALLY DEFERRED "
                "FOR EACH ROW EXECUTE FUNCTION fail_existing_intake_commit()"
            )
        )
    output = io.StringIO()
    assert (
        await run_admission("submit", io.StringIO(allocation.model_dump_json()), output, maker) == 8
    )
    assert json.loads(output.getvalue()) == {"verdict": "storage_error"}
    async with maker() as db:
        assert await db.scalar(text("SELECT count(*) FROM payout_delivery_intents")) == 0
