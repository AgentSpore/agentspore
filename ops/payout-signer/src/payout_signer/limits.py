"""Pure evaluation of a transfer against the rolling-window usage."""

from dataclasses import dataclass

from payout_signer.config import Config
from payout_signer.ledger import Usage


@dataclass(frozen=True)
class Transfer:
    recipient: str
    amount: int


def within_limits(config: Config, usage: Usage, transfer: Transfer) -> bool:
    """True only if every one of the four limits still holds after this transfer."""
    recipient_used = usage.per_recipient.get(transfer.recipient, 0)
    return (
        transfer.amount <= config.max_amount_per_transfer
        and recipient_used + transfer.amount <= config.max_amount_per_recipient_24h
        and usage.total + transfer.amount <= config.max_amount_total_24h
        and usage.transfers + 1 <= config.max_transfers_24h
    )
