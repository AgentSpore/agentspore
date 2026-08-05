"""One-shot backfill of contender ratings (V73), run by hand against prod.

Every auto-battle before V73 settled with no rating to move, so the columns the
migration adds open at their defaults and the leaderboard would show a roster of
identical 1200s that had in fact fought each other dozens of times. This replays
those battles in ``completed_at`` order through the SAME ``apply_battle_result``
the runner uses, so the seeded ratings are the ones the history justifies rather
than a second, hand-written approximation of the maths.

Idempotent: it resets every contender to the column defaults before replaying,
so running it twice produces the same numbers as running it once. Deliberately
NOT wired into startup — a rating that silently recomputes itself on every deploy
is a rating nobody can reason about.

    cd backend && uv run python -m scripts.backfill_contender_elo
"""

from __future__ import annotations

import asyncio

from sqlalchemy import text

from app.core.database import async_session_maker
from app.core.rating import DEFAULT_ELO, apply_battle_result
from app.schemas.battles import Winner

_RESET_SQL = """
UPDATE battle_contenders SET elo = 1200, wins = 0, losses = 0, ties = 0
"""

# Decided contender-vs-contender battles only. A void, a no-contest and a
# no-quorum battle all leave winner NULL or a judging_stop_reason, and none of
# them says anything about either contender.
_HISTORY_SQL = """
SELECT contender_a_id, contender_b_id, winner
  FROM battles
 WHERE status = 'completed'
   AND contender_a_id IS NOT NULL
   AND contender_b_id IS NOT NULL
   AND winner IS NOT NULL
   AND judging_stop_reason IS NULL
 ORDER BY completed_at, id
"""


class ContenderRecord:
    """One contender's replayed rating and counters."""

    def __init__(self, elo: int = DEFAULT_ELO) -> None:
        self.elo = elo
        self.wins = 0
        self.losses = 0
        self.ties = 0

    def record(self, elo: int, outcome: str) -> None:
        self.elo = elo
        if outcome == "win":
            self.wins += 1
        elif outcome == "loss":
            self.losses += 1
        else:
            self.ties += 1


def replay(battles: list[dict]) -> dict[str, ContenderRecord]:
    """Fold the battle history into a rating per contender."""
    records: dict[str, ContenderRecord] = {}
    for battle in battles:
        a_id = str(battle["contender_a_id"])
        b_id = str(battle["contender_b_id"])
        a = records.setdefault(a_id, ContenderRecord())
        b = records.setdefault(b_id, ContenderRecord())
        winner = Winner(battle["winner"])
        change = apply_battle_result(a.elo, b.elo, winner)
        if not change.applied:
            continue
        a.record(change.a_after, _outcome(winner, is_a=True))
        b.record(change.b_after, _outcome(winner, is_a=False))
    return records


def _outcome(winner: Winner, *, is_a: bool) -> str:
    if winner is Winner.TIE:
        return "tie"
    won = (winner is Winner.A) if is_a else (winner is Winner.B)
    return "win" if won else "loss"


async def main() -> None:
    async with async_session_maker() as session:
        await session.execute(text(_RESET_SQL))
        rows = [dict(r) for r in (await session.execute(text(_HISTORY_SQL))).mappings()]
        records = replay(rows)
        for contender_id, record in records.items():
            await session.execute(
                text(
                    """
                    UPDATE battle_contenders
                    SET elo = :elo, wins = :wins, losses = :losses, ties = :ties
                    WHERE id = CAST(:id AS UUID)
                    """
                ),
                {
                    "id": contender_id,
                    "elo": record.elo,
                    "wins": record.wins,
                    "losses": record.losses,
                    "ties": record.ties,
                },
            )
        await session.commit()

    print(f"replayed {len(rows)} decided contender battles")
    for contender_id, record in sorted(
        records.items(), key=lambda kv: -kv[1].elo
    ):
        print(
            f"  {contender_id}  elo={record.elo}  "
            f"W/L/T={record.wins}/{record.losses}/{record.ties}"
        )
    if not records:
        print("  no contender battles to replay — every contender stays at 1200")


if __name__ == "__main__":
    asyncio.run(main())
