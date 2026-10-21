#!/usr/bin/env python3
"""Replay a reviewed offline manifest; default rollback, never print database data."""

import argparse
import asyncio
import json
import sys
from pathlib import Path

from app.core.database import async_session_maker, engine
from app.schemas.public_recovery import RecoveryManifest
from app.services.public_recovery_service import recover_public_catalog


async def run(args: argparse.Namespace) -> dict:
    """Bound the import transaction and expose aggregate insert counts only."""
    try:
        if args.manifest.stat().st_size > 2 * 1024 * 1024:
            raise ValueError("Manifest exceeds limit.")
        manifest = RecoveryManifest.model_validate_json(args.manifest.read_bytes())
        async with asyncio.timeout(30), async_session_maker() as db, db.begin():
            counts = await recover_public_catalog(db, manifest)
            if not args.apply:
                await db.rollback()
        return {"status": "applied" if args.apply else "dry_run_rolled_back", **counts}
    finally:
        await engine.dispose()


def main() -> int:
    """Require explicit application; suppress exception bodies and configuration."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--apply", action="store_true", help="Commit instead of rolling back.")
    args = parser.parse_args()
    try:
        result = asyncio.run(run(args))
    except Exception:
        sys.stderr.write("Recovery failed; no exception details are printed.\n")
        return 1
    sys.stdout.write(json.dumps(result) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
