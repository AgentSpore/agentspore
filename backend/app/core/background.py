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

The lease is held for the DURATION of the cycle — `run_once` AND the
`interval_s` sleep that follows it — renewed throughout, then released
(ownership-checked compare-and-delete) only once the sleep ends or the task
is cancelled. This makes the lease represent "who owns this cycle", not
"who is currently executing": a fast `run_once` that released early let a
non-leader (already blocked in `_acquire_leader`) take the lock back and run
the same pass again within the same interval — the SAME task drafting or
judging N times over instead of once. `lock_ttl_s` therefore now covers the
full cycle, not just `run_once`.

`lock_ttl_s == interval_s` is the DEFECT BOUNDARY, not a safe value: the
final renewal (at `lock_ttl_s // 3` before expiry) lands at the same instant
the sleep ends, so any event-loop delay, GC pause, or slow Redis round-trip
right at that boundary loses the lease with zero margin. `_renew_lease`
losing the lease also sets `_lease_lost` to cut the sleep short and force a
re-acquire — a second line of defense — but the TTL itself must not depend
on that race winning. The standard formula is
`interval_s + max(MIN_RENEW_INTERVAL_S, interval_s // 3)`: the sleep plus one
extra renewal period of headroom.

How to choose `lock_ttl_s` (read this before changing one):

It answers exactly ONE question — *how long may a CRASHED worker's lease
block its replacement?* It is NOT "how long does run_once take" (renewal
covers that, however long it runs). It IS now "interval_s plus headroom",
because the lease spans the sleep — a TTL at or below interval_s leaves no
margin for the last renewal to land before expiry.

- Upper bound: this task's tolerance for being stalled after a crash. A
  user-visible reconciler wants seconds; a nightly-ish cleanup can wait.
- Lower bound: renewal fires every `lock_ttl_s // 3`, which must stay
  comfortably above a Redis round-trip so a transient blip cannot cost a
  lease mid-run. `MIN_RENEW_INTERVAL_S` pins that floor, and
  `test_scheduler_leader_lock.py` fails the build if any task drops below
  3x it — a too-small TTL must break loudly, not silently lose leases.

Tasks that coordinate via row-level atomic claims (e.g. cron scheduler
with `FOR UPDATE SKIP LOCKED`) set `lock_ttl_s = None` to disable the
leader gate — row-level claim is already exactly-once.

Redis outage behaviour is a per-task choice (`fail_closed`):
- `fail_closed = False` (default) — every worker considers itself leader
  and the task keeps running. Safe only for tasks that are idempotent by
  construction, which is why the existing tasks opt out: they guard with
  `GREATEST`, `WHERE status = 'pending'`, or `ON CONFLICT`, so a duplicate
  run converges to the same state.
- `fail_closed = True` — nobody runs. Required for anything that spends
  someone's budget or moves a rating, where a duplicate run is not
  recoverable (two workers starting the same battle, double Elo).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from abc import ABC, abstractmethod
from datetime import UTC, datetime, timedelta
from typing import Any, NamedTuple
from uuid import uuid4

from loguru import logger
from sqlalchemy import text

from app.core.config import get_settings
from app.core.database import async_session_maker
from app.core.redis_client import get_redis
from app.repositories.hosted_agent_repo import HostedAgentRepository
from app.repositories.mixer_repo import MixerRepository
from app.services.agent_service import AgentService
from app.services.github_service import get_github_service
from app.services.hosted_agent_service import HostedAgentService
from app.services.mixer_service import MixerService
from app.services.openrouter_service import OpenRouterService
from app.services.openviking_service import get_openviking_service

# Extend the lease only while we still own it: compare-and-expire, so a
# task whose lease already expired (and was taken by another worker)
# cannot stomp the new holder's key.
_RENEW_LEASE_LUA = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('expire', KEYS[1], ARGV[2])
end
return 0
"""

# Release only our OWN lease: compare-and-delete. A worker whose lease already
# expired (and was re-acquired by someone else) must never delete the new
# holder's key — that would let a third worker in and defeat the whole gate.
_RELEASE_LOCK_LUA = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
end
return 0
"""

# Floor for the renewal interval (`lock_ttl_s // 3`). A renewal is one Redis
# round-trip on an otherwise idle asyncio task, so single-digit seconds is
# already generous; the point is that no task may set a TTL so small that a
# momentary Redis hiccup costs it the lease while run_once is still going.
# Enforced over ALL_TASKS by test_scheduler_leader_lock.py.
MIN_RENEW_INTERVAL_S = 5


def lock_ttl_with_headroom(interval_s: int) -> int:
    """Standard `lock_ttl_s`: the sleep plus one renewal period of margin.

    See the module docstring: `interval_s` alone is the defect boundary, not
    a safe value.
    """
    return interval_s + max(MIN_RENEW_INTERVAL_S, interval_s // 3)


class ScheduledTask(ABC):
    """Periodic task template. Subclass sets class attributes + run_once."""

    name: str
    interval_s: int
    lock_ttl_s: int | None  # None disables leader lock
    initial_delay_s: int = 0
    non_leader_poll_s: int | None = None  # defaults to interval_s
    # Opt-in: deny execution when Redis is unreachable. Default preserves the
    # long-standing fail-open behaviour of the existing tasks.
    fail_closed: bool = False

    def __init__(self) -> None:
        # Identifies THIS worker's lease, so renewal can verify ownership.
        self._lock_token: str | None = None
        # Set by _renew_lease the moment it observes the lease is gone, so the
        # loop sleeping in start() can wake up and re-acquire instead of
        # sleeping to the end of interval_s on a lease it no longer holds.
        self._lease_lost = asyncio.Event()

    async def start(self) -> None:
        if self.initial_delay_s:
            await asyncio.sleep(self.initial_delay_s)
        while True:
            if self.lock_ttl_s is not None and not await self._acquire_leader():
                await asyncio.sleep(self.non_leader_poll_s or self.interval_s)
                continue
            self._lease_lost = asyncio.Event()
            renewer = (
                asyncio.create_task(self._renew_lease())
                if self._lock_token is not None
                else None
            )
            try:
                await self.run_once()
            except Exception as e:
                logger.warning("Task {} error: {}", self.name, e)
            try:
                # Race the interval sleep against lease loss: a lost lease
                # must cut the sleep short so we re-acquire on the next loop
                # iteration instead of sleeping out a lease a non-leader has
                # already taken. A CancelledError here (shutdown) still hits
                # the finally below and releases.
                sleeper = asyncio.create_task(asyncio.sleep(self.interval_s))
                lease_waiter = asyncio.create_task(self._lease_lost.wait())
                try:
                    await asyncio.wait(
                        {sleeper, lease_waiter}, return_when=asyncio.FIRST_COMPLETED,
                    )
                finally:
                    for t in (sleeper, lease_waiter):
                        t.cancel()
                    # gather, not TaskGroup: this only drains the cancellation
                    # of two tasks already cancelled above — TaskGroup is for
                    # supervising concurrent work with error propagation, not
                    # awaiting an already-decided cancellation to finish.
                    with contextlib.suppress(asyncio.CancelledError):
                        await asyncio.gather(sleeper, lease_waiter)
            finally:
                if renewer is not None:
                    # Await the cancellation before releasing: a renewer caught
                    # mid-EXPIRE would otherwise re-arm the key we just deleted,
                    # leaving an orphan lease that blocks the next lock_ttl_s.
                    renewer.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await renewer
                await self._release_leader()

    def _lock_key(self) -> str:
        return f"scheduler:leader:{self.name}"

    async def _acquire_leader(self) -> bool:
        self._lock_token = None
        try:
            redis = await get_redis()
            token = uuid4().hex
            got = await redis.set(
                self._lock_key(), token,
                ex=self.lock_ttl_s, nx=True,
            )
            if got:
                self._lock_token = token
            return bool(got)
        except Exception as e:
            logger.warning("Leader lock {}: {}", self.name, e)
            if self.fail_closed:
                # Spends budget / moves a rating — a duplicate run is worse
                # than a skipped one. Deny rather than let every worker run.
                logger.warning(
                    "Task {} is fail-closed: skipping cycle while Redis is unreachable",
                    self.name,
                )
                return False
            return True  # fail-open so a Redis outage doesn't halt the task

    async def _renew_lease(self) -> None:
        """Keep the lease alive for the whole cycle: run_once AND the sleep.

        Runs until cancelled (normal: the cycle ended) or until it confirms
        the lease is truly gone, in which case it sets `_lease_lost` so the
        loop — which may be blocked in the interval sleep, not run_once —
        wakes up and re-acquires instead of sleeping past its own lease.

        A transient error must NOT end renewal for the rest of the cycle: a
        single dropped round-trip used to fall through the `while True` and
        stop renewing for up to `interval_s` (3600s for mixer_cleanup). The
        try/except now sits INSIDE the loop, so one failed renewal just logs
        and is retried on the next tick.
        """
        token = self._lock_token
        if token is None or self.lock_ttl_s is None:
            return
        interval = max(1, self.lock_ttl_s // 3)
        while True:
            try:
                await asyncio.sleep(interval)
                redis = await get_redis()
                renewed = await redis.eval(
                    _RENEW_LEASE_LUA, 1, self._lock_key(), token, str(self.lock_ttl_s),
                )
                if not renewed:
                    logger.warning("Task {} lost its lease mid-cycle", self.name)
                    self._lease_lost.set()
                    return
            except asyncio.CancelledError:
                return  # normal: the cycle ended
            except Exception as e:
                logger.warning("Lease renewal {}: {}", self.name, e)

    async def _release_leader(self) -> None:
        """Drop our own lease once the cycle (run_once + interval sleep) ends.

        The lease is held for the DURATION of the cycle, not just run_once.
        Releasing before the interval sleep let a non-leader retake the lock
        and run the same pass again inside the same interval — this task
        running N times per cycle instead of once. With the release deferred
        past the sleep, lock_ttl_s is purely the crash-safety bound: the
        longest a dead worker's lease can block its replacement.

        Never raises into the loop — a Redis outage on release is a lease we
        leave to expire on its own, not a reason to kill the task.
        """
        token, self._lock_token = self._lock_token, None
        if token is None or self.lock_ttl_s is None:
            return
        try:
            redis = await get_redis()
            # register_script(...) over redis.eval(...): eval is typed
            # `Awaitable[str] | str` (one stub shared by the sync and async
            # clients) with loosely-typed *keys_and_args, so a direct call is
            # neither awaitable nor argument-checkable under `ty`. The Script
            # object models keys/args explicitly and its async __call__ returns
            # a real awaitable. Same runtime EVAL; a properly typed boundary.
            release = redis.register_script(_RELEASE_LOCK_LUA)
            await release(keys=[self._lock_key()], args=[token])
        except Exception as e:
            logger.warning("Leader lock release {}: {}", self.name, e)

    @abstractmethod
    async def run_once(self) -> None:
        ...


class GovernanceExpireTask(ScheduledTask):
    name = "governance_expire"
    interval_s = 600
    lock_ttl_s = lock_ttl_with_headroom(interval_s)  # see module docstring

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
    lock_ttl_s = lock_ttl_with_headroom(interval_s)  # see module docstring

    async def run_once(self) -> None:
        async with async_session_maker() as db:
            r1 = await db.execute(
                text("""
                    UPDATE hackathons SET status = 'active', updated_at = NOW()
                    WHERE status = 'upcoming'
                      AND starts_at <= NOW()
                      AND (
                        min_projects_to_start IS NULL
                        OR (SELECT COUNT(*) FROM projects WHERE hackathon_id = hackathons.id)
                           >= min_projects_to_start
                      )
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


class _PRSyncCtx(NamedTuple):
    """The five values every PR-outcome-sync helper needs, bundled so their
    signatures stay under the 5-param budget instead of threading each one
    through separately."""

    db: Any
    github: Any
    ov: Any
    project_id: str
    repo_name: str


class GitHubSyncTask(ScheduledTask):
    """Reconcile agent commit counts against GitHub every 5 minutes.

    Uses GREATEST guards so webhook/atomic-push counters aren't clobbered
    between cycles (see prior incident in project_commit_counting memory).
    """

    name = "github_sync"
    interval_s = 300
    # MEASURED ON PRODUCTION before this fix: old lock_ttl_s=60, released
    # before the sleep, combined with non_leader_poll_s=60 below, made every
    # non-leader retry the lock every 60s and find it open — 180 passes in a
    # 2-hour window against a ~24 expected, a 7.5x over-frequency hitting
    # GitHub's rate-limited API. The headroom formula (see module docstring)
    # closes the gap: the lease now outlives every non-leader's poll.
    lock_ttl_s = lock_ttl_with_headroom(interval_s)
    initial_delay_s = 30
    # Still meaningful with the lease spanning the sleep: bounds how soon a
    # non-leader notices a genuinely CRASHED leader, since a crashed leader's
    # renewer also dies and the lease is only reclaimed via TTL expiry.
    non_leader_poll_s = 60

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
        ov = get_openviking_service()

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
                await self._sync_project(db, github, ov, project, agent_map, agent_commits)

            # GitHub's count is reality for GitHub-hosted work, but it is NOT
            # the whole truth: this query selects vcs_provider = 'github' only,
            # so an agent's GitLab commits are invisible here. Overwriting with
            # the GitHub total would silently zero those.
            #
            # So GREATEST stays — but it is no longer the only correction
            # available. The floor it enforces is now auditable: every
            # increment written by the push paths carries a commit_sha
            # (V81), so an operator can compare code_commits against
            # COUNT(DISTINCT commit_sha) and see exactly where a divergence
            # came from instead of having to trust the number.
            #
            # INVARIANT(commit-count): if this ever becomes a plain
            # assignment, GitLab-only agents lose their entire history.
            for agent_id, total in agent_commits.items():
                await db.execute(
                    text("""
                        UPDATE agents SET code_commits = GREATEST(code_commits, :n)
                        WHERE id = :aid
                    """),
                    {"n": total, "aid": agent_id},
                )

            await self._log_sync_divergence(db, agent_commits)

            await db.commit()

            if agent_commits:
                logger.info(
                    "GitHub sync: updated {} agents across {} projects",
                    len(agent_commits), len(projects),
                )

    async def _sync_project(self, db, github, ov, project, agent_map: dict[str, str],
                            agent_commits: dict[str, int]) -> None:
        """Attribute one project's commits, accumulating totals into agent_commits."""
        project_id = str(project["id"])
        repo_url = project["repo_url"] or ""
        repo_name = repo_url.rstrip("/").split("/")[-1] if repo_url else ""
        if not repo_name:
            return

        closed_prs = await self._sync_merged_prs(db, github, project_id, repo_name)

        all_commits = await self._fetch_all_commits(github, repo_name)

        open_prs = await github.list_pull_requests(repo_name, state="open")
        ctx = _PRSyncCtx(db, github, ov, project_id, repo_name)
        await self._sync_pr_outcomes(ctx, agent_map, closed_prs, open_prs)
        if not all_commits:
            return

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
            # The API response carries the real sha. Recording it makes the
            # sync's own count auditable and lets a push that the webhook
            # already logged be recognised as the same commit.
            await self._record_sha(db, agent_id, project_id, commit.get("sha", ""))

        for agent_id, pts in project_agent_commits.items():
            await db.execute(
                text("""
                    INSERT INTO project_contributors
                        (id, project_id, agent_id, contribution_points)
                    VALUES (uuid_generate_v4(), :pid, :aid, :pts)
                    ON CONFLICT (project_id, agent_id)
                    DO UPDATE SET
                        contribution_points = GREATEST(
                            project_contributors.contribution_points,
                            EXCLUDED.contribution_points
                        ),
                        updated_at = NOW()
                """),
                {"pid": project_id, "aid": agent_id, "pts": pts},
            )

    @staticmethod
    async def _sync_merged_prs(db, github, project_id: str, repo_name: str) -> list[dict]:
        """Recount merged PRs for one repo and store the count on the project.

        Reads only closed PRs (single GitHub call) and counts the ones that
        carry merged_at. GREATEST guards the write for the same reason as the
        commit counter: a repo made temporarily unreachable must not zero out
        a previously observed count.

        Returns the closed PR list so callers (PR-outcome memory sync) reuse
        it instead of issuing a second `state=closed` call for the same repo.
        """
        closed_prs = await github.list_pull_requests(repo_name, state="closed")
        merged = sum(1 for pr in closed_prs if pr.get("merged_at"))
        await db.execute(
            text("""
                UPDATE projects SET merged_prs_count = GREATEST(merged_prs_count, :n)
                WHERE id = :pid
            """),
            {"n": merged, "pid": project_id},
        )
        return closed_prs

    # A PR open longer than this with no merge/close is a stale-open event.
    # Chosen to exceed a normal review cycle (days) without waiting so long
    # the lesson stops being actionable; matches the platform's other
    # "stuck" thresholds (e.g. approval-expiry windows) in order of magnitude.
    STALE_OPEN_DAYS = 7

    @classmethod
    async def _sync_pr_outcomes(
        cls, ctx: _PRSyncCtx,
        agent_map: dict[str, str], closed_prs: list[dict], open_prs: list[dict],
    ) -> None:
        """Write each PR-authoring agent's own outcome into its private memory.

        Best-effort, same contract as battle_runner._record_battle_lessons: a
        failed or disabled OpenViking must never look like it worked, and one
        agent's write failing must not stop the next agent's write. Author
        attribution reuses the commit-author match already used for the
        commit counter (`agent_map`, keyed by git commit author name) — GitHub
        PR `user.login` is the App/owner identity for every agent alike and
        cannot distinguish which agent opened the PR, but the PR's head commit
        carries the real agent author the same way a push commit does.
        """
        if not ctx.ov.enabled:
            logger.warning(
                "PR outcome sync: OpenViking disabled, skipping repo {}", ctx.repo_name
            )
            return

        sha_to_agent = await cls._sha_agent_map(ctx.github, ctx.repo_name, agent_map)

        for pr in closed_prs:
            agent_id = sha_to_agent.get(pr.get("head_sha", "")[:7])
            if not agent_id:
                continue
            event = "merged" if pr.get("merged_at") else "closed"
            reason = ""
            if event == "closed":
                reason = await cls._closing_reason(ctx.github, ctx.repo_name, pr["number"])
            await cls._record_pr_lesson(ctx, agent_id, pr, event, reason)

        cutoff = datetime.now(UTC) - timedelta(days=cls.STALE_OPEN_DAYS)
        for pr in open_prs:
            agent_id = sha_to_agent.get(pr.get("head_sha", "")[:7])
            if not agent_id:
                continue
            created = _parse_github_ts(pr.get("created_at", ""))
            if created is None or created > cutoff:
                continue
            await cls._record_pr_lesson(ctx, agent_id, pr, "stale", "")

    @staticmethod
    async def _sha_agent_map(github, repo_name: str, agent_map: dict[str, str]) -> dict[str, str]:
        """7-char commit sha -> agent_id, reusing the same commit page GitHub
        sync already fetches for the commit counter (no extra HTTP call)."""
        commits = await GitHubSyncTask._fetch_all_commits(github, repo_name)
        result: dict[str, str] = {}
        for commit in commits:
            author_name = commit.get("author", "")
            agent_id = agent_map.get(author_name.lower())
            if agent_id:
                result[commit.get("sha", "")] = agent_id
        return result

    @staticmethod
    async def _closing_reason(github, repo_name: str, pr_number: int) -> str:
        """Last PR-thread comment, if any — the closest thing to a stated
        reason a closed-without-merge PR carries. One extra call, only for
        this rare event (closed PRs are a small minority of the sync)."""
        comments = await github.list_pr_comments(repo_name, pr_number)
        return comments[-1]["body"] if comments else ""

    @staticmethod
    async def _record_pr_lesson(
        ctx: _PRSyncCtx, agent_id: str, pr: dict, event: str, reason: str,
    ) -> None:
        pr_key = f"{ctx.repo_name}#{pr['number']}:{event}"
        inserted = await ctx.db.execute(
            text("""
                INSERT INTO agent_activity
                    (agent_id, project_id, action_type, description, metadata)
                VALUES (
                    CAST(:agent_id AS UUID), CAST(:project_id AS UUID), 'pr_outcome',
                    :description, CAST(:metadata AS jsonb)
                )
                ON CONFLICT DO NOTHING
                RETURNING id
            """),
            {
                "agent_id": agent_id,
                "project_id": ctx.project_id,
                "description": f"PR outcome: {event}",
                "metadata": json.dumps({"pr_key": pr_key, "pr_number": pr["number"]}),
            },
        )
        if inserted.first() is None:
            return  # already delivered this event for this agent

        lesson = _pr_lesson(ctx.repo_name, pr, event, reason)
        try:
            ok = await ctx.ov.add_to_agent_session(agent_id, lesson)
        except Exception as exc:
            logger.warning(
                "PR outcome lesson write to agent {} raised ({}): {}",
                agent_id, pr_key, exc,
            )
            return
        if not ok:
            logger.warning("PR outcome lesson write failed for agent {} ({})", agent_id, pr_key)

    @staticmethod
    async def _record_sha(db, agent_id: str, project_id: str, sha: str) -> None:
        """Log one commit by sha; a sha already present for this agent is a no-op.

        This writer never touches code_commits — the counter is moved by the
        GREATEST statement at the end of the cycle. Adding a fourth increment
        path here would reintroduce exactly the double-counting V81 prevents.
        """
        if not sha:
            return
        await db.execute(
            text("""
                INSERT INTO agent_activity
                    (agent_id, project_id, action_type, description, metadata)
                VALUES (
                    CAST(:agent_id AS UUID), CAST(:project_id AS UUID), 'code_commit',
                    :description, CAST(:metadata AS jsonb)
                )
                ON CONFLICT DO NOTHING
            """),
            {
                "agent_id": agent_id,
                "project_id": project_id,
                "description": f"Commit {sha} (GitHub sync)",
                "metadata": json.dumps({"commit_sha": sha, "source": "github_sync"}),
            },
        )

    @staticmethod
    async def _log_sync_divergence(db, agent_commits: dict[str, int]) -> None:
        """Report agents whose counter exceeds what GitHub can account for.

        This is the whole point of the change: the counter used to be a number
        nothing could contradict. Now every cycle asks reality the question and
        says out loud when the answer disagrees. A positive gap is not
        automatically a bug — GitLab commits are legitimately invisible to this
        sync — but an unexplained gap is now VISIBLE instead of silently
        absorbed by GREATEST.
        """
        if not agent_commits:
            return
        rows = await db.execute(
            text("""
                SELECT a.id, a.handle, a.code_commits,
                       COUNT(DISTINCT aa.metadata->>'commit_sha') AS audited
                FROM agents a
                LEFT JOIN agent_activity aa
                       ON aa.agent_id = a.id
                      AND aa.action_type = 'code_commit'
                      AND aa.metadata->>'commit_sha' IS NOT NULL
                WHERE a.id = ANY(CAST(:ids AS UUID[]))
                GROUP BY a.id, a.handle, a.code_commits
            """),
            {"ids": list(agent_commits.keys())},
        )
        for row in rows.mappings():
            github_total = agent_commits.get(str(row["id"]), 0)
            gap = row["code_commits"] - max(github_total, row["audited"])
            if gap > 0:
                logger.warning(
                    "Commit counter unaccounted for agent {}: counter={} github={} audited_shas={} gap={}",
                    row["handle"], row["code_commits"], github_total, row["audited"], gap,
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
    lock_ttl_s = lock_ttl_with_headroom(interval_s)  # see module docstring

    async def run_once(self) -> None:
        async with async_session_maker() as db:
            # Build the service directly. `get_mixer_service` is a FastAPI-DI
            # factory: called outside a request its `repo=Depends(...)` default is
            # the Depends marker, not a repository, so `cleanup_expired` would blow
            # up on `self.repo.get_expired_sessions()`.
            # Build the service directly. `get_mixer_service` is a FastAPI-DI
            # factory: called outside a request its `repo=Depends(...)` default is
            # the Depends marker, not a repository, so `cleanup_expired` would blow
            # up on `self.repo.get_expired_sessions()`.
            svc = MixerService(db, MixerRepository(db))
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
            # Local import: circular dep — hosted_agent_service imports connection_manager
            # at module top, so the service layer can't be imported at this core module's top.
            from app.repositories.hosted_agent_repo import HostedAgentRepository  # noqa: PLC0415
            from app.services.agent_service import AgentService  # noqa: PLC0415
            from app.services.hosted_agent_service import HostedAgentService  # noqa: PLC0415
            from app.services.openrouter_service import OpenRouterService  # noqa: PLC0415

            repo = HostedAgentRepository(db)
            agent_svc = AgentService(db)
            openrouter = OpenRouterService()
            svc = HostedAgentService(
                repo=repo, agent_service=agent_svc, openrouter=openrouter,
            )
            count = await svc.execute_due_cron_tasks()
            if count:
                logger.info("Cron scheduler: executed {} tasks", count)


class BattleRunTask(ScheduledTask):
    """Drives running battles to a verdict: deadline -> judging -> completed.

    ``fail_closed=True`` — unlike every other task here. This one spends the
    platform's z.ai budget on judge calls, so if Redis is unreachable it must
    NOT run: without Redis there is no llm_gate, and without the gate an
    unbounded number of workers would hammer a 3-concurrency account.

    That flag is admission control, NOT a correctness fence, and the distinction
    is the whole design of battle_runner. Losing the leader lease mid-pass does
    not stop ``run_once()`` — ``_renew_lease`` only logs and returns while the
    loop keeps going. So a former leader and its replacement can execute battle
    work at the same instant. Correctness therefore lives in the per-row
    PostgreSQL claims and their tokens, never in this lease.

    ``interval_s`` is short because ``reconcile_once`` is a SHORT reconciler: it
    claims a bounded batch, takes one step each, and returns. It must never hold
    a battle for the length of the task's life.
    """

    name = "battle_run"
    interval_s = 30
    # 60 already exceeds lock_ttl_with_headroom(30)=40: kept as an explicit
    # constant since this is the most user-visible task on the platform and
    # deserves the wider, hand-picked margin rather than the formula default.
    lock_ttl_s = 60
    initial_delay_s = 20
    fail_closed = True

    async def run_once(self) -> None:
        # Deferred, like every task in this module: background.py loads at
        # startup and must not drag the whole battle stack in with it. Not a
        # cycle — no service or repository imports this module at top level.
        from app.services.battle_judges import (  # noqa: PLC0415 - deferred like every task here: this module loads at startup
            JUDGE_MODEL,
        )
        from app.services.battle_runner import (  # noqa: PLC0415 - deferred like every task here: this module loads at startup
            reconcile_once,
        )
        from app.services.llm_gate import (  # noqa: PLC0415 - deferred like every task here: this module loads at startup
            LLMGate,
        )
        from app.services.openrouter_service import (  # noqa: PLC0415 - deferred like every task here: this module loads at startup
            OpenRouterService,
        )
        from app.services.provider_health import (  # noqa: PLC0415 - deferred like every task here: this module loads at startup
            pick_live_model,
        )

        # The primary seat is whichever configured judge model actually answers
        # THIS pass (pick_live_model probes/caches — see that module), not a
        # hardcoded JUDGE_MODEL: a dead-but-keyed primary (mistral 402) used to
        # resolve credentials fine and then fail every judge call, so "has a
        # key" was never the right gate — "answers" is.
        candidates = get_settings().battle_judge_models or [JUDGE_MODEL]
        primary_model_id = await pick_live_model(candidates)
        # resolve_provider() reads the key itself and returns None when it is
        # unset, so this covers both "no credentials" and "unknown provider" —
        # no separate settings lookup needed. A None provider does NOT skip the
        # pass: only the paid judge panel needs it. reconcile_once still runs the
        # free DB-only lifecycle (arm/admit/start/close_deadline) and the reaper
        # every pass, so a provider outage never freezes battles or cleanup — it
        # only defers scoring. Passing provider (possibly None) lets reconcile
        # gate just the judging phase.
        creds = OpenRouterService().resolve_provider(primary_model_id)
        provider = (
            {
                "api_key": creds["api_key"],
                "base_url": creds["base_url"],
                "model_id": primary_model_id,
            }
            if creds is not None
            else None
        )
        # Circuit breaker (V68 B5): while open, treat the provider as absent so
        # the paid judge phases are skipped this pass, exactly like a missing
        # provider — the free lifecycle transitions and the reaper still run, and
        # the stranded-judging escape hatch stays off (it is provider-gated). A
        # Redis outage fails the breaker CLOSED, so judging is never frozen by the
        # breaker itself.
        from app.services.battle_budget import breaker_is_open  # noqa: PLC0415

        if provider is not None and await breaker_is_open():
            logger.warning("Battle run: judge breaker open — skipping judging this pass")
            provider = None
        counts = await reconcile_once(
            session_factory=async_session_maker,
            gate=LLMGate(await get_redis()),
            provider=provider,
        )
        if provider is None:
            # Logged every pass (not gated on counts) so a stuck provider is
            # observable even when no free phase advanced this pass.
            logger.warning(
                "Battle run (lifecycle ran, judging skipped: no usable provider for {}): {}",
                primary_model_id,
                counts,
            )
        elif any(counts.values()):
            logger.info("Battle run: {}", counts)


class BattleMatchmakerTask(ScheduledTask):
    """Creates the auto-battles that keep /battles alive without a human.

    ``fail_closed=True`` for the same reason BattleRunTask is: every battle this
    creates commits the platform to two answer calls and a judge panel against a
    provider whose free tier tops out around three concurrent requests. Without
    Redis there is no leader lock, so four workers would each create a battle per
    tick — four times the intended rate, at the exact moment the gate that would
    have throttled the calls is also gone.

    The cadence is a setting rather than a constant: it is the rate limit, and an
    operator hitting 429s must be able to slow it down. ``interval_s`` is a
    PROPERTY, not a class attribute, because the base loop re-reads it every
    cycle — read once at class-definition time it would have needed a restart,
    while ``battle_auto_enabled`` right beside it took effect live, and the two
    halves of one switch must behave the same way.
    """

    name = "battle_matchmaker"
    initial_delay_s = 60
    fail_closed = True

    @property
    def interval_s(self) -> int:  # type: ignore[override]  # base declares a plain int
        return get_settings().battle_auto_interval_seconds

    @property
    def lock_ttl_s(self) -> int:  # type: ignore[override]  # base declares a plain int | None
        # A property, not a fixed constant: interval_s is itself a live
        # setting, so a fixed lock_ttl_s could fall below it (and lose its
        # headroom) the moment an operator lowers the interval.
        return lock_ttl_with_headroom(self.interval_s)

    async def run_once(self) -> None:
        settings = get_settings()
        if not settings.battle_auto_enabled:
            return
        from app.services.battle_service import (  # noqa: PLC0415 - deferred like every task here: this module loads at startup
            BattleMatchmaker,
        )

        await BattleMatchmaker(async_session_maker).tick()


class BattleHarvesterTask(ScheduledTask):
    """Refills the generated task pool from open sources (GitHub/SO/HN).

    ``fail_closed=True`` for the same reason ``BattleMatchmakerTask`` is: each
    drafting call spends the judge panel's shared provider budget, so without
    Redis every worker running the pass at once would multiply that spend by
    the worker count at the exact moment nothing is left to throttle it.
    """

    name = "battle_harvester"
    initial_delay_s = 90
    fail_closed = True

    @property
    def interval_s(self) -> int:  # type: ignore[override]  # base declares a plain int
        return get_settings().battle_harvester_interval_seconds

    @property
    def lock_ttl_s(self) -> int:  # type: ignore[override]  # base declares a plain int | None
        # See BattleMatchmakerTask.lock_ttl_s for why this is a property.
        return lock_ttl_with_headroom(self.interval_s)

    async def run_once(self) -> None:
        settings = get_settings()
        if not settings.battle_harvester_enabled:
            return
        from app.repositories.battle_repo import (
            # Deferred like every other task here: this module loads at
            # startup and must not drag the battle stack in with it.
            BattleRepository,  # noqa: PLC0415
        )
        from app.services.battle_task_harvester import (
            TaskHarvesterService,  # noqa: PLC0415
        )
        from app.services.battle_task_sources import (
            default_sources,  # noqa: PLC0415
        )

        async with async_session_maker() as db:
            harvester = TaskHarvesterService(
                repo=BattleRepository(db),
                sources=default_sources(),
                session_factory=async_session_maker,
            )
            result = await harvester.harvest(
                pool_target=settings.battle_harvester_pool_target,
                max_per_pass=settings.battle_harvester_max_per_pass,
            )
            # BattleRepository.create_task does not commit (see its docstring);
            # the submission path in api/v1/battles.py commits after it, and so
            # must this one. Without it the session rolls back on close, every
            # harvested task is discarded, and the pass still reports created=N
            # while spending the provider budget again on the next cycle.
            await db.commit()
        if result.created or result.source_failures:
            logger.info(
                "Battle harvester: created={} dropped={} source_failures={}",
                result.created, result.dropped, result.source_failures,
            )


class HostedAgentReconcileTask(ScheduledTask):
    """Corrects hosted-agent rows that claim running with no container behind them.

    get_hosted_agent already probes the runner, but only for the one agent an
    owner opens. Measured on production: three rows had claimed running since
    19 July, updated_at never advancing past started_at, while the runner host
    had zero hosted containers — nobody had opened those pages in three weeks,
    so nothing ever ran the probe. The dashboard and the public agent list both
    read that status, so the platform was advertising agents that do not exist.

    fail_closed stays False: a pass only moves a row from running to stopped
    after the runner explicitly said the agent is not there, and repeating that
    correction is idempotent.
    """

    name = "hosted_agent_reconcile"
    interval_s = 900
    lock_ttl_s = lock_ttl_with_headroom(interval_s)  # see module docstring
    initial_delay_s = 120

    async def run_once(self) -> None:
        async with async_session_maker() as db:
            svc = HostedAgentService(
                repo=HostedAgentRepository(db),
                agent_service=AgentService(db),
                openrouter=OpenRouterService(),
            )
            # No commit here: HostedAgentRepository.update_status commits each
            # correction itself, so there is nothing left uncommitted and a
            # commit would read as if the writes depended on it.
            corrected = await svc.reconcile_running_agents()
        if corrected:
            logger.info("Hosted agent reconcile: corrected={}", corrected)


ALL_TASKS: tuple[type[ScheduledTask], ...] = (
    GovernanceExpireTask,
    HackathonAdvanceTask,
    GitHubSyncTask,
    MixerCleanupTask,
    CronSchedulerTask,
    BattleRunTask,
    BattleMatchmakerTask,
    BattleHarvesterTask,
    HostedAgentReconcileTask,
)


def spawn_background_tasks() -> list[asyncio.Task]:
    """Instantiate every registered task and schedule it on the event loop."""
    return [asyncio.create_task(cls().start()) for cls in ALL_TASKS]


_PR_LESSON_REASON_MAX_CHARS = 400


def _pr_lesson(repo_name: str, pr: dict, event: str, reason: str) -> str:
    """A short, first-person memory entry for one agent's own PR outcome.

    ``reason`` is the closing comment's free text (unbounded), truncated for
    the same reason battle_runner._battle_lesson truncates a judge's verdict:
    this is a memory entry re-read on a future heartbeat, not a transcript.
    """
    title = pr.get("title", f"PR #{pr['number']}")
    if event == "merged":
        outcome = "was merged"
    elif event == "closed":
        outcome = "was closed without merging"
    else:
        outcome = "has been open a while with no review yet"
    trimmed = reason.strip()
    if len(trimmed) > _PR_LESSON_REASON_MAX_CHARS:
        trimmed = trimmed[:_PR_LESSON_REASON_MAX_CHARS].rstrip() + "…"
    suffix = f' Comment: "{trimmed}"' if trimmed else ""
    return f'PR lesson: my pull request "{title}" in {repo_name} {outcome}.{suffix}'


def _parse_github_ts(value: str) -> datetime | None:
    """Parse a GitHub API timestamp ("...Z" ISO 8601). None on empty/bad input."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
