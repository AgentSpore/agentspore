"""Reject unsupported program metadata through the signed deposit HTTP boundary."""

import pytest
from solders.pubkey import Pubkey
from test_deposit_api import TOKEN2022_PROGRAM, TOKEN_PROGRAM

pytest_plugins = ("test_deposit_api",)


@pytest.mark.parametrize(
    "record",
    [(phase, index) for phase in ("preTokenBalances", "postTokenBalances") for index in (0, 1)],
)
@pytest.mark.parametrize("mode", ["missing", "null", "other_program", "foreign_program"])
async def test_balance_program_metadata_must_match_transfer(
    deposit_api, signed_deposit, deposit_transaction, record, mode
):
    phase, index = record
    balance = deposit_transaction["meta"][phase][index]
    if mode == "missing":
        balance.pop("programId")
    elif mode == "null":
        balance["programId"] = None
    elif mode == "other_program":
        balance["programId"] = (
            TOKEN2022_PROGRAM if balance["programId"] == TOKEN_PROGRAM else TOKEN_PROGRAM
        )
    else:
        balance["programId"] = "11111111111111111111111111111111"
    client, _, _ = deposit_api
    response = await client.post("/api/v1/users/me/aspore/deposit", json=signed_deposit)
    assert response.status_code == 400
    assert (await client.get("/api/v1/users/me/aspore")).json() == {"aspore_balance": 0}
    assert (await client.get("/api/v1/users/me/aspore/transactions")).json() == []


@pytest.mark.parametrize(
    "change", ["foreign_program", "fee_type", "fee_field", "delegate", "multisig", "hook"]
)
async def test_unsupported_transfer_program_or_extensions_do_not_credit(
    deposit_api, signed_deposit, deposit_transaction, change
):
    message = deposit_transaction["transaction"]["message"]
    instruction = message["instructions"][0]
    info = instruction["parsed"]["info"]
    if change == "foreign_program":
        program = "11111111111111111111111111111111"
        instruction["programId"] = program
        message["accountKeys"][3]["pubkey"] = program
        for phase in ("preTokenBalances", "postTokenBalances"):
            for record in deposit_transaction["meta"][phase]:
                record["programId"] = program
    elif change == "fee_type":
        instruction["parsed"]["type"] = "transferCheckedWithFee"
    elif change == "fee_field":
        info["feeAmount"] = "0"
    elif change == "delegate":
        info["authority"] = str(Pubkey.new_unique())
        message["accountKeys"].append({"pubkey": info["authority"], "signer": True})
    elif change == "multisig":
        info["multisigAuthority"] = info.pop("authority")
        info["signers"] = [info["multisigAuthority"]]
    else:
        deposit_transaction["meta"]["innerInstructions"] = [
            {"index": 0, "instructions": [{"programId": "11111111111111111111111111111111"}]}
        ]
    client, _, _ = deposit_api
    response = await client.post("/api/v1/users/me/aspore/deposit", json=signed_deposit)
    assert response.status_code == 400
    assert (await client.get("/api/v1/users/me/aspore")).json() == {"aspore_balance": 0}
    assert (await client.get("/api/v1/users/me/aspore/transactions")).json() == []
