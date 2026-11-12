import pytest
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.token.associated import get_associated_token_address

from payout_signer.signer import sign_request
from payout_signer.verdict import Verdict

from .conftest import ATA_PROGRAM, SYSTEM, TOKEN_2022


def run(env, raw, name="r.json", out="out.json"):
    return sign_request(env.config, env.request(raw, name), env.root / out, now=1_000_000.0)


def test_accepts_create_then_transfer(env):
    assert run(env, env.message_b()) is Verdict.SIGNED


def test_a_shape_still_signed(env):
    assert run(env, env.message()) is Verdict.SIGNED


def test_replay_same_digest_same_envelope(env):
    raw = env.message_b()
    assert run(env, raw, "a.json", "a.out") is Verdict.SIGNED
    assert run(env, raw, "b.json", "b.out") is Verdict.REPLAYED
    assert (env.root / "a.out").read_bytes() == (env.root / "b.out").read_bytes()


def test_rejects_wrong_ata_program(env):
    assert run(env, env.message_b(ata_program=Keypair().pubkey())) is Verdict.REJECTED


@pytest.mark.parametrize("data", [b"\x00", b"", b"\x01\x00"])
def test_rejects_wrong_data_byte(env, data):
    assert run(env, env.message_b(data=data)) is Verdict.REJECTED


def test_rejects_owner_mismatch(env):
    assert run(env, env.message_b(owner=Keypair().pubkey())) is Verdict.REJECTED


def test_rejects_ata_not_derived(env):
    assert run(env, env.message_b(ata=Keypair().pubkey())) is Verdict.REJECTED


def test_rejects_other_mint(env):
    assert run(env, env.message_b(create_mint=Keypair().pubkey())) is Verdict.REJECTED


@pytest.mark.parametrize("field", ["payer", "system", "token_program"])
def test_rejects_wrong_create_accounts(env, field):
    assert run(env, env.message_b(**{field: Keypair().pubkey()})) is Verdict.REJECTED


def test_rejects_wallet_off_curve(env):
    off_curve, _ = Pubkey.find_program_address([b"x"], Pubkey.default())
    assert not off_curve.is_on_curve()
    assert run(env, env.message_b(off_curve)) is Verdict.REJECTED


def test_rejects_wallet_is_signer(env):
    assert run(env, env.message_b(env.signer.pubkey())) is Verdict.REJECTED


@pytest.mark.parametrize(
    "reserved", ["mint", "token_program", "ata_program", "system", "own_source"]
)
def test_rejects_reserved_wallet(env, reserved):
    wallet = {
        "mint": env.mint,
        "token_program": TOKEN_2022,
        "ata_program": ATA_PROGRAM,
        "system": SYSTEM,
        "own_source": env.ata,
    }[reserved]
    assert run(env, env.message_b(wallet)) is Verdict.REJECTED


@pytest.mark.parametrize("target", ["other_wallet_ata", "arbitrary_account"])
def test_rejects_transfer_not_to_created_ata(env, target):
    other = Keypair().pubkey()
    dest = {
        "other_wallet_ata": get_associated_token_address(other, env.mint, TOKEN_2022),
        "arbitrary_account": other,
    }[target]
    assert run(env, env.message_b(dest=dest)) is Verdict.REJECTED


def test_destination_cap_cannot_be_dodged_with_fresh_wallets(env):
    env.write_config(max_amount_per_recipient_24h=100)
    dest = get_associated_token_address(Keypair().pubkey(), env.mint, TOKEN_2022)
    for i in range(2):
        raw = env.message_b(dest=dest)
        assert run(env, raw, f"{i}.json", f"{i}.out") is Verdict.REJECTED


def test_one_recipient_key_across_shapes(env):
    wallet = Keypair().pubkey()
    ata = get_associated_token_address(wallet, env.mint, TOKEN_2022)
    env.write_config(max_amount_per_recipient_24h=1000)
    assert run(env, env.message(1000, ata), "a.json", "a.out") is Verdict.SIGNED
    assert run(env, env.message_b(wallet, 1), "b.json", "b.out") is Verdict.LIMIT_EXCEEDED


def test_rejects_extra_instruction(env):
    assert run(env, env.message_b(extra=True)) is Verdict.REJECTED


def test_rejects_swapped_accounts(env):
    assert run(env, env.message_b(swap=True)) is Verdict.REJECTED
