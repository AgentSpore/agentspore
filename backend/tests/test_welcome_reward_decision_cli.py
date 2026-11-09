"""Operator input and transaction-boundary checks with synthetic references."""

import asyncio
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from io import StringIO
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from test_marketplace_tasks import marketplace_pg as marketplace_pg
from test_welcome_reward_decisions import review as _review
from test_welcome_rewards import ledger as _ledger

from app.cli import welcome_reward_decisions as cli
from app.cli.welcome_reward_decisions import run
from app.schemas.welcome_reward_decisions import WelcomeRewardDecision

ledger = _ledger
review = _review


@pytest.fixture
def payload():
    now = datetime.now(timezone.utc)
    owner = str(uuid4())
    return dict(
        grant_id=str(uuid4()),
        owner_user_id=owner,
        source_event=owner,
        policy_version="welcome-v1",
        outcome="allow",
        beneficiary_key="person-17",
        authority_ref="operator-1",
        evidence_ref="review-1",
        expected_revision=0,
        email_proof_at=None,
        issued_at=now.isoformat(),
        expires_at=(now + timedelta(hours=1)).isoformat(),
    )


def test_strict_contract(payload):
    assert WelcomeRewardDecision.model_validate_json(json.dumps(payload)).expected_revision == 0
    for changed in (
        {"expected_revision": "0"},
        {"outcome": "approve"},
        {"unknown": 1},
        {"issued_at": "2026-11-08T00:00:00"},
        {"beneficiary_key": None},
        {"expires_at": payload["issued_at"]},
    ):
        with pytest.raises(ValueError):
            WelcomeRewardDecision.model_validate_json(json.dumps(payload | changed))


async def test_invalid_input_never_echoed():
    destination = StringIO()
    assert await run(StringIO('{"canary-secret": 1}'), destination) == 5
    assert destination.getvalue() == '{"verdict": "invalid_input"}\n'


@pytest.mark.parametrize(
    "payload",
    [
        '{"evidence_ref":"canary-reference","evidence_ref":"duplicate"}',
        '"' + "x" * 8192 + '"',
        '"' + "é" * 5000 + '"',
        "[" * 2000 + "0" + "]" * 2000,
    ],
)
async def test_bounded_ambiguous_input(payload):
    destination = StringIO()
    assert await run(StringIO(payload), destination) == 5
    assert destination.getvalue() == '{"verdict": "invalid_input"}\n'


@pytest.mark.parametrize("shape", ["duplicate_key", "oversized_valid_object"])
async def test_valid_object_with_ambiguity_or_padding_rejected(payload, shape):
    body = json.dumps(payload)
    body = (
        body[:-1] + ', "evidence_ref": "other"}' if shape == "duplicate_key" else body + " " * 8200
    )
    assert WelcomeRewardDecision.model_validate_json(json.dumps(payload))
    destination = StringIO()
    assert await run(StringIO(body), destination) == 5
    assert destination.getvalue() == '{"verdict": "invalid_input"}\n'


def test_unknown_argument_rejected_before_any_work(monkeypatch):
    monkeypatch.setattr(cli, "run", AsyncMock(side_effect=AssertionError("must not run")))
    destination = StringIO()
    assert cli.main(["--canary-argument"], StringIO(""), destination) == 5
    assert destination.getvalue() == '{"verdict": "invalid_input"}\n'


async def test_dry_run_apply_and_commit_failure(review, monkeypatch):
    ledger, intent = review
    source = intent.model_dump_json()
    for apply, count in ((False, 0), (True, 1)):
        destination = StringIO()
        assert await run(StringIO(source), destination, apply, ledger.maker) == 0
        output = json.loads(destination.getvalue())
        assert output["applied"] is apply and output["verdict"] == (
            "recorded" if apply else "validated"
        )
        async with ledger.maker.begin() as db:
            assert (
                await db.scalar(text("SELECT count(*) FROM reward_eligibility_decisions")) == count
            )

    async def failed_commit(self):
        raise RuntimeError("canary-private-DETAIL")

    monkeypatch.setattr(AsyncSession, "commit", failed_commit)
    destination = StringIO()
    assert await run(StringIO(source), destination, True, ledger.maker) == 8
    assert destination.getvalue() == '{"verdict": "storage_error"}\n'


async def test_database_detail_sanitized(review):
    ledger, intent = review
    async with ledger.maker.begin() as db:
        await db.execute(
            text("""ALTER TABLE reward_eligibility_decisions
            ADD CONSTRAINT synthetic_reject CHECK (authority_ref <> 'canary-authority')""")
        )
    destination = StringIO()
    candidate = intent.model_copy(
        update={"authority_ref": "canary-authority", "evidence_ref": "canary-evidence"}
    )
    assert await run(StringIO(candidate.model_dump_json()), destination, True, ledger.maker) == 8
    assert destination.getvalue() == '{"verdict": "storage_error"}\n'
    async with ledger.maker.begin() as db:
        assert await db.scalar(text("SELECT count(*) FROM reward_eligibility_decisions")) == 0


async def test_dispose_failure_does_not_claim_success(review, monkeypatch):
    ledger, intent = review
    engine = SimpleNamespace(dispose=AsyncMock(side_effect=RuntimeError("canary-dispose")))
    monkeypatch.setattr(cli, "create_async_engine", lambda *a, **kw: engine)
    monkeypatch.setattr(cli, "async_sessionmaker", lambda *a, **kw: ledger.maker)
    destination = StringIO()
    assert await run(StringIO(intent.model_dump_json()), destination) == 8
    assert destination.getvalue() == '{"verdict": "storage_error"}\n'
    engine.dispose.assert_awaited_once()


def _run_module(payload, environ, prelude=""):
    env = {
        "SECRET_KEY": "synthetic-decision-test-key",
        "DATABASE_URL": "postgresql+asyncpg://synthetic:synthetic@database.invalid/synthetic",
        **environ,
    }
    code = (
        prelude
        + "import runpy;runpy.run_module('app.cli.welcome_reward_decisions',run_name='__main__')"
    )
    return subprocess.run(
        [sys.executable, "-c", code],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )


def test_argument_cannot_leak(payload):
    process = _run_module(payload, {}, "import sys;sys.argv[1:]=['--canary-argument'];")
    assert (process.returncode, process.stdout) == (5, '{"verdict": "invalid_input"}\n')
    assert "canary" not in process.stderr


def test_configuration_failure_is_fixed_verdict_without_env_echo(payload):
    process = _run_module(payload, {"DEBUG": "canary-config"})
    assert (process.returncode, process.stdout) == (9, '{"verdict": "configuration_error"}\n')
    assert "canary" not in process.stdout + process.stderr
    assert "Traceback" not in process.stderr


CANARY_USER_INFO = "synthetic:" + "canary-password"


@pytest.mark.parametrize(
    "environ",
    [
        {"DATABASE_URL": "notaurl-canary-password"},
        {"DATABASE_URL": "nosuchdialect+asyncpg://" + CANARY_USER_INFO + "@database.invalid/x"},
        {"SECRET_KEY": "super-secret-key-change-in-production"},
    ],
    ids=["malformed_url", "unknown_dialect", "default_secret_without_debug"],
)
def test_other_configuration_failures_are_fixed_verdict(payload, environ):
    process = _run_module(payload, environ)
    assert (process.returncode, process.stdout) == (9, '{"verdict": "configuration_error"}\n')
    assert "canary" not in process.stdout + process.stderr
    assert "Traceback" not in process.stderr


def test_code_defect_is_not_masked_as_storage_or_configuration(payload):
    prelude = (
        "import sys,types;"
        "sys.modules['app.services.welcome_reward_decision_service']=types.ModuleType('stub');"
    )
    process = _run_module(payload, {"APP_CANARY": "canary-env-value"}, prelude)
    assert process.returncode not in (0, 8, 9) and process.stdout == ""
    assert "ImportError" in process.stderr
    assert "canary" not in process.stderr


async def test_lock_timeout_reports_busy_then_retry_succeeds(review):
    ledger, intent = review
    destination = StringIO()
    async with ledger.maker.begin() as holder:
        await holder.execute(text("SELECT 1 FROM reward_programs WHERE id='welcome' FOR UPDATE"))
        async with asyncio.timeout(30):
            code = await run(StringIO(intent.model_dump_json()), destination, True, ledger.maker)
    assert (code, destination.getvalue()) == (11, '{"verdict": "busy"}\n')
    async with ledger.maker.begin() as db:
        assert await db.scalar(text("SELECT count(*) FROM reward_eligibility_decisions")) == 0
    destination = StringIO()
    assert await run(StringIO(intent.model_dump_json()), destination, True, ledger.maker) == 0


async def test_ambiguous_commit_retry_recovers_receipt(review, monkeypatch):
    ledger, intent = review
    original_commit = AsyncSession.commit

    async def committed_then_failed(self):
        await original_commit(self)
        raise SQLAlchemyError("canary-commit-acknowledgment")

    with monkeypatch.context() as patch:
        patch.setattr(AsyncSession, "commit", committed_then_failed)
        destination = StringIO()
        assert await run(StringIO(intent.model_dump_json()), destination, True, ledger.maker) == 8
        assert destination.getvalue() == '{"verdict": "storage_error"}\n'
    destination = StringIO()
    assert await run(StringIO(intent.model_dump_json()), destination, True, ledger.maker) == 0
    assert json.loads(destination.getvalue())["verdict"] == "replayed"
    async with ledger.maker.begin() as db:
        assert await db.scalar(text("SELECT count(*) FROM reward_eligibility_decisions")) == 1
