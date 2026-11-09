"""Real PostgreSQL decision replay, reviewed snapshots and serialization."""

import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import text
from test_marketplace_tasks import marketplace_pg as marketplace_pg
from test_welcome_rewards import ledger as _ledger

from app.repositories.welcome_reward_repo import WelcomeRewardRepository
from app.schemas.welcome_reward_decisions import WelcomeRewardDecision
from app.services.welcome_reward_decision_service import WelcomeRewardDecisionService
from app.services.welcome_reward_service import WelcomeRewardService

ledger = _ledger

pytestmark = pytest.mark.integration


@pytest.fixture
async def review(ledger):
    async with ledger.maker.begin() as db:
        await db.execute(
            text("UPDATE reward_programs SET enabled=true,admission_start='2020-01-01'")
        )
        service = WelcomeRewardService(WelcomeRewardRepository(db))
        for owner in (ledger.owner, ledger.other):
            await service.apply(owner)
        grant_id = await db.scalar(
            text("SELECT id FROM welcome_reward_grants WHERE owner_user_id=:owner"),
            {"owner": ledger.owner},
        )
        await db.execute(text("UPDATE reward_programs SET enabled=false"))
        now = await db.scalar(text("SELECT clock_timestamp()"))
    intent = WelcomeRewardDecision(
        grant_id=grant_id,
        owner_user_id=ledger.owner,
        source_event=ledger.owner,
        policy_version="welcome-v1",
        outcome="allow",
        beneficiary_key="canonical-person",
        authority_ref="synthetic-operator",
        evidence_ref="synthetic-review",
        expected_revision=0,
        email_proof_at=None,
        issued_at=now,
        expires_at=now + timedelta(hours=1),
    )
    return ledger, intent


async def test_exact_replay_and_changed_payload(review):
    ledger, intent = review
    async with ledger.maker.begin() as db:
        service = WelcomeRewardDecisionService(WelcomeRewardRepository(db))
        first = await service.record(intent)
        assert first.verdict == "recorded" and first.revision == 1
        assert (await service.record(intent)).decision_id == first.decision_id
        assert (
            await service.record(intent.model_copy(update={"expected_revision": 1}))
        ).verdict == "conflict"
        newer = intent.model_copy(
            update={"expected_revision": 1, "evidence_ref": "new-review", "outcome": "hold"}
        )
        assert (await service.record(newer)).revision == 2
        assert (await service.record(intent)).verdict == "replayed"
        other = intent.model_copy(
            update={"owner_user_id": ledger.other, "source_event": ledger.other}
        )
        assert (await service.record(other)).verdict == "conflict"
        assert await db.scalar(text("SELECT count(*) FROM reward_eligibility_decisions")) == 2


async def test_expired_replay_precedes_fresh_checks(review):
    ledger, intent = review
    expired = intent.model_copy(
        update={
            "issued_at": intent.issued_at - timedelta(hours=2),
            "expires_at": intent.issued_at - timedelta(hours=1),
        }
    )
    async with ledger.maker.begin() as db:
        repo = WelcomeRewardRepository(db)
        row = await repo.insert_decision(expired.model_dump(exclude={"expected_revision"}))
        await db.execute(text("UPDATE reward_programs SET tariff_version='welcome-v2'"))
        result = await WelcomeRewardDecisionService(repo).record(expired)
        assert result.verdict == "replayed" and result.decision_id == row["id"]


@pytest.mark.parametrize(
    "change,verdict",
    [
        ({"source_event": uuid4()}, "conflict"),
        ({"grant_id": uuid4()}, "conflict"),
        ({"owner_user_id": uuid4()}, "not_found"),
        ({"policy_version": "wrong"}, "stale"),
        ({"expected_revision": 1}, "stale"),
        ({"issued_at": datetime.now(timezone.utc) + timedelta(hours=2)}, "invalid_input"),
        ({"expires_at": datetime.now(timezone.utc) - timedelta(hours=1)}, "invalid_input"),
        ({"email_proof_at": datetime.now(timezone.utc)}, "stale"),
    ],
)
async def test_rejects_unreviewed_snapshot(review, change, verdict):
    ledger, intent = review
    async with ledger.maker.begin() as db:
        result = await WelcomeRewardDecisionService(WelcomeRewardRepository(db)).record(
            intent.model_copy(update=change)
        )
        assert result.verdict == verdict
        assert await db.scalar(text("SELECT count(*) FROM reward_eligibility_decisions")) == 0


@pytest.mark.parametrize("same", [True, False])
async def test_concurrent_replays_and_compare_swap(review, same):
    ledger, intent = review

    async def write(candidate):
        async with ledger.maker.begin() as db:
            return await WelcomeRewardDecisionService(WelcomeRewardRepository(db)).record(candidate)

    other = (
        intent
        if same
        else intent.model_copy(update={"evidence_ref": "competing-review", "outcome": "deny"})
    )
    async with asyncio.timeout(20):
        results = await asyncio.gather(write(intent), write(other))
    assert sorted(r.verdict for r in results) == (
        ["recorded", "replayed"] if same else ["recorded", "stale"]
    )
    async with ledger.maker.begin() as db:
        assert await db.scalar(text("SELECT count(*) FROM reward_eligibility_decisions")) == 1


async def test_proof_changed_and_changed_back(review):
    ledger, intent = review
    async with ledger.maker.begin() as db:
        await db.execute(
            text("""UPDATE users SET email_proof_email=email,
            email_proof_at=clock_timestamp(),email_proof_source='email_code' WHERE id=:id"""),
            {"id": ledger.owner},
        )
        proof = await db.scalar(
            text("SELECT email_proof_at FROM users WHERE id=:id"), {"id": ledger.owner}
        )
        current = intent.model_copy(update={"email_proof_at": proof})
        service = WelcomeRewardDecisionService(WelcomeRewardRepository(db))
        assert (await service.record(intent)).verdict == "stale"
        assert (await service.record(current)).verdict == "recorded"
        await db.execute(
            text("UPDATE users SET email='changed@example.com' WHERE id=:id"), {"id": ledger.owner}
        )
        await db.execute(
            text("UPDATE users SET email='synthetic@example.com' WHERE id=:id"),
            {"id": ledger.owner},
        )
        assert (
            await service.record(
                current.model_copy(update={"expected_revision": 1, "evidence_ref": "changed-back"})
            )
        ).verdict == "stale"
        assert (await service.record(current)).verdict == "replayed"


@pytest.fixture
async def funded(review):
    ledger, intent = review
    async with ledger.maker.begin() as db:
        await db.execute(text("UPDATE reward_programs SET enabled=true"))
        await db.execute(
            text("""INSERT INTO reward_funding_entries
            (allocation_id,program_id,amount_raw,evidence_ref,authority_ref)
            VALUES (:id,'welcome',20000000000,'funding-test','operator-test')"""),
            {"id": uuid4()},
        )
        assert (
            await WelcomeRewardDecisionService(WelcomeRewardRepository(db)).record(intent)
        ).verdict == "recorded"
        assert (
            await WelcomeRewardService(WelcomeRewardRepository(db)).advance(ledger.owner)
        ).state == "frozen"
    return ledger, intent.model_copy(
        update={"expected_revision": 1, "evidence_ref": "second-review"}
    )


async def test_funded_beneficiary_is_bound(funded):
    ledger, intent = funded
    async with ledger.maker.begin() as db:
        service = WelcomeRewardDecisionService(WelcomeRewardRepository(db))
        assert (
            await service.record(intent.model_copy(update={"beneficiary_key": "different-person"}))
        ).verdict == "conflict"
        await db.execute(text("UPDATE reward_programs SET tariff_version='welcome-v2'"))
        assert (await service.record(intent)).verdict == "recorded"
        assert (
            await WelcomeRewardService(WelcomeRewardRepository(db)).advance(ledger.owner)
        ).reason == "email_required"


@pytest.mark.parametrize("outcome", ["hold", "deny"])
async def test_hold_serializes_with_advance_and_preserves_backing(funded, outcome):
    ledger, intent = funded
    async with ledger.maker.begin() as db:
        await db.execute(
            text("""UPDATE users SET email_proof_email=email,
            email_proof_at=clock_timestamp(),email_proof_source='email_code' WHERE id=:id"""),
            {"id": ledger.owner},
        )
        proof = await db.scalar(
            text("SELECT email_proof_at FROM users WHERE id=:id"), {"id": ledger.owner}
        )
    candidate = intent.model_copy(update={"email_proof_at": proof, "outcome": outcome})
    snapshot = text("""SELECT
        (SELECT jsonb_agg(to_jsonb(p)) FROM reward_programs p),
        (SELECT jsonb_agg(to_jsonb(g) ORDER BY id) FROM welcome_reward_grants g),
        (SELECT jsonb_agg(to_jsonb(f)) FROM reward_funding_entries f),
        (SELECT sum(aspore_balance+token_balance) FROM users)""")
    async with ledger.maker.begin() as writer:
        before = (await writer.execute(snapshot)).one()
        blocker = await writer.scalar(text("SELECT pg_backend_pid()"))
        assert (
            await WelcomeRewardDecisionService(WelcomeRewardRepository(writer)).record(candidate)
        ).verdict == "recorded"

        async def advance():
            async with ledger.maker.begin() as db:
                return await WelcomeRewardService(WelcomeRewardRepository(db)).advance(ledger.owner)

        task = asyncio.create_task(advance())
        async with asyncio.timeout(10):
            while not await writer.scalar(
                text("""SELECT EXISTS(
                SELECT 1 FROM pg_locks WHERE NOT granted AND :pid=ANY(pg_blocking_pids(pid)))"""),
                {"pid": blocker},
            ):
                assert not task.done()
    async with asyncio.timeout(10):
        view = await task
    assert (view.state, view.reason, view.amount_raw) == ("frozen", "risk_review", 10000000000)
    async with ledger.maker.begin() as db:
        assert (await db.execute(snapshot)).one() == before
        assert await WelcomeRewardRepository(db).free_funding() == 10000000000


async def test_conditional_beneficiary_does_not_reserve(review):
    ledger, intent = review
    async with ledger.maker.begin() as db:
        service = WelcomeRewardDecisionService(WelcomeRewardRepository(db))
        assert (await service.record(intent)).verdict == "recorded"
        other_id = await db.scalar(
            text("SELECT id FROM welcome_reward_grants WHERE owner_user_id=:id"),
            {"id": ledger.other},
        )
        other = intent.model_copy(
            update={
                "owner_user_id": ledger.other,
                "source_event": ledger.other,
                "grant_id": other_id,
                "evidence_ref": "other-review",
            }
        )
        assert (await service.record(other)).verdict == "recorded"
        assert (
            await db.scalar(
                text("SELECT count(*) FROM welcome_reward_grants WHERE amount_raw IS NOT NULL")
            )
            == 0
        )
        assert await WelcomeRewardRepository(db).free_funding() == 0


async def test_writer_rejects_repeatable_read(review):
    ledger, intent = review
    async with ledger.maker.begin() as db:
        await db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"))
        with pytest.raises(ValueError, match="Reward mutations require READ COMMITTED"):
            await WelcomeRewardDecisionService(WelcomeRewardRepository(db)).record(intent)


async def test_writer_locks_before_reading_evidence(review):
    """A concurrent identical write must wait on the shared locks and then replay."""
    ledger, intent = review
    async with ledger.maker.begin() as holder:
        repo = WelcomeRewardRepository(holder)
        await repo.context(intent.owner_user_id, lock=True)
        pid = await holder.scalar(text("SELECT pg_backend_pid()"))
        row = await repo.insert_decision(intent.model_dump(exclude={"expected_revision"}))

        async def write():
            async with ledger.maker.begin() as db:
                return await WelcomeRewardDecisionService(WelcomeRewardRepository(db)).record(
                    intent
                )

        task = asyncio.create_task(write())
        async with asyncio.timeout(10):
            while not await holder.scalar(
                text(
                    "SELECT EXISTS(SELECT 1 FROM pg_locks WHERE NOT granted "
                    "AND :pid=ANY(pg_blocking_pids(pid)))"
                ),
                {"pid": pid},
            ):
                assert not task.done()
    async with asyncio.timeout(10):
        result = await task
    assert (result.verdict, result.decision_id) == ("replayed", row["id"])
