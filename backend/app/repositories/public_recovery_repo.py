"""Historical inserts without registration, runtime, financial or live-event effects."""

import json
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.public_recovery import HistoricalAgent, HistoricalCommit, HistoricalProject


class PublicRecoveryRepository:
    """Operate only within the caller-owned recovery transaction."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def lock(self) -> None:
        """Serialize imports and bound waits without changing shared configuration."""
        await self.db.execute(text("SET LOCAL statement_timeout = '10s'"))
        await self.db.execute(text("SET LOCAL lock_timeout = '5s'"))
        await self.db.execute(text("SELECT pg_advisory_xact_lock(812673094)"))

    async def restore_agent(self, agent: HistoricalAgent) -> bool:
        """Restore a retained identity once; any identity collision aborts the import."""
        handle = f"historical-{agent.id.hex}"
        rows = (
            (
                await self.db.execute(
                    text(
                        "SELECT id, name FROM agents WHERE id=:id OR name=:name OR handle=:handle"
                    ),
                    {"id": agent.id, "name": agent.name, "handle": handle},
                )
            )
            .mappings()
            .all()
        )
        if rows:
            if len(rows) != 1 or rows[0]["id"] != agent.id or rows[0]["name"] != agent.name:
                raise ValueError("Historical agent identity collision.")
            return False
        provenance = agent.model_dump(mode="json", exclude={"id", "name"})
        provenance["original_ids_recovered"] = True
        await self.db.execute(
            text("""
            INSERT INTO agents
                (id,name,handle,specialization,is_active,model_provider,model_name,
                 dna_risk,dna_speed,dna_verbosity,dna_creativity,recovery_provenance)
            VALUES (:id,:name,:handle,'unknown',FALSE,NULL,NULL,NULL,NULL,NULL,NULL,
                    CAST(:provenance AS jsonb) ||
                    jsonb_build_object('recovered_at',statement_timestamp()))
        """),
            {
                "id": agent.id,
                "name": agent.name,
                "handle": handle,
                "provenance": json.dumps(provenance),
            },
        )
        return True

    async def restore_project(self, project: HistoricalProject) -> tuple[UUID, bool]:
        """Preserve existing repository records, reporting conflicting UUID bindings."""
        rows = (
            (
                await self.db.execute(
                    text("""
            SELECT id,repo_url FROM projects WHERE id=:id OR
                regexp_replace(lower(repo_url), '\\.git/?$|/$', '', 'g')=:url
        """),
                    {"id": project.id, "url": project.url.lower()},
                )
            )
            .mappings()
            .all()
        )
        if rows:
            if len(rows) != 1 or not rows[0]["repo_url"]:
                raise ValueError("Historical project identity collision.")
            url = rows[0]["repo_url"].lower().rstrip("/").removesuffix(".git")
            if url != project.url.lower():
                raise ValueError("Historical project identity collision.")
            return rows[0]["id"], False
        provenance = {
            "source": "github_public_history",
            "repository_url": project.url,
            "id_kind": project.id_kind,
            "history_total": project.history_total,
            "history_imported": len(project.commits),
            "repository_pushed_at": str(project.pushed_at) if project.pushed_at else None,
        }
        await self.db.execute(
            text("""
            INSERT INTO projects (id,title,description,category,status,creator_agent_id,
                                  repo_url,created_at,recovery_provenance)
            VALUES (:id,:title,:description,'other','historical',NULL,:url,:created_at,
                    CAST(:provenance AS jsonb) ||
                    jsonb_build_object('recovered_at',statement_timestamp()))
        """),
            {
                "id": project.id,
                "title": project.slug,
                "description": project.description,
                "url": project.url,
                "created_at": project.created_at,
                "provenance": json.dumps(provenance),
            },
        )
        return project.id, True

    async def restore_commit(self, project_id: UUID, commit: HistoricalCommit, url: str) -> bool:
        """Keep actual commit time and full SHA; never attribute an unknown author."""
        metadata = {
            "source": "github_public_history",
            "commit_sha": commit.sha,
            "github_url": commit.url,
            "repository_url": url,
        }
        result = await self.db.execute(
            text("""
            INSERT INTO agent_activity
                (agent_id,project_id,action_type,description,metadata,created_at)
            SELECT NULL,:project_id,'code_commit',:subject,
                   CAST(:metadata AS jsonb) ||
                   jsonb_build_object('recovered_at',statement_timestamp()),
                   :created_at
            WHERE NOT EXISTS (SELECT 1 FROM agent_activity
                WHERE project_id=:project_id AND action_type='code_commit'
                  AND metadata->>'commit_sha'=:sha)
            ON CONFLICT DO NOTHING RETURNING id
        """),
            {
                "project_id": project_id,
                "subject": commit.subject,
                "metadata": json.dumps(metadata),
                "created_at": commit.created_at,
                "sha": commit.sha,
            },
        )
        return result.first() is not None
