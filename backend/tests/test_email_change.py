"""Email change over HTTP on real PostgreSQL, including its welcome-reward consequences."""

import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_marketplace_tasks import marketplace_pg as marketplace_pg
from test_welcome_rewards import admitted as _admitted
from test_welcome_rewards import ledger as _ledger

from app.api import deps
from app.api.deps import get_current_user
from app.core.redis_client import get_redis
from app.core.security import get_password_hash
from app.main import app
from app.repositories.welcome_reward_repo import WelcomeRewardRepository
from app.services.email_service import get_email_service
from app.services.welcome_reward_service import WelcomeRewardService

ledger, admitted = _ledger, _admitted
pytestmark = pytest.mark.integration

PASSWORD = "Passw0rd1"
NEW = "fresh@example.org"
CHANGE = "/api/v1/users/me/email/change"
CONFIRM = CHANGE + "/confirm"


class FakeRedis:
    def __init__(self):
        self.store: dict[str, str] = {}

    async def set(self, key, value, nx=False, ex=None):
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True

    async def setex(self, key, ttl, value):
        self.store[key] = value

    async def getdel(self, key):
        return self.store.pop(key, None)


@pytest.fixture
async def api(admitted, monkeypatch):
    ctx = admitted
    monkeypatch.setattr(deps, "async_session_maker", ctx.maker)
    async with ctx.maker.begin() as db:
        await db.execute(
            text("UPDATE users SET hashed_password=:h WHERE id=:o"),
            {"h": get_password_hash(PASSWORD), "o": ctx.owner},
        )
    redis, mail = FakeRedis(), AsyncMock()
    mail.send_email_change_link.return_value = True
    mail.send_email_changed_notice.return_value = True
    state = SimpleNamespace(uid=ctx.owner)
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=state.uid)
    app.dependency_overrides[get_redis] = lambda: redis
    app.dependency_overrides[get_email_service] = lambda: mail
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            yield SimpleNamespace(client=client, redis=redis, mail=mail, state=state, **vars(ctx))
    finally:
        app.dependency_overrides.clear()


async def ask(api, email=NEW, password=PASSWORD):
    api.redis.store.pop(f"email_change_rate:{api.owner}", None)
    return await api.client.post(CHANGE, json={"new_email": email, "current_password": password})


def token_of(api) -> str:
    return api.mail.send_email_change_link.call_args.args[1]


async def row(api, uid=None):
    async with api.maker() as db:
        r = await db.execute(
            text(
                "SELECT email,email_proof_email,email_proof_source,verification_purpose "
                "FROM users WHERE id=:i"
            ),
            {"i": uid or api.owner},
        )
        return r.one()


async def test_happy_path_switches_after_confirm_and_notifies_old(api):
    first = await ask(api)
    assert first.status_code == 202
    assert api.mail.send_email_change_link.call_args.args[0] == NEW
    assert (await row(api)).email == "synthetic@example.com"
    done = await api.client.post(CONFIRM, json={"token": token_of(api)})
    assert done.status_code == 200
    after = await row(api)
    assert (after.email, after.email_proof_email) == (NEW, NEW)
    assert after.email_proof_source == "email_link" and after.verification_purpose is None
    api.mail.send_email_changed_notice.assert_awaited_once_with("synthetic@example.com")


@pytest.mark.parametrize("password", ["Wrong0pass1", None])
async def test_wrong_or_missing_password_is_refused(api, password):
    assert (await ask(api, password=password)).status_code == 403
    api.mail.send_email_change_link.assert_not_called()
    assert (await row(api)).verification_purpose is None


async def test_disposable_domain_is_refused(api):
    assert (await ask(api, "x@mailinator.com")).status_code == 422
    api.mail.send_email_change_link.assert_not_called()


async def test_taken_email_looks_exactly_like_success_and_sends_nothing(api):
    taken = await ask(api, "OTHER@example.com")
    fresh = await ask(api)
    assert (taken.status_code, taken.json()) == (fresh.status_code, fresh.json())
    assert api.mail.send_email_change_link.await_count == 1
    assert api.mail.send_email_change_link.call_args.args[0] == NEW


async def test_same_email_gets_generic_reply_without_mail(api):
    assert (await ask(api, "synthetic@example.com")).status_code == 202
    api.mail.send_email_change_link.assert_not_called()


async def test_second_request_inside_cooldown_is_limited(api):
    assert (await ask(api)).status_code == 202
    again = await api.client.post(CHANGE, json={"new_email": NEW, "current_password": PASSWORD})
    assert again.status_code == 429


async def test_token_is_single_use(api):
    await ask(api)
    token = token_of(api)
    assert (await api.client.post(CONFIRM, json={"token": token})).status_code == 200
    assert (await api.client.post(CONFIRM, json={"token": token})).status_code == 400


async def test_garbage_and_expired_tokens_are_refused(api):
    await ask(api)
    token = token_of(api)
    assert (await api.client.post(CONFIRM, json={"token": "x" * 43})).status_code == 400
    async with api.maker.begin() as db:
        await db.execute(
            text("UPDATE users SET verification_expires_at=now()-interval '1 second' WHERE id=:o"),
            {"o": api.owner},
        )
    assert (await api.client.post(CONFIRM, json={"token": token})).status_code == 400
    assert (await row(api)).email == "synthetic@example.com"


async def test_another_user_cannot_use_the_token(api):
    await ask(api)
    api.state.uid = api.other
    assert (await api.client.post(CONFIRM, json={"token": token_of(api)})).status_code == 400
    assert (await row(api, api.other)).email == "other@example.com"
    assert (await row(api)).email == "synthetic@example.com"


async def test_token_is_stored_only_as_a_hash(api):
    await ask(api)
    async with api.maker() as db:
        stored = await db.scalar(
            text("SELECT verification_token FROM users WHERE id=:o"), {"o": api.owner}
        )
    assert stored == hashlib.sha256(token_of(api).encode()).hexdigest()


async def test_oauth_only_account_needs_a_current_email_proof(api):
    async with api.maker.begin() as db:
        await db.execute(
            text("UPDATE users SET hashed_password=NULL WHERE id=:o"), {"o": api.owner}
        )
    assert (await ask(api, password=None)).status_code == 403
    async with api.maker.begin() as db:
        await db.execute(
            text(
                "UPDATE users SET email_proof_email=email,email_proof_at=now(),"
                "email_proof_source='github' WHERE id=:o"
            ),
            {"o": api.owner},
        )
    assert (await ask(api, password=None)).status_code == 202


async def test_change_after_allow_cannot_pay_under_the_old_decision(api):
    async with api.maker.begin() as db:
        svc = WelcomeRewardService(WelcomeRewardRepository(db))
        await svc.advance(api.owner)
        await db.execute(
            text(
                "UPDATE users SET email_proof_email=email,email_proof_at=clock_timestamp(),"
                "email_proof_source='email_code' WHERE id=:o"
            ),
            {"o": api.owner},
        )
        await db.execute(
            text("""
            INSERT INTO reward_eligibility_decisions(grant_id,owner_user_id,source_event,
              policy_version,outcome,beneficiary_key,authority_ref,evidence_ref,
              email_proof_at,expires_at)
            SELECT g.id,g.owner_user_id,g.source_event,'welcome-v1','allow',g.beneficiary_key,
              'synthetic-review','proof-evidence',u.email_proof_at,
              clock_timestamp()+interval '1 hour'
            FROM welcome_reward_grants g JOIN users u ON u.id=g.owner_user_id
            WHERE u.id=:o"""),
            {"o": api.owner},
        )
    async with api.maker.begin() as db:
        assert (
            await WelcomeRewardService(WelcomeRewardRepository(db)).advance(api.owner)
        ).state == "available"
    await ask(api)
    assert (await api.client.post(CONFIRM, json={"token": token_of(api)})).status_code == 200
    async with api.maker.begin() as db:
        svc = WelcomeRewardService(WelcomeRewardRepository(db))
        view = await svc.read(api.owner)
        assert (view.state, view.reason) == ("frozen", "risk_review")
        assert (await svc.advance(api.owner)).reason == "risk_review"
        grants = await db.scalar(
            text("SELECT count(*) FROM welcome_reward_grants WHERE owner_user_id=:o"),
            {"o": api.owner},
        )
        key = await db.scalar(
            text("SELECT beneficiary_key FROM welcome_reward_grants WHERE owner_user_id=:o"),
            {"o": api.owner},
        )
    assert (grants, key) == (1, str(api.owner))
    with pytest.raises(DBAPIError):
        async with api.maker.begin() as db:
            await db.execute(
                text("UPDATE welcome_reward_grants SET state='frozen' WHERE owner_user_id=:o"),
                {"o": api.owner},
            )
            await db.execute(
                text("UPDATE welcome_reward_grants SET state='available' WHERE owner_user_id=:o"),
                {"o": api.owner},
            )
