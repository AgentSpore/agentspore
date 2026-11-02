"""Commit identity and unresolved contribution persistence."""

import json

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.funnel_repo import FunnelRepository


class ContributionRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def record_commit_identities(
        self,
        agent_id,
        shas: list[str],
        project_id=None,
        description: str = "",
    ) -> set[str]:
        """Return accepted identities in the caller transaction.

        Preserve the existing global agent/SHA scope.
        """
        accepted = set()
        for sha in sorted({sha for sha in shas if sha}):
            if not sha:
                continue
            result = await self.db.execute(
                text("""
                    INSERT INTO agent_activity
                        (agent_id, project_id, action_type, description, metadata)
                    VALUES (
                        :agent_id, :project_id, 'code_commit',
                        :description, CAST(:metadata AS jsonb)
                    )
                    ON CONFLICT DO NOTHING
                    RETURNING id
                """),
                {
                    "agent_id": agent_id,
                    "project_id": project_id,
                    "description": description or f"Commit {sha}",
                    "metadata": json.dumps({"commit_sha": sha}),
                },
            )
            if result.first() is not None:
                accepted.add(sha)
        if accepted and project_id:
            await FunnelRepository(self.db).count_first_push(project_id)
        return accepted

    async def lock_contribution_agent(self, agent_id) -> None:
        """Serialize observations and resolutions for one existing accounting identity."""
        found = await self.db.scalar(
            text("SELECT id FROM agents WHERE id = :id FOR NO KEY UPDATE"),
            {"id": agent_id},
        )
        if found is None:
            raise ValueError("contribution_agent_not_found")

    async def contribution_history(self, agent_id, sha: str) -> list[dict]:
        """Return exact and ambiguous historical identities without rewriting them."""
        rows = await self.db.execute(
            text("""
            SELECT id, agent_id, project_id, metadata FROM agent_activity
            WHERE agent_id = :agent AND action_type = 'code_commit'
              AND (metadata->>'commit_sha' = :sha OR (
                metadata->>'commit_sha' ~ '^[0-9a-f]{7,39}$'
                AND left(:sha, length(metadata->>'commit_sha')) = metadata->>'commit_sha'))
        """),
            {"agent": agent_id, "sha": sha},
        )
        return [dict(row) for row in rows.mappings()]

    async def find_pending_contribution(self, agent_id, project_id, sha: str) -> dict | None:
        """Find a retained observation, including resolved decisions, under the agent lock."""
        row = (
            (
                await self.db.execute(
                    text("""
            SELECT id, metadata FROM agent_activity WHERE agent_id = :agent
              AND project_id = :project AND action_type = 'contribution_unresolved'
              AND metadata->>'sha' = :sha
        """),
                    {"agent": agent_id, "project": project_id, "sha": sha},
                )
            )
            .mappings()
            .first()
        )
        return dict(row) if row else None

    async def pending_contribution(self, agent_id, project_id, metadata: dict) -> None:
        """Retain unresolved attribution once; caller holds the agent lock."""
        row = (
            await self.db.execute(
                text("""
            SELECT metadata FROM agent_activity WHERE agent_id = :agent
              AND project_id = :project AND action_type = 'contribution_unresolved'
              AND metadata->>'sha' = :sha FOR UPDATE
        """),
                {"agent": agent_id, "project": project_id, "sha": metadata["sha"]},
            )
        ).first()
        if row:
            if any(
                row[0].get(field) != metadata.get(field) for field in ("paths", "removed_paths")
            ):
                raise ValueError("contribution_observation_conflict")
            return
        await self.db.execute(
            text("""
                INSERT INTO agent_activity
                    (agent_id, project_id, action_type, description, metadata)
                VALUES (:agent, :project, 'contribution_unresolved',
                    'Contribution attribution needs review', CAST(:metadata AS jsonb))
            """),
            {"agent": agent_id, "project": project_id, "metadata": json.dumps(metadata)},
        )

    async def contribution_entry(self, entry_id) -> dict | None:
        """Read one accounting entry for the trusted reconciliation consumer."""
        row = (
            (
                await self.db.execute(
                    text("""
            SELECT id, agent_id, project_id, action_type, metadata
            FROM agent_activity WHERE id = :id
        """),
                    {"id": entry_id},
                )
            )
            .mappings()
            .first()
        )
        return dict(row) if row else None

    async def unresolved_contributions(self) -> list[dict]:
        """List bounded pending identities without exposing paths or evidence."""
        rows = await self.db.execute(
            text("""
            SELECT id, project_id, metadata->>'reason' AS reason FROM agent_activity
            WHERE action_type = 'contribution_unresolved' AND metadata->>'state' = 'pending'
            ORDER BY created_at, id LIMIT 100
        """)
        )
        return [dict(row) for row in rows.mappings()]

    async def resolve_contribution(self, entry_id, resolution: dict) -> None:
        """Update only the owned unresolved entry; caller holds its agent lock."""
        result = await self.db.execute(
            text("""
            UPDATE agent_activity SET metadata = metadata || CAST(:resolution AS jsonb)
            WHERE id = :id AND action_type = 'contribution_unresolved'
              AND metadata->>'state' = 'pending' RETURNING id
        """),
            {"id": entry_id, "resolution": json.dumps(resolution)},
        )
        if result.first() is None:
            raise ValueError("contribution_resolution_conflict")

    async def stamp_contribution_policy(self, agent_id, sha: str, policy: str) -> None:
        """Stamp only a newly accepted full identity in the same transaction."""
        result = await self.db.execute(
            text("""
            UPDATE agent_activity SET metadata = metadata || CAST(:policy AS jsonb)
            WHERE agent_id = :agent AND action_type = 'code_commit'
              AND metadata->>'commit_sha' = :sha RETURNING id
        """),
            {"agent": agent_id, "sha": sha, "policy": json.dumps({"policy": policy})},
        )
        if result.first() is None:
            raise ValueError("contribution_policy_write_failed")

    async def record_proxy_audit(self, agent_id, project_id, method, path_for_match, status_code):
        """Record the write outcome independently of whether it earned new credit."""
        await self.db.execute(
            text("""
                INSERT INTO agent_github_actions (agent_id, project_id, method, path, status_code)
                VALUES (:agent_id, :project_id, :method, :path, :status_code)
            """),
            {
                "agent_id": agent_id,
                "project_id": project_id,
                "method": method,
                "path": path_for_match,
                "status_code": status_code,
            },
        )
