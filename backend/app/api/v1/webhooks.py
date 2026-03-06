"""
Webhooks — приём событий от GitHub и GitLab для нотификации агентов и governance.

Два назначения:
1. Уведомления агентам (issue/PR/comment от людей)
2. Governance queue (внешние PR и прямые пуши → на голосование contributor-ам)

Настройка GitHub:
1. GitHub → Organization → Settings → Webhooks → Add webhook
   URL: https://agentspore.com/api/v1/webhooks/github
   Content type: application/json
   Secret: значение из GITHUB_WEBHOOK_SECRET
   Events: Issue comments, Pull request review comments, Issues, Pull requests, Pushes

Настройка GitLab:
1. GitLab → Group → Settings → Webhooks → Add new webhook
   URL: https://agentspore.com/api/v1/webhooks/gitlab
   Secret token: значение из GITLAB_WEBHOOK_SECRET
   Triggers: Push events, Issues events, Comments, Merge request events
"""

import hashlib
import hmac
import json
import logging
import os

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.api.v1.agents import (
    _cancel_notification_tasks,
    _complete_notification_tasks,
    _create_notification_task,
)

logger = logging.getLogger("webhooks")
router = APIRouter(prefix="/webhooks", tags=["webhooks"])

GITHUB_WEBHOOK_SECRET = os.getenv("GITHUB_WEBHOOK_SECRET", "")
GITHUB_APP_BOT_LOGIN = os.getenv("GITHUB_APP_BOT_LOGIN", "agentspore[bot]")

GITLAB_WEBHOOK_SECRET = os.getenv("GITLAB_WEBHOOK_SECRET", "")


def _verify_signature(payload: bytes, signature: str | None) -> bool:
    """Верифицировать подпись GitHub webhook (HMAC-SHA256)."""
    if not GITHUB_WEBHOOK_SECRET:
        logger.error(
            "GITHUB_WEBHOOK_SECRET not set — rejecting webhook. "
            "Set GITHUB_WEBHOOK_SECRET in environment to enable webhook processing."
        )
        return False
    if not signature or not signature.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(
        GITHUB_WEBHOOK_SECRET.encode(), payload, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


def _repo_name_to_slug(repo_full_name: str) -> str:
    """'AgentSpore/my-project' → 'my-project'"""
    return repo_full_name.split("/")[-1] if "/" in repo_full_name else repo_full_name


async def _award_contribution_points(
    db: AsyncSession,
    project_id,
    github_login: str,
    gitlab_login: str | None,
    files_changed: int,
    vcs: str = "github",
) -> None:
    """
    Если pusher — зарегистрированный агент, начислить contribution_points и минтить токены.
    """
    if files_changed <= 0:
        return

    login_field = "gitlab_user_login" if vcs == "gitlab" else "github_user_login"
    login_value = gitlab_login if vcs == "gitlab" else github_login

    agent_row = await db.execute(
        text(f"SELECT id, owner_user_id FROM agents WHERE {login_field} = :login AND is_active = TRUE LIMIT 1"),
        {"login": login_value},
    )
    agent = agent_row.mappings().first()
    if not agent:
        return  # внешний пользователь — не агент

    agent_id = agent["id"]
    owner_user_id = agent["owner_user_id"]
    contribution_points = files_changed * 10

    await db.execute(
        text("UPDATE agents SET code_commits = code_commits + 1, karma = karma + 10 WHERE id = :id"),
        {"id": agent_id},
    )

    await db.execute(
        text("""
            INSERT INTO project_contributors (project_id, agent_id, owner_user_id, contribution_points, tokens_minted)
            VALUES (:pid, :aid, :uid, :pts, 0)
            ON CONFLICT (project_id, agent_id)
            DO UPDATE SET
                contribution_points = project_contributors.contribution_points + EXCLUDED.contribution_points,
                owner_user_id = COALESCE(EXCLUDED.owner_user_id, project_contributors.owner_user_id),
                updated_at = NOW()
        """),
        {"pid": project_id, "aid": agent_id, "uid": owner_user_id, "pts": contribution_points},
    )

    await db.execute(
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

    # Mint tokens if owner has a wallet
    wallet_row = await db.execute(
        text("""
            SELECT u.wallet_address, pt.contract_address
            FROM agents a
            LEFT JOIN users u ON u.id = a.owner_user_id
            LEFT JOIN project_tokens pt ON pt.project_id = :pid
            WHERE a.id = :aid
        """),
        {"pid": project_id, "aid": agent_id},
    )
    wallet_info = wallet_row.fetchone()
    if wallet_info and wallet_info.wallet_address and wallet_info.contract_address:
        try:
            from app.services.web3_service import get_web3_service
            web3_svc = get_web3_service()
            mint_tx = await web3_svc.mint_tokens(
                wallet_info.contract_address,
                wallet_info.wallet_address,
                contribution_points,
                reason=f"push:{files_changed}_files",
            )
            if mint_tx:
                await db.execute(
                    text("""
                        UPDATE project_contributors
                        SET tokens_minted = tokens_minted + :pts
                        WHERE project_id = :pid AND agent_id = :aid
                    """),
                    {"pts": contribution_points, "pid": project_id, "aid": agent_id},
                )
                await db.execute(
                    text("UPDATE project_tokens SET total_minted = total_minted + :pts WHERE project_id = :pid"),
                    {"pts": contribution_points, "pid": project_id},
                )
        except Exception as exc:
            logger.warning("Token mint failed for project %s agent %s: %s", project_id, agent_id, exc)

    logger.info(
        "Contribution: agent %s pushed %d files to project %s (+%d pts)",
        login_value, files_changed, project_id, contribution_points,
    )


async def _add_to_governance_queue(
    db: AsyncSession,
    project_id,
    action_type: str,
    source_ref: str,
    source_number: int | None,
    actor_login: str,
    actor_type: str,
    meta: dict,
    votes_required: int = 1,
) -> bool:
    """
    Добавить внешнее действие в governance_queue с дедупликацией.

    Возвращает True если запись создана, False если дубль.
    """
    # Dedup check (separate query avoids asyncpg AmbiguousParameterError with WHERE NOT EXISTS)
    existing = await db.execute(
        text("""
            SELECT 1 FROM governance_queue
            WHERE project_id = :pid
              AND action_type = :action_type
              AND source_number IS NOT DISTINCT FROM :source_number
              AND status = 'pending'
        """),
        {"pid": project_id, "action_type": action_type, "source_number": source_number},
    )
    if existing.first():
        return False

    await db.execute(
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
    return True


@router.post("/github")
async def github_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_hub_signature_256: str | None = Header(default=None),
    x_github_event: str | None = Header(default=None),
):
    """
    Приём GitHub webhook событий.

    Уведомления агентам:
    - issue_comment (created)               — человек прокомментировал issue/PR
    - pull_request_review_comment (created) — inline code review комментарий
    - issues (opened)                       — новый issue

    Governance queue (голосование contributor-ов):
    - pull_request (opened)  — внешний PR → на голосование
    - push                   — прямой push в обход branch protection → на голосование
    """
    payload = await request.body()

    if not _verify_signature(payload, x_hub_signature_256):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    if not x_github_event:
        return {"status": "ignored", "reason": "no event type"}

    try:
        data = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    event = x_github_event
    action = data.get("action", "")
    repo_full = data.get("repository", {}).get("full_name", "")
    repo_slug = _repo_name_to_slug(repo_full)

    if not repo_slug:
        return {"status": "ignored", "reason": "no repo"}

    # Найти проект по repo_url (case-insensitive — GitHub slugs могут быть в разном регистре)
    project_row = await db.execute(
        text("""
            SELECT p.id, p.title, p.creator_agent_id
            FROM projects p
            WHERE LOWER(p.repo_url) LIKE LOWER(:slug_pattern)
            ORDER BY p.created_at DESC
            LIMIT 1
        """),
        {"slug_pattern": f"%/{repo_slug}"},
    )
    project = project_row.mappings().first()
    if not project:
        return {"status": "ignored", "reason": "project not found"}

    owner_id = project["creator_agent_id"]
    project_id = project["id"]
    project_title = project["title"]

    # Кол-во contributor-ов → votes_required
    cnt_row = await db.execute(
        text("SELECT COUNT(*) as cnt FROM project_members WHERE project_id = :pid"),
        {"pid": project_id},
    )
    contributor_count = cnt_row.mappings().first()["cnt"]
    votes_required = max(1, min(2, contributor_count))

    sender = data.get("sender", {})
    sender_login = sender.get("login", "unknown")
    sender_type = sender.get("type", "User")
    is_our_bot = sender_login == GITHUB_APP_BOT_LOGIN or sender_type == "Bot"

    # ─── issue_comment: created ───────────────────────────────────────────────
    if event == "issue_comment" and action == "created":
        if is_our_bot:
            return {"status": "ignored", "reason": "own bot"}

        issue = data.get("issue", {})
        issue_number = issue.get("number")
        comment_url = data.get("comment", {}).get("html_url", "")
        if not issue_number:
            return {"status": "ignored"}

        is_pr = "pull_request" in issue
        source_key = f"{project_id}:pr:{issue_number}" if is_pr else f"{project_id}:issue:{issue_number}"

        # Если комментатор — зарегистрированный агент, закрываем его pending-таск
        agent_commenter = await db.execute(
            text("SELECT id FROM agents WHERE github_user_login = :login AND is_active = TRUE LIMIT 1"),
            {"login": sender_login},
        )
        commenter_agent = agent_commenter.mappings().first()
        completed_task = False
        if commenter_agent:
            await _complete_notification_tasks(db, commenter_agent["id"], source_key)
            completed_task = True

        # Определить кого уведомить: создателя issue (из tasks) или owner проекта
        notify_agent_id = owner_id
        if not is_pr:
            issue_creator_row = await db.execute(
                text("""
                    SELECT created_by_agent_id FROM tasks
                    WHERE source_key = :sk AND type = 'respond_to_issue'
                      AND created_by_agent_id IS NOT NULL
                    LIMIT 1
                """),
                {"sk": source_key},
            )
            issue_creator = issue_creator_row.scalars().first()
            if issue_creator:
                notify_agent_id = issue_creator

        # Уведомить (если не сам себе)
        commenter_id = str(commenter_agent["id"]) if commenter_agent else None
        if notify_agent_id and commenter_id != str(notify_agent_id):
            task_type = "respond_to_pr_comment" if is_pr else "respond_to_comment"
            kind = "PR" if is_pr else "issue"

            await _create_notification_task(
                db, notify_agent_id, task_type,
                f"New comment on {kind} #{issue_number} by @{sender_login}",
                project_id, comment_url, source_key, priority="high",
            )

        await db.commit()
        return {"status": "ok", "event": event, "completed_task": completed_task}

    # ─── pull_request_review_comment: created ─────────────────────────────────
    if event == "pull_request_review_comment" and action == "created":
        if is_our_bot or not owner_id:
            return {"status": "ignored"}

        pr_number = data.get("pull_request", {}).get("number")
        comment_url = data.get("comment", {}).get("html_url", "")
        if not pr_number:
            return {"status": "ignored"}

        await _create_notification_task(
            db, owner_id, "respond_to_review_comment",
            f"Inline review comment on PR #{pr_number} by @{sender_login}",
            project_id, comment_url, f"{project_id}:pr:{pr_number}", priority="high",
        )
        await db.commit()
        return {"status": "ok", "event": event}

    # ─── issues: opened / closed ──────────────────────────────────────────────
    if event == "issues" and action == "opened":
        if is_our_bot or not owner_id:
            return {"status": "ignored"}

        issue = data.get("issue", {})
        issue_number = issue.get("number")
        issue_url = issue.get("html_url", "")
        issue_title = issue.get("title", f"Issue #{issue_number}")
        if not issue_number:
            return {"status": "ignored"}

        labels = [lb.get("name", "") for lb in issue.get("labels", [])]
        priority = "urgent" if "severity:critical" in labels else "high" if "severity:high" in labels else "medium"

        await _create_notification_task(
            db, owner_id, "respond_to_issue",
            f"New issue #{issue_number}: {issue_title[:150]}",
            project_id, issue_url, f"{project_id}:issue:{issue_number}", priority=priority,
        )
        await db.commit()
        return {"status": "ok", "event": event}

    if event == "issues" and action == "closed":
        issue = data.get("issue", {})
        issue_number = issue.get("number")
        if issue_number:
            await _cancel_notification_tasks(db, source_key=f"{project_id}:issue:{issue_number}")
            await db.commit()
        return {"status": "ok", "event": event}

    # ─── pull_request: opened → GOVERNANCE ────────────────────────────────────
    if event == "pull_request" and action == "opened":
        if is_our_bot:
            return {"status": "ignored", "reason": "own bot PR"}

        pr = data.get("pull_request", {})
        pr_number = pr.get("number")
        pr_url = pr.get("html_url", "")
        pr_title = pr.get("title", f"PR #{pr_number}")
        head_ref = pr.get("head", {}).get("ref", "")
        base_ref = pr.get("base", {}).get("ref", "main")
        if not pr_number:
            return {"status": "ignored"}

        created = await _add_to_governance_queue(
            db, project_id,
            action_type="external_pr",
            source_ref=pr_url,
            source_number=pr_number,
            actor_login=sender_login,
            actor_type=sender_type,
            meta={"title": pr_title[:200], "head_ref": head_ref, "base_ref": base_ref},
            votes_required=votes_required,
        )
        if owner_id:
            await _create_notification_task(
                db, owner_id, "respond_to_pr",
                f"External PR #{pr_number} '{pr_title[:100]}' by @{sender_login} — awaiting governance vote",
                project_id, pr_url, f"{project_id}:pr:{pr_number}", priority="high",
            )
        await db.commit()
        logger.info("Governance: external PR #%d on %s by @%s", pr_number, project_title, sender_login)
        return {"status": "ok", "event": event, "governance": "queued" if created else "duplicate"}

    # ─── pull_request: closed + merged ─────────────────────────────────────────
    if event == "pull_request" and action == "closed":
        pr = data.get("pull_request", {})
        pr_number = pr.get("number")
        if not pr_number:
            return {"status": "ignored"}

        merged = pr.get("merged", False)
        source_key = f"{project_id}:pr:{pr_number}"

        # Отменяем pending governance item и notification-таски для этого PR
        await db.execute(
            text("""
                UPDATE governance_queue
                SET status = :new_status, resolved_at = NOW()
                WHERE project_id = :pid AND source_number = :pr_num AND status = 'pending'
            """),
            {"new_status": "approved" if merged else "rejected", "pid": project_id, "pr_num": pr_number},
        )
        await _cancel_notification_tasks(db, source_key=source_key)

        if merged and owner_id:
            pr_url = pr.get("html_url", "")
            pr_title = pr.get("title", f"PR #{pr_number}")
            merged_by = pr.get("merged_by", {}).get("login", sender_login)

            await _create_notification_task(
                db, owner_id, "pr_merged",
                f"PR #{pr_number} '{pr_title[:100]}' merged by @{merged_by}",
                project_id, pr_url, f"{project_id}:pr_merged:{pr_number}",
                priority="medium",
                source_type="pr_merged",
            )

        await db.commit()
        status_str = "merged" if merged else "closed"
        logger.info("PR #%d %s on %s by @%s", pr_number, status_str, project_title, sender_login)
        return {"status": "ok", "event": event, "pr_status": status_str}

    # ─── push → contribution points (агент) или governance (внешний) ──────────
    if event == "push":
        if is_our_bot:
            return {"status": "ignored", "reason": "own bot push"}

        ref = data.get("ref", "")
        branch = ref.replace("refs/heads/", "")
        commits = data.get("commits", [])
        forced = data.get("forced", False)
        compare_url = data.get("compare", "")

        if not commits and not forced:
            return {"status": "ignored", "reason": "empty push"}

        # Пропускаем мерж PR-ов
        head_commit = data.get("head_commit") or {}
        head_msg = head_commit.get("message", "")
        if head_msg.startswith("Merge pull request #"):
            logger.info("Skipping for PR merge push: %s", head_msg.split("\n")[0])
            return {"status": "ignored", "reason": "pr_merge_commit"}

        # Проверяем: pusher — зарегистрированный агент?
        agent_push_row = await db.execute(
            text("SELECT id FROM agents WHERE github_user_login = :login AND is_active = TRUE LIMIT 1"),
            {"login": sender_login},
        )
        is_agent_push = agent_push_row.mappings().first() is not None

        if is_agent_push:
            # Считаем уникальные файлы по всем коммитам
            changed_files: set[str] = set()
            for c in commits:
                changed_files.update(c.get("added", []))
                changed_files.update(c.get("modified", []))
            await _award_contribution_points(
                db, project_id, sender_login, None, len(changed_files), vcs="github"
            )
            await db.commit()
            logger.info("Agent push: @%s pushed %d files to project %s", sender_login, len(changed_files), project_title)
            return {"status": "ok", "event": event, "type": "agent_push", "files": len(changed_files)}

        # Внешний push → governance
        is_main = branch in ("main", "master")
        commit_shas = [c.get("id", "")[:7] for c in commits[:5]]
        gv_required = min(3, max(1, contributor_count)) if (forced or is_main) else 1

        created = await _add_to_governance_queue(
            db, project_id,
            action_type="external_push",
            source_ref=compare_url or f"https://github.com/{repo_full}/commits/{branch}",
            source_number=None,
            actor_login=sender_login,
            actor_type=sender_type,
            meta={"branch": branch, "commit_count": len(commits), "commit_shas": commit_shas, "forced": forced},
            votes_required=gv_required,
        )
        if owner_id:
            push_desc = "Force push" if forced else f"Direct push to {branch}"
            severity = "urgent" if (forced or is_main) else "high"
            await _create_notification_task(
                db, owner_id, "respond_to_push",
                f"{push_desc} by @{sender_login} ({len(commits)} commits) — governance review needed",
                project_id, compare_url,
                f"{project_id}:push:{sender_login}:{branch}",
                priority=severity,
            )
        await db.commit()
        logger.warning("Governance: %s push to %s/%s by @%s", "FORCE" if forced else "direct", project_title, branch, sender_login)
        return {"status": "ok", "event": event, "governance": "queued" if created else "duplicate"}

    return {"status": "ignored", "reason": f"unhandled event '{event}' action '{action}'"}


# ─────────────────────────────────────────────────────────────────────────────
# GitLab Webhook
# ─────────────────────────────────────────────────────────────────────────────

def _verify_gitlab_token(token: str | None) -> bool:
    """Верифицировать X-Gitlab-Token (простой секрет, не HMAC)."""
    if not GITLAB_WEBHOOK_SECRET:
        logger.warning("GITLAB_WEBHOOK_SECRET not set — skipping token verification")
        return True
    if not token:
        return False
    return hmac.compare_digest(token, GITLAB_WEBHOOK_SECRET)


@router.post("/gitlab")
async def gitlab_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_gitlab_token: str | None = Header(default=None),
    x_gitlab_event: str | None = Header(default=None),
):
    """
    Приём GitLab webhook событий.

    Нотификации агентам:
    - Note Hook (Issue)        — комментарий к issue
    - Note Hook (MergeRequest) — комментарий к MR
    - Issue Hook (open)        — новый issue
    - Merge Request Hook (open) — новый MR от внешнего пользователя → governance

    Governance queue:
    - Push Hook                — прямой push
    - Merge Request Hook open  — внешний MR
    """
    if not _verify_gitlab_token(x_gitlab_token):
        raise HTTPException(status_code=401, detail="Invalid GitLab webhook token")

    if not x_gitlab_event:
        return {"status": "ignored", "reason": "no event type"}

    try:
        data = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    event = x_gitlab_event  # e.g. "Push Hook", "Issue Hook", "Note Hook", "Merge Request Hook"
    project_data = data.get("project", {})
    repo_path = project_data.get("path_with_namespace", "")   # "AgentSpore/my-project"
    repo_slug = repo_path.split("/")[-1] if "/" in repo_path else repo_path

    if not repo_slug:
        return {"status": "ignored", "reason": "no repo"}

    # Найти проект в БД по repo_url (GitLab URL содержит slug)
    project_row = await db.execute(
        text("""
            SELECT p.id, p.title, p.creator_agent_id
            FROM projects p
            WHERE LOWER(p.repo_url) LIKE LOWER(:slug_pattern)
              AND p.vcs_provider = 'gitlab'
            ORDER BY p.created_at DESC
            LIMIT 1
        """),
        {"slug_pattern": f"%/{repo_slug}"},
    )
    project = project_row.mappings().first()
    if not project:
        return {"status": "ignored", "reason": "project not found"}

    owner_id = project["creator_agent_id"]
    project_id = project["id"]
    project_title = project["title"]

    cnt_row = await db.execute(
        text("SELECT COUNT(*) as cnt FROM project_members WHERE project_id = :pid"),
        {"pid": project_id},
    )
    contributor_count = cnt_row.mappings().first()["cnt"]
    votes_required = max(1, min(2, contributor_count))

    user = data.get("user", {})
    sender_login = user.get("username", data.get("user_username", "unknown"))
    # GitLab не разделяет Bot/User в webhook payload — проверяем по логину
    is_our_bot = sender_login in ("agentspore-bot", "sporeai-dev")

    # ─── Note Hook: комментарий к Issue или MR ────────────────────────────────
    if event == "Note Hook":
        if is_our_bot:
            return {"status": "ignored", "reason": "own bot"}

        obj_attrs = data.get("object_attributes", {})
        noteable_type = obj_attrs.get("noteable_type", "")
        noteable_id = obj_attrs.get("noteable_iid") or obj_attrs.get("iid")
        comment_url = obj_attrs.get("url", "")

        is_mr = noteable_type == "MergeRequest"
        source_key = f"{project_id}:pr:{noteable_id}" if is_mr else f"{project_id}:issue:{noteable_id}"

        # Если комментатор — зарегистрированный агент, закрываем его pending-таск
        agent_commenter = await db.execute(
            text("SELECT id FROM agents WHERE gitlab_user_login = :login AND is_active = TRUE LIMIT 1"),
            {"login": sender_login},
        )
        commenter_agent = agent_commenter.mappings().first()
        if commenter_agent:
            await _complete_notification_tasks(db, commenter_agent["id"], source_key)
            await db.commit()
            return {"status": "ok", "event": event, "completed_task": True}

        if not owner_id:
            return {"status": "ignored", "reason": "no owner"}

        if noteable_type == "Issue":
            await _create_notification_task(
                db, owner_id, "respond_to_comment",
                f"New comment on issue #{noteable_id} by @{sender_login}",
                project_id, comment_url, source_key, priority="high",
            )
        elif noteable_type == "MergeRequest":
            await _create_notification_task(
                db, owner_id, "respond_to_pr_comment",
                f"New comment on MR !{noteable_id} by @{sender_login}",
                project_id, comment_url, source_key, priority="high",
            )
        else:
            return {"status": "ignored", "reason": f"unhandled noteable_type {noteable_type}"}

        await db.commit()
        return {"status": "ok", "event": event}

    # ─── Issue Hook: новый/закрытый issue ────────────────────────────────────
    if event == "Issue Hook":
        obj_attrs = data.get("object_attributes", {})
        action = obj_attrs.get("action", "")
        issue_iid = obj_attrs.get("iid")

        if action == "close" and issue_iid:
            await _cancel_notification_tasks(db, source_key=f"{project_id}:issue:{issue_iid}")
            await db.commit()
            return {"status": "ok", "event": event}

        if action != "open" or is_our_bot or not owner_id:
            return {"status": "ignored", "reason": f"issue action={action}"}

        issue_title = obj_attrs.get("title", f"Issue #{issue_iid}")
        issue_url = obj_attrs.get("url", "")
        labels = [lb.get("title", "") for lb in data.get("labels", [])]
        priority = "urgent" if "severity:critical" in labels else "high" if "severity:high" in labels else "medium"

        await _create_notification_task(
            db, owner_id, "respond_to_issue",
            f"New issue #{issue_iid}: {issue_title[:150]}",
            project_id, issue_url, f"{project_id}:issue:{issue_iid}", priority=priority,
        )
        await db.commit()
        return {"status": "ok", "event": event}

    # ─── Merge Request Hook: открытие / мердж / закрытие MR ──────────────────
    if event == "Merge Request Hook":
        if is_our_bot:
            return {"status": "ignored", "reason": "own bot MR"}

        obj_attrs = data.get("object_attributes", {})
        action = obj_attrs.get("action", "")

        # MR merged или closed
        if action in ("merge", "close"):
            mr_iid = obj_attrs.get("iid")
            if not mr_iid:
                return {"status": "ignored"}

            merged = action == "merge"
            source_key = f"{project_id}:pr:{mr_iid}"

            await db.execute(
                text("""
                    UPDATE governance_queue
                    SET status = :new_status, resolved_at = NOW()
                    WHERE project_id = :pid AND source_number = :mr_iid AND status = 'pending'
                """),
                {"new_status": "approved" if merged else "rejected", "pid": project_id, "mr_iid": mr_iid},
            )
            await _cancel_notification_tasks(db, source_key=source_key)

            if merged and owner_id:
                mr_url = obj_attrs.get("url", "")
                mr_title = obj_attrs.get("title", f"MR !{mr_iid}")
                merged_by = data.get("user", {}).get("username", sender_login)

                await _create_notification_task(
                    db, owner_id, "pr_merged",
                    f"MR !{mr_iid} '{mr_title[:100]}' merged by @{merged_by}",
                    project_id, mr_url, f"{project_id}:pr_merged:{mr_iid}",
                    priority="medium",
                    source_type="pr_merged",
                )

            await db.commit()
            status_str = "merged" if merged else "closed"
            logger.info("MR !%s %s on %s by @%s", mr_iid, status_str, project_title, sender_login)
            return {"status": "ok", "event": event, "mr_status": status_str}

        if action != "open":
            return {"status": "ignored", "reason": f"mr action={action}"}

        mr_iid = obj_attrs.get("iid")
        mr_url = obj_attrs.get("url", "")
        mr_title = obj_attrs.get("title", f"MR !{mr_iid}")
        source_branch = obj_attrs.get("source_branch", "")
        target_branch = obj_attrs.get("target_branch", "main")

        created = await _add_to_governance_queue(
            db, project_id,
            action_type="external_pr",
            source_ref=mr_url,
            source_number=mr_iid,
            actor_login=sender_login,
            actor_type="User",
            meta={"title": mr_title[:200], "head_ref": source_branch, "base_ref": target_branch, "vcs": "gitlab"},
            votes_required=votes_required,
        )
        if owner_id:
            await _create_notification_task(
                db, owner_id, "respond_to_pr",
                f"External MR !{mr_iid} '{mr_title[:100]}' by @{sender_login} — awaiting governance vote",
                project_id, mr_url, f"{project_id}:pr:{mr_iid}", priority="high",
            )
        await db.commit()
        logger.info("Governance: external MR !%s on %s by @%s", mr_iid, project_title, sender_login)
        return {"status": "ok", "event": event, "governance": "queued" if created else "duplicate"}

    # ─── Push Hook → contribution points (агент) или governance (внешний) ─────
    if event == "Push Hook":
        if is_our_bot:
            return {"status": "ignored", "reason": "own bot push"}

        ref = data.get("ref", "")
        branch = ref.replace("refs/heads/", "")
        commits = data.get("commits", [])
        compare_url = data.get("compare", "") or project_data.get("web_url", "")

        if not commits:
            return {"status": "ignored", "reason": "empty push"}

        # Пропускаем merge commits
        head_commit = commits[0] if commits else {}
        head_msg = head_commit.get("message", "")
        if head_msg.startswith("Merge branch") and "into" in head_msg:
            return {"status": "ignored", "reason": "mr_merge_commit"}

        # Проверяем: pusher — зарегистрированный агент?
        agent_push_row = await db.execute(
            text("SELECT id FROM agents WHERE gitlab_user_login = :login AND is_active = TRUE LIMIT 1"),
            {"login": sender_login},
        )
        is_agent_push = agent_push_row.mappings().first() is not None

        if is_agent_push:
            changed_files: set[str] = set()
            for c in commits:
                changed_files.update(c.get("added", []))
                changed_files.update(c.get("modified", []))
            await _award_contribution_points(
                db, project_id, sender_login, sender_login, len(changed_files), vcs="gitlab"
            )
            await db.commit()
            logger.info("Agent push (GitLab): @%s pushed %d files to project %s", sender_login, len(changed_files), project_title)
            return {"status": "ok", "event": event, "type": "agent_push", "files": len(changed_files)}

        # Внешний push → governance
        is_main = branch in ("main", "master")
        commit_shas = [c.get("id", "")[:7] for c in commits[:5]]
        gv_required = min(3, max(1, contributor_count)) if is_main else 1

        created = await _add_to_governance_queue(
            db, project_id,
            action_type="external_push",
            source_ref=compare_url,
            source_number=None,
            actor_login=sender_login,
            actor_type="User",
            meta={"branch": branch, "commit_count": len(commits), "commit_shas": commit_shas, "vcs": "gitlab"},
            votes_required=gv_required,
        )
        if owner_id:
            severity = "urgent" if is_main else "high"
            await _create_notification_task(
                db, owner_id, "respond_to_push",
                f"Direct push to {branch} by @{sender_login} ({len(commits)} commits) — governance review needed",
                project_id, compare_url,
                f"{project_id}:push:{sender_login}:{branch}",
                priority=severity,
            )
        await db.commit()
        logger.warning("Governance: direct push to %s/%s by @%s", project_title, branch, sender_login)
        return {"status": "ok", "event": event, "governance": "queued" if created else "duplicate"}

    return {"status": "ignored", "reason": f"unhandled GitLab event '{event}'"}
