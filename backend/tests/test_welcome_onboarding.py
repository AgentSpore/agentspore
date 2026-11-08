"""Keep optional reward applications isolated from core identity transactions."""

from uuid import uuid4

import pytest
import test_marketplace_tasks
import test_welcome_rewards
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.models.user import User
from app.repositories.welcome_reward_repo import WelcomeRewardRepository
from app.services.welcome_reward_service import WelcomeRewardService

marketplace_pg = test_marketplace_tasks.marketplace_pg
ledger = test_welcome_rewards.ledger


async def test_optional_application_preserves_core_user_after_sql_failure(ledger):
    uid = uuid4()
    async with ledger.maker.begin() as db:
        await db.execute(
            text("UPDATE reward_programs SET enabled=true, admission_start='2020-01-01'")
        )
        await db.execute(text("ALTER TABLE welcome_reward_grants RENAME TO unavailable_grants"))
        db.add(User(id=uid, email="synthetic-core@example.com", name="Core"))
        await WelcomeRewardService(WelcomeRewardRepository(db)).apply_onboarding(uid)
        await db.execute(text("UPDATE users SET name='Committed' WHERE id=:id"), {"id": uid})
    async with ledger.maker() as db:
        assert (
            await db.scalar(text("SELECT name FROM users WHERE id=:id"), {"id": uid}) == "Committed"
        )


async def test_core_flush_failure_is_not_swallowed(ledger):
    async with ledger.maker() as db:
        db.add(User(id=ledger.owner, email="duplicate@example.com", name="Duplicate"))
        with pytest.raises(IntegrityError):
            await WelcomeRewardService(WelcomeRewardRepository(db)).apply_onboarding(ledger.owner)
        assert not db.is_active
        await db.rollback()


@pytest.mark.parametrize("enabled", [False, True])
async def test_application_keeps_timeout_settings_and_no_money(ledger, enabled):
    async with ledger.maker.begin() as db:
        await db.execute(
            text("UPDATE reward_programs SET enabled=:enabled, admission_start='2020-01-01'"),
            {"enabled": enabled},
        )
        await db.execute(text("SET LOCAL lock_timeout='3s'"))
        await db.execute(text("SET LOCAL statement_timeout='4s'"))
        svc = WelcomeRewardService(WelcomeRewardRepository(db))
        await svc.apply_onboarding(ledger.owner)
        await svc.apply_onboarding(ledger.owner)
        assert await db.scalar(text("SHOW lock_timeout")) == "3s"
        assert await db.scalar(text("SHOW statement_timeout")) == "4s"
        assert await db.scalar(text("SELECT count(*) FROM welcome_reward_grants")) == int(enabled)
        assert (
            await db.scalar(
                text("SELECT count(*) FROM welcome_reward_grants WHERE amount_raw IS NOT NULL")
            )
            == 0
        )
        assert await db.scalar(text("SELECT count(*) FROM reward_funding_entries")) == 0


async def test_lock_timeout_preserves_identity_and_settings(ledger):
    async with ledger.maker.begin() as db:
        await db.execute(
            text("UPDATE reward_programs SET enabled=true, admission_start='2020-01-01'")
        )
    async with ledger.maker.begin() as blocker:
        await blocker.execute(text("SELECT id FROM reward_programs FOR UPDATE"))
        async with ledger.maker.begin() as db:
            await db.execute(text("SET LOCAL lock_timeout='3s'"))
            await db.execute(
                text("UPDATE users SET name='Retained' WHERE id=:id"), {"id": ledger.owner}
            )
            await WelcomeRewardService(WelcomeRewardRepository(db)).apply_onboarding(ledger.owner)
            assert await db.scalar(text("SHOW lock_timeout")) == "3s"
    async with ledger.maker() as db:
        assert (
            await db.scalar(text("SELECT name FROM users WHERE id=:id"), {"id": ledger.owner})
            == "Retained"
        )
        assert await db.scalar(text("SELECT count(*) FROM welcome_reward_grants")) == 0


async def test_invalidated_connection_is_not_optional(ledger, monkeypatch):
    async def disconnected(_repo):
        raise DBAPIError(
            None, None, RuntimeError("synthetic connection lost"), connection_invalidated=True
        )

    monkeypatch.setattr(WelcomeRewardRepository, "enabled", disconnected)
    async with ledger.maker.begin() as db:
        with pytest.raises(DBAPIError):
            await WelcomeRewardService(WelcomeRewardRepository(db)).apply_onboarding(ledger.owner)


async def test_outer_rollback_removes_application_with_identity_change(ledger):
    async with ledger.maker.begin() as db:
        await db.execute(
            text("UPDATE reward_programs SET enabled=true, admission_start='2020-01-01'")
        )
    async with ledger.maker() as db:
        await db.execute(
            text("UPDATE users SET name='Rolled back' WHERE id=:id"), {"id": ledger.owner}
        )
        await WelcomeRewardService(WelcomeRewardRepository(db)).apply_onboarding(ledger.owner)
        await db.rollback()
    async with ledger.maker() as db:
        assert (
            await db.scalar(text("SELECT name FROM users WHERE id=:id"), {"id": ledger.owner})
            != "Rolled back"
        )
        assert await db.scalar(text("SELECT count(*) FROM welcome_reward_grants")) == 0
