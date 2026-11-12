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
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from sqlalchemy import text
from test_marketplace_tasks import marketplace_pg as marketplace_pg
from test_welcome_reward_decisions import review as _review
from test_welcome_rewards import ledger as _ledger

from app.cli import welcome_payouts as cli
from app.repositories.welcome_reward_repo import WelcomeRewardRepository
from app.schemas.payout_delivery import NetworkConfig
from app.services.welcome_payout_link_service import (
    SYSTEM_PROGRAM,
    TOKEN_2022,
    WelcomePayoutLinkService,
    associated_token_account,
)
from app.services.welcome_reward_decision_service import WelcomeRewardDecisionService
from app.services.welcome_reward_service import WelcomeRewardService

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
CONFIG_ATA = CONFIG.model_copy(update={"create_recipient_ata": True})
AMOUNT = "10000000000"


PROGRAM_MINT = "5ZkjEjfDAPuSg7pRxCRJsJuZ8FByRSyAgAA8SLMMpump"  # fixed by migration V115
# Independent vectors from @solana/spl-token 0.4.13
# getAssociatedTokenAddressSync(mint, owner, true, TOKEN_2022_PROGRAM_ID); wallets are synthetic.
SYNTHETIC_MINT = str(Pubkey.from_bytes(bytes([3]) * 32))
SYNTHETIC_ATA = "5HyfqqJF1uGkJr9VLjnJdHcbDLqf8nuD4YSoiTStaP3h"  # (WALLET, SYNTHETIC_MINT)
WALLET_ATA = "CjnjTtspAbXwsiQAotu9xEtckc4CdRzx8M6t2F9burcV"  # (WALLET, PROGRAM_MINT)
OTHER_ATA = "3p4WVCTfX74WhaGVXoNjJYc2PYwZN2feHqwa7bV36yLV"  # (OTHER_WALLET, PROGRAM_MINT)
# Shape B needs an on-curve wallet; the byte-pattern wallets above are not.
GOOD_WALLET = str(Keypair().pubkey())
GOOD_ATA = str(
    associated_token_account(Pubkey.from_string(GOOD_WALLET), Pubkey.from_string(PROGRAM_MINT))
)
HOLDERS = {WALLET_ATA: WALLET, OTHER_ATA: OTHER_WALLET, GOOD_ATA: GOOD_WALLET}


def system_account(lamports):
    """Shape of a System-owned account in getMultipleAccounts jsonParsed (mainnet capture)."""
    return {
        "data": ["", "base64"],
        "executable": False,
        "lamports": lamports,
        "owner": SYSTEM_PROGRAM,
        "rentEpoch": 18446744073709551615,
        "space": 0,
    }


class Chain:
    """RPC double: the token account exists only at the exact expected address."""

    def __init__(self):
        self.exists = True
        self.fail = False
        self.genesis = GENESIS
        self.program = str(TOKEN_2022)
        self.mint = PROGRAM_MINT
        self.holder = None
        self.state = "initialized"
        self.hidden = set()
        self.accounts = []
        self.wallet_value = None
        self.system_ata = False

    def account(self, address):
        info = {
            "mint": self.mint,
            "owner": self.holder or HOLDERS[address],
            "state": self.state,
        }
        return {
            "owner": self.program,
            "data": {"program": "spl-token-2022", "parsed": {"type": "account", "info": info}},
        }

    def __call__(self, request):
        body = json.loads(request.content)
        if body["method"] == "getGenesisHash":
            result = self.genesis
        else:
            address = body["params"][0]
            if body["method"] == "getMultipleAccounts":
                address = address[1]
            self.accounts.append(address)
            if self.fail:
                error = {"code": -32000, "message": "synthetic failure"}
                return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "error": error})
            found = self.exists and address in HOLDERS and address not in self.hidden
            value = self.account(address) if found else None
            if body["method"] == "getMultipleAccounts":
                if value is None and self.system_ata:
                    value = system_account(1_000_000)
                value = [self.wallet_value, value]
            result = {"value": value}
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
    assert out["skipped"] == {}
    maker = linker.ledger.maker
    assert await intents(maker) == 1
    async with maker.begin() as db:
        row = (await db.execute(text("SELECT * FROM payout_delivery_intents"))).mappings().one()
    grant = await scalar(
        maker, "SELECT id::text FROM welcome_reward_grants WHERE state='available'"
    )
    assert (row["producer_namespace"], row["allocation_id"]) == ("welcome", grant)
    assert (row["raw_amount"], row["destination"], row["recipient"]) == (
        AMOUNT,
        WALLET_ATA,
        WALLET,
    )
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


OTHER_KEY = str(Pubkey.from_bytes(bytes([9]) * 32))


@pytest.mark.parametrize(
    ("reason", "mutation"),
    [
        ("no_wallet", "UPDATE users SET solana_wallet=NULL"),
        ("not_available", "UPDATE users SET email_proof_at=clock_timestamp()"),
        ("not_available", "UPDATE users SET email_proof_at=NULL"),
        ("no_token_account", ("exists", False)),
        ("no_token_account", ("program", OTHER_KEY)),
        ("no_token_account", ("state", "frozen")),
        ("no_token_account", ("mint", OTHER_KEY)),
        ("no_token_account", ("holder", OTHER_KEY)),
        ("rpc_error", ("fail", True)),
        ("not_available", "hold"),
        ("not_available", "UPDATE welcome_reward_grants SET state='denied'"),
    ],
)
async def test_guards_produce_no_intent(linker, chain, reason, mutation):
    if isinstance(mutation, tuple):
        setattr(chain, *mutation)
    elif mutation == "hold":
        await hold_decision(linker)
    else:
        async with linker.ledger.maker.begin() as db:
            await db.execute(text(mutation))
    outcome, _ = await linker.service.link_one(linker.ledger.owner, apply=True)
    assert outcome == reason
    assert (await linker.service.link(10, apply=True))["linked"] == 0
    assert await intents(linker.ledger.maker) == 0


def test_associated_token_account_matches_independent_vector():
    derived = associated_token_account(
        Pubkey.from_string(WALLET), Pubkey.from_string(SYNTHETIC_MINT)
    )
    assert str(derived) == SYNTHETIC_ATA


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


@pytest.fixture
def configured(monkeypatch, tmp_path):
    path = tmp_path / "network.json"
    path.write_text(CONFIG.model_dump_json())
    path.chmod(0o600)
    monkeypatch.setattr(
        cli, "get_settings", lambda: SimpleNamespace(payout_network_config_path=str(path))
    )


async def test_cli_output_discloses_nothing(linker, chain, configured):
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


async def test_denied_or_walletless_grant_is_not_a_candidate(linker):
    assert len(await linker.service.candidates(10)) == 1
    async with linker.ledger.maker.begin() as db:
        await db.execute(text("UPDATE users SET solana_wallet=NULL"))
    assert await linker.service.candidates(10) == []
    async with linker.ledger.maker.begin() as db:
        await db.execute(text("UPDATE users SET solana_wallet=:w"), {"w": WALLET})
        await db.execute(text("UPDATE welcome_reward_grants SET state='denied'"))
    assert await linker.service.candidates(10) == []


@pytest.mark.parametrize(
    ("expected", "change"),
    [
        ("wallet_changed", "UPDATE users SET solana_wallet='" + OTHER_WALLET + "'"),
        ("not_available", "UPDATE users SET email_proof_at=NULL"),
        ("not_available", "hold"),
    ],
)
async def test_change_between_check_and_admission_is_skipped(linker, monkeypatch, expected, change):
    original = linker.service._precheck

    async def checked_then_changed(owner):
        verdict = await original(owner)
        if change == "hold":
            await hold_decision(linker)
        else:
            async with linker.ledger.maker.begin() as db:
                await db.execute(text(change))
        return verdict

    monkeypatch.setattr(linker.service, "_precheck", checked_then_changed)
    outcome, _ = await linker.service.link_one(linker.ledger.owner, apply=True)
    assert outcome == expected
    assert await intents(linker.ledger.maker) == 0


async def test_older_blocked_grant_does_not_starve_a_payable_one(linker, chain):
    ledger = linker.ledger
    chain.hidden.add(WALLET_ATA)
    async with ledger.maker.begin() as db:
        await db.execute(
            text("UPDATE users SET solana_wallet=:w WHERE id=:o"),
            {"w": OTHER_WALLET, "o": ledger.other},
        )
        await db.execute(
            text(
                "INSERT INTO reward_eligibility_decisions(grant_id,owner_user_id,source_event,"
                "policy_version,outcome,beneficiary_key,authority_ref,evidence_ref,expires_at,"
                "email_proof_at) SELECT g.id,g.owner_user_id,g.source_event,'welcome-v1','allow',"
                "'github:1002','op','second',clock_timestamp()+interval '1 hour',u.email_proof_at "
                "FROM welcome_reward_grants g JOIN users u ON u.id=g.owner_user_id "
                "WHERE g.owner_user_id=:o"
            ),
            {"o": ledger.other},
        )
    candidates = await linker.service.candidates(10)
    assert [c["owner_user_id"] for c in candidates] == [ledger.owner, ledger.other]
    out = await linker.service.link(1, apply=True)
    assert (out["linked"], out["skipped"]) == (1, {"no_token_account": 1})
    assert (
        await scalar(ledger.maker, "SELECT recipient FROM payout_delivery_intents") == OTHER_WALLET
    )


async def test_committed_frozen_grant_stays_a_candidate_and_links(linker):
    async with linker.ledger.maker.begin() as db:
        view = await WelcomeRewardService(WelcomeRewardRepository(db)).advance(linker.ledger.owner)
    assert view.state == "frozen"
    state = await scalar(
        linker.ledger.maker,
        "SELECT state FROM welcome_reward_grants WHERE owner_user_id=:o",
        o=linker.ledger.owner,
    )
    assert state == "frozen"
    assert len(await linker.service.candidates(10)) == 1
    out = await linker.service.link(10, apply=True)
    assert out["linked"] == 1


async def test_cap_bounds_one_run(linker):
    out = await linker.service.link(1, apply=True, cap=0)
    assert (out["verdict"], out["linked"]) == ("ok", 0)
    assert await intents(linker.ledger.maker) == 0


async def test_foreign_genesis_links_nothing(linker, chain):
    chain.genesis = str(Hash.new_unique())
    assert await linker.service.link(10, apply=True) == {"verdict": "configuration_error"}
    assert chain.accounts == [] and await intents(linker.ledger.maker) == 0


async def test_cli_foreign_genesis_exits_configuration_error(linker, chain, configured):
    chain.genesis = str(Hash.new_unique())
    out = io.StringIO()
    code = await cli.run_link(out, True, linker.ledger.maker, httpx.MockTransport(chain))
    assert (code, json.loads(out.getvalue())) == (9, {"verdict": "configuration_error"})


async def test_cli_masks_settings_failures_and_unready_imports(monkeypatch):
    def broken():
        raise ValueError("secret-env-value")

    monkeypatch.setattr(cli, "get_settings", broken)
    out = io.StringIO()
    assert await cli.run_link(out) == 9
    assert out.getvalue() == '{"verdict": "configuration_error"}\n'
    monkeypatch.setattr(cli, "IMPORT_READY", False)
    out = io.StringIO()
    assert await cli.run_link(out) == 9
    assert "secret-env-value" not in out.getvalue()


@pytest.fixture
async def flag_on(linker):
    linker.service.config = CONFIG_ATA
    async with linker.ledger.maker.begin() as db:
        await db.execute(text("UPDATE users SET solana_wallet=:w"), {"w": GOOD_WALLET})
    return linker


async def test_ata_absent_links_flag_on(flag_on, chain):
    chain.exists = False
    outcome, intent_id = await flag_on.service.link_one(flag_on.ledger.owner, apply=True)
    assert outcome == "linked" and intent_id
    maker = flag_on.ledger.maker
    assert await scalar(maker, "SELECT destination FROM payout_delivery_intents") == GOOD_ATA
    assert await scalar(maker, "SELECT recipient FROM payout_delivery_intents") == GOOD_WALLET


async def test_ata_absent_skips_flag_off(linker, chain):
    chain.exists = False
    outcome, _ = await linker.service.link_one(linker.ledger.owner, apply=True)
    assert outcome == "no_token_account"
    assert await intents(linker.ledger.maker) == 0


async def test_ata_exists_usable_links(flag_on):
    outcome, _ = await flag_on.service.link_one(flag_on.ledger.owner, apply=True)
    assert outcome == "linked"


async def test_system_owned_funded_ata_links(flag_on, chain):
    chain.exists, chain.system_ata = False, True
    outcome, _ = await flag_on.service.link_one(flag_on.ledger.owner, apply=True)
    assert outcome == "linked"


async def test_wrong_program_at_ata_bad_token_account(flag_on, chain):
    chain.program = OTHER_KEY
    outcome, _ = await flag_on.service.link_one(flag_on.ledger.owner, apply=True)
    assert outcome == "bad_token_account"
    assert await intents(flag_on.ledger.maker) == 0


@pytest.mark.parametrize(
    "mutation", [("mint", OTHER_KEY), ("holder", OTHER_KEY), ("state", "frozen")]
)
async def test_wrong_mint_or_owner_at_ata_bad_token_account(flag_on, chain, mutation):
    setattr(chain, *mutation)
    out = await flag_on.service.link(10, apply=True)
    assert (out["linked"], out["skipped"]) == (0, {"bad_token_account": 1})
    assert await intents(flag_on.ledger.maker) == 0


async def set_wallet(linker, wallet):
    async with linker.ledger.maker.begin() as db:
        await db.execute(text("UPDATE users SET solana_wallet=:w"), {"w": wallet})


async def test_wallet_off_curve_invalid_wallet(flag_on, chain):
    pda, _ = Pubkey.find_program_address([b"seed"], Pubkey.from_bytes(bytes([5]) * 32))
    assert not pda.is_on_curve()
    await set_wallet(flag_on, str(pda))
    chain.exists = False
    outcome, _ = await flag_on.service.link_one(flag_on.ledger.owner, apply=True)
    assert outcome == "invalid_wallet"
    assert await intents(flag_on.ledger.maker) == 0


@pytest.mark.parametrize(
    "wallet_value",
    [
        system_account(1) | {"owner": str(TOKEN_2022)},
        system_account(1) | {"data": ["AAEC", "base64"], "space": 3},
    ],
)
async def test_wallet_not_system_owned_invalid_wallet(flag_on, chain, wallet_value):
    chain.wallet_value = wallet_value
    outcome, _ = await flag_on.service.link_one(flag_on.ledger.owner, apply=True)
    assert outcome == "invalid_wallet"
    assert await intents(flag_on.ledger.maker) == 0


async def test_rpc_error_unchanged(flag_on, chain):
    chain.fail = True
    outcome, _ = await flag_on.service.link_one(flag_on.ledger.owner, apply=True)
    assert outcome == "rpc_error"
    chain.fail = False
    chain.exists = False
    chain.wallet_value = "malformed"
    assert (await flag_on.service.link_one(flag_on.ledger.owner, apply=True))[0] == "invalid_wallet"


async def test_replayed_link_idempotent(flag_on, chain):
    chain.exists = False
    first = await flag_on.service.link_one(flag_on.ledger.owner, apply=True)
    second = await flag_on.service.link_one(flag_on.ledger.owner, apply=True)
    assert (first[0], second[0]) == ("linked", "replayed")
    assert first[1] == second[1] and await intents(flag_on.ledger.maker) == 1
