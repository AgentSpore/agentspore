"""Welcome ledger invariants through real PostgreSQL and authenticated HTTP."""

import asyncio
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from test_marketplace_tasks import marketplace_pg as marketplace_pg

from app.api.deps import get_current_user
from app.core.database import get_db
from app.main import app
from app.repositories.welcome_reward_repo import WelcomeRewardRepository
from app.services.welcome_reward_service import WelcomeRewardService

pytestmark = pytest.mark.integration


@pytest.fixture
async def ledger(marketplace_pg):
    url = marketplace_pg.get_connection_url().replace("psycopg2", "asyncpg")
    schema = "welcome_" + uuid4().hex
    admin = create_async_engine(url)
    engine = create_async_engine(url, connect_args={"server_settings": {"search_path": schema}})
    maker = async_sessionmaker(engine, expire_on_commit=False)
    owner, other = uuid4(), uuid4()
    async with admin.begin() as conn:
        await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    async with engine.begin() as conn:
        raw = await conn.get_raw_connection()
        driver = raw.driver_connection
        assert driver is not None
        await driver.execute("""
            CREATE TABLE users (id uuid PRIMARY KEY, email text NOT NULL,
            name text DEFAULT 'Owner', is_verified boolean DEFAULT true,
            hashed_password text, avatar_url text, is_admin boolean DEFAULT false,
            verification_token text, verification_expires_at timestamptz,
            verification_email text, verification_purpose text, oauth_provider text, oauth_id text,
            solana_wallet text, updated_at timestamptz DEFAULT now(),
            created_at timestamptz DEFAULT clock_timestamp(),
            email_proof_email text, email_proof_at timestamptz, email_proof_source text,
            aspore_balance bigint DEFAULT 0, token_balance integer DEFAULT 0);
        """)
        for uid in (owner, other):
            await conn.execute(
                text("INSERT INTO users(id,email) VALUES(:id,'synthetic@example.com')"), {"id": uid}
            )
        for name in (
            "V115__welcome_reward_ledger.sql",
            "R__welcome_reward_integrity.sql",
            "R__email_ownership_proof.sql",
        ):
            await driver.execute(
                (Path(__file__).resolve().parents[2] / "db/migrations" / name).read_text()
            )
    try:
        yield SimpleNamespace(maker=maker, owner=owner, other=other)
    finally:
        await engine.dispose()
        async with admin.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin.dispose()


async def test_disabled_reads_do_not_create_applications(ledger):
    async with ledger.maker.begin() as db:
        svc = WelcomeRewardService(WelcomeRewardRepository(db))
        view = await svc.read(ledger.owner)
        assert view.state == "disabled" and view.amount_raw is None
        assert (await svc.apply(ledger.owner)).state == "disabled"
        assert await db.scalar(text("SELECT count(*) FROM welcome_reward_grants")) == 0
        assert await db.scalar(text("SELECT count(*) FROM reward_funding_entries")) == 0


@pytest.fixture
async def admitted(ledger):
    async with ledger.maker.begin() as db:
        await db.execute(
            text("""
            UPDATE reward_programs SET enabled=true, admission_start='2020-01-01';
        """)
        )
        for uid in (ledger.owner, ledger.other):
            await WelcomeRewardService(WelcomeRewardRepository(db)).apply(uid)
            await db.execute(
                text("""
                INSERT INTO reward_eligibility_decisions(grant_id,owner_user_id,source_event,
                  policy_version,outcome,beneficiary_key,authority_ref,evidence_ref,expires_at)
                SELECT id,owner_user_id,source_event,'welcome-v1','allow',:key,
                       'synthetic-review',:evidence,clock_timestamp()+interval '1 hour'
                FROM welcome_reward_grants WHERE owner_user_id=:owner
            """),
                {"owner": uid, "key": str(uid), "evidence": str(uuid4())},
            )
        await db.execute(
            text("""
            INSERT INTO reward_funding_entries(allocation_id,program_id,amount_raw,
                evidence_ref,authority_ref)
            VALUES(:id,'welcome',10000000000,'synthetic-funding','synthetic-authority')
        """),
            {"id": uuid4()},
        )
    return ledger


async def test_competing_reservations_and_replay(admitted):
    async def advance(uid):
        async with admitted.maker.begin() as db:
            return await WelcomeRewardService(WelcomeRewardRepository(db)).advance(uid)

    async with asyncio.timeout(20):
        replies = await asyncio.gather(advance(admitted.owner), advance(admitted.other))
    assert sorted(r.state for r in replies) == ["application", "frozen"]
    assert sorted(r.reason for r in replies) == ["email_required", "funding_unavailable"]
    winner = admitted.owner if replies[0].state == "frozen" else admitted.other
    async with admitted.maker.begin() as db:
        svc = WelcomeRewardService(WelcomeRewardRepository(db))
        await db.execute(
            text("UPDATE reward_programs SET tariff_version='welcome-v2',amount_raw=20000000000")
        )
        assert (await svc.apply(winner)).amount_raw == 10000000000
        assert await WelcomeRewardRepository(db).free_funding() == 0
        assert await db.scalar(text("SELECT count(*) FROM welcome_reward_grants")) == 2
        assert await db.scalar(text("SELECT sum(aspore_balance+token_balance) FROM users")) == 0


async def test_unknown_risk_and_historical_registration(ledger):
    async with ledger.maker.begin() as db:
        await db.execute(
            text("UPDATE reward_programs SET enabled=true,admission_start='2020-01-01'")
        )
        svc = WelcomeRewardService(WelcomeRewardRepository(db))
        assert (await svc.apply(ledger.owner)).state == "application"
        assert (await svc.advance(ledger.owner)).reason == "risk_review"
        await db.execute(
            text("UPDATE users SET created_at='2019-01-01' WHERE id=:id"), {"id": ledger.other}
        )
        assert (await svc.apply(ledger.other)).reason == "not_eligible"
        assert await WelcomeRewardRepository(db).free_funding() == 0


async def test_rollback_does_not_consume_funding(admitted):
    async with admitted.maker() as db:
        assert (
            await WelcomeRewardService(WelcomeRewardRepository(db)).advance(admitted.owner)
        ).state == "frozen"
        await db.rollback()
    async with admitted.maker.begin() as db:
        assert (
            await WelcomeRewardService(WelcomeRewardRepository(db)).advance(admitted.other)
        ).state == "frozen"


async def test_new_decision_holds_funded_grant_without_releasing_reserve(admitted):
    async with admitted.maker.begin() as db:
        svc = WelcomeRewardService(WelcomeRewardRepository(db))
        await svc.advance(admitted.owner)
        await db.execute(
            text("""
            INSERT INTO reward_eligibility_decisions(grant_id,owner_user_id,source_event,
                policy_version,outcome,authority_ref,evidence_ref,expires_at)
            SELECT id,owner_user_id,source_event,'welcome-v1','deny',
                'synthetic-review','deny-evidence',clock_timestamp()+interval '1 hour'
            FROM welcome_reward_grants WHERE owner_user_id=:owner
        """),
            {"owner": admitted.owner},
        )
        view = await svc.advance(admitted.owner)
        assert (view.state, view.reason, view.amount_raw) == ("frozen", "risk_review", 10000000000)
        assert await WelcomeRewardRepository(db).free_funding() == 0
        await db.execute(text("UPDATE reward_programs SET enabled=false"))
        assert (await svc.read(admitted.owner)).reason == "program_disabled"
        assert (await svc.read(admitted.owner)).amount_raw == 10000000000


async def test_email_snapshot_unlock_and_change_back_hold(admitted):
    async with admitted.maker.begin() as db:
        svc = WelcomeRewardService(WelcomeRewardRepository(db))
        await svc.advance(admitted.owner)
        await db.execute(
            text("""
            UPDATE users SET email_proof_email=email,email_proof_at=clock_timestamp(),
              email_proof_source='email_code' WHERE id=:owner
        """),
            {"owner": admitted.owner},
        )
        assert (await svc.advance(admitted.owner)).reason == "email_required"
        await db.execute(
            text("""
            INSERT INTO reward_eligibility_decisions(grant_id,owner_user_id,source_event,
                policy_version,outcome,beneficiary_key,authority_ref,evidence_ref,
                email_proof_at,expires_at)
            SELECT g.id,g.owner_user_id,g.source_event,'welcome-v1','allow',g.beneficiary_key,
                'synthetic-review','proof-evidence',u.email_proof_at,
                clock_timestamp()+interval '1 hour'
            FROM welcome_reward_grants g JOIN users u ON u.id=g.owner_user_id WHERE u.id=:owner
        """),
            {"owner": admitted.owner},
        )
        assert (await svc.advance(admitted.owner)).state == "available"
        await db.execute(
            text("UPDATE users SET email='changed@example.com' WHERE id=:owner"),
            {"owner": admitted.owner},
        )
        await db.execute(
            text("UPDATE users SET email='synthetic@example.com' WHERE id=:owner"),
            {"owner": admitted.owner},
        )
        await db.execute(
            text("""UPDATE users SET email_proof_at=clock_timestamp(),
                         email_proof_email=email,email_proof_source='email_code'
                         WHERE id=:owner"""),
            {"owner": admitted.owner},
        )
        view = await svc.read(admitted.owner)
        assert (view.state, view.reason) == ("frozen", "email_required")
        assert await WelcomeRewardRepository(db).free_funding() == 0


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE reward_funding_entries SET amount_raw=amount_raw+1",
        "DELETE FROM reward_funding_entries",
        "UPDATE reward_eligibility_decisions SET outcome='allow'",
        "DELETE FROM reward_eligibility_decisions",
        "DELETE FROM users WHERE id=:owner",
        "DELETE FROM welcome_reward_grants WHERE owner_user_id=:owner",
        "UPDATE welcome_reward_grants SET source_event=gen_random_uuid() "
        "WHERE owner_user_id=:owner",
    ],
)
async def test_immutable_financial_history(admitted, statement):
    with pytest.raises(IntegrityError):
        async with admitted.maker.begin() as db:
            await db.execute(text(statement), {"owner": admitted.owner})


async def test_funded_grants_cannot_be_cancelled_or_repriced(admitted):
    async with admitted.maker.begin() as db:
        await WelcomeRewardService(WelcomeRewardRepository(db)).advance(admitted.owner)
    for assignment in (
        "state='denied'",
        "amount_raw=1",
        "beneficiary_key='different'",
        "tariff_version='new'",
    ):
        with pytest.raises(IntegrityError):
            async with admitted.maker.begin() as db:
                await db.execute(
                    text(
                        "UPDATE welcome_reward_grants SET "
                        + assignment
                        + " WHERE owner_user_id=:owner"
                    ),
                    {"owner": admitted.owner},
                )


async def test_canonical_beneficiary_and_funding_identity_dedup(admitted):
    async with admitted.maker.begin() as db:
        svc = WelcomeRewardService(WelcomeRewardRepository(db))
        await svc.advance(admitted.owner)
        await db.execute(
            text("""
            INSERT INTO reward_funding_entries(allocation_id,program_id,amount_raw,
                evidence_ref,authority_ref)
            VALUES(:id,'welcome',10000000000,'second-independent-allocation','synthetic-authority')
        """),
            {"id": uuid4()},
        )
        await db.execute(
            text("""
            INSERT INTO reward_eligibility_decisions(grant_id,owner_user_id,source_event,
                policy_version,outcome,beneficiary_key,authority_ref,evidence_ref,expires_at)
            SELECT id,owner_user_id,source_event,'welcome-v1','allow',:key,
                'synthetic-review','duplicate-person',clock_timestamp()+interval '1 hour'
            FROM welcome_reward_grants WHERE owner_user_id=:other
        """),
            {"other": admitted.other, "key": str(admitted.owner)},
        )
        assert (await svc.advance(admitted.other)).state == "application"
        assert await WelcomeRewardRepository(db).free_funding() == 10000000000
    with pytest.raises(IntegrityError):
        async with admitted.maker.begin() as db:
            await db.execute(
                text("""
                INSERT INTO reward_funding_entries(allocation_id,program_id,amount_raw,
                evidence_ref,authority_ref)
                VALUES(:id,'welcome',20000000000,'synthetic-funding','different-authority')
            """),
                {"id": uuid4()},
            )


async def test_raw_sql_cannot_reserve_unfunded_or_unapproved(admitted):
    async with admitted.maker.begin() as db:
        await WelcomeRewardService(WelcomeRewardRepository(db)).advance(admitted.owner)
    with pytest.raises(IntegrityError, match="Reward funding unavailable"):
        async with admitted.maker.begin() as db:
            await db.execute(
                text("""
                UPDATE welcome_reward_grants g SET state='frozen',amount_raw=10000000000,
                  tariff_version='welcome-v1',beneficiary_key=:key,
                  decision_id=(SELECT max(id) FROM reward_eligibility_decisions d
                               WHERE d.grant_id=g.id)
                WHERE owner_user_id=:other
            """),
                {"key": str(admitted.other), "other": admitted.other},
            )


async def test_concurrent_applications_single_owner_and_missing_canonical(ledger):
    async with ledger.maker.begin() as db:
        await db.execute(
            text("UPDATE reward_programs SET enabled=true,admission_start='2020-01-01'")
        )

    async def apply():
        async with ledger.maker.begin() as db:
            return await WelcomeRewardService(WelcomeRewardRepository(db)).apply(ledger.owner)

    async with asyncio.timeout(20):
        replies = await asyncio.gather(apply(), apply(), apply())
    assert all(r.state == "application" for r in replies)
    async with ledger.maker.begin() as db:
        assert await db.scalar(text("SELECT count(*) FROM welcome_reward_grants")) == 1
        await db.execute(
            text("""
            INSERT INTO reward_eligibility_decisions(grant_id,owner_user_id,source_event,
                policy_version,outcome,authority_ref,evidence_ref,expires_at)
            SELECT id,owner_user_id,source_event,'welcome-v1','allow','synthetic','no-canonical',
                clock_timestamp()+interval '1 hour' FROM welcome_reward_grants
        """)
        )
        assert (
            await WelcomeRewardService(WelcomeRewardRepository(db)).advance(ledger.owner)
        ).reason == "risk_review"


async def test_read_endpoint_auth_scope_and_no_writes(admitted):
    async def database():
        async with admitted.maker.begin() as db:
            yield db

    app.dependency_overrides[get_db] = database
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            assert (await client.get("/api/v1/users/me/rewards/welcome")).status_code in (401, 403)
            async with admitted.maker.begin() as db:
                await WelcomeRewardService(WelcomeRewardRepository(db)).advance(admitted.owner)
            app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=admitted.other)
            response = await client.get(
                "/api/v1/users/me/rewards/welcome?owner_user_id=" + str(admitted.owner)
            )
            assert response.status_code == 200
            assert response.json() == {
                "state": "application",
                "reason": "funding_unavailable",
                "amount_raw": None,
                "decimals": 6,
                "asset": "ASPORE",
                "next_action": "await_funding",
            }
            async with admitted.maker.begin() as db:
                assert await db.scalar(text("SELECT count(*) FROM welcome_reward_grants")) == 2
                assert (
                    await db.scalar(text("SELECT count(*) FROM reward_eligibility_decisions")) == 2
                )
    finally:
        app.dependency_overrides.clear()


@pytest.mark.parametrize("issued,expires", [("-2 hours", "-1 hour"), ("1 hour", "2 hours")])
async def test_expired_and_future_decisions_fail_closed(admitted, issued, expires):
    async with admitted.maker.begin() as db:
        await db.execute(
            text("""
            INSERT INTO reward_eligibility_decisions(grant_id,owner_user_id,source_event,
                policy_version,outcome,beneficiary_key,authority_ref,evidence_ref,issued_at,expires_at)
            SELECT id,owner_user_id,source_event,'welcome-v1','allow',:key,'synthetic-review',
                'historical-evidence',clock_timestamp()+CAST(CAST(:issued AS text) AS interval),
                clock_timestamp()+CAST(CAST(:expires AS text) AS interval)
            FROM welcome_reward_grants WHERE owner_user_id=:owner
        """),
            {
                "owner": admitted.owner,
                "key": str(admitted.owner),
                "issued": issued,
                "expires": expires,
            },
        )
        assert (
            await WelcomeRewardService(WelcomeRewardRepository(db)).advance(admitted.owner)
        ).reason == "risk_review"
        assert await WelcomeRewardRepository(db).free_funding() == 10000000000


async def test_later_serialized_deny_supersedes_larger_identity_allow(admitted):
    allocated, allow_committed = asyncio.Event(), asyncio.Event()

    async def delayed_deny():
        async with admitted.maker.begin() as db:
            low = await db.scalar(
                text("SELECT nextval(pg_get_serial_sequence('reward_eligibility_decisions','id'))")
            )
            allocated.set()
            await allow_committed.wait()
            await db.execute(
                text("""
                INSERT INTO reward_eligibility_decisions(id,grant_id,owner_user_id,source_event,
                    policy_version,outcome,authority_ref,evidence_ref,expires_at)
                OVERRIDING SYSTEM VALUE
                SELECT :id,id,owner_user_id,source_event,'welcome-v1','deny','synthetic',
                    'delayed-denial',clock_timestamp()+interval '1 hour'
                FROM welcome_reward_grants WHERE owner_user_id=:owner
            """),
                {"owner": admitted.owner, "id": low},
            )

    async def earlier_allow():
        await allocated.wait()
        async with admitted.maker.begin() as db:
            await db.execute(
                text("""
                INSERT INTO reward_eligibility_decisions(grant_id,owner_user_id,source_event,
                    policy_version,outcome,beneficiary_key,authority_ref,evidence_ref,expires_at)
                SELECT id,owner_user_id,source_event,'welcome-v1','allow',:key,'synthetic',
                    'earlier-allow',clock_timestamp()+interval '1 hour'
                FROM welcome_reward_grants WHERE owner_user_id=:owner
            """),
                {"owner": admitted.owner, "key": str(admitted.owner)},
            )
        allow_committed.set()

    async with asyncio.timeout(20):
        async with asyncio.TaskGroup() as group:
            group.create_task(delayed_deny())
            group.create_task(earlier_allow())
    async with admitted.maker.begin() as db:
        svc = WelcomeRewardService(WelcomeRewardRepository(db))
        assert (await svc.advance(admitted.owner)).reason == "risk_review"
        assert await WelcomeRewardRepository(db).free_funding() == 10000000000
        latest = (
            await db.execute(
                text("""SELECT outcome,id,revision
            FROM reward_eligibility_decisions WHERE owner_user_id=:owner ORDER BY revision DESC
        """),
                {"owner": admitted.owner},
            )
        ).all()
        assert latest[0].outcome == "deny"
        assert latest[0].id < latest[1].id and latest[0].revision > latest[1].revision


@pytest.mark.parametrize("operation", ["grant", "decision"])
async def test_non_read_committed_mutations_rejected(admitted, operation):
    async with admitted.maker.begin() as db:
        await WelcomeRewardService(WelcomeRewardRepository(db)).advance(admitted.owner)
    with pytest.raises(IntegrityError, match="Reward mutations require READ COMMITTED"):
        async with admitted.maker.begin() as db:
            await db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"))
            if operation == "grant":
                await db.execute(
                    text("UPDATE welcome_reward_grants SET state=state WHERE owner_user_id=:id"),
                    {"id": admitted.owner},
                )
            else:
                await db.execute(
                    text("""
                    INSERT INTO reward_eligibility_decisions(grant_id,owner_user_id,source_event,
                        policy_version,outcome,authority_ref,evidence_ref,expires_at)
                    SELECT id,owner_user_id,source_event,'welcome-v1','deny','synthetic',
                        'rr-rejection',clock_timestamp()+interval '1 hour'
                    FROM welcome_reward_grants WHERE owner_user_id=:owner
                """),
                    {"owner": admitted.owner},
                )
    async with admitted.maker.begin() as db:
        assert await WelcomeRewardRepository(db).free_funding() == 0
        assert (
            await WelcomeRewardService(WelcomeRewardRepository(db)).read(admitted.owner)
        ).state == "frozen"
