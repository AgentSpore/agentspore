"""PayoutRepository — data access layer for Solana wallets and $ASPORE payouts."""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class PayoutRepository:
    """All database operations for Solana wallets and token payouts."""

    def __init__(self, db: AsyncSession):
        self.db = db

    # ── Solana Wallet ───────────────────────────────────────────────────

    async def check_solana_wallet_uniqueness(self, solana_wallet: str, user_id: str) -> bool:
        row = await self.db.execute(
            text("SELECT id FROM users WHERE solana_wallet = :w AND id != :uid"),
            {"w": solana_wallet, "uid": user_id},
        )
        return row.scalar_one_or_none() is not None

    async def update_solana_wallet(self, user_id: str, solana_wallet: str | None) -> None:
        await self.db.execute(
            text("UPDATE users SET solana_wallet = :w, updated_at = NOW() WHERE id = :uid"),
            {"w": solana_wallet, "uid": user_id},
        )

    async def get_user_solana_wallet(self, user_id: str) -> str | None:
        row = await self.db.execute(
            text("SELECT solana_wallet FROM users WHERE id = :uid"),
            {"uid": user_id},
        )
        return row.scalar_one_or_none()

    # ── Contribution Points Aggregation ─────────────────────────────────

    async def get_user_contribution_summary(self, period_start: str, period_end: str) -> list[dict]:
        """
        Aggregate contribution points per user for the given period.
        Only users with a linked solana_wallet are included.
        """
        result = await self.db.execute(
            text("""
                SELECT
                    u.id AS user_id,
                    u.name,
                    u.email,
                    u.solana_wallet,
                    COALESCE(pc_points.total, 0) AS contributor_points,
                    COALESCE(pm_points.total, 0) AS governance_points,
                    COALESCE(tt_points.total, 0) AS platform_points,
                    (COALESCE(pc_points.total, 0) +
                     COALESCE(pm_points.total, 0) +
                     COALESCE(tt_points.total, 0)) AS total_points
                FROM users u
                LEFT JOIN LATERAL (
                    SELECT SUM(pc.contribution_points) AS total
                    FROM project_contributors pc
                    JOIN agents a ON a.id = pc.agent_id
                    WHERE a.owner_user_id = u.id
                ) pc_points ON TRUE
                LEFT JOIN LATERAL (
                    SELECT SUM(pm.contribution_points) AS total
                    FROM project_members pm
                    WHERE pm.user_id = u.id
                ) pm_points ON TRUE
                LEFT JOIN LATERAL (
                    SELECT SUM(tt.amount) AS total
                    FROM token_transactions tt
                    WHERE tt.user_id = u.id
                      AND tt.created_at >= :period_start::DATE
                      AND tt.created_at < :period_end::DATE
                ) tt_points ON TRUE
                WHERE u.solana_wallet IS NOT NULL
                  AND (COALESCE(pc_points.total, 0) +
                       COALESCE(pm_points.total, 0) +
                       COALESCE(tt_points.total, 0)) > 0
                ORDER BY total_points DESC
            """),
            {"period_start": period_start, "period_end": period_end},
        )
        return [dict(row) for row in result.mappings()]

    # ── Payout Records ──────────────────────────────────────────────────

    async def insert_payout(
        self, *,
        user_id: str,
        solana_wallet: str,
        amount: int,
        contribution_points: int,
        pool_total: int,
        period_start: str,
        period_end: str,
    ) -> dict:
        result = await self.db.execute(
            text("""
                INSERT INTO token_payouts
                    (user_id, solana_wallet, amount, contribution_points,
                     pool_total, period_start, period_end, status)
                VALUES
                    (:user_id, :wallet, :amount, :points,
                     :pool, :ps, :pe, 'pending')
                RETURNING id, status, created_at
            """),
            {
                "user_id": user_id, "wallet": solana_wallet,
                "amount": amount, "points": contribution_points,
                "pool": pool_total, "ps": period_start, "pe": period_end,
            },
        )
        return dict(result.mappings().first())

    async def update_payout_status(
        self, payout_id: str, *,
        status: str,
        tx_signature: str | None = None,
        error_message: str | None = None,
    ) -> None:
        ts_field = ""
        if status == "sent":
            ts_field = ", sent_at = NOW()"
        elif status == "confirmed":
            ts_field = ", confirmed_at = NOW()"

        await self.db.execute(
            text(f"""
                UPDATE token_payouts
                SET status = :status, tx_signature = :tx, error_message = :err{ts_field}
                WHERE id = :id
            """),
            {"id": payout_id, "status": status, "tx": tx_signature, "err": error_message},
        )

    async def get_payouts_by_period(self, period_start: str, period_end: str) -> list[dict]:
        result = await self.db.execute(
            text("""
                SELECT tp.*, u.name AS user_name, u.email
                FROM token_payouts tp
                JOIN users u ON u.id = tp.user_id
                WHERE tp.period_start = :ps AND tp.period_end = :pe
                ORDER BY tp.amount DESC
            """),
            {"ps": period_start, "pe": period_end},
        )
        return [dict(row) for row in result.mappings()]

    async def get_pending_payouts(self) -> list[dict]:
        result = await self.db.execute(
            text("""
                SELECT tp.*, u.name AS user_name, u.email
                FROM token_payouts tp
                JOIN users u ON u.id = tp.user_id
                WHERE tp.status = 'pending'
                ORDER BY tp.created_at
            """),
        )
        return [dict(row) for row in result.mappings()]

    async def get_user_payouts(self, user_id: str, limit: int = 20) -> list[dict]:
        result = await self.db.execute(
            text("""
                SELECT id, amount, contribution_points, pool_total,
                       period_start, period_end, tx_signature, status,
                       created_at, sent_at, confirmed_at
                FROM token_payouts
                WHERE user_id = :uid
                ORDER BY created_at DESC
                LIMIT :limit
            """),
            {"uid": user_id, "limit": limit},
        )
        return [dict(row) for row in result.mappings()]

    # ── $ASPORE Balance ────────────────────────────────────────────────

    async def get_aspore_balance(self, user_id: str) -> int:
        row = await self.db.execute(
            text("SELECT aspore_balance FROM users WHERE id = :uid"),
            {"uid": user_id},
        )
        return row.scalar_one_or_none() or 0

    async def credit_aspore(self, user_id: str, amount: int) -> int:
        """Add $ASPORE to user balance. Returns new balance."""
        result = await self.db.execute(
            text("""
                UPDATE users SET aspore_balance = aspore_balance + :amount, updated_at = NOW()
                WHERE id = :uid
                RETURNING aspore_balance
            """),
            {"uid": user_id, "amount": amount},
        )
        return result.scalar_one()

    async def debit_aspore(self, user_id: str, amount: int) -> int:
        """Deduct $ASPORE from user balance. Returns new balance. Raises if insufficient."""
        result = await self.db.execute(
            text("""
                UPDATE users SET aspore_balance = aspore_balance - :amount, updated_at = NOW()
                WHERE id = :uid AND aspore_balance >= :amount
                RETURNING aspore_balance
            """),
            {"uid": user_id, "amount": amount},
        )
        row = result.scalar_one_or_none()
        if row is None:
            raise ValueError("Insufficient $ASPORE balance")
        return row

    # ── $ASPORE Transaction Log ─────────────────────────────────────────

    async def insert_aspore_tx(
        self, *,
        user_id: str,
        tx_type: str,
        amount: int,
        balance_after: int,
        solana_tx: str | None = None,
        reference_type: str | None = None,
        reference_id: str | None = None,
        note: str | None = None,
    ) -> dict:
        result = await self.db.execute(
            text("""
                INSERT INTO aspore_transactions
                    (user_id, tx_type, amount, balance_after, solana_tx,
                     reference_type, reference_id, note)
                VALUES
                    (:uid, :tx_type, :amount, :bal, :stx,
                     :ref_type, :ref_id, :note)
                RETURNING id, created_at
            """),
            {
                "uid": user_id, "tx_type": tx_type, "amount": amount,
                "bal": balance_after, "stx": solana_tx,
                "ref_type": reference_type, "ref_id": reference_id, "note": note,
            },
        )
        return dict(result.mappings().first())

    async def check_solana_tx_used(self, solana_tx: str) -> bool:
        """Check if a Solana tx signature was already used (prevent double-credit)."""
        row = await self.db.execute(
            text("SELECT id FROM aspore_transactions WHERE solana_tx = :tx"),
            {"tx": solana_tx},
        )
        return row.scalar_one_or_none() is not None

    async def get_aspore_transactions(self, user_id: str, limit: int = 50) -> list[dict]:
        result = await self.db.execute(
            text("""
                SELECT id, tx_type, amount, balance_after, solana_tx,
                       reference_type, reference_id, note, created_at
                FROM aspore_transactions
                WHERE user_id = :uid
                ORDER BY created_at DESC
                LIMIT :limit
            """),
            {"uid": user_id, "limit": limit},
        )
        return [dict(row) for row in result.mappings()]
