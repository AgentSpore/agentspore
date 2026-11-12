"""Receipt reconciliation of payout shape A (transfer) and shape B (create ATA, transfer)."""

import copy
import os

import base58
import pytest
from pydantic import AnyHttpUrl
from solders.hash import Hash
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.signature import Signature
from solders.token.associated import get_associated_token_address

from app.schemas.payout_delivery import ReceiptIntent
from app.services.payout_delivery_service import receipt_verdict
from app.services.payout_transaction import build_payout_message

TOKEN_2022 = Pubkey.from_string("TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb")
SYSTEM = "11111111111111111111111111111111"
RENT = 1_513_840
FEE = 5_000
SIGNATURE = str(Signature.from_bytes(bytes([7]) * 64))


@pytest.fixture
def intent():
    payer, mint, recipient = Keypair().pubkey(), Pubkey.new_unique(), Keypair().pubkey()
    return ReceiptIntent(
        genesis_hash=str(Hash.default()),
        mint=str(mint),
        program="TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb",
        source=str(get_associated_token_address(payer, mint, TOKEN_2022)),
        destination=str(get_associated_token_address(recipient, mint, TOKEN_2022)),
        authority=str(payer),
        fee_payer=str(payer),
        recipient=str(recipient),
        decimals=6,
        raw_amount="1500000",
        signature=SIGNATURE,
        rpc_endpoint=AnyHttpUrl("https://rpc.invalid"),
    )


def receipt_of(intent, create, existing=False):
    """Finalized legacy getTransaction (json encoding) for the compiled message."""
    message = build_payout_message(intent, str(Hash.from_bytes(os.urandom(32))), create)
    keys = [str(key) for key in message.account_keys]
    header = message.header
    rent = RENT if create and not existing else 0
    pre = [10_000_000] * len(keys)
    post = list(pre)
    dest = keys.index(intent.destination)
    if create and not existing:
        pre[dest] = 0
    post[0], post[dest] = pre[0] - FEE - rent, pre[dest] + rent
    if not create:
        post[0] = pre[0] - FEE

    def balance(account, owner, amount):
        return {
            "accountIndex": keys.index(account),
            "mint": intent.mint,
            "programId": intent.program,
            "owner": owner,
            "uiTokenAmount": {"amount": amount, "decimals": 6},
        }

    source = balance(intent.source, intent.authority, "3000000")
    pre_tokens = (
        [source]
        if create and not existing
        else [source, balance(intent.destination, intent.recipient, "0")]
    )
    post_tokens = [
        balance(intent.source, intent.authority, "1500000"),
        balance(intent.destination, intent.recipient, "1500000"),
    ]
    inner = []
    if create and not existing:
        inner = [
            {
                "index": 0,
                "instructions": [
                    {
                        "programIdIndex": keys.index(program),
                        "accounts": [0, dest],
                        "data": "3Bxs4h24hBtQy9rw",
                    }
                    for program in (SYSTEM, intent.program, SYSTEM)
                ],
            }
        ]
    return {
        "slot": 9,
        "version": "legacy",
        "meta": {
            "err": None,
            "fee": FEE,
            "innerInstructions": inner,
            "preBalances": pre,
            "postBalances": post,
            "preTokenBalances": pre_tokens,
            "postTokenBalances": post_tokens,
        },
        "transaction": {
            "signatures": [SIGNATURE],
            "message": {
                "accountKeys": keys,
                "header": {
                    "numRequiredSignatures": header.num_required_signatures,
                    "numReadonlySignedAccounts": header.num_readonly_signed_accounts,
                    "numReadonlyUnsignedAccounts": header.num_readonly_unsigned_accounts,
                },
                "recentBlockhash": str(message.recent_blockhash),
                "instructions": [
                    {
                        "programIdIndex": ix.program_id_index,
                        "accounts": list(ix.accounts),
                        "data": base58.b58encode(ix.data).decode(),
                    }
                    for ix in message.instructions
                ],
            },
        },
    }


def test_receipt_a_matching(intent):
    assert receipt_verdict(receipt_of(intent, create=False), intent) == "matching"


def test_receipt_b_created_matching(intent):
    receipt = receipt_of(intent, create=True)
    assert receipt["meta"]["innerInstructions"] and len(receipt["meta"]["preTokenBalances"]) == 1
    assert receipt_verdict(receipt, intent) == "matching"


def test_receipt_b_existing_no_inner_matching(intent):
    receipt = receipt_of(intent, create=True, existing=True)
    assert receipt["meta"]["innerInstructions"] == []
    assert receipt_verdict(receipt, intent) == "matching"


def test_receipt_b_extra_lamport_delta_unsupported(intent):
    receipt = receipt_of(intent, create=True)
    receipt["meta"]["postBalances"][2] += 1
    assert receipt_verdict(receipt, intent) == "unsupported"


def test_receipt_b_payer_pays_more_than_destination_gets_unsupported(intent):
    receipt = receipt_of(intent, create=True)
    receipt["meta"]["postBalances"][0] -= 1
    assert receipt_verdict(receipt, intent) == "unsupported"


def test_receipt_b_wrong_owner_unsupported(intent):
    receipt = receipt_of(intent, create=True)
    receipt["meta"]["postTokenBalances"][1]["owner"] = str(Keypair().pubkey())
    assert receipt_verdict(receipt, intent) == "unsupported"


def test_receipt_a_with_inner_unsupported(intent):
    receipt = receipt_of(intent, create=False)
    receipt["meta"]["innerInstructions"] = receipt_of(intent, create=True)["meta"][
        "innerInstructions"
    ]
    assert receipt_verdict(receipt, intent) == "unsupported"


def test_receipt_b_unknown_inner_program_unsupported(intent):
    receipt = receipt_of(intent, create=True)
    keys = receipt["transaction"]["message"]["accountKeys"]
    receipt["meta"]["innerInstructions"][0]["instructions"][1]["programIdIndex"] = keys.index(
        "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL"
    )
    assert receipt_verdict(receipt, intent) == "unsupported"


@pytest.mark.parametrize("mutation", ["swap", "drop_create", "three", "data"])
def test_receipt_b_instruction_shape_unsupported(intent, mutation):
    receipt = receipt_of(intent, create=True, existing=True)
    instructions = receipt["transaction"]["message"]["instructions"]
    if mutation == "swap":
        instructions.reverse()
    elif mutation == "drop_create":
        instructions.pop(0)
        receipt = copy.deepcopy(receipt)
    elif mutation == "three":
        instructions.append(copy.deepcopy(instructions[1]))
    else:
        instructions[0]["data"] = base58.b58encode(b"\x00").decode()
    assert receipt_verdict(receipt, intent) == "unsupported"


def test_receipt_b_missing_post_destination_balance_unresolved(intent):
    receipt = receipt_of(intent, create=True)
    receipt["meta"]["postTokenBalances"].pop()
    with pytest.raises(ValueError):
        receipt_verdict(receipt, intent)
