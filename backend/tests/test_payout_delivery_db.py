"""Prove immutable intake with synthetic data and actual PostgreSQL transactions."""

import asyncio
import io
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from asyncpg import exceptions as pg_errors
from solders.pubkey import Pubkey
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from test_marketplace_tasks import marketplace_pg as marketplace_pg

from app.cli.payout_delivery import run_admission
from app.repositories.payout_delivery_repo import PayoutDeliveryRepository
from app.schemas.payout_delivery import AllocationIntent, AllocationKey
from app.services.payout_delivery_service import admit_intent


@pytest.fixture
async def delivery_db(request):
    pg = request.getfixturevalue("marketplace_pg")
    url = pg.get_connection_url().replace("psycopg2", "asyncpg")
    schema = "delivery_" + uuid4().hex
    admin = create_async_engine(url)
    engine = create_async_engine(url, connect_args={"server_settings": {"search_path": schema}})
    try:
        async with admin.begin() as db:
            await db.execute(text(f'CREATE SCHEMA "{schema}"'))
        async with engine.begin() as db:
            raw = await db.get_raw_connection()
            assert raw.driver_connection is not None
            for name in ("V111__payout_delivery.sql", "R__payout_delivery_identity.sql"):
                migration = Path(__file__).resolve().parents[2] / "db/migrations" / name
                await raw.driver_connection.execute(migration.read_text())
        yield async_sessionmaker(engine, expire_on_commit=False), url, schema
    finally:
        await engine.dispose()
        async with admin.begin() as db:
            await db.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin.dispose()


@pytest.fixture
def allocation():
    fields = ("mint", "source", "destination", "authority", "fee_payer", "recipient")
    payload = {name: str(Pubkey.from_bytes(bytes([n]) * 32)) for n, name in enumerate(fields, 1)}
    return AllocationIntent(
        **payload,
        producer_namespace="synthetic",
        allocation_id="one",
        genesis_hash="1" * 32,
        decimals=6,
        raw_amount="9223372036854775808",
        program="TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb",
    )


@pytest.fixture
def key(allocation):
    return AllocationKey(
        producer_namespace=allocation.producer_namespace, allocation_id=allocation.allocation_id
    )


async def test_cli_commit_show_replay(delivery_db, allocation, key):
    maker, _, _ = delivery_db
    results = []
    for command, payload in (("submit", allocation), ("show", key), ("submit", allocation)):
        out = io.StringIO()
        assert await run_admission(command, io.StringIO(payload.model_dump_json()), out, maker) == 0
        results.append(json.loads(out.getvalue()))
    assert [r["verdict"] for r in results] == ["accepted", "found", "replayed"]
    assert len({r["intent_id"] for r in results}) == 1
    assert all(r["state"] == "admitted" for r in results)
    async with maker() as db:
        row = await PayoutDeliveryRepository(db).read(key)
        assert row is not None
        assert all(row[name] == value for name, value in allocation.model_dump().items())


@pytest.mark.parametrize(
    "field",
    [
        "genesis_hash",
        "mint",
        "source",
        "destination",
        "authority",
        "fee_payer",
        "recipient",
        "decimals",
        "raw_amount",
    ],
)
async def test_conflict_preserves_every_variable_payload_field(delivery_db, allocation, key, field):
    maker, _, _ = delivery_db
    value = {"decimals": 7, "raw_amount": "1"}.get(field, str(Pubkey.from_bytes(bytes([9]) * 32)))
    async with maker.begin() as db:
        original = await admit_intent(PayoutDeliveryRepository(db), allocation)
    out = io.StringIO()
    changed = allocation.model_copy(update={field: value})
    assert await run_admission("submit", io.StringIO(changed.model_dump_json()), out, maker) == 7
    assert json.loads(out.getvalue()) == {"verdict": "conflict"}
    async with maker() as db:
        row = await PayoutDeliveryRepository(db).read(key)
        assert row is not None and str(row["intent_id"]) == original["intent_id"]
        assert row[field] == getattr(allocation, field)


@pytest.mark.parametrize("field", ["producer_namespace", "allocation_id"])
async def test_independent_identities(delivery_db, allocation, field):
    maker, _, _ = delivery_db
    async with maker.begin() as db:
        repo = PayoutDeliveryRepository(db)
        first = await admit_intent(repo, allocation)
        second = await admit_intent(repo, allocation.model_copy(update={field: "other"}))
        assert second["verdict"] == "accepted" and first["intent_id"] != second["intent_id"]


@pytest.mark.parametrize(
    "rollback,changed,expected",
    [(False, False, "replayed"), (False, True, "conflict"), (True, False, "accepted")],
)
async def test_two_connections_wait_for_winner(
    delivery_db, allocation, rollback, changed, expected
):
    maker, _, _ = delivery_db
    async with maker() as first, maker() as second:
        await first.begin()
        winner = await admit_intent(PayoutDeliveryRepository(first), allocation)
        pid1 = await first.scalar(text("SELECT pg_backend_pid()"))
        pid2 = await second.scalar(text("SELECT pg_backend_pid()"))
        assert pid1 != pid2
        candidate = allocation.model_copy(update={"raw_amount": "1"}) if changed else allocation
        task = asyncio.create_task(admit_intent(PayoutDeliveryRepository(second), candidate))
        try:
            async with asyncio.timeout(10):
                while True:
                    locks = await first.scalar(
                        text("SELECT cardinality(pg_blocking_pids(:pid))"), {"pid": pid2}
                    )
                    if locks:
                        break
                    await asyncio.sleep(0)
            assert not task.done()
            await first.rollback() if rollback else await first.commit()
            result = await asyncio.wait_for(task, 10)
            assert result["verdict"] == expected
            if not rollback and not changed:
                assert result["intent_id"] == winner["intent_id"]
            await second.commit()
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    async with maker() as db:
        assert await db.scalar(text("SELECT count(*) FROM payout_delivery_intents")) == 1


@pytest.mark.parametrize("preexisting", [False, True])
async def test_caller_rollback(delivery_db, allocation, key, preexisting):
    maker, _, _ = delivery_db
    if preexisting:
        async with maker.begin() as db:
            await admit_intent(PayoutDeliveryRepository(db), allocation)
    with pytest.raises(RuntimeError, match="caller abort"):
        async with maker.begin() as db:
            await admit_intent(PayoutDeliveryRepository(db), allocation)
            raise RuntimeError("caller abort")
    async with maker() as db:
        assert (await PayoutDeliveryRepository(db).read(key) is not None) == preexisting


@pytest.mark.parametrize("amount", ["1", "9223372036854775808", "18446744073709551615"])
async def test_u64_preserved(delivery_db, allocation, key, amount):
    maker, _, _ = delivery_db
    async with maker.begin() as db:
        await admit_intent(
            PayoutDeliveryRepository(db), allocation.model_copy(update={"raw_amount": amount})
        )
    async with maker() as db:
        row = await PayoutDeliveryRepository(db).read(key)
        assert row is not None and row["raw_amount"] == amount


@pytest.mark.parametrize(
    "field,value",
    [
        ("raw_amount", v)
        for v in ["0", "-1", "+1", "01", " 1", "1e1", "1.2", "18446744073709551616", None]
    ]
    + [
        ("decimals", -1),
        ("decimals", 256),
        ("program", "1" * 32),
        ("source", "bad"),
        ("producer_namespace", " Bad"),
        ("allocation_id", ""),
        ("state", "paid"),
        ("genesis_hash", "bad"),
        ("recipient", None),
    ],
)
async def test_raw_sql_constraints(delivery_db, allocation, field, value):
    maker, _, _ = delivery_db
    payload = allocation.model_dump() | {field: value}
    columns = ", ".join(payload)
    params = ", ".join(":" + name for name in payload)
    with pytest.raises(DBAPIError):
        async with maker.begin() as db:
            await db.execute(
                text(f"INSERT INTO payout_delivery_intents ({columns}) VALUES ({params})"), payload
            )


@pytest.mark.parametrize("field", list(AllocationIntent.model_fields) + ["intent_id", "state"])
async def test_update_guard(delivery_db, allocation, field):
    maker, _, _ = delivery_db
    async with maker.begin() as db:
        await admit_intent(PayoutDeliveryRepository(db), allocation)
    with pytest.raises(DBAPIError, match="immutable"):
        async with maker.begin() as db:
            await db.execute(text(f"UPDATE payout_delivery_intents SET {field} = {field}"))
    async with maker() as db:
        assert await db.scalar(text("SELECT count(*) FROM payout_delivery_intents")) == 1


@pytest.mark.parametrize("operation", ["DELETE FROM", "TRUNCATE"])
async def test_removal_guard(delivery_db, allocation, operation):
    maker, _, _ = delivery_db
    async with maker.begin() as db:
        await admit_intent(PayoutDeliveryRepository(db), allocation)
    with pytest.raises(DBAPIError, match="immutable"):
        async with maker.begin() as db:
            await db.execute(text(operation + " payout_delivery_intents"))


@pytest.mark.parametrize(
    "change",
    [{"raw_amount": v} for v in [True, 1, 1.1, "01", "0"]]
    + [
        {"signature": "private-marker"},
        {"rpc_endpoint": "http://private-marker.invalid"},
        {"program": "1" * 32},
        {"producer_namespace": "Bad"},
        {"allocation_id": ""},
    ],
)
async def test_invalid_private_input(delivery_db, allocation, change, capsys):
    maker, _, _ = delivery_db
    out = io.StringIO()
    payload = allocation.model_dump() | change
    assert await run_admission("submit", io.StringIO(json.dumps(payload)), out, maker) == 5
    assert json.loads(out.getvalue()) == {"verdict": "invalid_input"}
    assert capsys.readouterr().err == ""


async def test_real_storage_and_commit_failure(delivery_db, allocation, capsys):
    maker, _, _ = delivery_db
    async with maker.begin() as db:
        await db.execute(
            text("""CREATE FUNCTION reject_delivery_commit() RETURNS TRIGGER AS $$
            BEGIN RAISE EXCEPTION 'private-marker'; END; $$ LANGUAGE plpgsql""")
        )
        await db.execute(
            text("""CREATE CONSTRAINT TRIGGER reject_delivery_commit
            AFTER INSERT ON payout_delivery_intents DEFERRABLE INITIALLY DEFERRED
            FOR EACH ROW EXECUTE FUNCTION reject_delivery_commit()""")
        )
    out = io.StringIO()
    assert await run_admission("submit", io.StringIO(allocation.model_dump_json()), out, maker) == 8
    assert json.loads(out.getvalue()) == {"verdict": "storage_error"}
    async with maker.begin() as db:
        assert await db.scalar(text("SELECT count(*) FROM payout_delivery_intents")) == 0
        await db.execute(text("DROP TABLE payout_delivery_intents"))
    out = io.StringIO()
    assert await run_admission("submit", io.StringIO(allocation.model_dump_json()), out, maker) == 8
    assert json.loads(out.getvalue()) == {"verdict": "storage_error"}
    assert capsys.readouterr().err == ""


async def test_actual_module_subprocess(delivery_db, allocation, key):
    _, url, schema = delivery_db
    scoped = make_url(url).update_query_dict({"dsn": "postgresql:///?search_path=" + schema})
    env = os.environ | {"DATABASE_URL": scoped.render_as_string(hide_password=False)}
    for command, payload, verdict in (("submit", allocation, "accepted"), ("show", key, "found")):
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
            process.communicate(payload.model_dump_json().encode()), 20
        )
        assert process.returncode == 0 and stderr == b""
        assert json.loads(stdout)["verdict"] == verdict


@pytest.mark.parametrize(
    "pair",
    [
        ("mint", "program"),
        ("mint", "source"),
        ("mint", "destination"),
        ("program", "source"),
        ("program", "destination"),
        ("source", "destination"),
    ],
)
async def test_raw_sql_distinct_roles(delivery_db, allocation, pair):
    maker, _, _ = delivery_db
    payload = allocation.model_dump()
    payload[pair[1]] = payload[pair[0]]
    columns = ", ".join(payload)
    params = ", ".join(":" + name for name in payload)
    with pytest.raises(DBAPIError):
        async with maker.begin() as db:
            await db.execute(
                text(f"INSERT INTO payout_delivery_intents ({columns}) VALUES ({params})"), payload
            )


@pytest.mark.parametrize(
    "command,payload",
    [
        ("bad", "{}"),
        ("submit", "{"),
        ("submit", "x" * 65537),
        ("submit", "{}"),
        ("show", '{"allocation_id":"one"}'),
    ],
)
async def test_bounded_input(command, payload):
    out = io.StringIO()
    assert await run_admission(command, io.StringIO(payload), out) == 5
    assert json.loads(out.getvalue()) == {"verdict": "invalid_input"}


async def test_missing_show(delivery_db, key):
    maker, _, _ = delivery_db
    out = io.StringIO()
    assert await run_admission("show", io.StringIO(key.model_dump_json()), out, maker) == 6
    assert json.loads(out.getvalue()) == {"verdict": "not_found"}


@pytest.mark.parametrize("field", list(AllocationIntent.model_fields))
async def test_missing_required_field(allocation, field):
    payload = allocation.model_dump()
    del payload[field]
    out = io.StringIO()
    assert await run_admission("submit", io.StringIO(json.dumps(payload)), out) == 5
    assert json.loads(out.getvalue()) == {"verdict": "invalid_input"}


async def test_handler_configuration_error(allocation, monkeypatch):
    monkeypatch.setattr(
        "app.cli.payout_delivery.get_settings",
        lambda: SimpleNamespace(database_url="private-marker"),
    )
    out = io.StringIO()
    assert await run_admission("submit", io.StringIO(allocation.model_dump_json()), out) == 8
    assert json.loads(out.getvalue()) == {"verdict": "storage_error"}


async def test_migration_replay_retains_identity_and_guards(delivery_db, allocation, key):
    maker, _, _ = delivery_db
    async with maker.begin() as db:
        original = await admit_intent(PayoutDeliveryRepository(db), allocation)
    async with maker.begin() as db:
        conn = await db.connection()
        raw = await conn.get_raw_connection()
        assert raw.driver_connection is not None
        for name in (
            "V111__payout_delivery.sql",
            "R__payout_delivery_identity.sql",
            "R__payout_delivery_identity.sql",
        ):
            migration = Path(__file__).resolve().parents[2] / "db/migrations" / name
            await raw.driver_connection.execute(migration.read_text())
    for statement in (
        "UPDATE payout_delivery_intents SET state = state",
        "DELETE FROM payout_delivery_intents",
        "TRUNCATE payout_delivery_intents",
    ):
        with pytest.raises(DBAPIError, match="immutable"):
            async with maker.begin() as db:
                await db.execute(text(statement))
    async with maker() as db:
        row = await PayoutDeliveryRepository(db).read(key)
        assert row is not None and str(row["intent_id"]) == original["intent_id"]
        assert all(row[name] == value for name, value in allocation.model_dump().items())
    with pytest.raises(DBAPIError):
        async with maker.begin() as db:
            invalid = allocation.model_copy(update={"raw_amount": "0", "allocation_id": "bad"})
            await PayoutDeliveryRepository(db).insert(invalid)


@pytest.mark.parametrize(
    "dispose_error",
    [None, RuntimeError("private-marker"), pg_errors.InternalClientError("private-marker")],
)
async def test_owned_engine_lifetime(delivery_db, allocation, monkeypatch, capsys, dispose_error):
    """Commit with echo disabled and dispose the command-owned engine before output."""
    maker, url, schema = delivery_db
    engine = create_async_engine(url, connect_args={"server_settings": {"search_path": schema}})
    calls = []
    original_dispose = type(engine).dispose

    async def dispose(owned, *args, **kwargs):
        await original_dispose(owned, *args, **kwargs)
        if owned is engine:
            calls.append("disposed")
            if dispose_error:
                raise dispose_error

    def create_owned(url, **kwargs):
        assert kwargs == {"echo": False}
        return engine

    monkeypatch.setattr(type(engine), "dispose", dispose)
    monkeypatch.setattr("app.cli.payout_delivery.create_async_engine", create_owned)
    monkeypatch.setattr(
        "app.cli.payout_delivery.get_settings", lambda: SimpleNamespace(database_url=url)
    )
    out = io.StringIO()
    code = await run_admission("submit", io.StringIO(allocation.model_dump_json()), out)
    assert calls == ["disposed"] and code == (8 if dispose_error else 0)
    result = json.loads(out.getvalue())
    assert (
        result == {"verdict": "storage_error"} if dispose_error else result["verdict"] == "accepted"
    )
    async with maker() as db:
        assert await db.scalar(text("SELECT count(*) FROM payout_delivery_intents")) == 1
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize(
    "failure",
    [
        OverflowError("private SQL"),
        pg_errors.InvalidPasswordError("private SQL"),
        pg_errors.InterfaceError("private SQL"),
        pg_errors.TargetServerAttributeNotMatched("private SQL"),
    ],
    ids=["overflow", "postgres", "interface", "internal-client"],
)
async def test_raw_connection_failure_is_private(allocation, monkeypatch, failure, capsys):
    """Keep raw connection failures private through the real SQLAlchemy pool path."""

    async def connect():
        raise failure

    engine = create_async_engine("postgresql+asyncpg://", async_creator=connect)
    monkeypatch.setattr("app.cli.payout_delivery.create_async_engine", lambda *a, **kw: engine)
    monkeypatch.setattr(
        "app.cli.payout_delivery.get_settings",
        lambda: SimpleNamespace(database_url="postgresql://example.invalid/private"),
    )
    output = io.StringIO()
    assert await run_admission("submit", io.StringIO(allocation.model_dump_json()), output) == 8
    assert json.loads(output.getvalue()) == {"verdict": "storage_error"}
    assert capsys.readouterr().err == ""
