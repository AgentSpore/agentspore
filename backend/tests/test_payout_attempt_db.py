"""Prove offline preparation, durable wire identity and deferred atomicity on PostgreSQL."""

import asyncio
import base64
import io
import json
import os
import signal
import stat
import sys
from contextlib import suppress
from uuid import UUID, uuid4

import pytest
import test_payout_claims_db as leases
import test_payout_preparation as native
from solders.hash import Hash
from solders.message import Message
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

from app.cli.payout_delivery import run_admission
from app.repositories.payout_delivery_repo import PayoutDeliveryRepository
from app.schemas.payout_delivery import AllocationIntent, ClaimFence, CompileRequest, SignedEnvelope
from app.services.payout_delivery_service import admit_intent, manage_claim, prepare_attempt
from app.services.payout_transaction import build_payout_message, parse_signed_candidate

native_case = native.native_case
claims_db = leases.claims_db
delivery_db = leases.delivery_db
marketplace_pg = leases.marketplace_pg
MIGRATIONS = leases.MIGRATIONS


@pytest.fixture
def allocation(native_case):
    intent, _ = native_case
    return AllocationIntent(
        **intent.model_dump(), producer_namespace="synthetic", allocation_id="attempt"
    )


@pytest.fixture
async def attempt_db(claims_db):
    maker, _, _, _ = claims_db
    maker.kw["bind"].sync_engine.hide_parameters = True
    async with maker.begin() as db:
        connection = await db.connection()
        raw = await connection.get_raw_connection()
        assert raw.driver_connection is not None
        for name in (
            "V113__payout_delivery_attempts.sql",
            "R__payout_delivery_attempt_identity.sql",
        ):
            await raw.driver_connection.execute((MIGRATIONS / name).read_text())
    return claims_db


@pytest.fixture
async def signed_envelope(attempt_db, native_case):
    maker, _, _, key = attempt_db
    assert (await leases.invoke_claim(maker, "claim", key | {"ttl_seconds": 300}))[0] == 0
    intent, signer = native_case
    blockhash = str(Hash.from_bytes(os.urandom(32)))
    return SignedEnvelope.model_validate_json(
        json.dumps(
            key
            | {
                "epoch": 1,
                "format_version": 1,
                "genesis_hash": intent.genesis_hash,
                "last_valid_block_height": "0",
                "signed_transaction_base64": signer.encode(build_payout_message(intent, blockhash)),
            }
        )
    )


async def invoke_attempt(maker, command, request, path=None):
    """Exercise the real command transaction and capture only its ordinary JSON channel."""
    out = io.StringIO()
    code = await run_admission(command, io.StringIO(request.model_dump_json()), out, maker, path)
    return code, json.loads(out.getvalue())


def attempt_values(envelope):
    """Derive raw SQL input from the actual native-verified synthetic wire."""
    candidate = parse_signed_candidate(envelope.signed_transaction_base64)
    return envelope.model_dump(
        include={"intent_id", "epoch", "worker_id", "last_valid_block_height"}
    ) | {
        "signed_bytes": candidate.signed_bytes,
        "first_signature": candidate.first_signature,
        "recent_blockhash": candidate.recent_blockhash,
    }


async def test_complete_private_compile_commit_replay_show(attempt_db, signed_envelope, tmp_path):
    maker, _, _, key = attempt_db
    candidate = parse_signed_candidate(signed_envelope.signed_transaction_base64)
    request = CompileRequest(
        **signed_envelope.model_dump(exclude={"format_version", "signed_transaction_base64"}),
        recent_blockhash=candidate.recent_blockhash,
    )
    path = tmp_path / "private-request.json"
    code, compiled = await invoke_attempt(maker, "prepare-message", request, str(path))
    assert code == 0 and set(compiled) == {
        "verdict",
        "intent_id",
        "worker_id",
        "epoch",
        "message_digest",
    }
    artifact = json.loads(path.read_text())
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert base64.b64decode(artifact["unsigned_message_base64"]) == candidate.message_bytes
    code, prepared = await invoke_attempt(maker, "prepare-commit", signed_envelope)
    assert code == 0 and prepared["verdict"] == "prepared" and prepared["state"] == "prepared"
    assert set(prepared) == {
        "verdict",
        "intent_id",
        "state",
        "worker_id",
        "epoch",
        "transaction_digest",
    }
    assert (await invoke_attempt(maker, "prepare-commit", signed_envelope))[1] == prepared | {
        "verdict": "replayed"
    }
    show = ClaimFence.model_validate_json(json.dumps(key | {"epoch": 1}))
    code, found = await leases.invoke_claim(maker, "attempt-show", {"intent_id": key["intent_id"]})
    assert code == 0 and found == prepared | {"verdict": "found"}
    async with maker() as db:
        row = await PayoutDeliveryRepository(db).read_attempt(show)
        assert row is not None and bytes(row["signed_bytes"]) == candidate.signed_bytes
        assert bytes(row["first_signature"]) == candidate.first_signature
        assert await db.scalar(text("SELECT state FROM payout_delivery_claims")) == "prepared"


@pytest.mark.parametrize("kind", ["existing", "symlink", "missing_parent"])
async def test_private_artifact_path_rejection(attempt_db, signed_envelope, tmp_path, kind):
    maker, _, _, _ = attempt_db
    candidate = parse_signed_candidate(signed_envelope.signed_transaction_base64)
    request = CompileRequest(
        **signed_envelope.model_dump(exclude={"format_version", "signed_transaction_base64"}),
        recent_blockhash=candidate.recent_blockhash,
    )
    target = tmp_path / "unchanged"
    target.write_text("private-marker")
    path = target
    if kind == "symlink":
        path = tmp_path / "link"
        path.symlink_to(target)
    elif kind == "missing_parent":
        path = tmp_path / "absent" / "request"
    assert await invoke_attempt(maker, "prepare-message", request, str(path)) == (
        5,
        {"verdict": "invalid_input"},
    )
    assert target.read_text() == "private-marker"


@pytest.mark.parametrize(
    "change", ["height", "worker", "epoch", "wire", "mismatched_amount", "genesis"]
)
async def test_existing_attempt_exact_binding_before_conflict(
    attempt_db, signed_envelope, native_case, change
):
    maker, _, _, _ = attempt_db
    await invoke_attempt(maker, "prepare-commit", signed_envelope)
    updates = {
        "height": {"last_valid_block_height": "1"},
        "worker": {"worker_id": uuid4()},
        "epoch": {"epoch": 2},
        "genesis": {"genesis_hash": str(Hash.from_bytes(os.urandom(32)))},
    }
    if change in {"wire", "mismatched_amount"}:
        intent, signer = native_case
        if change == "mismatched_amount":
            intent = intent.model_copy(update={"raw_amount": "11"})
        updates[change] = {
            "signed_transaction_base64": signer.encode(
                build_payout_message(intent, str(Hash.from_bytes(os.urandom(32))))
            )
        }
    altered = signed_envelope.model_copy(update=updates[change])
    expected = (
        (5, {"verdict": "invalid_input"})
        if change in {"mismatched_amount", "genesis"}
        else (7, {"verdict": "conflict"})
    )
    assert await invoke_attempt(maker, "prepare-commit", altered) == expected


async def test_missing_parent_stale_and_no_candidate_insert(attempt_db, signed_envelope):
    maker, _, _, key = attempt_db
    absent = signed_envelope.model_copy(update={"intent_id": uuid4()})
    assert await invoke_attempt(maker, "prepare-commit", absent) == (6, {"verdict": "not_found"})
    await leases.invoke_claim(maker, "release", key | {"epoch": 1})
    await leases.invoke_claim(maker, "claim", key | {"ttl_seconds": 300})
    assert await invoke_attempt(maker, "prepare-commit", signed_envelope) == (
        10,
        {"verdict": "stale"},
    )
    async with maker() as db:
        assert await db.scalar(text("SELECT count(*) FROM payout_delivery_attempts")) == 0


@pytest.mark.parametrize(
    "half", ["claim_only", "attempt_only", "wrong_fence", "rollback_cas", "rollback_insert"]
)
async def test_atomic_halves_and_caller_rollback(attempt_db, signed_envelope, half):
    maker, _, _, _ = attempt_db
    expected = RuntimeError if half.startswith("rollback") else DBAPIError
    with pytest.raises(expected):
        async with maker.begin() as db:
            repo = PayoutDeliveryRepository(db)
            if half != "attempt_only":
                assert await repo.prepare_fence(signed_envelope)
            if half == "rollback_cas":
                raise RuntimeError("caller abort")
            if half != "claim_only":
                values = attempt_values(signed_envelope)
                if half == "wrong_fence":
                    values["worker_id"] = uuid4()
                await repo.insert_attempt(values)
            if half == "rollback_insert":
                raise RuntimeError("caller abort")
    async with maker() as db:
        assert await db.scalar(text("SELECT state FROM payout_delivery_claims")) == "claimed"
        assert await db.scalar(text("SELECT count(*) FROM payout_delivery_attempts")) == 0


async def test_actual_commit_failure_private_and_atomic(attempt_db, signed_envelope, capsys):
    maker, _, _, _ = attempt_db
    async with maker.begin() as db:
        await db.execute(
            text(
                "CREATE FUNCTION fail_attempt_commit() RETURNS trigger AS $$ BEGIN "
                "RAISE EXCEPTION 'private-marker'; END; $$ LANGUAGE plpgsql"
            )
        )
        await db.execute(
            text(
                "CREATE CONSTRAINT TRIGGER "
                "fail_attempt_commit AFTER INSERT ON payout_delivery_attempts "
                "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION fail_attempt_commit()"
            )
        )
    assert await invoke_attempt(maker, "prepare-commit", signed_envelope) == (
        8,
        {"verdict": "storage_error"},
    )
    assert capsys.readouterr().err == ""
    async with maker() as db:
        assert await db.scalar(text("SELECT state FROM payout_delivery_claims")) == "claimed"
        assert await db.scalar(text("SELECT count(*) FROM payout_delivery_attempts")) == 0


async def test_duplicate_signature_rolls_back_other_claim(attempt_db, signed_envelope, allocation):
    maker, _, _, _ = attempt_db
    await invoke_attempt(maker, "prepare-commit", signed_envelope)
    async with maker.begin() as db:
        other = await admit_intent(
            PayoutDeliveryRepository(db), allocation.model_copy(update={"allocation_id": "other"})
        )
    worker = str(uuid4())
    await leases.invoke_claim(
        maker, "claim", {"intent_id": other["intent_id"], "worker_id": worker, "ttl_seconds": 300}
    )
    duplicate = signed_envelope.model_copy(
        update={"intent_id": UUID(other["intent_id"]), "worker_id": UUID(worker)}
    )
    assert await invoke_attempt(maker, "prepare-commit", duplicate) == (7, {"verdict": "conflict"})
    async with maker() as db:
        assert (
            await db.scalar(
                text("SELECT state FROM payout_delivery_claims WHERE intent_id = :id"),
                {"id": other["intent_id"]},
            )
            == "claimed"
        )
        assert await db.scalar(text("SELECT count(*) FROM payout_delivery_attempts")) == 1


@pytest.mark.parametrize("operation", ["same", "different", "release", "recover"])
async def test_prepare_races_parent_lock_and_actual_pids(
    attempt_db, signed_envelope, native_case, operation
):
    maker, _, _, _ = attempt_db
    candidate = signed_envelope
    if operation == "different":
        intent, signer = native_case
        candidate = signed_envelope.model_copy(
            update={
                "signed_transaction_base64": signer.encode(
                    build_payout_message(intent, str(Hash.from_bytes(os.urandom(32))))
                )
            }
        )
    async with maker() as first, maker() as second:
        assert (await prepare_attempt(PayoutDeliveryRepository(first), signed_envelope))[
            "verdict"
        ] == "prepared"
        pid1 = await first.scalar(text("SELECT pg_backend_pid()"))
        pid2 = await second.scalar(text("SELECT pg_backend_pid()"))
        assert pid1 != pid2
        with pytest.raises(DBAPIError) as error:
            if operation in {"release", "recover"}:
                await manage_claim(
                    PayoutDeliveryRepository(second),
                    operation,
                    ClaimFence(
                        **signed_envelope.model_dump(include={"intent_id", "worker_id", "epoch"})
                    ),
                )
            else:
                await prepare_attempt(PayoutDeliveryRepository(second), candidate)
        assert getattr(error.value.orig, "sqlstate") == "55P03"
        await second.rollback()
        await first.commit()
    if operation in {"release", "recover"}:
        assert (
            await leases.invoke_claim(
                maker,
                operation,
                signed_envelope.model_dump(
                    mode="json", include={"intent_id", "worker_id", "epoch"}
                ),
            )
        )[0] == 10
    else:
        assert (await invoke_attempt(maker, "prepare-commit", candidate))[0] == (
            7 if operation == "different" else 0
        )


@pytest.mark.parametrize("operation", ["release", "recover"])
async def test_relinquishment_winner_blocks_preparation(attempt_db, native_case, operation):
    maker, _, _, key = attempt_db
    intent, signer = native_case
    async with maker.begin() as db:
        await db.execute(
            text(
                "INSERT INTO payout_delivery_claims VALUES (:intent_id, 1, 'claimed', :worker_id, "
                "clock_timestamp() + :offset * interval '1 second')"
            ),
            key | {"offset": -1 if operation == "recover" else 300},
        )
    envelope = SignedEnvelope.model_validate_json(
        json.dumps(
            key
            | {
                "epoch": 1,
                "format_version": 1,
                "genesis_hash": intent.genesis_hash,
                "last_valid_block_height": "0",
                "signed_transaction_base64": signer.encode(
                    build_payout_message(intent, str(Hash.from_bytes(os.urandom(32))))
                ),
            }
        )
    )
    async with maker() as holder, maker() as contender:
        fence = ClaimFence.model_validate_json(json.dumps(key | {"epoch": 1}))
        assert (await manage_claim(PayoutDeliveryRepository(holder), operation, fence))[
            "state"
        ] == "ready"
        assert await holder.scalar(text("SELECT pg_backend_pid()")) != await contender.scalar(
            text("SELECT pg_backend_pid()")
        )
        with pytest.raises(DBAPIError):
            await prepare_attempt(PayoutDeliveryRepository(contender), envelope)
        await contender.rollback()
        await holder.commit()
    assert await invoke_attempt(maker, "prepare-commit", envelope) == (10, {"verdict": "stale"})


async def test_prepared_survives_expiry_and_old_operations(attempt_db, signed_envelope):
    maker, _, _, key = attempt_db
    await leases.invoke_claim(maker, "release", key | {"epoch": 1})
    await leases.invoke_claim(maker, "claim", key | {"ttl_seconds": 1})
    envelope = signed_envelope.model_copy(update={"epoch": 2})
    assert (await invoke_attempt(maker, "prepare-commit", envelope))[0] == 0
    async with maker() as db:
        async with asyncio.timeout(3):
            while not await db.scalar(
                text("SELECT expires_at <= clock_timestamp() FROM payout_delivery_claims")
            ):
                continue
    assert (await invoke_attempt(maker, "prepare-commit", envelope))[1]["verdict"] == "replayed"
    for command in ("release", "recover", "renew"):
        payload = key | {"epoch": 2} | ({"ttl_seconds": 1} if command == "renew" else {})
        assert (await leases.invoke_claim(maker, command, payload))[0] == 10
    assert (await leases.invoke_claim(maker, "claim", key | {"ttl_seconds": 1}))[0] == 11


@pytest.mark.parametrize(
    "sql",
    ["DELETE FROM payout_delivery_attempts", "TRUNCATE payout_delivery_attempts"]
    + [
        "UPDATE payout_delivery_attempts SET " + name + " = " + name
        for name in [
            "intent_id",
            "epoch",
            "worker_id",
            "signed_bytes",
            "first_signature",
            "recent_blockhash",
            "last_valid_block_height",
        ]
    ]
    + [
        "UPDATE payout_delivery_claims SET state = state",
        "UPDATE payout_delivery_claims SET state = 'ready', worker_id = NULL, expires_at = NULL",
        "UPDATE payout_delivery_intents SET state = state",
    ],
)
async def test_immutable_attempt_prepared_and_parent(attempt_db, signed_envelope, sql):
    maker, _, _, _ = attempt_db
    await invoke_attempt(maker, "prepare-commit", signed_envelope)
    with pytest.raises(DBAPIError):
        async with maker.begin() as db:
            await db.execute(text(sql))


@pytest.mark.parametrize(
    "field,value",
    [
        ("signed_bytes", b""),
        ("signed_bytes", b"x" * 1233),
        ("first_signature", b"x" * 63),
        ("first_signature", b"x" * 65),
        ("recent_blockhash", "bad"),
        ("last_valid_block_height", "01"),
        ("last_valid_block_height", "18446744073709551616"),
    ]
    + [
        (name, None)
        for name in [
            "intent_id",
            "epoch",
            "worker_id",
            "signed_bytes",
            "first_signature",
            "recent_blockhash",
            "last_valid_block_height",
        ]
    ],
)
async def test_attempt_raw_constraints(attempt_db, signed_envelope, field, value):
    maker, _, _, _ = attempt_db
    with pytest.raises(DBAPIError):
        async with maker.begin() as db:
            repo = PayoutDeliveryRepository(db)
            assert await repo.prepare_fence(signed_envelope)
            await repo.insert_attempt(attempt_values(signed_envelope) | {field: value})


async def test_repeatable_retains_material_and_pair(attempt_db, signed_envelope):
    maker, _, _, _ = attempt_db
    _, original = await invoke_attempt(maker, "prepare-commit", signed_envelope)
    async with maker.begin() as db:
        conn = await db.connection()
        raw = await conn.get_raw_connection()
        assert raw.driver_connection is not None
        for name in (
            "V113__payout_delivery_attempts.sql",
            "R__payout_delivery_attempt_identity.sql",
            "R__payout_delivery_claim_identity.sql",
            "R__payout_delivery_attempt_identity.sql",
        ):
            await raw.driver_connection.execute((MIGRATIONS / name).read_text())
    assert (await invoke_attempt(maker, "prepare-commit", signed_envelope))[1] == original | {
        "verdict": "replayed"
    }


async def test_attempt_table_lock_is_bounded_and_observed(attempt_db, signed_envelope):
    maker, _, _, _ = attempt_db
    async with maker() as holder, maker() as waiter:
        pid2 = await waiter.scalar(text("SELECT pg_backend_pid()"))
        await holder.execute(text("LOCK TABLE payout_delivery_attempts IN ACCESS EXCLUSIVE MODE"))
        pid1 = await holder.scalar(text("SELECT pg_backend_pid()"))
        assert pid1 != pid2
        task = asyncio.create_task(
            prepare_attempt(PayoutDeliveryRepository(waiter), signed_envelope)
        )
        try:
            async with asyncio.timeout(3):
                while pid1 not in await holder.scalar(
                    text("SELECT pg_blocking_pids(:pid)"), {"pid": pid2}
                ):
                    await asyncio.sleep(0)
            assert (
                await holder.scalar(
                    text("SELECT count(*) FROM pg_locks WHERE pid = :pid AND NOT granted"),
                    {"pid": pid2},
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


async def stop_owned_process(process):
    """Terminate only this child, escalate within a bound, and collect its exit."""
    if process.returncode is not None:
        return
    with suppress(ProcessLookupError):
        process.terminate()
    try:
        await asyncio.wait_for(process.wait(), 1)
    except TimeoutError:
        with suppress(ProcessLookupError):
            process.kill()
        await asyncio.wait_for(process.wait(), 2)


async def communicate_owned(process, payload, timeout=20):
    """Collect the owned child even when communication times out or is cancelled."""
    try:
        return await asyncio.wait_for(process.communicate(json.dumps(payload).encode()), timeout)
    finally:
        await stop_owned_process(process)


async def invoke_module(env, command, payload, options):
    """Own actual subprocess I/O while keeping every privileged material out of normal output."""
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "app.cli.payout_delivery",
        command,
        *options,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    stdout, stderr = await communicate_owned(process, payload)
    assert process.returncode == 0 and stderr == b""
    result = json.loads(stdout)
    fields = {"verdict", "intent_id", "worker_id", "epoch"}
    fields |= (
        {"message_digest"} if result["verdict"] == "compiled" else {"state", "transaction_digest"}
    )
    assert set(result) == fields
    return result


def sign_request_artifact(request, path, signer):
    """Perform the explicitly synthetic external-signature handoff on exact artifact bytes."""
    artifact = json.loads(path.read_text())
    message = Message.from_bytes(base64.b64decode(artifact["unsigned_message_base64"]))
    return request.model_dump(mode="json", exclude={"recent_blockhash"}) | {
        "format_version": 1,
        "signed_transaction_base64": signer.encode(message),
    }


async def test_actual_module_compile_sign_commit_fresh_show(attempt_db, native_case, tmp_path):
    maker, url, schema, key = attempt_db
    intent, signer = native_case
    await leases.invoke_claim(maker, "claim", key | {"ttl_seconds": 300})
    request = CompileRequest.model_validate_json(
        json.dumps(
            key
            | {
                "epoch": 1,
                "genesis_hash": intent.genesis_hash,
                "recent_blockhash": str(Hash.from_bytes(os.urandom(32))),
                "last_valid_block_height": "0",
            }
        )
    )
    scoped = make_url(url).update_query_dict({"dsn": "postgresql:///?search_path=" + schema})
    env = os.environ | {"DATABASE_URL": scoped.render_as_string(hide_password=False)}
    path = tmp_path / "private.json"
    result = await invoke_module(
        env, "prepare-message", request.model_dump(mode="json"), ["--output", str(path)]
    )
    assert result["verdict"] == "compiled" and result["intent_id"] == key["intent_id"]
    envelope = sign_request_artifact(request, path, signer)
    for command, payload, verdict in [
        ("prepare-commit", envelope, "prepared"),
        ("attempt-show", {"intent_id": key["intent_id"]}, "found"),
        ("prepare-commit", envelope, "replayed"),
    ]:
        result = await invoke_module(env, command, payload, [])
        assert result["verdict"] == verdict and result["intent_id"] == key["intent_id"]
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


@pytest.mark.parametrize("change", ["absent", "worker", "epoch", "genesis", "prepared"])
async def test_compile_requires_actual_live_context(attempt_db, signed_envelope, tmp_path, change):
    maker, _, _, _ = attempt_db
    candidate = parse_signed_candidate(signed_envelope.signed_transaction_base64)
    request = CompileRequest(
        **signed_envelope.model_dump(exclude={"format_version", "signed_transaction_base64"}),
        recent_blockhash=candidate.recent_blockhash,
    )
    if change == "prepared":
        await invoke_attempt(maker, "prepare-commit", signed_envelope)
    else:
        changes = {
            "absent": {"intent_id": uuid4()},
            "worker": {"worker_id": uuid4()},
            "epoch": {"epoch": 2},
            "genesis": {"genesis_hash": str(Hash.from_bytes(os.urandom(32)))},
        }
        request = request.model_copy(update=changes[change])
    expected = 6 if change == "absent" else (5 if change == "genesis" else 10)
    path = tmp_path / "must-not-exist"
    assert (await invoke_attempt(maker, "prepare-message", request, str(path)))[0] == expected
    assert not path.exists()


async def test_expired_unprepared_candidate_and_compile_are_stale(
    attempt_db, native_case, tmp_path
):
    maker, _, _, key = attempt_db
    intent, signer = native_case
    async with maker.begin() as db:
        await db.execute(
            text(
                "INSERT INTO payout_delivery_claims VALUES "
                "(:intent_id, 1, 'claimed', :worker_id, clock_timestamp() - interval '1 second')"
            ),
            key,
        )
    request = CompileRequest.model_validate_json(
        json.dumps(
            key
            | {
                "epoch": 1,
                "genesis_hash": intent.genesis_hash,
                "last_valid_block_height": "0",
                "recent_blockhash": str(Hash.from_bytes(os.urandom(32))),
            }
        )
    )
    envelope = SignedEnvelope(
        **request.model_dump(exclude={"recent_blockhash"}),
        format_version=1,
        signed_transaction_base64=signer.encode(
            build_payout_message(intent, request.recent_blockhash)
        ),
    )
    assert await invoke_attempt(maker, "prepare-commit", envelope) == (10, {"verdict": "stale"})
    path = tmp_path / "absent"
    assert await invoke_attempt(maker, "prepare-message", request, str(path)) == (
        10,
        {"verdict": "stale"},
    )
    assert not path.exists()
    async with maker() as db:
        assert await db.scalar(text("SELECT count(*) FROM payout_delivery_attempts")) == 0


async def test_prepared_transition_cannot_change_expiry(attempt_db, signed_envelope):
    maker, _, _, _ = attempt_db
    with pytest.raises(DBAPIError):
        async with maker.begin() as db:
            await db.execute(
                text(
                    "UPDATE payout_delivery_claims SET state = 'prepared', "
                    "expires_at = expires_at + interval '1 second' WHERE intent_id = :intent_id"
                ),
                {"intent_id": signed_envelope.intent_id},
            )
            await PayoutDeliveryRepository(db).insert_attempt(attempt_values(signed_envelope))


@pytest.fixture
async def ordinary_writer(attempt_db):
    maker, _, schema, _ = attempt_db
    role = "writer_" + uuid4().hex
    async with maker.begin() as db:
        await db.execute(text(f'CREATE ROLE "{role}" NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE'))
        await db.execute(text(f'GRANT USAGE ON SCHEMA "{schema}" TO "{role}"'))
        await db.execute(
            text(
                f'GRANT SELECT, INSERT, UPDATE ON "{schema}".payout_delivery_claims, '
                f'"{schema}".payout_delivery_attempts TO "{role}"'
            )
        )
    try:
        yield role
    finally:
        async with maker.begin() as db:
            await db.execute(text(f'REVOKE ALL ON ALL TABLES IN SCHEMA "{schema}" FROM "{role}"'))
            await db.execute(text(f'REVOKE USAGE ON SCHEMA "{schema}" FROM "{role}"'))
            await db.execute(text(f'DROP ROLE "{role}"'))


@pytest.mark.parametrize("half", ["claim", "attempt"])
async def test_persistent_pair_rejects_temp_names(
    attempt_db, signed_envelope, ordinary_writer, half
):
    maker, _, schema, _ = attempt_db
    async with maker() as holder:
        pid = await holder.scalar(text("SELECT pg_backend_pid()"))
        with pytest.raises(DBAPIError) as error:
            async with maker.begin() as db:
                assert await db.scalar(text("SELECT pg_backend_pid()")) != pid
                await db.execute(text(f'SET LOCAL ROLE "{ordinary_writer}"'))
                assert (
                    await db.scalar(
                        text("SELECT rolsuper FROM pg_roles WHERE rolname = current_user")
                    )
                    is False
                )
                for table in ("payout_delivery_claims", "payout_delivery_attempts"):
                    await db.execute(
                        text(f'CREATE TEMP TABLE {table} (LIKE "{schema}".{table}) ON COMMIT DROP')
                    )
                if half == "claim":
                    await db.execute(
                        text(
                            f'UPDATE "{schema}".payout_delivery_claims SET state = :prepared '
                            "WHERE intent_id = :intent_id"
                        ),
                        {"intent_id": signed_envelope.intent_id, "prepared": "prepared"},
                    )
                else:
                    await db.execute(
                        text(
                            f'INSERT INTO "{schema}".payout_delivery_attempts '
                            "(intent_id, epoch, worker_id, "
                            "signed_bytes, first_signature, recent_blockhash, "
                            "last_valid_block_height) VALUES "
                            "(:intent_id, :epoch, :worker_id, :signed_bytes, "
                            ":first_signature, :recent_blockhash, :last_valid_block_height)"
                        ),
                        attempt_values(signed_envelope),
                    )
        assert getattr(error.value.orig, "sqlstate") == "23514"


@pytest.mark.parametrize("mode", ["timeout", "cancellation"])
async def test_owned_child_timeout_and_cancellation(mode, request):
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        "import os, signal; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        "os.write(1, b'R'); signal.pause()",
        stdout=asyncio.subprocess.PIPE,
        stdin=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    request.node.user_properties.append(("owned_child_pid", str(process.pid)))
    try:
        assert process.stdout is not None
        assert await asyncio.wait_for(process.stdout.readexactly(1), 2) == b"R"
        if mode == "timeout":
            with pytest.raises(TimeoutError):
                await communicate_owned(process, {}, 0.01)
        else:
            task = asyncio.create_task(communicate_owned(process, {}))
            await asyncio.sleep(0)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert process.returncode == -signal.SIGKILL
        with pytest.raises(ProcessLookupError):
            os.kill(process.pid, 0)
    finally:
        await stop_owned_process(process)
