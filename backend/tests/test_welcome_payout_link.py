"""Welcome grant to payout intent linking on real PostgreSQL with an injected RPC."""

import asyncio
import io
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from solders.hash import Hash
from solders.pubkey import Pubkey
from sqlalchemy import text
from test_marketplace_tasks import marketplace_pg as marketplace_pg
from test_welcome_reward_decisions import review as _review
from test_welcome_rewards import ledger as _ledger

from app.cli import welcome_payouts as cli
from app.repositories.welcome_reward_repo import WelcomeRewardRepository
from app.schemas.payout_delivery import NetworkConfig
from app.services.welcome_payout_link_service import (
    TOKEN_2022,
    WelcomePayoutLinkService,
    associated_token_account,
)
from app.services.welcome_reward_decision_service import WelcomeRewardDecisionService

ledger = _ledger
review = _review

pytestmark = pytest.mark.integration

MIGRATIONS = Path(__file__).resolve().parents[2] / "db/migrations"
ENDPOINT = "https://rpc-private.invalid/secret-path-marker"
GENESIS = str(Hash.new_unique())
WALLET = str(Pubkey.from_bytes(bytes([7]) * 32))
OTHER_WALLET = str(Pubkey.from_bytes(bytes([8]) * 32))
CONFIG = NetworkConfig.model_validate(
    {
        "rpc_endpoint": ENDPOINT,
        "trusted_genesis_hash": GENESIS,
        "signer_pubkey": str(Pubkey.from_bytes(bytes([1]) * 32)),
        "source_token_account": str(Pubkey.from_bytes(bytes([2]) * 32)),
    }
)
AMOUNT = "10000000000"


class Chain:
    """Token account lookup double; flip `exists` or `owner` to change the answer."""

    def __init__(self):
        self.exists = True
        self.owner = str(TOKEN_2022)
        self.accounts = []

    def __call__(self, request):
        body = json.loads(request.content)
        if body["method"] == "getGenesisHash":
            result = GENESIS
        else:
            self.accounts.append(body["params"][0])
            result = {"value": {"owner": self.owner} if self.exists else None}
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": result})


@pytest.fixture
def chain():
    return Chain()


@pytest.fixture
async def linker(review, chain):
    ledger, intent = review
    async with ledger.maker.begin() as db:
        raw = await (await db.connection()).get_raw_connection()
        for name in ("V111__payout_delivery.sql", "R__payout_delivery_identity.sql"):
            await raw.driver_connection.execute((MIGRATIONS / name).read_text())
        await db.execute(text("UPDATE reward_programs SET enabled=true"))
        await db.execute(
            text(
                "INSERT INTO reward_funding_entries(allocation_id,program_id,amount_raw,"
                "evidence_ref,authority_ref) VALUES(:id,'welcome',50000000000,'f','a')"
            ),
            {"id": uuid4()},
        )
        await db.execute(
            text("UPDATE users SET solana_wallet=:w WHERE id=:o"),
            {"w": WALLET, "o": ledger.owner},
        )
        result = await WelcomeRewardDecisionService(WelcomeRewardRepository(db)).record(intent)
        assert result.verdict == "recorded"
    client = httpx.Client(transport=httpx.MockTransport(chain))
    service = WelcomePayoutLinkService(ledger.maker, CONFIG, client)
    yield SimpleNamespace(service=service, ledger=ledger, intent=intent)
    client.close()


async def scalar(maker, sql, **params):
    async with maker.begin() as db:
        return await db.scalar(text(sql), params)


async def intents(maker):
    return await scalar(maker, "SELECT count(*) FROM payout_delivery_intents")


async def test_allowed_grant_creates_exact_intent(linker):
    out = await linker.service.link(10, apply=True)
    assert (out["linked"], out["replayed"], out["conflict"]) == (1, 0, 0)
    assert out["skipped"] == {"not_available": 1}
    maker = linker.ledger.maker
    assert await intents(maker) == 1
    async with maker.begin() as db:
        row = (await db.execute(text("SELECT * FROM payout_delivery_intents"))).mappings().one()
    grant = await scalar(
        maker, "SELECT id::text FROM welcome_reward_grants WHERE state='available'"
    )
    ata = str(associated_token_account(Pubkey.from_string(WALLET), Pubkey.from_string(row["mint"])))
    assert (row["producer_namespace"], row["allocation_id"]) == ("welcome", grant)
    assert (row["raw_amount"], row["destination"], row["recipient"]) == (AMOUNT, ata, WALLET)
    assert (row["source"], row["authority"], row["fee_payer"]) == (
        CONFIG.source_token_account,
        CONFIG.signer_pubkey,
        CONFIG.signer_pubkey,
    )
    assert out["intents"] == [str(row["intent_id"])]


async def test_rerun_is_replayed_and_batch_has_nothing_left(linker):
    await linker.service.link(10, apply=True)
    outcome, intent_id = await linker.service.link_one(linker.ledger.owner, apply=True)
    assert outcome == "replayed" and intent_id
    again = await linker.service.link(10, apply=True)
    assert again["linked"] == 0
    assert await intents(linker.ledger.maker) == 1


async def test_concurrent_linkers_make_one_row(linker):
    results = await asyncio.gather(
        *(linker.service.link_one(linker.ledger.owner, apply=True) for _ in range(2))
    )
    assert sorted(outcome for outcome, _ in results) == ["linked", "replayed"]
    assert await intents(linker.ledger.maker) == 1


async def test_changed_wallet_conflicts_and_row_is_untouched(linker):
    await linker.service.link(10, apply=True)
    maker = linker.ledger.maker
    async with maker.begin() as db:
        await db.execute(text("UPDATE users SET solana_wallet=:w"), {"w": OTHER_WALLET})
    outcome, _ = await linker.service.link_one(linker.ledger.owner, apply=True)
    assert outcome == "conflict"
    assert await scalar(maker, "SELECT recipient FROM payout_delivery_intents") == WALLET
    assert await intents(maker) == 1


async def hold_decision(linker):
    ledger, intent = linker.ledger, linker.intent
    async with ledger.maker.begin() as db:
        held = intent.model_copy(
            update={
                "outcome": "hold",
                "evidence_ref": "synthetic-hold",
                "expected_revision": 1,
            }
        )
        result = await WelcomeRewardDecisionService(WelcomeRewardRepository(db)).record(held)
        assert result.verdict == "recorded"


@pytest.mark.parametrize(
    ("reason", "mutation"),
    [
        ("no_wallet", "UPDATE users SET solana_wallet=NULL"),
        ("not_available", "UPDATE users SET email_proof_at=clock_timestamp()"),
        ("not_available", "UPDATE users SET email_proof_at=NULL"),
        ("no_token_account", "chain.exists"),
        ("no_token_account", "chain.owner"),
        ("not_available", "hold"),
        ("not_available", "UPDATE welcome_reward_grants SET state='denied'"),
    ],
)
async def test_guards_produce_no_intent(linker, chain, reason, mutation):
    if mutation == "chain.exists":
        chain.exists = False
    elif mutation == "chain.owner":
        chain.owner = str(Pubkey.from_bytes(bytes([9]) * 32))
    elif mutation == "hold":
        await hold_decision(linker)
    else:
        async with linker.ledger.maker.begin() as db:
            await db.execute(text(mutation))
    out = await linker.service.link(10, apply=True)
    assert out["linked"] == 0
    assert await intents(linker.ledger.maker) == 0
    if mutation != "UPDATE welcome_reward_grants SET state='denied'":
        assert out["skipped"][reason] >= 1


async def test_disabled_program_produces_nothing(linker, chain):
    async with linker.ledger.maker.begin() as db:
        await db.execute(text("UPDATE reward_programs SET enabled=false"))
    out = await linker.service.link(10, apply=True)
    assert (out["linked"], out["skipped"]) == (0, {})
    outcome, _ = await linker.service.link_one(linker.ledger.owner, apply=True)
    assert outcome == "not_available"
    assert chain.accounts == [] and await intents(linker.ledger.maker) == 0


async def test_dry_run_leaves_no_rows(linker):
    out = await linker.service.link(10, apply=False)
    assert (out["linked"], out["intents"], out["applied"]) == (1, [], False)
    assert await intents(linker.ledger.maker) == 0
    assert (
        await scalar(
            linker.ledger.maker,
            "SELECT count(*) FROM welcome_reward_grants WHERE state='available'",
        )
        == 0
    )


async def test_cli_output_discloses_nothing(linker, chain, monkeypatch, tmp_path):
    path = tmp_path / "network.json"
    path.write_text(CONFIG.model_dump_json())
    path.chmod(0o600)
    monkeypatch.setattr(
        cli, "get_settings", lambda: SimpleNamespace(payout_network_config_path=str(path))
    )
    out = io.StringIO()
    code = await cli.run_link(out, True, linker.ledger.maker, httpx.MockTransport(chain))
    assert code == 0
    parsed = json.loads(out.getvalue())
    assert parsed["linked"] == 1 and len(parsed["intents"]) == 1
    for secret in (WALLET, "example.com", "secret-path-marker", AMOUNT, CONFIG.signer_pubkey):
        assert secret not in out.getvalue()


async def test_cli_rejects_bad_arguments_and_missing_config(monkeypatch):
    out = io.StringIO()
    assert cli.main(["link", "--bogus"], out) == 5
    monkeypatch.setattr(cli, "get_settings", lambda: SimpleNamespace(payout_network_config_path=""))
    out = io.StringIO()
    assert await cli.run_link(out) == 9
    assert json.loads(out.getvalue()) == {"verdict": "configuration_error"}


async def test_denied_grant_is_not_a_candidate(linker):
    async with linker.ledger.maker.begin() as db:
        await db.execute(text("UPDATE welcome_reward_grants SET state='denied'"))
    assert await linker.service.candidates(10) == []


async def test_wallet_changed_between_check_and_admission_is_skipped(linker, monkeypatch):
    original = linker.service._precheck

    async def checked_then_changed(owner):
        verdict = await original(owner)
        async with linker.ledger.maker.begin() as db:
            await db.execute(text("UPDATE users SET solana_wallet=:w"), {"w": OTHER_WALLET})
        return verdict

    monkeypatch.setattr(linker.service, "_precheck", checked_then_changed)
    outcome, _ = await linker.service.link_one(linker.ledger.owner, apply=True)
    assert outcome == "wallet_changed"
    assert await intents(linker.ledger.maker) == 0
