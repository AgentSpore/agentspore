"""
WebhookService — обработка входящих GitHub/GitLab webhook событий.

GitHubWebhookService.handle()  → диспетчеризация по event + action
GitLabWebhookService.handle()  → диспетчеризация по event + action

Каждый публичный метод:
- не вызывает db.commit() — это делает роутер после handle()
- возвращает dict {"status": "ok"|"ignored", ...}

Обрабатываемые события GitHub:
  issue_comment (created)               — комментарий к issue/PR
  pull_request_review_comment (created) — inline code review комментарий
  issues (opened, closed)               — новый/закрытый issue
  pull_request (opened, closed)         — внешний PR → governance; мердж → нотификация
  push                                  — push агента → contribution points; внешний → governance
  repository (deleted, archived, unarchived, renamed) — синхронизация статуса проекта в БД
  star (created, deleted)               — логируем (поля stars в БД пока нет)

Обрабатываемые события GitLab:
  Note Hook        — комментарий к issue/MR
  Issue Hook       — новый/закрытый issue
  Merge Request Hook (opened, merged, closed) — внешний MR → governance; мердж → нотификация
  Push Hook        — push агента → contribution points; внешний → governance
"""

import logging
import os

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.webhook_repo import WebhookRepository
from app.repositories import agent_repo
from app.services.agent_service import get_agent_service

logger = logging.getLogger("webhook_service")

GITHUB_APP_BOT_LOGIN = os.getenv("GITHUB_APP_BOT_LOGIN", "agentspore[bot]")
GITLAB_BOT_LOGINS = {"agentspore-bot", "sporeai-dev"}


class GitHubWebhookService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.repo = WebhookRepository(db)

    # ── Entry point ───────────────────────────────────────────────────────────

    async def handle(self, event: str, data: dict) -> dict:
        repo_full = data.get("repository", {}).get("full_name", "")
        repo_slug = repo_full.split("/")[-1] if "/" in repo_full else repo_full
        if not repo_slug:
            return {"status": "ignored", "reason": "no repo"}

        project = await self.repo.find_project_by_repo_slug(repo_slug)
        if not project:
            return {"status": "ignored", "reason": "project not found"}

        sender = data.get("sender", {})
        ctx = {
            "event": event,
            "action": data.get("action", ""),
            "project": project,
            "sender_login": sender.get("login", "unknown"),
            "sender_type": sender.get("type", "User"),
            "is_bot": sender.get("login") == GITHUB_APP_BOT_LOGIN or sender.get("type") == "Bot",
            "repo_full": repo_full,
            "contributor_count": await self.repo.count_project_members(project["id"]),
        }

        handlers = {
            "issue_comment": self._on_issue_comment,
            "pull_request_review_comment": self._on_pr_review_comment,
            "issues": self._on_issues,
            "pull_request": self._on_pull_request,
            "push": self._on_push,
            "repository": self._on_repository,
            "star": self._on_star,
        }

        handler = handlers.get(event)
        if not handler:
            return {"status": "ignored", "reason": f"unhandled event '{event}'"}

        result = await handler(data, ctx)
        return {**result, "event": event}

    # ── Event handlers ────────────────────────────────────────────────────────

    async def _on_issue_comment(self, data: dict, ctx: dict) -> dict:
        if ctx["is_bot"] or ctx["action"] != "created":
            return {"status": "ignored", "reason": "own bot or not created"}

        project_id = ctx["project"]["id"]
        owner_id = ctx["project"]["creator_agent_id"]
        issue = data.get("issue", {})
        issue_number = issue.get("number")
        if not issue_number:
            return {"status": "ignored"}

        comment_url = data.get("comment", {}).get("html_url", "")
        is_pr = "pull_request" in issue
        source_key = f"{project_id}:pr:{issue_number}" if is_pr else f"{project_id}:issue:{issue_number}"

        commenter_agent = await self.repo.get_agent_by_github_login(ctx["sender_login"])
        completed_task = False
        if commenter_agent:
            await get_agent_service().complete_notification_tasks(self.db, commenter_agent["id"], source_key)
            completed_task = True

        notify_agent_id = owner_id
        if not is_pr:
            issue_creator = await self.repo.get_issue_creator_agent(source_key)
            if issue_creator:
                notify_agent_id = issue_creator

        commenter_id = str(commenter_agent["id"]) if commenter_agent else None
        if notify_agent_id and commenter_id != str(notify_agent_id):
            kind = "PR" if is_pr else "issue"
            task_type = "respond_to_pr_comment" if is_pr else "respond_to_comment"
            await get_agent_service().create_notification_task(
                self.db, notify_agent_id, task_type,
                f"New comment on {kind} #{issue_number} by @{ctx['sender_login']}",
                project_id, comment_url, source_key, priority="high",
            )

        return {"status": "ok", "completed_task": completed_task}

    async def _on_pr_review_comment(self, data: dict, ctx: dict) -> dict:
        if ctx["is_bot"] or ctx["action"] != "created":
            return {"status": "ignored"}

        project_id = ctx["project"]["id"]
        owner_id = ctx["project"]["creator_agent_id"]
        if not owner_id:
            return {"status": "ignored", "reason": "no owner"}

        pr_number = data.get("pull_request", {}).get("number")
        if not pr_number:
            return {"status": "ignored"}

        comment_url = data.get("comment", {}).get("html_url", "")
        await get_agent_service().create_notification_task(
            self.db, owner_id, "respond_to_review_comment",
            f"Inline review comment on PR #{pr_number} by @{ctx['sender_login']}",
            project_id, comment_url, f"{project_id}:pr:{pr_number}", priority="high",
        )
        return {"status": "ok"}

    async def _on_issues(self, data: dict, ctx: dict) -> dict:
        project_id = ctx["project"]["id"]
        owner_id = ctx["project"]["creator_agent_id"]
        action = ctx["action"]

        if action == "opened":
            if ctx["is_bot"] or not owner_id:
                return {"status": "ignored"}
            issue = data.get("issue", {})
            issue_number = issue.get("number")
            if not issue_number:
                return {"status": "ignored"}
            labels = [lb.get("name", "") for lb in issue.get("labels", [])]
            priority = "urgent" if "severity:critical" in labels else "high" if "severity:high" in labels else "medium"
            await get_agent_service().create_notification_task(
                self.db, owner_id, "respond_to_issue",
                f"New issue #{issue_number}: {issue.get('title', '')[:150]}",
                project_id, issue.get("html_url", ""),
                f"{project_id}:issue:{issue_number}", priority=priority,
            )
            return {"status": "ok"}

        if action == "closed":
            issue_number = data.get("issue", {}).get("number")
            if issue_number:
                await get_agent_service().cancel_notification_tasks(self.db, source_key=f"{project_id}:issue:{issue_number}")
            return {"status": "ok"}

        return {"status": "ignored", "reason": f"issues action={action}"}

    async def _on_pull_request(self, data: dict, ctx: dict) -> dict:
        if ctx["is_bot"]:
            return {"status": "ignored", "reason": "own bot PR"}

        project_id = ctx["project"]["id"]
        owner_id = ctx["project"]["creator_agent_id"]
        action = ctx["action"]
        votes_required = max(1, min(2, ctx["contributor_count"]))

        if action == "opened":
            pr = data.get("pull_request", {})
            pr_number = pr.get("number")
            if not pr_number:
                return {"status": "ignored"}
            pr_url = pr.get("html_url", "")
            pr_title = pr.get("title", f"PR #{pr_number}")
            created = await self._queue_governance(
                project_id,
                action_type="external_pr",
                source_ref=pr_url,
                source_number=pr_number,
                actor_login=ctx["sender_login"],
                actor_type=ctx["sender_type"],
                meta={"title": pr_title[:200], "head_ref": pr.get("head", {}).get("ref", ""), "base_ref": pr.get("base", {}).get("ref", "main")},
                votes_required=votes_required,
            )
            if owner_id:
                await get_agent_service().create_notification_task(
                    self.db, owner_id, "respond_to_pr",
                    f"External PR #{pr_number} '{pr_title[:100]}' by @{ctx['sender_login']} — awaiting governance vote",
                    project_id, pr_url, f"{project_id}:pr:{pr_number}", priority="high",
                )
            logger.info("Governance: external PR #%d on %s by @%s", pr_number, ctx["project"]["title"], ctx["sender_login"])
            return {"status": "ok", "governance": "queued" if created else "duplicate"}

        if action == "closed":
            pr = data.get("pull_request", {})
            pr_number = pr.get("number")
            if not pr_number:
                return {"status": "ignored"}
            merged = pr.get("merged", False)
            source_key = f"{project_id}:pr:{pr_number}"
            await self.repo.resolve_governance_by_pr(project_id, pr_number, merged)
            await get_agent_service().cancel_notification_tasks(self.db, source_key=source_key)
            if merged and owner_id:
                merged_by = pr.get("merged_by", {}).get("login", ctx["sender_login"])
                await get_agent_service().create_notification_task(
                    self.db, owner_id, "pr_merged",
                    f"PR #{pr_number} '{pr.get('title', '')[:100]}' merged by @{merged_by}",
                    project_id, pr.get("html_url", ""),
                    f"{project_id}:pr_merged:{pr_number}", priority="medium", source_type="pr_merged",
                )
            status_str = "merged" if merged else "closed"
            logger.info("PR #%d %s on %s", pr_number, status_str, ctx["project"]["title"])
            return {"status": "ok", "pr_status": status_str}

        return {"status": "ignored", "reason": f"pull_request action={action}"}

    async def _on_push(self, data: dict, ctx: dict) -> dict:
        if ctx["is_bot"]:
            return {"status": "ignored", "reason": "own bot push"}

        project_id = ctx["project"]["id"]
        owner_id = ctx["project"]["creator_agent_id"]
        ref = data.get("ref", "")
        branch = ref.replace("refs/heads/", "")
        commits = data.get("commits", [])
        forced = data.get("forced", False)
        compare_url = data.get("compare", "")

        if not commits and not forced:
            return {"status": "ignored", "reason": "empty push"}

        head_msg = (data.get("head_commit") or {}).get("message", "")
        if head_msg.startswith("Merge pull request #"):
            return {"status": "ignored", "reason": "pr_merge_commit"}

        is_agent = await self.repo.get_agent_by_github_login(ctx["sender_login"]) is not None
        if is_agent:
            changed_files: set[str] = set()
            for c in commits:
                changed_files.update(c.get("added", []))
                changed_files.update(c.get("modified", []))
            await self._award_contribution_points(project_id, ctx["sender_login"], len(changed_files), len(commits), vcs="github")
            logger.info("Agent push: @%s → %d files, %d commits on %s", ctx["sender_login"], len(changed_files), len(commits), ctx["project"]["title"])
            return {"status": "ok", "type": "agent_push", "files": len(changed_files), "commits": len(commits)}

        is_main = branch in ("main", "master")
        gv_required = min(3, max(1, ctx["contributor_count"])) if (forced or is_main) else 1
        commit_shas = [c.get("id", "")[:7] for c in commits[:5]]
        created = await self._queue_governance(
            project_id,
            action_type="external_push",
            source_ref=compare_url or f"https://github.com/{ctx['repo_full']}/commits/{branch}",
            source_number=None,
            actor_login=ctx["sender_login"],
            actor_type=ctx["sender_type"],
            meta={"branch": branch, "commit_count": len(commits), "commit_shas": commit_shas, "forced": forced},
            votes_required=gv_required,
        )
        if owner_id:
            push_desc = "Force push" if forced else f"Direct push to {branch}"
            severity = "urgent" if (forced or is_main) else "high"
            await get_agent_service().create_notification_task(
                self.db, owner_id, "respond_to_push",
                f"{push_desc} by @{ctx['sender_login']} ({len(commits)} commits) — governance review needed",
                project_id, compare_url,
                f"{project_id}:push:{ctx['sender_login']}:{branch}", priority=severity,
            )
        logger.warning("Governance: %s push to %s/%s by @%s", "FORCE" if forced else "direct", ctx["project"]["title"], branch, ctx["sender_login"])
        return {"status": "ok", "governance": "queued" if created else "duplicate"}

    async def _on_repository(self, data: dict, ctx: dict) -> dict:
        project_id = ctx["project"]["id"]
        owner_id = ctx["project"]["creator_agent_id"]
        action = ctx["action"]

        if action == "deleted":
            await agent_repo.delete_project_and_related(self.db, project_id)
            if owner_id:
                await agent_repo.recount_agent_projects(self.db, owner_id)
            logger.info("Repository deleted on GitHub — project %s removed from DB", project_id)
            return {"status": "ok", "project_deleted": True}

        if action == "archived":
            await self.repo.update_project_status(project_id, "archived")
            logger.info("Repository archived — project %s → archived", project_id)
            return {"status": "ok"}

        if action == "unarchived":
            await self.repo.update_project_status(project_id, "active")
            logger.info("Repository unarchived — project %s → active", project_id)
            return {"status": "ok"}

        if action == "renamed":
            new_url = data.get("repository", {}).get("html_url", "")
            if new_url:
                await self.repo.update_project_repo_url(project_id, new_url)
                logger.info("Repository renamed — project %s repo_url → %s", project_id, new_url)
            return {"status": "ok"}

        return {"status": "ignored", "reason": f"repository action={action}"}

    async def _on_star(self, data: dict, ctx: dict) -> dict:
        stars = data.get("repository", {}).get("stargazers_count", 0)
        await self.repo.update_project_stars(ctx["project"]["id"], stars)
        logger.info("Star %s on project %s — total: %d", ctx["action"], ctx["project"]["title"], stars)
        return {"status": "ok", "stars": stars}

    # ── Private helpers ───────────────────────────────────────────────────────

    async def _queue_governance(self, project_id, action_type, source_ref, source_number, actor_login, actor_type, meta, votes_required=1) -> bool:
        if await self.repo.governance_item_exists(project_id, action_type, source_number):
            return False
        await self.repo.insert_governance_item(project_id, action_type, source_ref, source_number, actor_login, actor_type, meta, votes_required)
        return True

    async def _award_contribution_points(self, project_id, login: str, files_changed: int, commit_count: int = 1, vcs: str = "github") -> None:
        if files_changed <= 0:
            return
        agent = await self.repo.get_agent_by_vcs_login(login, vcs)
        if not agent:
            return
        agent_id = agent["id"]
        owner_user_id = agent["owner_user_id"]
        points = files_changed * 10
        await self.repo.increment_commits_and_karma(agent_id, commit_count)
        await self.repo.upsert_contributor_points(project_id, agent_id, owner_user_id, points)
        await self.repo.recalculate_share_pct(project_id)
        wallet_info = await self.repo.get_wallet_and_contract(project_id, agent_id)
        if wallet_info and wallet_info.wallet_address and wallet_info.contract_address:
            try:
                from app.services.web3_service import get_web3_service
                web3_svc = get_web3_service()
                mint_tx = await web3_svc.mint_tokens(
                    wallet_info.contract_address, wallet_info.wallet_address,
                    points, reason=f"push:{files_changed}_files",
                )
                if mint_tx:
                    await self.repo.increment_tokens_minted(project_id, agent_id, points)
                    await self.repo.increment_project_total_minted(project_id, points)
            except Exception as exc:
                logger.warning("Token mint failed for project %s agent %s: %s", project_id, agent_id, exc)
        logger.info("Contribution: @%s pushed %d files (%d commits) to project %s (+%d pts)", login, files_changed, commit_count, project_id, points)


# ─────────────────────────────────────────────────────────────────────────────


class GitLabWebhookService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.repo = WebhookRepository(db)

    # ── Entry point ───────────────────────────────────────────────────────────

    async def handle(self, event: str, data: dict) -> dict:
        project_data = data.get("project", {})
        repo_path = project_data.get("path_with_namespace", "")
        repo_slug = repo_path.split("/")[-1] if "/" in repo_path else repo_path
        if not repo_slug:
            return {"status": "ignored", "reason": "no repo"}

        project = await self.repo.find_project_by_repo_slug(repo_slug, vcs_provider="gitlab")
        if not project:
            return {"status": "ignored", "reason": "project not found"}

        user = data.get("user", {})
        sender_login = user.get("username", data.get("user_username", "unknown"))
        ctx = {
            "event": event,
            "project": project,
            "sender_login": sender_login,
            "is_bot": sender_login in GITLAB_BOT_LOGINS,
            "project_data": project_data,
            "contributor_count": await self.repo.count_project_members(project["id"]),
        }

        handlers = {
            "Note Hook": self._on_note,
            "Issue Hook": self._on_issue,
            "Merge Request Hook": self._on_merge_request,
            "Push Hook": self._on_push,
        }

        handler = handlers.get(event)
        if not handler:
            return {"status": "ignored", "reason": f"unhandled event '{event}'"}

        result = await handler(data, ctx)
        return {**result, "event": event}

    # ── Event handlers ────────────────────────────────────────────────────────

    async def _on_note(self, data: dict, ctx: dict) -> dict:
        if ctx["is_bot"]:
            return {"status": "ignored", "reason": "own bot"}

        project_id = ctx["project"]["id"]
        owner_id = ctx["project"]["creator_agent_id"]
        obj_attrs = data.get("object_attributes", {})
        noteable_type = obj_attrs.get("noteable_type", "")
        noteable_id = obj_attrs.get("noteable_iid") or obj_attrs.get("iid")
        comment_url = obj_attrs.get("url", "")
        is_mr = noteable_type == "MergeRequest"
        source_key = f"{project_id}:pr:{noteable_id}" if is_mr else f"{project_id}:issue:{noteable_id}"

        commenter_agent = await self.repo.get_agent_by_gitlab_login(ctx["sender_login"])
        if commenter_agent:
            await get_agent_service().complete_notification_tasks(self.db, commenter_agent["id"], source_key)
            return {"status": "ok", "completed_task": True}

        if not owner_id:
            return {"status": "ignored", "reason": "no owner"}

        if noteable_type == "Issue":
            task_type, msg = "respond_to_comment", f"New comment on issue #{noteable_id} by @{ctx['sender_login']}"
        elif noteable_type == "MergeRequest":
            task_type, msg = "respond_to_pr_comment", f"New comment on MR !{noteable_id} by @{ctx['sender_login']}"
        else:
            return {"status": "ignored", "reason": f"unhandled noteable_type {noteable_type}"}

        await get_agent_service().create_notification_task(self.db, owner_id, task_type, msg, project_id, comment_url, source_key, priority="high")
        return {"status": "ok"}

    async def _on_issue(self, data: dict, ctx: dict) -> dict:
        project_id = ctx["project"]["id"]
        owner_id = ctx["project"]["creator_agent_id"]
        obj_attrs = data.get("object_attributes", {})
        action = obj_attrs.get("action", "")
        issue_iid = obj_attrs.get("iid")

        if action == "close":
            if issue_iid:
                await get_agent_service().cancel_notification_tasks(self.db, source_key=f"{project_id}:issue:{issue_iid}")
            return {"status": "ok"}

        if action != "open" or ctx["is_bot"] or not owner_id:
            return {"status": "ignored", "reason": f"issue action={action}"}

        labels = [lb.get("title", "") for lb in data.get("labels", [])]
        priority = "urgent" if "severity:critical" in labels else "high" if "severity:high" in labels else "medium"
        await get_agent_service().create_notification_task(
            self.db, owner_id, "respond_to_issue",
            f"New issue #{issue_iid}: {obj_attrs.get('title', '')[:150]}",
            project_id, obj_attrs.get("url", ""),
            f"{project_id}:issue:{issue_iid}", priority=priority,
        )
        return {"status": "ok"}

    async def _on_merge_request(self, data: dict, ctx: dict) -> dict:
        if ctx["is_bot"]:
            return {"status": "ignored", "reason": "own bot MR"}

        project_id = ctx["project"]["id"]
        owner_id = ctx["project"]["creator_agent_id"]
        obj_attrs = data.get("object_attributes", {})
        action = obj_attrs.get("action", "")
        votes_required = max(1, min(2, ctx["contributor_count"]))

        if action in ("merge", "close"):
            mr_iid = obj_attrs.get("iid")
            if not mr_iid:
                return {"status": "ignored"}
            merged = action == "merge"
            source_key = f"{project_id}:pr:{mr_iid}"
            await self.repo.resolve_governance_by_pr(project_id, mr_iid, merged)
            await get_agent_service().cancel_notification_tasks(self.db, source_key=source_key)
            if merged and owner_id:
                merged_by = data.get("user", {}).get("username", ctx["sender_login"])
                await get_agent_service().create_notification_task(
                    self.db, owner_id, "pr_merged",
                    f"MR !{mr_iid} '{obj_attrs.get('title', '')[:100]}' merged by @{merged_by}",
                    project_id, obj_attrs.get("url", ""),
                    f"{project_id}:pr_merged:{mr_iid}", priority="medium", source_type="pr_merged",
                )
            status_str = "merged" if merged else "closed"
            logger.info("MR !%s %s on %s", mr_iid, status_str, ctx["project"]["title"])
            return {"status": "ok", "mr_status": status_str}

        if action != "open":
            return {"status": "ignored", "reason": f"mr action={action}"}

        mr_iid = obj_attrs.get("iid")
        mr_url = obj_attrs.get("url", "")
        mr_title = obj_attrs.get("title", f"MR !{mr_iid}")
        created = await self._queue_governance(
            project_id,
            action_type="external_pr",
            source_ref=mr_url,
            source_number=mr_iid,
            actor_login=ctx["sender_login"],
            actor_type="User",
            meta={"title": mr_title[:200], "head_ref": obj_attrs.get("source_branch", ""), "base_ref": obj_attrs.get("target_branch", "main"), "vcs": "gitlab"},
            votes_required=votes_required,
        )
        if owner_id:
            await get_agent_service().create_notification_task(
                self.db, owner_id, "respond_to_pr",
                f"External MR !{mr_iid} '{mr_title[:100]}' by @{ctx['sender_login']} — awaiting governance vote",
                project_id, mr_url, f"{project_id}:pr:{mr_iid}", priority="high",
            )
        logger.info("Governance: external MR !%s on %s by @%s", mr_iid, ctx["project"]["title"], ctx["sender_login"])
        return {"status": "ok", "governance": "queued" if created else "duplicate"}

    async def _on_push(self, data: dict, ctx: dict) -> dict:
        if ctx["is_bot"]:
            return {"status": "ignored", "reason": "own bot push"}

        project_id = ctx["project"]["id"]
        owner_id = ctx["project"]["creator_agent_id"]
        ref = data.get("ref", "")
        branch = ref.replace("refs/heads/", "")
        commits = data.get("commits", [])
        compare_url = data.get("compare", "") or ctx["project_data"].get("web_url", "")

        if not commits:
            return {"status": "ignored", "reason": "empty push"}

        head_msg = (commits[0] if commits else {}).get("message", "")
        if head_msg.startswith("Merge branch") and "into" in head_msg:
            return {"status": "ignored", "reason": "mr_merge_commit"}

        is_agent = await self.repo.get_agent_by_gitlab_login(ctx["sender_login"]) is not None
        if is_agent:
            changed_files: set[str] = set()
            for c in commits:
                changed_files.update(c.get("added", []))
                changed_files.update(c.get("modified", []))
            await self._award_contribution_points(project_id, ctx["sender_login"], len(changed_files), len(commits), vcs="gitlab")
            logger.info("Agent push (GitLab): @%s → %d files on %s", ctx["sender_login"], len(changed_files), ctx["project"]["title"])
            return {"status": "ok", "type": "agent_push", "files": len(changed_files)}

        is_main = branch in ("main", "master")
        gv_required = min(3, max(1, ctx["contributor_count"])) if is_main else 1
        commit_shas = [c.get("id", "")[:7] for c in commits[:5]]
        created = await self._queue_governance(
            project_id,
            action_type="external_push",
            source_ref=compare_url,
            source_number=None,
            actor_login=ctx["sender_login"],
            actor_type="User",
            meta={"branch": branch, "commit_count": len(commits), "commit_shas": commit_shas, "vcs": "gitlab"},
            votes_required=gv_required,
        )
        if owner_id:
            severity = "urgent" if is_main else "high"
            await get_agent_service().create_notification_task(
                self.db, owner_id, "respond_to_push",
                f"Direct push to {branch} by @{ctx['sender_login']} ({len(commits)} commits) — governance review needed",
                project_id, compare_url,
                f"{project_id}:push:{ctx['sender_login']}:{branch}", priority=severity,
            )
        logger.warning("Governance: direct push to %s/%s by @%s", ctx["project"]["title"], branch, ctx["sender_login"])
        return {"status": "ok", "governance": "queued" if created else "duplicate"}

    # ── Private helpers ───────────────────────────────────────────────────────

    async def _queue_governance(self, project_id, action_type, source_ref, source_number, actor_login, actor_type, meta, votes_required=1) -> bool:
        if await self.repo.governance_item_exists(project_id, action_type, source_number):
            return False
        await self.repo.insert_governance_item(project_id, action_type, source_ref, source_number, actor_login, actor_type, meta, votes_required)
        return True

    async def _award_contribution_points(self, project_id, login: str, files_changed: int, commit_count: int = 1, vcs: str = "gitlab") -> None:
        if files_changed <= 0:
            return
        agent = await self.repo.get_agent_by_vcs_login(login, vcs)
        if not agent:
            return
        agent_id = agent["id"]
        owner_user_id = agent["owner_user_id"]
        points = files_changed * 10
        await self.repo.increment_commits_and_karma(agent_id, commit_count)
        await self.repo.upsert_contributor_points(project_id, agent_id, owner_user_id, points)
        await self.repo.recalculate_share_pct(project_id)
        wallet_info = await self.repo.get_wallet_and_contract(project_id, agent_id)
        if wallet_info and wallet_info.wallet_address and wallet_info.contract_address:
            try:
                from app.services.web3_service import get_web3_service
                web3_svc = get_web3_service()
                mint_tx = await web3_svc.mint_tokens(
                    wallet_info.contract_address, wallet_info.wallet_address,
                    points, reason=f"push:{files_changed}_files",
                )
                if mint_tx:
                    await self.repo.increment_tokens_minted(project_id, agent_id, points)
                    await self.repo.increment_project_total_minted(project_id, points)
            except Exception as exc:
                logger.warning("Token mint failed for project %s agent %s: %s", project_id, agent_id, exc)
        logger.info("Contribution: @%s pushed %d files to project %s (+%d pts)", login, files_changed, project_id, points)
