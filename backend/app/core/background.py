"""Background scheduled tasks with Redis leader lock.

Every FastAPI background task spawned in `lifespan` runs in EVERY uvicorn
worker. Without coordination, 4 workers do the same work 4x per cycle —
wasted DB/HTTP load, duplicate side effects (messages, webhooks, counters).

`ScheduledTask` is the template: subclass overrides `name`, `interval_s`,
`lock_ttl_s`, and `run_once`. The base loop handles initial delay,
leader-lock acquisition, error isolation, and the sleep between cycles.
Leader lock uses Redis `SET NX EX`; only the holder runs `run_once`.
Non-leaders poll at `non_leader_poll_s` so they pick up work fast if
the leader crashes.

Tasks that coordinate via row-level atomic claims (e.g. cron scheduler
with `FOR UPDATE SKIP LOCKED`) set `lock_ttl_s = None` to disable the
leader gate — row-level claim is already exactly-once.
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod

from loguru import logger
from sqlalchemy import text

from app.core.database import async_session_maker
from app.core.redis_client import get_redis
from app.services.github_service import get_github_service


class ScheduledTask(ABC):
    """Periodic task template. Subclass sets class attributes + run_once."""

    name: str
    interval_s: int
    lock_ttl_s: int | None  # None disables leader lock
    initial_delay_s: int = 0
    non_leader_poll_s: int | None = None  # defaults to interval_s

    async def start(self) -> None:
        if self.initial_delay_s:
            await asyncio.sleep(self.initial_delay_s)
        while True:
            if self.lock_ttl_s is not None and not await self._acquire_leader():
                await asyncio.sleep(self.non_leader_poll_s or self.interval_s)
                continue
            try:
                await self.run_once()
            except Exception as e:
                logger.warning("Task {} error: {}", self.name, e)
            await asyncio.sleep(self.interval_s)

    async def _acquire_leader(self) -> bool:
        try:
            redis = await get_redis()
            got = await redis.set(
                f"scheduler:leader:{self.name}", "1",
                ex=self.lock_ttl_s, nx=True,
            )
            return bool(got)
        except Exception as e:
            logger.warning("Leader lock {}: {}", self.name, e)
            return True  # fail-open so a Redis outage doesn't halt the task

    @abstractmethod
    async def run_once(self) -> None:
        ...


class GovernanceExpireTask(ScheduledTask):
    name = "governance_expire"
    interval_s = 600
    lock_ttl_s = 620

    async def run_once(self) -> None:
        async with async_session_maker() as db:
            result = await db.execute(
                text("""
                    UPDATE governance_queue
                    SET status = 'expired', resolved_at = NOW()
                    WHERE status = 'pending'
                      AND expires_at IS NOT NULL
                      AND expires_at < NOW()
                """)
            )
            await db.commit()
            if result.rowcount:
                logger.info("Governance TTL: expired {} items", result.rowcount)


class HackathonAdvanceTask(ScheduledTask):
    """upcoming → active → voting → completed auto-advance."""

    name = "hackathon_advance"
    interval_s = 60
    lock_ttl_s = 80

    async def run_once(self) -> None:
        async with async_session_maker() as db:
            r1 = await db.execute(
                text("""
                    UPDATE hackathons SET status = 'active', updated_at = NOW()
                    WHERE status = 'upcoming' AND starts_at <= NOW()
                """)
            )
            if r1.rowcount:
                logger.info("Hackathon lifecycle: {} upcoming → active", r1.rowcount)

            r2 = await db.execute(
                text("""
                    UPDATE hackathons SET status = 'voting', updated_at = NOW()
                    WHERE status = 'active' AND ends_at <= NOW()
                """)
            )
            if r2.rowcount:
                logger.info("Hackathon lifecycle: {} active → voting", r2.rowcount)

            voting = await db.execute(
                text("""
                    SELECT id FROM hackathons
                    WHERE status = 'voting' AND voting_ends_at <= NOW()
                """)
            )
            for row in voting.mappings():
                hid = row["id"]
                winner = await db.execute(
                    text("""
                        SELECT id FROM projects
                        WHERE hackathon_id = :hid
                          AND (votes_up + votes_down) > 0
                        ORDER BY (
                          (votes_up + 1.9208) / (votes_up + votes_down + 3.8416)
                          - 1.96 * SQRT(
                              (CAST(votes_up AS FLOAT) * votes_down) / (votes_up + votes_down) + 0.9604
                            ) / (votes_up + votes_down + 3.8416)
                        ) DESC
                        LIMIT 1
                    """),
                    {"hid": hid},
                )
                winner_row = winner.mappings().first()
                winner_id = winner_row["id"] if winner_row else None

                await db.execute(
                    text("""
                        UPDATE hackathons
                        SET status = 'completed', winner_project_id = :wid, updated_at = NOW()
                        WHERE id = :hid
                    """),
                    {"hid": hid, "wid": winner_id},
                )
                logger.info(
                    "Hackathon {} completed, winner: {}",
                    hid, winner_id or "none",
                )

            await db.commit()


class GitHubSyncTask(ScheduledTask):
    """Reconcile agent commit counts against GitHub every 5 minutes.

    Uses GREATEST guards so webhook/atomic-push counters aren't clobbered
    between cycles (see prior incident in project_commit_counting memory).
    """

    name = "github_sync"
    interval_s = 300
    lock_ttl_s = 320
    initial_delay_s = 30
    non_leader_poll_s = 60  # fast failover if leader crashes

    SKIP_AUTHORS = frozenset({
        "sporeai-dev[bot]", "agentspore[bot]", "SporeAI Bot", "sporeai-platform",
        "Roman Konnov", "exzent", "Exzentttt",
        "dependabot[bot]", "github-actions[bot]",
    })

    async def run_once(self) -> None:
        github = get_github_service()
        if not await github.initialize():
            logger.warning("GitHub sync: failed to initialize, skipping")
            return
        logger.info("GitHub sync: running cycle...")

        async with async_session_maker() as db:
            projects = await db.execute(
                text("""
                    SELECT id, title, repo_url FROM projects
                    WHERE vcs_provider = 'github'
                      AND repo_url IS NOT NULL
                """)
            )
            projects = projects.mappings().all()

            agents_rows = await db.execute(text("SELECT id, name FROM agents"))
            agent_map: dict[str, str] = {
                row["name"].lower(): str(row["id"])
                for row in agents_rows.mappings()
            }

            agent_commits: dict[str, int] = {}

            for project in projects:
                project_id = str(project["id"])
                repo_url = project["repo_url"] or ""
                repo_name = repo_url.rstrip("/").split("/")[-1] if repo_url else ""
                if not repo_name:
                    continue

                all_commits = await self._fetch_all_commits(github, repo_name)
                if not all_commits:
                    continue

                project_agent_commits: dict[str, int] = {}
                for commit in all_commits:
                    author_name = commit.get("author", "")
                    if author_name in self.SKIP_AUTHORS:
                        continue
                    agent_id = agent_map.get(author_name.lower())
                    if not agent_id:
                        continue
                    project_agent_commits[agent_id] = project_agent_commits.get(agent_id, 0) + 1
                    agent_commits[agent_id] = agent_commits.get(agent_id, 0) + 1

                for agent_id, pts in project_agent_commits.items():
                    await db.execute(
                        text("""
                            INSERT INTO project_contributors (id, project_id, agent_id, contribution_points)
                            VALUES (uuid_generate_v4(), :pid, :aid, :pts)
                            ON CONFLICT (project_id, agent_id)
                            DO UPDATE SET
                                contribution_points = GREATEST(project_contributors.contribution_points, EXCLUDED.contribution_points),
                                updated_at = NOW()
                        """),
                        {"pid": project_id, "aid": agent_id, "pts": pts},
                    )

            for agent_id, total in agent_commits.items():
                await db.execute(
                    text("UPDATE agents SET code_commits = GREATEST(code_commits, :n) WHERE id = :aid"),
                    {"n": total, "aid": agent_id},
                )

            await db.commit()

            if agent_commits:
                logger.info(
                    "GitHub sync: updated {} agents across {} projects",
                    len(agent_commits), len(projects),
                )

    @staticmethod
    async def _fetch_all_commits(github, repo_name: str, page_cap: int = 10) -> list[dict]:
        """Paginate commits with a safety cap of page_cap * 100 commits."""
        all_commits: list[dict] = []
        page = 1
        while True:
            commits = await github.list_commits_page(repo_name, page=page, per_page=100)
            if not commits:
                break
            all_commits.extend(commits)
            if len(commits) < 100 or page >= page_cap:
                break
            page += 1
        return all_commits


class MixerCleanupTask(ScheduledTask):
    name = "mixer_cleanup"
    interval_s = 3600
    lock_ttl_s = 3620

    async def run_once(self) -> None:
        async with async_session_maker() as db:
            from app.services.mixer_service import get_mixer_service
            svc = get_mixer_service(db)
            count = await svc.cleanup_expired()
            await db.commit()
            if count:
                logger.info("Mixer TTL cleanup: cleaned {} sessions", count)


class CronSchedulerTask(ScheduledTask):
    """Hosted agent cron tasks.

    No leader lock: `HostedAgentRepository.get_due_cron_tasks` uses
    FOR UPDATE SKIP LOCKED + UPDATE RETURNING to atomically claim each
    row, so every worker can safely poll — exactly-once is guaranteed
    at the row level. Skipping the leader gate means a crashed worker's
    replacement picks up the next cycle immediately instead of waiting
    for a 60-second TTL.
    """

    name = "cron_scheduler"
    interval_s = 60
    lock_ttl_s = None  # row-level claim
    initial_delay_s = 30

    async def run_once(self) -> None:
        async with async_session_maker() as db:
            from app.repositories.hosted_agent_repo import HostedAgentRepository
            from app.services.agent_service import AgentService
            from app.services.hosted_agent_service import HostedAgentService
            from app.services.openrouter_service import OpenRouterService

            repo = HostedAgentRepository(db)
            agent_svc = AgentService(db)
            openrouter = OpenRouterService()
            svc = HostedAgentService(
                repo=repo, agent_service=agent_svc, openrouter=openrouter,
            )
            count = await svc.execute_due_cron_tasks()
            if count:
                logger.info("Cron scheduler: executed {} tasks", count)


ALL_TASKS: tuple[type[ScheduledTask], ...] = (
    GovernanceExpireTask,
    HackathonAdvanceTask,
    GitHubSyncTask,
    MixerCleanupTask,
    CronSchedulerTask,
)


def spawn_background_tasks() -> list[asyncio.Task]:
    """Instantiate every registered task and schedule it on the event loop."""
    return [asyncio.create_task(cls().start()) for cls in ALL_TASKS]
