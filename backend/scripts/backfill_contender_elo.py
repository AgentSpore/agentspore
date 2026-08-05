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

The whole run is ONE transaction that takes an EXCLUSIVE lock on the roster
first, because the matchmaker keeps settling battles while this runs: without
the lock a rating written between the reset and the replay is silently
overwritten by numbers computed from a snapshot that predates it.

    cd backend && uv run python -m scripts.backfill_contender_elo --dry-run
    cd backend && uv run python -m scripts.backfill_contender_elo
"""

from __future__ import annotations

import argparse
import asyncio

from loguru import logger

from app.core.database import async_session_maker
from app.core.rating import DEFAULT_ELO, apply_battle_result
from app.repositories.battle_repo import BattleRepository
from app.schemas.battles import Winner


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


def _report(records: dict[str, ContenderRecord], battle_count: int) -> None:
    logger.info("replayed {} decided contender battles", battle_count)
    for contender_id, record in sorted(records.items(), key=lambda kv: -kv[1].elo):
        logger.info(
            "  {}  elo={}  W/L/T={}/{}/{}",
            contender_id, record.elo, record.wins, record.losses, record.ties,
        )
    if not records:
        logger.info("  nothing to replay — every contender stays at {}", DEFAULT_ELO)


async def backfill(*, dry_run: bool) -> dict[str, ContenderRecord]:
    """Reset, replay and persist, or roll back when ``dry_run``."""
    async with async_session_maker() as session:
        repo = BattleRepository(session)
        await repo.lock_and_reset_contender_ratings()
        battles = await repo.list_decided_contender_battles()
        records = replay(battles)
        for contender_id, record in records.items():
            await repo.set_contender_rating(
                contender_id, record.elo, record.wins, record.losses, record.ties
            )
        _report(records, len(battles))
        if dry_run:
            await session.rollback()
            logger.warning("dry run — rolled back, nothing was written")
        else:
            await session.commit()
            logger.info("committed")
        return records


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the resulting ratings and roll back without writing",
    )
    asyncio.run(backfill(dry_run=parser.parse_args().dry_run))
