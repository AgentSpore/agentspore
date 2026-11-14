"""Canonical welcome beneficiary: email key, GitHub age signal, email proof, writer gate."""

import json
from datetime import datetime, timedelta, timezone
from io import StringIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from test_marketplace_tasks import marketplace_pg as marketplace_pg
from test_welcome_reward_decisions import OTHER_KEY, OWNER_KEY
from test_welcome_reward_decisions import review as _review
from test_welcome_rewards import ledger as _ledger

from app.api.v1 import oauth
from app.cli import welcome_reward_decisions as cli
from app.models.user import User
from app.repositories.welcome_reward_repo import WelcomeRewardRepository
from app.services.welcome_beneficiary_service import WelcomeBeneficiaryService
from app.services.welcome_reward_decision_service import WelcomeRewardDecisionService

ledger = _ledger
review = _review

pytestmark = pytest.mark.integration

DAYS_30 = timedelta(days=30)


@pytest.fixture
def edit_owner(review):
    ledger, _ = review

    async def apply(**columns):
        assignments = ", ".join(f"{name}=:{name}" for name in columns)
        async with ledger.maker.begin() as db:
            await db.execute(
                text(f"UPDATE users SET {assignments} WHERE id=:owner"),
                columns | {"owner": ledger.owner},
            )

    return apply


@pytest.fixture
def disposable_owner(edit_owner):
    """Switch the owner to a disposable domain; the email trigger drops proof, so re-prove."""

    async def apply():
        proof_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        await edit_owner(email="Canary@MAILINATOR.com")
        await edit_owner(
            email_proof_email="Canary@MAILINATOR.com",
            email_proof_source="github",
            email_proof_at=proof_at,
        )
        return proof_at

    return apply


@pytest.fixture
def assess(review):
    ledger, _ = review

    async def run(now=None):
        async with ledger.maker.begin() as db:
            now = now or await db.scalar(text("SELECT clock_timestamp()"))
            result = await WelcomeBeneficiaryService(WelcomeRewardRepository(db)).assess(
                ledger.owner, now
            )
        return result

    return run


async def test_review_owner_is_eligible(assess):
    result = await assess()
    assert (result.beneficiary_key, result.holds) == (OWNER_KEY, ())


@pytest.mark.parametrize(
    "columns,key,holds",
    [
        ({"oauth_provider": "google", "oauth_id": "1001"}, OWNER_KEY, ()),
        ({"oauth_provider": None, "oauth_id": None}, OWNER_KEY, ()),
        ({"oauth_id": "octocat"}, OWNER_KEY, ()),
        ({"oauth_id": "01001"}, OWNER_KEY, ()),
        ({"oauth_account_created_at": None}, OWNER_KEY, ("github_account_age_unknown",)),
        ({"email_proof_at": None}, OWNER_KEY, ("no_current_email_proof",)),
        ({"email_proof_email": "old@example.com"}, OWNER_KEY, ("no_current_email_proof",)),
        ({"is_verified": False}, OWNER_KEY, ("no_current_email_proof",)),
        (
            {"oauth_provider": "google", "email_proof_at": None, "email": "a@mailinator.com"},
            WelcomeBeneficiaryService.email_key("a@mailinator.com"),
            ("no_current_email_proof", "disposable_email_domain"),
        ),
    ],
)
async def test_hold_reasons(edit_owner, assess, columns, key, holds):
    await edit_owner(**columns)
    result = await assess()
    assert (result.beneficiary_key, result.holds) == (key, holds)


@pytest.mark.parametrize(
    "email,normalized",
    [
        ("Alice@Example.COM", "alice@example.com"),
        ("  alice@example.com ", "alice@example.com"),
        ("a.l.i.c.e@gmail.com", "alice@gmail.com"),
        ("A.Lice+promo@GoogleMail.com", "alice@gmail.com"),
        ("alice+x+y@example.com", "alice@example.com"),
        ("a.lice@example.com", "a.lice@example.com"),
        ("bob@bücher.example", "bob@xn--bcher-kva.example"),
        ("bob@XN--BCHER-KVA.example", "bob@xn--bcher-kva.example"),
        ("a.b+x@yandex.ru", "a-b@yandex.ru"),
        ("a.b@ya.ru", "a-b@yandex.ru"),
        ("a-b@narod.ru", "a-b@yandex.ru"),
        ("ab@yandex.com", "ab@yandex.ru"),
        ("ab@yandex.by", "ab@yandex.ru"),
        ("ab@yandex.kz", "ab@yandex.ru"),
        ("ab@yandex.ua", "ab@yandex.ru"),
        ("ab@yandex.com.tr", "ab@yandex.ru"),
        ("a.b+t@me.com", "a.b@icloud.com"),
        ("a.b@mac.com", "a.b@icloud.com"),
        ("a.b@iCloud.com", "a.b@icloud.com"),
        ("ab+t@protonmail.com", "ab@proton.me"),
        ("ab@protonmail.ch", "ab@proton.me"),
        ("ab@pm.me", "ab@proton.me"),
        ("ab@proton.me", "ab@proton.me"),
    ],
)
def test_email_normalization(email, normalized):
    assert WelcomeBeneficiaryService.normalize_email(email) == normalized
    key = WelcomeBeneficiaryService.email_key(email)
    assert key == WelcomeBeneficiaryService.email_key(normalized)
    assert key.startswith("email:") and len(key) == 70 and "@" not in key


def test_distinct_mailboxes_get_distinct_keys():
    keys = {
        WelcomeBeneficiaryService.email_key(e)
        for e in ("alice@example.com", "alice@gmail.com", "alice@other.org", "b.ob@example.com")
    }
    assert len(keys) == 4


async def test_disposable_domain_is_matched_case_insensitively(disposable_owner, assess):
    await disposable_owner()
    result = await assess()
    assert (result.beneficiary_key, result.holds) == (
        WelcomeBeneficiaryService.email_key("Canary@MAILINATOR.com"),
        ("disposable_email_domain",),
    )


@pytest.mark.parametrize(
    "offset,holds", [(timedelta(0), ()), (timedelta(seconds=1), ("github_account_too_new",))]
)
async def test_account_age_boundary_is_thirty_days(edit_owner, assess, review, offset, holds):
    ledger, _ = review
    async with ledger.maker.begin() as db:
        now = await db.scalar(text("SELECT clock_timestamp()"))
    await edit_owner(oauth_account_created_at=now - DAYS_30 + offset)
    assert (await assess(now)).holds == holds


async def test_unknown_user_is_not_assessed(review):
    ledger, _ = review
    async with ledger.maker.begin() as db:
        service = WelcomeBeneficiaryService(WelcomeRewardRepository(db))
        assert await service.assess(uuid4(), datetime.now(timezone.utc)) is None


async def test_second_user_cannot_bind_same_github_identity(review):
    ledger, _ = review
    async with ledger.maker() as db:
        with pytest.raises(IntegrityError):
            await db.execute(
                text("UPDATE users SET oauth_provider='github', oauth_id='1001' WHERE id=:id"),
                {"id": ledger.owner},
            )
            await db.execute(
                text("UPDATE users SET oauth_provider='github', oauth_id='1001' WHERE id=:id"),
                {"id": ledger.other},
            )


@pytest.mark.parametrize("outcome", ["hold", "deny"])
async def test_only_allow_requires_eligibility(review, edit_owner, outcome):
    ledger, intent = review
    await edit_owner(oauth_provider="google")
    async with ledger.maker.begin() as db:
        service = WelcomeRewardDecisionService(WelcomeRewardRepository(db))
        result = await service.record(
            intent.model_copy(update={"outcome": outcome, "beneficiary_key": None})
        )
        assert result.verdict == "recorded"


@pytest.mark.parametrize(
    "change",
    [
        {"beneficiary_key": "github:1001"},
        {"beneficiary_key": "github:9"},
        {"beneficiary_key": "1001"},
        {"beneficiary_key": OTHER_KEY},
    ],
)
async def test_allow_requires_canonical_beneficiary(review, change):
    ledger, intent = review
    async with ledger.maker.begin() as db:
        service = WelcomeRewardDecisionService(WelcomeRewardRepository(db))
        result = await service.record(intent.model_copy(update=change))
        assert result.verdict == "ineligible"
        assert await db.scalar(text("SELECT count(*) FROM reward_eligibility_decisions")) == 0


async def test_allow_rejected_while_any_hold_reason_remains(review, disposable_owner):
    ledger, intent = review
    intent = intent.model_copy(update={"email_proof_at": await disposable_owner()})
    async with ledger.maker.begin() as db:
        result = await WelcomeRewardDecisionService(WelcomeRewardRepository(db)).record(intent)
        assert result.model_dump(exclude_none=True) == {"verdict": "ineligible"}


async def test_cli_ineligible_exit_code_and_no_input_echo(review, disposable_owner):
    ledger, intent = review
    intent = intent.model_copy(update={"email_proof_at": await disposable_owner()})
    destination = StringIO()
    code = await cli.run(StringIO(intent.model_dump_json()), destination, True, ledger.maker)
    assert code == 12
    assert destination.getvalue() == '{"verdict": "ineligible", "applied": false}\n'
    async with ledger.maker.begin() as db:
        assert await db.scalar(text("SELECT count(*) FROM reward_eligibility_decisions")) == 0


async def test_cli_assess_prints_key_and_reason_codes_only(review, disposable_owner):
    ledger, _ = review
    await disposable_owner()
    destination = StringIO()
    assert await cli.run_assess(str(ledger.owner), destination, ledger.maker) == 0
    assert json.loads(destination.getvalue()) == {
        "verdict": "assessed",
        "beneficiary_key": WelcomeBeneficiaryService.email_key("Canary@MAILINATOR.com"),
        "holds": ["disposable_email_domain"],
    }
    assert "canary" not in destination.getvalue()


async def test_cli_assess_unknown_user(review):
    ledger, _ = review
    destination = StringIO()
    assert await cli.run_assess(str(uuid4()), destination, ledger.maker) == 6
    assert destination.getvalue() == '{"verdict": "not_found"}\n'


def test_cli_assess_bad_arguments_rejected_without_echo():
    for arguments in (["assess", "--user-id", "canary-not-uuid"], ["assess"]):
        destination = StringIO()
        assert cli.main(arguments, StringIO(""), destination) == 5
        assert destination.getvalue() == '{"verdict": "invalid_input"}\n'


def _email_link_db(user):
    db = AsyncMock()
    first, second = MagicMock(), MagicMock()
    first.scalar_one_or_none.return_value = None
    second.scalar_one_or_none.return_value = user
    db.execute.side_effect = [first, second]
    return db


@pytest.mark.parametrize(
    "bound,expected",
    [(("github", "1001"), ("github", "1001")), (("google", "g-1"), ("google", "g-1"))],
)
async def test_email_link_never_rebinds_existing_oauth_identity(bound, expected):
    user = User(email="owner@example.com", is_verified=True)
    user.oauth_provider, user.oauth_id = bound
    result = await oauth._upsert_oauth_user(
        _email_link_db(user),
        provider="github",
        oauth_id="777",
        email="owner@example.com",
        name="Owner",
        avatar_url=None,
    )
    assert result is user and (user.oauth_provider, user.oauth_id) == expected


async def test_email_link_binds_account_without_oauth_identity():
    user = User(email="owner@example.com", is_verified=True)
    await oauth._upsert_oauth_user(
        _email_link_db(user),
        provider="github",
        oauth_id="777",
        email="owner@example.com",
        name="Owner",
        avatar_url=None,
    )
    assert (user.oauth_provider, user.oauth_id) == ("github", "777")


@pytest.fixture
def github_callback(monkeypatch):
    async def call(profile, user):
        responses = [
            SimpleNamespace(status_code=200, json=lambda: {"access_token": "synthetic"}),
            SimpleNamespace(status_code=200, json=lambda: profile),
            SimpleNamespace(
                status_code=200,
                json=lambda: [{"primary": True, "verified": True, "email": "o@example.com"}],
            ),
        ]
        client = AsyncMock()
        client.post.return_value = responses[0]
        client.get.side_effect = responses[1:]
        factory = MagicMock()
        factory.return_value.__aenter__.return_value = client
        monkeypatch.setattr(oauth.httpx, "AsyncClient", factory)
        monkeypatch.setattr(oauth, "_consume_oauth_state", AsyncMock(return_value=True))
        monkeypatch.setattr(oauth, "_consume_oauth_origin", AsyncMock(return_value=None))
        monkeypatch.setattr(oauth, "_upsert_oauth_user", AsyncMock(return_value=user))
        monkeypatch.setattr(
            oauth,
            "settings",
            SimpleNamespace(
                debug=True,
                user_github_oauth_client_id="id",
                user_github_oauth_client_secret="synthetic",
                oauth_redirect_base_url="https://example.com",
            ),
        )
        request = MagicMock(cookies={})
        await oauth.github_callback(
            code="c", request=request, db=AsyncMock(), state="s", redis=AsyncMock()
        )

    return call


@pytest.mark.parametrize(
    "created,expected",
    [
        ("2015-03-02T10:00:00Z", datetime(2015, 3, 2, 10, tzinfo=timezone.utc)),
        ("not-a-date", None),
        ("2015-03-02T10:00:00", None),
        (None, None),
        (12345, None),
    ],
)
async def test_callback_stores_account_creation_time(github_callback, created, expected):
    user = User(email="o@example.com", oauth_provider="github", oauth_id="42")
    await github_callback({"id": 42, "login": "octocat", "created_at": created}, user)
    assert user.oauth_account_created_at == expected


async def test_callback_keeps_existing_time_and_ignores_other_identity(github_callback):
    stored = datetime(2010, 1, 1, tzinfo=timezone.utc)
    user = User(
        email="o@example.com",
        oauth_provider="github",
        oauth_id="42",
        oauth_account_created_at=stored,
    )
    await github_callback({"id": 42, "created_at": "2020-01-01T00:00:00Z"}, user)
    assert user.oauth_account_created_at == stored
    other = User(email="o@example.com", oauth_provider="google", oauth_id="g")
    await github_callback({"id": 42, "created_at": "2020-01-01T00:00:00Z"}, other)
    assert other.oauth_account_created_at is None


@pytest.mark.parametrize(
    "domain,disposable",
    [
        ("mailinator.com", True),
        ("MAILINATOR.com.", True),
        ("sub.mailinator.com", True),
        ("a.b.mailinator.com", True),
        ("xn--rhqv96g.tv", True),
        ("世界.tv", True),
        ("mailinator.com.example.org", False),
        ("example.com", False),
        ("com", False),
        ("\ud800.com", False),
    ],
)
def test_disposable_domain_matching(domain, disposable):
    assert WelcomeBeneficiaryService.is_disposable_domain(domain) is disposable
