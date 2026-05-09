"""DB-driven case loader for hosted agent evals.

Loads system_prompts from the platform DB so tests always reflect the live
configuration without manual sync. Falls back to the hardcoded fixtures in
``cases.py`` when ``AGENTSPORE_DB_DSN`` is not set.

Usage::

    from tests.evals.from_db import load_agent_specs
    specs = load_agent_specs()  # returns tuple[AgentSpec, ...]

The function is synchronous; it uses psycopg2 (bundled with the runner's
test container) or falls back gracefully when the driver is absent.
"""
from __future__ import annotations

import os
from typing import TYPE_CHECKING

from .cases import AgentSpec, ALL_AGENTS

if TYPE_CHECKING:
    pass

_AGENT_HANDLES: tuple[str, ...] = ("contentagent", "platformanalyst", "qaagent")

_QUERY = """
    SELECT handle, name, system_prompt, model
    FROM agents
    WHERE handle = ANY(%(handles)s)
      AND is_hosted = TRUE
"""


def load_agent_specs() -> tuple[AgentSpec, ...]:
    """Return agent specs from DB when AGENTSPORE_DB_DSN is set, else hardcoded.

    The caller should not depend on ordering; iterate by ``.handle``.
    """
    dsn = os.environ.get("AGENTSPORE_DB_DSN", "")
    if not dsn:
        return ALL_AGENTS

    try:
        import psycopg2  # type: ignore[import-untyped]
    except ImportError:
        return ALL_AGENTS

    try:
        conn = psycopg2.connect(dsn)
        try:
            with conn.cursor() as cur:
                cur.execute(_QUERY, {"handles": list(_AGENT_HANDLES)})
                rows = cur.fetchall()
        finally:
            conn.close()
    except Exception:
        return ALL_AGENTS

    if not rows:
        return ALL_AGENTS

    handle_index = {spec.handle: spec for spec in ALL_AGENTS}
    specs: list[AgentSpec] = []
    for row in rows:
        handle, name, system_prompt, model = row
        fallback = handle_index.get(handle)
        specs.append(
            AgentSpec(
                name=name or (fallback.name if fallback else handle),
                handle=handle,
                system_prompt=system_prompt,
                model=model or (fallback.model if fallback else "openai:gpt-oss-120b:free"),
            )
        )
    return tuple(specs) if specs else ALL_AGENTS
