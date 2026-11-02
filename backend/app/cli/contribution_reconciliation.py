"""Trusted operator CLI for nonmonetary contribution reconciliation."""

import argparse
import asyncio
import json
import re
from pathlib import Path
from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError

from app.core.database import async_session_maker
from app.repositories.agent_repo import AgentRepository
from app.repositories.contribution_repo import ContributionRepository
from app.services.contribution_reconciliation import ContributionReconciliationService


async def run(args) -> dict:
    """Default to a rolled-back dry run; never fetch evidence or call a payout service."""
    async with async_session_maker() as db:
        repo = AgentRepository(db)
        try:
            if args.action == "list":
                return {"pending": await ContributionRepository(db).unresolved_contributions()}
            if args.action == "show":
                row = await ContributionRepository(db).contribution_entry(args.entry)
                if not row or row["action_type"] != "contribution_unresolved":
                    raise ValueError("contribution_pending_not_found")
                return {"entry": row}
            decision = json.loads(args.decision.read_text())
            result = await ContributionReconciliationService(repo).reconcile(
                args.entry, decision, args.apply
            )
            if args.apply:
                await db.commit()
            return {
                **result,
                "applied": args.apply,
                "evidence": "operator_attestation_not_automatically_verified",
            }
        finally:
            await db.rollback()


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Trusted operator only. Evidence URI/digest is an operator attestation, not automatic "
            "verification. No evidence URLs are fetched. Resolve defaults to dry-run. "
            "Cross-agent correction is unsupported: contribution_actor_mismatch retains pending "
            "credit for separately reviewed attribution correction. Decision JSON fields: decision "
            "(award|duplicate), attributed_agent_id, verified_commit_sha, verified_paths, "
            "verified_removed_paths, "
            "evidence_uri (HTTPS without credentials/query), evidence_sha256, operator_ref, "
            "duplicate_activity_id (null for award). No ASPORE payment or entitlement is created."
        )
    )
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("list")
    show = sub.add_parser("show")
    show.add_argument("entry", type=UUID)
    resolve = sub.add_parser("resolve")
    resolve.add_argument("entry", type=UUID)
    resolve.add_argument("--decision", type=Path, required=True, help="JSON operator decision file")
    resolve.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    try:
        result = asyncio.run(run(args))
    except (ValueError, OSError, SQLAlchemyError, RuntimeError) as exc:
        reason = (
            str(exc)
            if isinstance(exc, ValueError) and re.fullmatch(r"contribution_[a-z_]+", str(exc))
            else "reconciliation_failed"
        )
        print(json.dumps({"status": "failed", "reason": reason, "error_type": type(exc).__name__}))
        raise SystemExit(1) from None
    print(json.dumps(result, default=str))


if __name__ == "__main__":
    main()
