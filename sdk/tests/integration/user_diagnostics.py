"""Serialize bounded exception metadata without messages, locals or remote values."""

import re
import traceback
from builtins import BaseExceptionGroup
from pathlib import Path
from typing import Any


def _frames(error: BaseException) -> list[str]:
    return [
        f"{Path(frame.filename).name}:{frame.lineno}:{frame.name}"[:128]
        for frame in traceback.extract_tb(error.__traceback__)[-8:]
    ]


def _metadata(error: BaseException) -> dict[str, Any]:
    result = {"type": type(error).__name__[:80]}
    state = getattr(error, "sqlstate", None)
    if isinstance(state, str) and re.fullmatch(r"[0-9A-Z]{5}", state):
        result["sqlstate"] = state
    return result


def safe_exception(error: BaseException) -> dict[str, Any]:
    """Prioritize group leaves; return bounded public type/source metadata only."""
    pending = [error]
    seen: set[int] = set()
    causes, leaves = [], []
    for _ in range(16):
        if not pending:
            break
        current = pending.pop(0)
        if id(current) in seen:
            continue
        seen.add(id(current))
        causes.append(_metadata(current))
        if isinstance(current, BaseExceptionGroup):
            pending[0:0] = list(current.exceptions)
        elif len(leaves) < 8:
            leaves.append({**_metadata(current), "frames": _frames(current)})
        for linked in (current.__cause__, current.__context__, getattr(current, "orig", None)):
            if isinstance(linked, BaseException):
                pending.append(linked)
    return {
        "exception_type": type(error).__name__[:80],
        "frames": _frames(error),
        "causes": causes,
        "leaves": leaves,
        "truncated": bool(pending),
    }


def safe_dm_checks(reply: dict[str, Any], original: dict[str, Any], sender: dict[str, Any]) -> dict[str, bool]:
    """Require thread linkage, immutable sender ID and the documented display fallback."""
    return {
        "reply_link_matches": reply.get("reply_to_dm_id") == original["id"],
        "sender_id_matches": reply.get("from_id") == sender["agent_id"],
        "sender_label_allowed": reply.get("from") in {sender["handle"], sender["agent_id"]},
    }


def safe_route(path: str) -> str:
    """Return only constant route templates, never handles, IDs or query parameters."""
    allowed = frozenset(
        "api v1 auth register login verify-email me agents users external-agents projects tasks chat dm review messages".split()
    )
    allowed = allowed | {""}
    return "/".join(part if part in allowed else "{resource}" for part in path.partition("?")[0].split("/"))
