"""PayoutService — business logic for $ASPORE token payouts, deposits, and spending."""

import os
from uuid import UUID

import httpx
from fastapi import Depends
from loguru import logger
from solders.pubkey import Pubkey
from solders.signature import Signature
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.repositories.payout_repo import PayoutRepository
from app.schemas.ownership import (
    AsporeDepositRequest,
    DepositMessage,
    DepositParsedTransfer,
    DepositRpcResponse,
    DepositTokenAmount,
    DepositTokenBalance,
    DepositTransactionResult,
)

# Minimum payout threshold (avoid dust transactions)
MIN_PAYOUT_ASPORE = 1_000
ASPORE_TOKEN_MINT = os.getenv("ASPORE_TOKEN_MINT", "5ZkjEjfDAPuSg7pRxCRJsJuZ8FByRSyAgAA8SLMMpump")
TREASURY_WALLET = os.getenv("TREASURY_WALLET", "GsEqxS6g9Vj7FpnbT5pYspjyU9CYu93BsBeseYmiH8hm")
SOLANA_RPC_URL = os.getenv("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com")
ASPORE_DECIMALS = 6


class PayoutService:
    """$ASPORE: deposits, withdrawals, monthly payouts."""

    def __init__(self, db: AsyncSession):
        self.db = db
        self.repo = PayoutRepository(db)

    # ── Solana Wallet ───────────────────────────────────────────────────

    async def connect_solana_wallet(self, user_id: str, solana_wallet: str) -> None:
        if await self.repo.check_solana_wallet_uniqueness(solana_wallet, user_id):
            raise ValueError("Solana wallet already connected to another account")
        await self.repo.update_solana_wallet(user_id, solana_wallet)

    async def disconnect_solana_wallet(self, user_id: str) -> None:
        await self.repo.update_solana_wallet(user_id, None)

    # ── Deposit (verify on-chain tx → credit balance) ────────────────

    async def verify_and_credit_deposit(self, user_id: str, claim: AsporeDepositRequest) -> dict:
        """Verify wallet and transfer before entering the caller-owned DB transaction."""
        message = self.deposit_claim_message(user_id, claim)
        signature = Signature.from_string(claim.claim_signature)
        if not signature.verify(Pubkey.from_string(claim.solana_wallet), message):
            raise ValueError("Invalid deposit ownership proof")
        # Verify on Solana RPC
        amount = await self._verify_solana_transfer(claim)
        # Prevent double-credit
        await self.repo.lock_deposit_claim(claim.tx_signature)
        if await self.repo.check_solana_tx_used(claim.tx_signature):
            raise ValueError("Transaction already processed")
        # Credit balance
        new_balance = await self.repo.credit_deposit(user_id, amount)
        # Log transaction
        await self.repo.insert_aspore_tx(
            user_id=user_id,
            tx_type="deposit",
            amount=amount,
            balance_after=new_balance,
            solana_tx=claim.tx_signature,
            note=f"Deposit {amount} $ASPORE",
        )
        return {"amount": amount, "balance_after": new_balance}

    @staticmethod
    def deposit_claim_message(user_id: str, claim: AsporeDepositRequest) -> bytes:
        """Build the fixed UTF-8 authorization message without a trailing newline."""
        return (
            "AgentSpore ASPORE deposit claim v1\nnetwork:solana:mainnet-beta\n"
            f"user_id:{UUID(user_id)}\ntx_signature:{claim.tx_signature}\n"
            f"solana_wallet:{claim.solana_wallet}\nmint:{ASPORE_TOKEN_MINT}\n"
            f"treasury:{TREASURY_WALLET}"
        ).encode("utf-8")

    async def _verify_solana_transfer(self, claim: AsporeDepositRequest) -> int:
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.post(
                    SOLANA_RPC_URL,
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "getTransaction",
                        "params": [
                            claim.tx_signature,
                            {
                                "encoding": "jsonParsed",
                                "maxSupportedTransactionVersion": 0,
                                "commitment": "finalized",
                            },
                        ],
                    },
                )
                response.raise_for_status()
                envelope = DepositRpcResponse.model_validate(response.json())
            if envelope.id != 1:
                raise ValueError("Invalid RPC response identity")
            transaction = envelope.result
            if not transaction.transaction.signatures or (
                transaction.transaction.signatures[0] != claim.tx_signature
            ):
                raise ValueError("Invalid transaction identity")
            return self._attributed_amount(transaction, claim.solana_wallet)
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            raise ValueError("Unable to verify deposit transaction") from exc

    @staticmethod
    def _direct_transfer(message: DepositMessage) -> DepositParsedTransfer:
        transfers = []
        keys = {account.pubkey for account in message.account_keys}
        for instruction in message.instructions:
            if instruction.program_id not in keys:
                raise ValueError("Instruction program is not in account keys")
            if instruction.program_id == "ComputeBudget111111111111111111111111111111":
                if (
                    instruction.parsed is not None
                    or instruction.accounts != []
                    or not instruction.data
                ):
                    raise ValueError("Unsupported compute-budget instruction")
            elif instruction.program_id == "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA":
                if instruction.parsed is None:
                    raise ValueError("A parsed direct transfer is required")
                transfers.append(instruction.parsed)
            else:
                raise ValueError("Unsupported deposit program")
        if len(transfers) != 1:
            raise ValueError("Exactly one direct transfer is required")
        return transfers[0]

    @staticmethod
    def _raw_amount(amount: DepositTokenAmount) -> int:
        raw = int(amount.amount)
        if amount.decimals != ASPORE_DECIMALS or raw > (1 << 64) - 1:
            raise ValueError("Unsupported token amount")
        return raw

    @classmethod
    def _instruction_amount(cls, transfer: DepositParsedTransfer) -> int:
        info = transfer.info
        if transfer.type == "transferChecked":
            if (
                info.mint != ASPORE_TOKEN_MINT
                or info.token_amount is None
                or info.amount is not None
            ):
                raise ValueError("Invalid checked transfer")
            return cls._raw_amount(info.token_amount)
        if info.amount is None or info.token_amount is not None or info.mint is not None:
            raise ValueError("Invalid direct transfer")
        return cls._raw_amount(DepositTokenAmount(amount=info.amount, decimals=ASPORE_DECIMALS))

    @classmethod
    def _owned_balance(cls, balance: DepositTokenBalance, owner: str) -> int:
        if balance.mint != ASPORE_TOKEN_MINT or balance.owner != owner:
            raise ValueError("Token account ownership differs")
        return cls._raw_amount(balance.ui_token_amount)

    @classmethod
    def _attributed_amount(cls, result: DepositTransactionResult, wallet: str) -> int:
        message = result.transaction.message
        keys = [str(Pubkey.from_string(account.pubkey)) for account in message.account_keys]
        if len(keys) != len(set(keys)) or result.meta.inner_instructions:
            raise ValueError("Ambiguous transaction accounts or CPI")
        if wallet == TREASURY_WALLET or not any(
            account.pubkey == wallet and account.signer for account in message.account_keys
        ):
            raise ValueError("The source owner must sign the transaction")
        transfer = cls._direct_transfer(message)
        info = transfer.info
        if transfer.type == "transferChecked" and info.mint not in keys:
            raise ValueError("Transfer mint is not in account keys")
        if info.authority != wallet or info.source == info.destination:
            raise ValueError("Invalid transfer authority")
        source, destination = keys.index(info.source), keys.index(info.destination)
        expected = {source, destination}
        before = {record.account_index: record for record in result.meta.pre_token_balances}
        after = {record.account_index: record for record in result.meta.post_token_balances}
        if (
            set(before) != expected
            or set(after) != expected
            or (
                len(result.meta.pre_token_balances) != 2
                or len(result.meta.post_token_balances) != 2
            )
        ):
            raise ValueError("Existing source and treasury token accounts are required")
        debit = cls._owned_balance(before[source], wallet) - cls._owned_balance(
            after[source], wallet
        )
        credit = cls._owned_balance(after[destination], TREASURY_WALLET) - cls._owned_balance(
            before[destination],
            TREASURY_WALLET,
        )
        raw = cls._instruction_amount(transfer)
        scale = 10**ASPORE_DECIMALS
        if raw <= 0 or debit != raw or credit != raw or raw % scale:
            raise ValueError("Exact positive whole-token transfer required")
        return raw // scale

    # ── Balance & History ───────────────────────────────────────────────

    async def get_balance(self, user_id: str) -> int:
        return await self.repo.get_aspore_balance(user_id)

    async def get_transactions(self, user_id: str, limit: int = 50) -> list[dict]:
        return await self.repo.get_aspore_transactions(user_id, limit)

    async def get_user_payouts(self, user_id: str) -> list[dict]:
        return await self.repo.get_user_payouts(user_id)

    # ── Monthly Payout Calculation ──────────────────────────────────────

    async def calculate_payouts(
        self,
        *,
        pool_total: int,
        period_start: str,
        period_end: str,
    ) -> list[dict]:
        """
        Calculate payout amounts for all eligible users.
        Formula: user_payout = (user_points / total_points) * pool_total
        """
        users = await self.repo.get_user_contribution_summary(period_start, period_end)

        if not users:
            logger.info("No eligible users for payout period {} — {}", period_start, period_end)
            return []

        total_points = sum(u["total_points"] for u in users)
        if total_points == 0:
            return []

        payouts = []
        for u in users:
            amount = int((u["total_points"] / total_points) * pool_total)
            if amount < MIN_PAYOUT_ASPORE:
                continue
            payouts.append(
                {
                    "user_id": str(u["user_id"]),
                    "name": u["name"],
                    "email": u["email"],
                    "solana_wallet": u["solana_wallet"],
                    "contribution_points": u["total_points"],
                    "amount": amount,
                }
            )

        return payouts

    async def create_payout_records(
        self,
        *,
        payouts: list[dict],
        pool_total: int,
        period_start: str,
        period_end: str,
    ) -> list[dict]:
        """Insert payout records into DB with status=pending."""
        records = []
        for p in payouts:
            record = await self.repo.insert_payout(
                user_id=p["user_id"],
                solana_wallet=p["solana_wallet"],
                amount=p["amount"],
                contribution_points=p["contribution_points"],
                pool_total=pool_total,
                period_start=period_start,
                period_end=period_end,
            )
            records.append({**p, **record})
        return records

    async def mark_payout_sent(self, payout_id: str, tx_signature: str) -> None:
        await self.repo.update_payout_status(payout_id, status="sent", tx_signature=tx_signature)

    async def mark_payout_confirmed(self, payout_id: str) -> None:
        await self.repo.update_payout_status(payout_id, status="confirmed")

    async def mark_payout_failed(self, payout_id: str, error: str) -> None:
        await self.repo.update_payout_status(payout_id, status="failed", error_message=error)


def get_payout_service(db: AsyncSession = Depends(get_db)) -> PayoutService:
    return PayoutService(db)
