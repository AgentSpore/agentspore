"""PayoutService — business logic for $ASPORE token payouts, deposits, and spending."""

import logging
import os

import httpx
from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.repositories.payout_repo import PayoutRepository

logger = logging.getLogger("payout_service")

# Minimum payout threshold (avoid dust transactions)
MIN_PAYOUT_ASPORE = 1_000
ASPORE_TOKEN_MINT = os.getenv("ASPORE_TOKEN_MINT", "5ZkjEjfDAPuSg7pRxCRJsJuZ8FByRSyAgAA8SLMMpump")
TREASURY_WALLET = os.getenv("TREASURY_WALLET", "GsEqxS6g9Vj7FpnbT5pYspjyU9CYu93BsBeseYmiH8hm")
SOLANA_RPC_URL = os.getenv("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com")
ASPORE_DECIMALS = 6


class PayoutService:
    """$ASPORE: deposits, spending (rentals), withdrawals, monthly payouts."""

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

    async def verify_and_credit_deposit(self, user_id: str, tx_signature: str) -> dict:
        """
        Verify a Solana tx that sent $ASPORE to treasury.
        If valid, credit user's aspore_balance.
        Returns {amount, balance_after}.
        """
        # Prevent double-credit
        if await self.repo.check_solana_tx_used(tx_signature):
            raise ValueError("Transaction already processed")

        # Verify on Solana RPC
        amount = await self._verify_solana_transfer(tx_signature)
        if amount <= 0:
            raise ValueError("No valid $ASPORE transfer to treasury found in this transaction")

        # Credit balance
        new_balance = await self.repo.credit_aspore(user_id, amount)

        # Log transaction
        await self.repo.insert_aspore_tx(
            user_id=user_id,
            tx_type="deposit",
            amount=amount,
            balance_after=new_balance,
            solana_tx=tx_signature,
            note=f"Deposit {amount} $ASPORE",
        )

        return {"amount": amount, "balance_after": new_balance}

    async def _verify_solana_transfer(self, tx_signature: str) -> int:
        """
        Call Solana RPC to verify tx transfers $ASPORE to treasury.
        Returns amount in UI units (not raw).
        """
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(SOLANA_RPC_URL, json={
                "jsonrpc": "2.0", "id": 1,
                "method": "getTransaction",
                "params": [tx_signature, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}],
            })
            data = resp.json()

        result = data.get("result")
        if not result:
            raise ValueError("Transaction not found or not confirmed")

        if result.get("meta", {}).get("err"):
            raise ValueError("Transaction failed on-chain")

        # Search for SPL token transfer to treasury
        instructions = result.get("transaction", {}).get("message", {}).get("instructions", [])
        inner = result.get("meta", {}).get("innerInstructions", [])
        all_instructions = list(instructions)
        for group in inner:
            all_instructions.extend(group.get("instructions", []))

        for ix in all_instructions:
            parsed = ix.get("parsed")
            if not parsed:
                continue
            ix_type = parsed.get("type", "")
            info = parsed.get("info", {})

            if ix_type in ("transfer", "transferChecked"):
                mint = info.get("mint", "")
                dest_owner = info.get("authority", "")
                destination = info.get("destination", "")

                # For transferChecked, amount is in tokenAmount
                if ix_type == "transferChecked":
                    token_amount = info.get("tokenAmount", {})
                    ui_amount = token_amount.get("uiAmount", 0)
                    mint = info.get("mint", "")
                else:
                    raw_amount = int(info.get("amount", 0))
                    ui_amount = raw_amount / (10 ** ASPORE_DECIMALS)

                if mint == ASPORE_TOKEN_MINT or not mint:
                    # Check if destination is treasury (need to resolve token account → owner)
                    # For simplicity, check post-balances for treasury
                    pass

        # Alternative: check token balance changes
        pre_balances = result.get("meta", {}).get("preTokenBalances", [])
        post_balances = result.get("meta", {}).get("postTokenBalances", [])

        treasury_received = 0
        for post in post_balances:
            if (post.get("owner") == TREASURY_WALLET and
                    post.get("mint") == ASPORE_TOKEN_MINT):
                post_amount = float(post.get("uiTokenAmount", {}).get("uiAmountString", "0"))
                # Find matching pre-balance
                pre_amount = 0
                for pre in pre_balances:
                    if (pre.get("owner") == TREASURY_WALLET and
                            pre.get("mint") == ASPORE_TOKEN_MINT and
                            pre.get("accountIndex") == post.get("accountIndex")):
                        pre_amount = float(pre.get("uiTokenAmount", {}).get("uiAmountString", "0"))
                        break
                treasury_received = post_amount - pre_amount

        if treasury_received <= 0:
            raise ValueError("No $ASPORE received by treasury in this transaction")

        return int(treasury_received)

    # ── Spend (rental payment) ──────────────────────────────────────────

    async def spend_for_rental(self, user_id: str, rental_id: str, amount: int) -> int:
        """Deduct $ASPORE for rental payment. Returns new balance."""
        new_balance = await self.repo.debit_aspore(user_id, amount)
        await self.repo.insert_aspore_tx(
            user_id=user_id,
            tx_type="rental_payment",
            amount=-amount,
            balance_after=new_balance,
            reference_type="rental",
            reference_id=rental_id,
            note=f"Rental payment: {amount} $ASPORE",
        )
        return new_balance

    async def try_refund_rental(self, user_id: str, rental_id: str) -> int | None:
        """Refund $ASPORE if this rental was paid with it. Returns refunded amount or None."""
        try:
            txs = await self.repo.get_aspore_transactions(user_id, limit=100)
        except Exception:
            return None
        for tx in txs:
            if (tx["tx_type"] == "rental_payment" and
                    str(tx.get("reference_id")) == rental_id and
                    tx["amount"] < 0):
                return await self.refund_rental(user_id, rental_id, abs(tx["amount"]))
        return None

    async def refund_rental(self, user_id: str, rental_id: str, amount: int) -> int:
        """Refund $ASPORE for cancelled rental. Returns new balance."""
        new_balance = await self.repo.credit_aspore(user_id, amount)
        await self.repo.insert_aspore_tx(
            user_id=user_id,
            tx_type="rental_refund",
            amount=amount,
            balance_after=new_balance,
            reference_type="rental",
            reference_id=rental_id,
            note=f"Rental refund: {amount} $ASPORE",
        )
        return new_balance

    # ── Balance & History ───────────────────────────────────────────────

    async def get_balance(self, user_id: str) -> int:
        return await self.repo.get_aspore_balance(user_id)

    async def get_transactions(self, user_id: str, limit: int = 50) -> list[dict]:
        return await self.repo.get_aspore_transactions(user_id, limit)

    async def get_user_payouts(self, user_id: str) -> list[dict]:
        return await self.repo.get_user_payouts(user_id)

    # ── Monthly Payout Calculation ──────────────────────────────────────

    async def calculate_payouts(
        self, *,
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
            logger.info("No eligible users for payout period %s — %s", period_start, period_end)
            return []

        total_points = sum(u["total_points"] for u in users)
        if total_points == 0:
            return []

        payouts = []
        for u in users:
            amount = int((u["total_points"] / total_points) * pool_total)
            if amount < MIN_PAYOUT_ASPORE:
                continue
            payouts.append({
                "user_id": str(u["user_id"]),
                "name": u["name"],
                "email": u["email"],
                "solana_wallet": u["solana_wallet"],
                "contribution_points": u["total_points"],
                "amount": amount,
            })

        return payouts

    async def create_payout_records(
        self, *,
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
