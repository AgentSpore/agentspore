"""Both public activity readers project future reconciliation metadata safely."""

import json
from uuid import uuid4

import pytest
import test_service_usage
from sqlalchemy import text

from app.repositories.activity_repo import get_activity_events
from app.repositories.agent_repo import AgentRepository

usage_pg = test_service_usage.usage_pg
usage_world = test_service_usage.usage_world
pytestmark = pytest.mark.integration
UNKNOWN = {"state": "unknown", "reason": "review_required", "policy": "unknown"}


@pytest.mark.parametrize(
    ("metadata", "expected"),
    [
        (
            {
                "state": "pending",
                "reason": "ambiguous_actor",
                "policy": "commit-paths-v1",
                "paths": ["synthetic-path-sentinel"],
                "actor": "synthetic-actor-sentinel",
            },
            {"state": "pending", "reason": "ambiguous_actor", "policy": "commit-paths-v1"},
        ),
        (
            {
                "state": "resolved",
                "reason": "legacy_short_identity",
                "policy": "commit-paths-v1",
                "resolution": {
                    "evidence": "synthetic-resolution-sentinel",
                    "paths": ["synthetic-path-sentinel"],
                },
            },
            {"state": "resolved", "reason": "legacy_short_identity", "policy": "commit-paths-v1"},
        ),
        (
            {"state": "pending", "reason": "incomplete_paths", "policy": "commit-paths-v1"},
            {"state": "pending", "reason": "incomplete_paths", "policy": "commit-paths-v1"},
        ),
        (
            {
                "state": "synthetic-state-sentinel",
                "reason": "synthetic-reason-sentinel",
                "policy": "synthetic-policy-sentinel",
            },
            UNKNOWN,
        ),
        (
            {"state": {"nested": "synthetic-nested-sentinel"}, "reason": [], "policy": False},
            UNKNOWN,
        ),
        ({}, UNKNOWN),
        (None, UNKNOWN),
        (["synthetic-array-sentinel"], UNKNOWN),
        ("synthetic-scalar-sentinel", UNKNOWN),
    ],
)
async def test_both_public_readers_limit_metadata_without_changing_stored_evidence(
    usage_world,
    metadata,
    expected,
):
    entry = uuid4()
    agent = usage_world.agents[1]
    async with usage_world.maker.begin() as db:
        await db.execute(
            text(
                "INSERT INTO agent_activity (id, agent_id, action_type, description, metadata) "
                "VALUES (:id, :agent, 'contribution_unresolved', 'Synthetic review', "
                "CAST(:metadata AS jsonb))"
            ),
            {"id": entry, "agent": agent, "metadata": json.dumps(metadata)},
        )
    async with usage_world.maker() as db:
        readers = [
            await get_activity_events(db, agent_id=str(agent)),
            await AgentRepository(db).get_github_activity(
                "aa.agent_id = :agent", {"agent": agent, "limit": 50}
            ),
        ]
        for rows in readers:
            public = next(row for row in rows if str(row["id"]) == str(entry))
            assert public["metadata"] == expected
            assert "sentinel" not in json.dumps(public["metadata"])
        stored = await db.scalar(
            text("SELECT metadata FROM agent_activity WHERE id=:id"), {"id": entry}
        )
        assert stored == metadata


async def test_other_activity_metadata_is_unchanged(usage_world):
    metadata = {"commit_sha": "a" * 40, "files_changed": 2, "nested": {"public": True}}
    agent = usage_world.agents[1]
    async with usage_world.maker.begin() as db:
        await AgentRepository(db).insert_activity(
            agent, "code_commit", "Synthetic commit", None, metadata
        )
    async with usage_world.maker() as db:
        for rows in (
            await get_activity_events(db, agent_id=str(agent)),
            await AgentRepository(db).get_github_activity(
                "aa.agent_id = :agent", {"agent": agent, "limit": 50}
            ),
        ):
            assert len(rows) == 1
            assert rows[0]["metadata"] == metadata
