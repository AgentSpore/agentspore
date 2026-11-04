"""Verify one explicit payout intent from stdin; emit only a fixed JSON verdict."""

import json
import sys
from typing import TextIO

import httpx

from app.schemas.payout_delivery import ReceiptIntent
from app.services.payout_delivery_service import verify_receipt


def run(source: TextIO, destination: TextIO, transport: httpx.BaseTransport | None = None) -> int:
    """Own input validation, HTTP lifetime and machine-readable output."""
    try:
        payload = source.read(65537)
        intent = ReceiptIntent.model_validate_json(payload if len(payload) <= 65536 else "")
        with httpx.Client(timeout=10, transport=transport, trust_env=False) as client:
            verdict = verify_receipt(client, intent)
    except (ValueError, TypeError, OSError, RecursionError):
        verdict = "invalid_input"
    destination.write(json.dumps({"verdict": verdict}) + "\n")
    return {"matching": 0, "unresolved": 2, "unsupported": 3, "failed": 4, "invalid_input": 5}[
        verdict
    ]


if __name__ == "__main__":
    sys.exit(run(sys.stdin, sys.stdout))
