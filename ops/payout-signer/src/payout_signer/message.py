"""Accept exactly one Token-2022 TransferChecked from the signer's own associated account."""

from solders.hash import Hash
from solders.instruction import AccountMeta, Instruction
from solders.message import Message
from solders.pubkey import Pubkey
from solders.token.associated import get_associated_token_address

from payout_signer.limits import Transfer

TOKEN_2022 = Pubkey.from_string("TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb")
TRANSFER_CHECKED = 12


class RejectedMessageError(Exception):
    """The message is not the single permitted transfer."""


def validate_message(raw: bytes, signer: Pubkey, mint: Pubkey, decimals: int) -> Transfer:
    """Parse natively, then require the bytes to equal the one message we would build ourselves."""
    try:
        message = Message.from_bytes(raw)
        keys = message.account_keys
        (instruction,) = message.instructions
        source, mint_key, destination, authority = (keys[i] for i in instruction.accounts)
        data = instruction.data
        amount = int.from_bytes(data[1:9], "little")
        expected = _expected_message(
            (signer, mint, decimals, destination, amount), message.recent_blockhash
        )
        if len(instruction.accounts) != 4 or keys[instruction.program_id_index] != TOKEN_2022:
            raise RejectedMessageError
    except (ValueError, TypeError, IndexError):
        raise RejectedMessageError from None
    own_source = get_associated_token_address(signer, mint, TOKEN_2022)
    exact = (source, mint_key, authority) == (own_source, mint, signer)
    if not exact or amount < 1 or bytes(expected) != raw:
        raise RejectedMessageError
    return Transfer(recipient=str(destination), amount=amount)


def _expected_message(fields: tuple, blockhash: Hash) -> Message:
    signer, mint, decimals, destination, amount = fields
    data = bytes([TRANSFER_CHECKED]) + amount.to_bytes(8, "little") + bytes([decimals])
    metas = [
        AccountMeta(get_associated_token_address(signer, mint, TOKEN_2022), False, True),
        AccountMeta(mint, False, False),
        AccountMeta(destination, False, True),
        AccountMeta(signer, True, False),
    ]
    return Message.new_with_blockhash([Instruction(TOKEN_2022, data, metas)], signer, blockhash)
