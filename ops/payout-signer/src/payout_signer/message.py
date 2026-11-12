"""Accept one Token-2022 TransferChecked from the signer's own associated account.

The transfer may be preceded by one CreateIdempotent for the recipient's associated account.
"""

from solders.hash import Hash
from solders.instruction import AccountMeta, Instruction
from solders.message import Message
from solders.pubkey import Pubkey
from solders.token.associated import get_associated_token_address

from payout_signer.limits import Transfer

TOKEN_2022 = Pubkey.from_string("TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb")
SYSTEM_PROGRAM = Pubkey.default()
ATA_PROGRAM = Pubkey.from_string("ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL")
TRANSFER_CHECKED = 12
CREATE_IDEMPOTENT = b"\x01"


class RejectedMessageError(Exception):
    """The message is not the single permitted transfer."""


def validate_message(raw: bytes, signer: Pubkey, mint: Pubkey, decimals: int) -> Transfer:
    """Parse natively, then require the bytes to equal the one message we would build ourselves."""
    try:
        message = Message.from_bytes(raw)
        keys = message.account_keys
        *creates, instruction = message.instructions
        if len(creates) > 1:
            raise RejectedMessageError
        wallet = _created_wallet(creates[0], keys, signer, mint) if creates else None
        source, mint_key, destination, authority = (keys[i] for i in instruction.accounts)
        data = instruction.data
        amount = int.from_bytes(data[1:9], "little")
        if wallet is not None:  # the transfer must go to the account this message creates
            destination = get_associated_token_address(wallet, mint, TOKEN_2022)
        expected = _expected_message(
            (signer, mint, decimals, destination, amount, wallet), message.recent_blockhash
        )
        if len(instruction.accounts) != 4 or keys[instruction.program_id_index] != TOKEN_2022:
            raise RejectedMessageError
    except (ValueError, TypeError, IndexError):
        raise RejectedMessageError from None
    own_source = get_associated_token_address(signer, mint, TOKEN_2022)
    exact = (source, mint_key, authority) == (own_source, mint, signer)
    if not exact or amount < 1 or bytes(expected) != raw:
        raise RejectedMessageError
    return Transfer(recipient=str(destination), amount=amount, creates_ata=wallet is not None)


def _created_wallet(instruction, keys: list, signer: Pubkey, mint: Pubkey) -> Pubkey:
    """Owner of the account the create instruction makes; the byte comparison pins the rest."""
    if (
        keys[instruction.program_id_index] != ATA_PROGRAM
        or instruction.data != CREATE_IDEMPOTENT
        or len(instruction.accounts) != 6
    ):
        raise RejectedMessageError
    wallet = keys[instruction.accounts[2]]
    reserved = (signer, mint, TOKEN_2022, ATA_PROGRAM, SYSTEM_PROGRAM)
    if not wallet.is_on_curve() or wallet in reserved:
        raise RejectedMessageError
    return wallet


def _expected_message(fields: tuple, blockhash: Hash) -> Message:
    signer, mint, decimals, destination, amount, wallet = fields
    data = bytes([TRANSFER_CHECKED]) + amount.to_bytes(8, "little") + bytes([decimals])
    metas = [
        AccountMeta(get_associated_token_address(signer, mint, TOKEN_2022), False, True),
        AccountMeta(mint, False, False),
        AccountMeta(destination, False, True),
        AccountMeta(signer, True, False),
    ]
    instructions = [Instruction(TOKEN_2022, data, metas)]
    if wallet is not None:
        instructions.insert(0, _create_instruction(signer, mint, wallet))
    return Message.new_with_blockhash(instructions, signer, blockhash)


def _create_instruction(signer: Pubkey, mint: Pubkey, wallet: Pubkey) -> Instruction:
    metas = [
        AccountMeta(signer, True, True),
        AccountMeta(get_associated_token_address(wallet, mint, TOKEN_2022), False, True),
        AccountMeta(wallet, False, False),
        AccountMeta(mint, False, False),
        AccountMeta(SYSTEM_PROGRAM, False, False),
        AccountMeta(TOKEN_2022, False, False),
    ]
    return Instruction(ATA_PROGRAM, CREATE_IDEMPOTENT, metas)
