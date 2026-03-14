"""Agent repository — all SQL queries for agents, OAuth, heartbeat, projects, tasks, notifications."""

import json
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


# ── Auth & Agent Lookup ──

async def get_agent_by_api_key_hash(db: AsyncSession, key_hash: str) -> dict | None:
    result = await db.execute(
        text("SELECT * FROM agents WHERE api_key_hash = :hash AND is_active = TRUE"),
        {"hash": key_hash},
    )
    row = result.mappings().first()
    return dict(row) if row else None


async def get_agent_by_id(db: AsyncSession, agent_id) -> dict | None:
    result = await db.execute(text("SELECT * FROM agents WHERE id = :id"), {"id": agent_id})
    row = result.mappings().first()
    return dict(row) if row else None


async def handle_exists(db: AsyncSession, handle: str) -> bool:
    result = await db.execute(text("SELECT 1 FROM agents WHERE handle = :h"), {"h": handle})
    return result.first() is not None


async def get_agent_id_by_api_key_hash(db: AsyncSession, key_hash: str):
    result = await db.execute(
        text("SELECT id FROM agents WHERE api_key_hash = :hash AND is_active = TRUE"),
        {"hash": key_hash},
    )
    row = result.mappings().first()
    return row["id"] if row else None


# ── Registration ──

async def insert_agent(db: AsyncSession, params: dict) -> None:
    await db.execute(
        text("""
            INSERT INTO agents (id, name, handle, agent_type, model_provider, model_name,
                              specialization, skills, description, api_key_hash,
                              is_active, github_oauth_state,
                              dna_risk, dna_speed, dna_verbosity, dna_creativity, bio,
                              owner_email, owner_user_id)
            VALUES (:id, :name, :handle, 'external', :provider, :model, :spec, :skills, :desc, :api_key,
                    TRUE, :oauth_state,
                    :dna_risk, :dna_speed, :dna_verbosity, :dna_creativity, :bio,
                    :owner_email, :owner_user_id)
        """),
        params,
    )


async def update_api_key_hash(db: AsyncSession, agent_id, new_hash: str) -> None:
    await db.execute(
        text("UPDATE agents SET api_key_hash = :hash WHERE id = :id"),
        {"hash": new_hash, "id": agent_id},
    )


# ── OAuth (GitHub) ──

async def get_agent_by_github_state(db: AsyncSession, state: str) -> dict | None:
    result = await db.execute(
        text("SELECT id FROM agents WHERE github_oauth_state = :state"),
        {"state": state},
    )
    row = result.mappings().first()
    return dict(row) if row else None


async def update_github_oauth(db: AsyncSession, agent_id, params: dict) -> None:
    await db.execute(
        text("""
            UPDATE agents SET
                is_active = TRUE,
                github_oauth_id = :github_id,
                github_oauth_token = :token,
                github_oauth_refresh_token = :refresh_token,
                github_oauth_scope = :scope,
                github_oauth_expires_at = :expires_at,
                github_user_login = :login,
                github_oauth_state = NULL,
                github_oauth_connected_at = NOW()
            WHERE id = :id
        """),
        {"id": agent_id, **params},
    )


async def clear_github_oauth(db: AsyncSession, agent_id) -> None:
    await db.execute(
        text("""
            UPDATE agents SET
                github_oauth_token = NULL,
                github_oauth_refresh_token = NULL,
                github_oauth_expires_at = NULL
            WHERE id = :id
        """),
        {"id": agent_id},
    )


async def update_github_oauth_tokens(db: AsyncSession, agent_id, token: str, refresh: str | None, expires_at) -> None:
    await db.execute(
        text("""
            UPDATE agents SET
                github_oauth_token = :token,
                github_oauth_refresh_token = :refresh,
                github_oauth_expires_at = :expires_at
            WHERE id = :id
        """),
        {"id": agent_id, "token": token, "refresh": refresh, "expires_at": expires_at},
    )


async def update_github_oauth_state(db: AsyncSession, agent_id, state: str) -> None:
    await db.execute(
        text("UPDATE agents SET github_oauth_state = :state WHERE id = :id"),
        {"state": state, "id": agent_id},
    )


async def revoke_github_oauth(db: AsyncSession, agent_id) -> None:
    await db.execute(
        text("""
            UPDATE agents SET
                is_active = FALSE,
                github_oauth_token = NULL,
                github_oauth_refresh_token = NULL,
                github_oauth_scope = NULL,
                github_oauth_expires_at = NULL,
                github_oauth_connected_at = NULL
            WHERE id = :id
        """),
        {"id": agent_id},
    )


# ── OAuth (GitLab) ──

async def update_gitlab_oauth_state(db: AsyncSession, agent_id, state: str) -> None:
    await db.execute(
        text("UPDATE agents SET gitlab_oauth_state = :state WHERE id = :id"),
        {"id": agent_id, "state": state},
    )


async def get_agent_by_gitlab_state(db: AsyncSession, state: str) -> dict | None:
    result = await db.execute(
        text("SELECT id FROM agents WHERE gitlab_oauth_state = :state"),
        {"state": state},
    )
    row = result.mappings().first()
    return dict(row) if row else None


async def update_gitlab_oauth(db: AsyncSession, agent_id, params: dict) -> None:
    await db.execute(
        text("""
            UPDATE agents SET
                gitlab_oauth_id = :gitlab_id,
                gitlab_oauth_token = :token,
                gitlab_oauth_refresh_token = :refresh_token,
                gitlab_oauth_scope = :scope,
                gitlab_oauth_expires_at = :expires_at,
                gitlab_user_login = :login,
                gitlab_oauth_state = NULL,
                gitlab_oauth_connected_at = NOW()
            WHERE id = :id
        """),
        {"id": agent_id, **params},
    )


async def revoke_gitlab_oauth(db: AsyncSession, agent_id) -> None:
    await db.execute(
        text("""
            UPDATE agents SET
                gitlab_oauth_token = NULL,
                gitlab_oauth_refresh_token = NULL,
                gitlab_oauth_scope = NULL,
                gitlab_oauth_expires_at = NULL,
                gitlab_oauth_connected_at = NULL
            WHERE id = :id
        """),
        {"id": agent_id},
    )


# ── Activity Logging ──

async def insert_activity(db: AsyncSession, agent_id, action_type: str, description: str, project_id=None, metadata: dict | None = None) -> None:
    await db.execute(
        text("""
            INSERT INTO agent_activity (agent_id, project_id, action_type, description, metadata)
            VALUES (:agent_id, :project_id, :action_type, :description, CAST(:metadata AS jsonb))
        """),
        {
            "agent_id": agent_id,
            "project_id": project_id,
            "action_type": action_type,
            "description": description,
            "metadata": json.dumps(metadata or {}),
        },
    )


async def insert_activity_simple(db: AsyncSession, agent_id, action_type: str, description: str, meta) -> None:
    """Insert activity row with raw meta (for OAuth callbacks where meta is already serialized)."""
    await db.execute(
        text("""
            INSERT INTO agent_activity (agent_id, action_type, description, metadata)
            VALUES (:agent_id, :action_type, :desc, :meta)
        """),
        {"agent_id": agent_id, "action_type": action_type, "desc": description, "meta": meta},
    )


# ── Heartbeat ──

async def update_heartbeat(db: AsyncSession, agent_id) -> None:
    await db.execute(
        text("UPDATE agents SET last_heartbeat = NOW(), is_active = TRUE WHERE id = :id"),
        {"id": agent_id},
    )


async def insert_heartbeat_log(db: AsyncSession, agent_id, status: str, tasks_completed: int) -> None:
    await db.execute(
        text("INSERT INTO heartbeat_logs (agent_id, status, tasks_completed) VALUES (:agent_id, :status, :completed)"),
        {"agent_id": agent_id, "status": status, "completed": tasks_completed},
    )


async def add_karma(db: AsyncSession, agent_id, karma: int) -> None:
    await db.execute(
        text("UPDATE agents SET karma = karma + :karma WHERE id = :id"),
        {"karma": karma, "id": agent_id},
    )


async def get_feature_requests_for_agent(db: AsyncSession, agent_id, limit: int) -> list[dict]:
    result = await db.execute(
        text("""
            SELECT fr.id, fr.title, fr.description, fr.votes, fr.project_id
            FROM feature_requests fr
            JOIN projects p ON p.id = fr.project_id
            WHERE fr.status = 'proposed' AND p.creator_agent_id = :agent_id
            ORDER BY fr.votes DESC LIMIT :limit
        """),
        {"agent_id": agent_id, "limit": limit},
    )
    return [dict(r) for r in result.mappings()]


async def accept_feature_request(db: AsyncSession, feature_id, agent_id) -> None:
    await db.execute(
        text("UPDATE feature_requests SET status = 'accepted', assigned_agent_id = :aid WHERE id = :id"),
        {"aid": agent_id, "id": feature_id},
    )


async def get_bug_reports_for_agent(db: AsyncSession, agent_id, limit: int) -> list[dict]:
    result = await db.execute(
        text("""
            SELECT br.id, br.title, br.description, br.severity, br.project_id
            FROM bug_reports br
            JOIN projects p ON p.id = br.project_id
            WHERE br.status = 'open' AND p.creator_agent_id = :agent_id
            ORDER BY CASE br.severity WHEN 'critical' THEN 0 WHEN 'high' THEN 1 ELSE 2 END
            LIMIT :limit
        """),
        {"agent_id": agent_id, "limit": limit},
    )
    return [dict(r) for r in result.mappings()]


async def assign_bug_report(db: AsyncSession, bug_id, agent_id) -> None:
    await db.execute(
        text("UPDATE bug_reports SET status = 'in_progress', assigned_agent_id = :aid WHERE id = :id"),
        {"aid": agent_id, "id": bug_id},
    )


async def get_project_comments_for_agent(db: AsyncSession, agent_id) -> list[dict]:
    result = await db.execute(
        text("""
            SELECT pc.content, u.name as user_name, p.title as project_title, pc.created_at
            FROM project_comments pc
            JOIN users u ON u.id = pc.user_id
            JOIN projects p ON p.id = pc.project_id
            WHERE p.creator_agent_id = :agent_id
            ORDER BY pc.created_at DESC LIMIT 10
        """),
        {"agent_id": agent_id},
    )
    return [dict(r) for r in result.mappings()]


async def get_pending_notifications(db: AsyncSession, agent_id) -> list[dict]:
    result = await db.execute(
        text("""
            SELECT t.id, t.type, t.title, t.project_id, t.source_ref, t.source_key,
                   t.priority, t.created_at,
                   a.handle as from_handle, a.name as from_name
            FROM tasks t
            LEFT JOIN agents a ON a.id = t.created_by_agent_id
            WHERE t.assigned_to_agent_id = :agent_id AND t.status = 'pending'
            ORDER BY
                CASE t.priority WHEN 'urgent' THEN 0 WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END,
                t.created_at
            LIMIT 20
        """),
        {"agent_id": agent_id},
    )
    return [dict(r) for r in result.mappings()]


async def get_unread_dms(db: AsyncSession, agent_id) -> list[dict]:
    result = await db.execute(
        text("""
            SELECT d.id, d.content, d.from_agent_id, d.human_name, d.created_at,
                   a.name as from_agent_name, a.handle as from_agent_handle
            FROM agent_dms d
            LEFT JOIN agents a ON a.id = d.from_agent_id
            WHERE d.to_agent_id = :agent_id AND d.is_read = FALSE
            ORDER BY d.created_at
            LIMIT 20
        """),
        {"agent_id": agent_id},
    )
    return [dict(r) for r in result.mappings()]


async def mark_dms_read(db: AsyncSession, dm_ids: list[str]) -> None:
    if not dm_ids:
        return
    placeholders = ", ".join(f":id_{i}" for i in range(len(dm_ids)))
    params = {f"id_{i}": uid for i, uid in enumerate(dm_ids)}
    await db.execute(
        text(f"UPDATE agent_dms SET is_read = TRUE WHERE id IN ({placeholders})"),
        params,
    )


# ── Notification Tasks ──

async def check_notification_exists(db: AsyncSession, assigned_to_agent_id, source_key: str) -> bool:
    result = await db.execute(
        text("""
            SELECT 1 FROM tasks
            WHERE assigned_to_agent_id = :assigned_to
              AND source_key = :source_key
              AND status = 'pending'
        """),
        {"assigned_to": assigned_to_agent_id, "source_key": source_key},
    )
    return result.first() is not None


async def insert_notification_task(db: AsyncSession, params: dict) -> None:
    await db.execute(
        text("""
            INSERT INTO tasks (
                type, title, project_id, priority, status,
                assigned_to_agent_id, created_by_agent_id,
                source_ref, source_key, source_type, created_by
            ) VALUES (
                :type, :title, :project_id, :priority, 'pending',
                :assigned_to, :created_by_agent,
                :source_ref, :source_key, :source_type, 'platform'
            )
        """),
        params,
    )


async def complete_notification_tasks(db: AsyncSession, agent_id, source_key: str) -> None:
    await db.execute(
        text("""
            UPDATE tasks SET status = 'completed', completed_at = NOW()
            WHERE assigned_to_agent_id = :agent_id
              AND source_key = :source_key
              AND status = 'pending'
        """),
        {"agent_id": agent_id, "source_key": source_key},
    )


async def cancel_notification_tasks(db: AsyncSession, source_key: str) -> None:
    await db.execute(
        text("""
            UPDATE tasks SET status = 'cancelled'
            WHERE source_key = :source_key AND status = 'pending'
        """),
        {"source_key": source_key},
    )


async def complete_notification_by_id(db: AsyncSession, task_id, agent_id) -> None:
    await db.execute(
        text("""
            UPDATE tasks SET status = 'completed', completed_at = NOW()
            WHERE id = :task_id
              AND assigned_to_agent_id = :agent_id
              AND status = 'pending'
        """),
        {"task_id": task_id, "agent_id": agent_id},
    )


# ── Projects (agent-side) ──

async def get_project_owner_name(db: AsyncSession, agent_id) -> str | None:
    result = await db.execute(
        text("""
            SELECT u.name as owner_name
            FROM agents a
            LEFT JOIN users u ON u.id = a.owner_user_id
            WHERE a.id = :aid
        """),
        {"aid": agent_id},
    )
    row = result.mappings().first()
    return row["owner_name"] if row else None


async def get_hackathon_status(db: AsyncSession, hackathon_id) -> dict | None:
    result = await db.execute(
        text("SELECT status FROM hackathons WHERE id = :hid"),
        {"hid": hackathon_id},
    )
    row = result.mappings().first()
    return dict(row) if row else None


async def insert_project(db: AsyncSession, params: dict) -> None:
    await db.execute(
        text("""
            INSERT INTO projects (id, title, description, category, creator_agent_id, tech_stack, status, repo_url, hackathon_id, vcs_provider)
            VALUES (:id, :title, :desc, :cat, :agent_id, :stack, 'building', :git_url, :hackathon_id, :vcs)
        """),
        params,
    )


async def increment_projects_created(db: AsyncSession, agent_id) -> None:
    await db.execute(
        text("UPDATE agents SET projects_created = projects_created + 1, karma = karma + 20 WHERE id = :id"),
        {"id": agent_id},
    )


async def insert_project_token(db: AsyncSession, project_id, contract_address: str, symbol: str, deploy_tx: str | None) -> None:
    await db.execute(
        text("""
            INSERT INTO project_tokens (project_id, chain_id, contract_address, token_symbol, deploy_tx_hash)
            VALUES (:pid, 8453, :addr, :sym, :tx)
            ON CONFLICT (project_id) DO NOTHING
        """),
        {"pid": project_id, "addr": contract_address, "sym": symbol, "tx": deploy_tx},
    )


async def get_project_full(db: AsyncSession, project_id) -> dict | None:
    result = await db.execute(text("SELECT * FROM projects WHERE id = :id"), {"id": project_id})
    row = result.mappings().first()
    return dict(row) if row else None


async def list_agent_projects(db: AsyncSession, filters: dict, params: dict) -> list[dict]:
    """List projects with dynamic filtering. `filters` is the WHERE clause string."""
    result = await db.execute(
        text(f"""
            SELECT p.id, p.title, p.description, p.status, p.repo_url,
                   p.category, p.tech_stack, p.created_at, p.github_stars,
                   p.creator_agent_id, a.handle as creator_handle, a.name as creator_name
            FROM projects p
            LEFT JOIN agents a ON a.id = p.creator_agent_id
            WHERE {filters}
            ORDER BY p.created_at DESC LIMIT :limit
        """),
        params,
    )
    return [dict(r) for r in result.mappings()]


async def get_project_code_files(db: AsyncSession, project_id) -> list[dict]:
    result = await db.execute(
        text("""
            SELECT DISTINCT ON (path) path, content, language, version
            FROM code_files WHERE project_id = :pid
            ORDER BY path, version DESC
        """),
        {"pid": project_id},
    )
    return [dict(f) for f in result.mappings()]


async def get_project_basic(db: AsyncSession, project_id, fields: str = "title, repo_url, vcs_provider") -> dict | None:
    result = await db.execute(
        text(f"SELECT {fields} FROM projects WHERE id = :id"),
        {"id": project_id},
    )
    row = result.mappings().first()
    return dict(row) if row else None


async def get_project_feedback(db: AsyncSession, project_id) -> dict:
    features = await db.execute(
        text("SELECT id, title, description, votes, status FROM feature_requests WHERE project_id = :pid AND status IN ('proposed', 'accepted') ORDER BY votes DESC"),
        {"pid": project_id},
    )
    bugs = await db.execute(
        text("SELECT id, title, description, severity, status FROM bug_reports WHERE project_id = :pid AND status IN ('open', 'in_progress') ORDER BY severity"),
        {"pid": project_id},
    )
    comments = await db.execute(
        text("SELECT pc.content, u.name as user_name, pc.created_at FROM project_comments pc JOIN users u ON u.id = pc.user_id WHERE pc.project_id = :pid ORDER BY pc.created_at DESC LIMIT 20"),
        {"pid": project_id},
    )
    return {
        "feature_requests": [dict(f) for f in features.mappings()],
        "bug_reports": [dict(b) for b in bugs.mappings()],
        "recent_comments": [dict(c) for c in comments.mappings()],
    }


async def update_project_deployed(db: AsyncSession, project_id, deploy_url: str) -> None:
    await db.execute(
        text("UPDATE projects SET status = 'deployed', deploy_url = :url, preview_url = :url WHERE id = :id"),
        {"url": deploy_url, "id": project_id},
    )


async def delete_project_and_related(db: AsyncSession, project_id) -> None:
    RELATED_TABLES = ("project_contributors", "code_reviews", "agent_activity", "governance_queue", "tasks")
    for table in RELATED_TABLES:
        await db.execute(
            text(f"DELETE FROM {table} WHERE project_id = :id"),  # noqa: S608
            {"id": project_id},
        )
    await db.execute(text("DELETE FROM projects WHERE id = :id"), {"id": project_id})


async def recount_agent_projects(db: AsyncSession, agent_id) -> None:
    await db.execute(
        text("UPDATE agents SET projects_created = (SELECT COUNT(*) FROM projects WHERE creator_agent_id = :aid) WHERE id = :aid"),
        {"aid": agent_id},
    )


async def get_agent_project_ids(db: AsyncSession, agent_id, limit: int) -> list[dict]:
    result = await db.execute(
        text("SELECT id, title FROM projects WHERE creator_agent_id = :aid ORDER BY created_at DESC LIMIT :limit"),
        {"aid": agent_id, "limit": limit},
    )
    return [dict(r) for r in result.mappings()]


# ── Reviews ──

async def insert_code_review(db: AsyncSession, review_id, project_id, agent_id, status: str, summary: str, model: str | None) -> None:
    await db.execute(
        text("INSERT INTO code_reviews (id, project_id, reviewer_agent_id, status, summary, model_used) VALUES (:id, :pid, :aid, :st, :sum, :model)"),
        {"id": review_id, "pid": project_id, "aid": agent_id, "st": status, "sum": summary, "model": model},
    )


async def insert_model_usage(db: AsyncSession, agent_id, model: str, task_type: str, ref_id, ref_type: str) -> None:
    await db.execute(
        text("""
            INSERT INTO agent_model_usage (agent_id, model, task_type, ref_id, ref_type)
            VALUES (:agent_id, :model, :task_type, :ref_id, :ref_type)
        """),
        {"agent_id": agent_id, "model": model, "task_type": task_type, "ref_id": ref_id, "ref_type": ref_type},
    )


async def insert_review_comment(db: AsyncSession, review_id, file_path, line_number, comment: str, suggestion) -> None:
    await db.execute(
        text("INSERT INTO review_comments (review_id, file_path, line_number, comment, suggestion) VALUES (:rid, :fp, :ln, :c, :s)"),
        {"rid": review_id, "fp": file_path, "ln": line_number, "c": comment, "s": suggestion},
    )


async def increment_reviews_done(db: AsyncSession, agent_id) -> None:
    await db.execute(
        text("UPDATE agents SET reviews_done = reviews_done + 1, karma = karma + 5 WHERE id = :id"),
        {"id": agent_id},
    )


async def get_project_for_review(db: AsyncSession, project_id) -> dict | None:
    result = await db.execute(
        text("SELECT title, repo_url, creator_agent_id FROM projects WHERE id = :id"),
        {"id": project_id},
    )
    row = result.mappings().first()
    return dict(row) if row else None


# ── Agent DNA ──

async def update_agent_dna(db: AsyncSession, agent_id, safe_keys: list[str], updates: dict) -> None:
    set_clause = ", ".join(f"{k} = :{k}" for k in safe_keys)
    updates["id"] = agent_id
    await db.execute(text(f"UPDATE agents SET {set_clause} WHERE id = :id"), updates)


# ── Tasks ──

async def list_open_tasks(db: AsyncSession, where_clause: str, params: dict) -> list[dict]:
    result = await db.execute(
        text(f"""
            SELECT t.id, t.project_id, t.type, t.title, t.description,
                   t.priority, t.status, t.source_type, t.created_at,
                   p.title as project_title
            FROM tasks t
            LEFT JOIN projects p ON p.id = t.project_id
            WHERE {where_clause}
            ORDER BY
                CASE t.priority WHEN 'urgent' THEN 1 WHEN 'high' THEN 2 WHEN 'medium' THEN 3 ELSE 4 END,
                t.created_at ASC
            LIMIT :limit
        """),
        params,
    )
    return [dict(r) for r in result.mappings()]


async def get_task_by_id(db: AsyncSession, task_id) -> dict | None:
    result = await db.execute(text("SELECT * FROM tasks WHERE id = :id"), {"id": task_id})
    row = result.mappings().first()
    return dict(row) if row else None


async def claim_task(db: AsyncSession, task_id, agent_id) -> None:
    await db.execute(
        text("""
            UPDATE tasks
            SET status = 'claimed', claimed_by_agent_id = :agent_id, claimed_at = NOW(), updated_at = NOW()
            WHERE id = :id AND status = 'open'
        """),
        {"id": task_id, "agent_id": agent_id},
    )


async def complete_task(db: AsyncSession, task_id, result_text: str) -> None:
    await db.execute(
        text("""
            UPDATE tasks
            SET status = 'completed', result = :result, completed_at = NOW(), updated_at = NOW()
            WHERE id = :id
        """),
        {"id": task_id, "result": result_text},
    )


async def unclaim_task(db: AsyncSession, task_id) -> None:
    await db.execute(
        text("""
            UPDATE tasks
            SET status = 'open', claimed_by_agent_id = NULL, claimed_at = NULL, updated_at = NOW()
            WHERE id = :id
        """),
        {"id": task_id},
    )


# ── Leaderboard & Stats ──

async def get_leaderboard(db: AsyncSession, order_clause: str, specialization: str | None, limit: int) -> list[dict]:
    params: dict = {"limit": limit}
    if specialization:
        query = text(
            "SELECT * FROM agents WHERE is_active = TRUE AND specialization = :spec"
            f" ORDER BY {order_clause} LIMIT :limit"
        )
        params["spec"] = specialization
    else:
        query = text(
            f"SELECT * FROM agents WHERE is_active = TRUE ORDER BY {order_clause} LIMIT :limit"
        )
    result = await db.execute(query, params)
    return [dict(r) for r in result.mappings()]


async def get_platform_stats(db: AsyncSession) -> dict:
    result = await db.execute(text("""
        SELECT
            (SELECT COUNT(*) FROM agents) as total_agents,
            (SELECT COUNT(*) FROM agents WHERE is_active = TRUE) as active_agents,
            (SELECT COUNT(*) FROM projects) as total_projects,
            (SELECT COALESCE(SUM(code_commits), 0) FROM agents) as total_code_commits,
            (SELECT COALESCE(SUM(reviews_done), 0) FROM agents) as total_reviews,
            (SELECT COUNT(*) FROM projects WHERE status = 'deployed') as total_deploys,
            (SELECT COUNT(*) FROM feature_requests) as total_feature_requests,
            (SELECT COUNT(*) FROM bug_reports) as total_bug_reports
    """))
    return dict(result.mappings().first())


async def get_model_usage(db: AsyncSession, agent_id) -> list[dict]:
    result = await db.execute(
        text("""
            SELECT
                model,
                task_type,
                COUNT(*) AS call_count,
                MAX(created_at) AS last_used
            FROM agent_model_usage
            WHERE agent_id = :agent_id
            GROUP BY model, task_type
            ORDER BY call_count DESC
        """),
        {"agent_id": agent_id},
    )
    return [dict(r) for r in result.mappings()]


async def get_github_activity(db: AsyncSession, where_clause: str, params: dict) -> list[dict]:
    result = await db.execute(
        text(f"""
            SELECT aa.id, aa.action_type, aa.description, aa.metadata,
                   aa.project_id, aa.created_at,
                   p.title AS project_title, p.repo_url AS project_repo_url
            FROM agent_activity aa
            LEFT JOIN projects p ON p.id = aa.project_id
            WHERE {where_clause}
            ORDER BY aa.created_at DESC
            LIMIT :limit
        """),
        params,
    )
    return [dict(r) for r in result.mappings()]


# ── Ownership by Email ──

async def find_user_id_by_email(db: AsyncSession, email: str):
    """Find user ID by email (case-insensitive). Returns UUID or None."""
    result = await db.execute(
        text("SELECT id FROM users WHERE LOWER(email) = LOWER(:email)"),
        {"email": email},
    )
    row = result.mappings().first()
    return row["id"] if row else None


async def link_agents_by_email(db: AsyncSession, user_id, email: str) -> int:
    """Auto-link all agents with matching owner_email to this user. Returns count of linked agents."""
    result = await db.execute(
        text("""
            UPDATE agents SET owner_user_id = :uid
            WHERE LOWER(owner_email) = LOWER(:email) AND owner_user_id IS NULL
        """),
        {"uid": str(user_id), "email": email},
    )
    linked_count = result.rowcount
    if linked_count > 0:
        await db.execute(
            text("""
                UPDATE project_contributors SET owner_user_id = :uid
                WHERE agent_id IN (
                    SELECT id FROM agents WHERE LOWER(owner_email) = LOWER(:email) AND owner_user_id = :uid
                ) AND owner_user_id IS NULL
            """),
            {"uid": str(user_id), "email": email},
        )
    return linked_count


async def link_contributors_to_user(db: AsyncSession, user_id, agent_id) -> None:
    """Link project_contributors for a specific agent to a user."""
    await db.execute(
        text("UPDATE project_contributors SET owner_user_id = :uid WHERE agent_id = :aid"),
        {"uid": str(user_id), "aid": str(agent_id)},
    )


# ── Admin ──

async def get_agents_with_github(db: AsyncSession) -> list[dict]:
    result = await db.execute(
        text(
            "SELECT id, github_user_login FROM agents "
            "WHERE github_oauth_token IS NOT NULL AND github_user_login IS NOT NULL"
        )
    )
    return [dict(r) for r in result.mappings()]
