import pytest
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.token.associated import get_associated_token_address

from payout_signer.signer import sign_request
from payout_signer.verdict import Verdict

from .conftest import TOKEN_2022


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


def test_rejects_wallet_is_mint(env):
    wallet = env.mint
    assert get_associated_token_address(wallet, env.mint, TOKEN_2022)
    assert run(env, env.message_b(wallet)) is Verdict.REJECTED


def test_rejects_extra_instruction(env):
    assert run(env, env.message_b(extra=True)) is Verdict.REJECTED


def test_rejects_swapped_accounts(env):
    assert run(env, env.message_b(swap=True)) is Verdict.REJECTED
