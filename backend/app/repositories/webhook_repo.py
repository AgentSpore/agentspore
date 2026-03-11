"""Webhook repository — SQL queries for GitHub/GitLab webhook processing."""

import json
import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger("webhook_repo")


class WebhookRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # ── Project Lookup ────────────────────────────────────────────────────────

    async def find_project_by_repo_slug(self, repo_slug: str, vcs_provider: str | None = None) -> dict | None:
        if vcs_provider:
            result = await self.db.execute(
                text("""
                    SELECT p.id, p.title, p.creator_agent_id
                    FROM projects p
                    WHERE LOWER(p.repo_url) LIKE LOWER(:slug_pattern)
                      AND p.vcs_provider = :vcs
                    ORDER BY p.created_at DESC
                    LIMIT 1
                """),
                {"slug_pattern": f"%/{repo_slug}", "vcs": vcs_provider},
            )
        else:
            result = await self.db.execute(
                text("""
                    SELECT p.id, p.title, p.creator_agent_id
                    FROM projects p
                    WHERE LOWER(p.repo_url) LIKE LOWER(:slug_pattern)
                    ORDER BY p.created_at DESC
                    LIMIT 1
                """),
                {"slug_pattern": f"%/{repo_slug}"},
            )
        row = result.mappings().first()
        return dict(row) if row else None

    async def count_project_members(self, project_id) -> int:
        result = await self.db.execute(
            text("SELECT COUNT(*) as cnt FROM project_members WHERE project_id = :pid"),
            {"pid": project_id},
        )
        return result.mappings().first()["cnt"]

    async def update_project_status(self, project_id, status: str) -> None:
        await self.db.execute(
            text("UPDATE projects SET status = :status WHERE id = :id"),
            {"status": status, "id": project_id},
        )

    async def update_project_repo_url(self, project_id, repo_url: str) -> None:
        await self.db.execute(
            text("UPDATE projects SET repo_url = :url WHERE id = :id"),
            {"url": repo_url, "id": project_id},
        )

    # ── Agent Lookup ──────────────────────────────────────────────────────────

    async def get_agent_by_github_login(self, login: str) -> dict | None:
        result = await self.db.execute(
            text("SELECT id FROM agents WHERE github_user_login = :login AND is_active = TRUE LIMIT 1"),
            {"login": login},
        )
        row = result.mappings().first()
        return dict(row) if row else None

    async def get_agent_by_gitlab_login(self, login: str) -> dict | None:
        result = await self.db.execute(
            text("SELECT id FROM agents WHERE gitlab_user_login = :login AND is_active = TRUE LIMIT 1"),
            {"login": login},
        )
        row = result.mappings().first()
        return dict(row) if row else None

    async def get_agent_by_vcs_login(self, login: str, vcs: str = "github") -> dict | None:
        login_field = "gitlab_user_login" if vcs == "gitlab" else "github_user_login"
        result = await self.db.execute(
            text(f"SELECT id, owner_user_id FROM agents WHERE {login_field} = :login AND is_active = TRUE LIMIT 1"),  # noqa: S608
            {"login": login},
        )
        row = result.mappings().first()
        return dict(row) if row else None

    async def get_issue_creator_agent(self, source_key: str):
        result = await self.db.execute(
            text("""
                SELECT created_by_agent_id FROM tasks
                WHERE source_key = :sk AND type = 'respond_to_issue'
                  AND created_by_agent_id IS NOT NULL
                LIMIT 1
            """),
            {"sk": source_key},
        )
        return result.scalars().first()

    # ── Contribution Points ───────────────────────────────────────────────────

    async def increment_commits_and_karma(self, agent_id) -> None:
        await self.db.execute(
            text("UPDATE agents SET code_commits = code_commits + 1, karma = karma + 10 WHERE id = :id"),
            {"id": agent_id},
        )

    async def upsert_contributor_points(self, project_id, agent_id, owner_user_id, points: int) -> None:
        await self.db.execute(
            text("""
                INSERT INTO project_contributors (project_id, agent_id, owner_user_id, contribution_points, tokens_minted)
                VALUES (:pid, :aid, :uid, :pts, 0)
                ON CONFLICT (project_id, agent_id)
                DO UPDATE SET
                    contribution_points = project_contributors.contribution_points + EXCLUDED.contribution_points,
                    owner_user_id = COALESCE(EXCLUDED.owner_user_id, project_contributors.owner_user_id),
                    updated_at = NOW()
            """),
            {"pid": project_id, "aid": agent_id, "uid": owner_user_id, "pts": points},
        )

    async def recalculate_share_pct(self, project_id) -> None:
        await self.db.execute(
            text("""
                UPDATE project_contributors pc
                SET share_pct = ROUND(
                    pc.contribution_points * 100.0 /
                    NULLIF((SELECT SUM(contribution_points) FROM project_contributors WHERE project_id = :pid), 0),
                    2
                )
                WHERE pc.project_id = :pid
            """),
            {"pid": project_id},
        )

    async def get_wallet_and_contract(self, project_id, agent_id):
        result = await self.db.execute(
            text("""
                SELECT u.wallet_address, pt.contract_address
                FROM agents a
                LEFT JOIN users u ON u.id = a.owner_user_id
                LEFT JOIN project_tokens pt ON pt.project_id = :pid
                WHERE a.id = :aid
            """),
            {"pid": project_id, "aid": agent_id},
        )
        return result.fetchone()

    async def increment_tokens_minted(self, project_id, agent_id, points: int) -> None:
        await self.db.execute(
            text("""
                UPDATE project_contributors
                SET tokens_minted = tokens_minted + :pts
                WHERE project_id = :pid AND agent_id = :aid
            """),
            {"pts": points, "pid": project_id, "aid": agent_id},
        )

    async def increment_project_total_minted(self, project_id, points: int) -> None:
        await self.db.execute(
            text("UPDATE project_tokens SET total_minted = total_minted + :pts WHERE project_id = :pid"),
            {"pts": points, "pid": project_id},
        )

    # ── Governance Queue ──────────────────────────────────────────────────────

    async def governance_item_exists(self, project_id, action_type: str, source_number: int | None) -> bool:
        result = await self.db.execute(
            text("""
                SELECT 1 FROM governance_queue
                WHERE project_id = :pid
                  AND action_type = :action_type
                  AND source_number IS NOT DISTINCT FROM :source_number
                  AND status = 'pending'
            """),
            {"pid": project_id, "action_type": action_type, "source_number": source_number},
        )
        return result.first() is not None

    async def insert_governance_item(
        self,
        project_id,
        action_type: str,
        source_ref: str,
        source_number: int | None,
        actor_login: str,
        actor_type: str,
        meta: dict,
        votes_required: int,
    ) -> None:
        await self.db.execute(
            text("""
                INSERT INTO governance_queue
                    (project_id, action_type, source_ref, source_number,
                     actor_login, actor_type, meta, votes_required)
                VALUES
                    (:pid, :action_type, :source_ref, :source_number,
                     :actor_login, :actor_type, CAST(:meta AS jsonb), :votes_req)
            """),
            {
                "pid": project_id,
                "action_type": action_type,
                "source_ref": source_ref,
                "source_number": source_number,
                "actor_login": actor_login,
                "actor_type": actor_type,
                "meta": json.dumps(meta),
                "votes_req": votes_required,
            },
        )

    async def resolve_governance_by_pr(self, project_id, pr_number: int, approved: bool) -> None:
        await self.db.execute(
            text("""
                UPDATE governance_queue
                SET status = :new_status, resolved_at = NOW()
                WHERE project_id = :pid AND source_number = :pr_num AND status = 'pending'
            """),
            {"new_status": "approved" if approved else "rejected", "pid": project_id, "pr_num": pr_number},
        )
