"""A4 P1 admission: author gate, flag, hostname, quotas and status on real PostgreSQL."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool
from test_deployment_control_plane import deployment_client as deployment_client
from test_deployment_control_plane import deployment_engine as deployment_engine
from test_deployment_control_plane import deployment_postgres as deployment_postgres
from test_deployment_control_plane import deployment_project as deployment_project
from test_deployment_control_plane import deployment_session as deployment_session

from app.core.config import get_settings
from app.services.agent_service import AgentService
from app.services.deployment_admission import RESERVED_HANDLES, candidate_handle

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]
SHA = "a" * 40
GITHUB = "https://github.com/AgentSpore/"


@pytest.mark.parametrize(
    ("repo_url", "expected"),
    [
        (GITHUB + "My-App", "my-app"),
        (GITHUB + "api", None),
        (GITHUB + "podmemory", None),
        (GITHUB + "xn--e1afmkfd", None),
        (GITHUB + "ab--app", None),
        (GITHUB + "p-" + "a" * 32, None),
        (GITHUB + "p-anything", None),
        (GITHUB + "ab", None),
        (GITHUB + "a" * 41, None),
        (GITHUB + "-lead", None),
        (GITHUB + "trail-", None),
        (GITHUB + "under_score", None),
        (None, None),
    ],
)
def test_handle_denylist_and_shape(repo_url, expected):
    assert candidate_handle(repo_url) == expected


def test_denylist_covers_platform_subdomains():
    assert {
        "api",
        "www",
        "admin",
        "mail",
        "app",
        "static",
        "docs",
        "status",
        "auth",
        "login",
        "cdn",
        "podmemory",
        "otkrytka",
        "pereklichka",
    } <= RESERVED_HANDLES


@pytest_asyncio.fixture(loop_scope="module")
async def enabled(monkeypatch, deployment_session):
    """Flag on and an empty queue for the test."""
    monkeypatch.setattr(get_settings(), "a4_deploy_enabled", True)
    await deployment_session.execute(
        text(
            "UPDATE deployment_worker_lease SET deployment_id = NULL, worker_id = NULL, "
            "expires_at = NULL"
        )
    )
    await deployment_session.execute(text("DELETE FROM project_deployments"))
    await deployment_session.commit()


@pytest_asyncio.fixture(loop_scope="module")
async def new_project(deployment_session):
    """Factory: agent (optionally with an owner user) plus a project named by its repo."""

    async def create(repo_name: str, owner: UUID | None = None) -> dict:
        agent_id, project_id, key = uuid4(), uuid4(), uuid4().hex
        await deployment_session.execute(
            text(
                "INSERT INTO agents (id, name, handle, api_key_hash, owner_user_id) "
                "VALUES (:id, :n, :h, :hash, :owner)"
            ),
            {
                "id": agent_id,
                "n": f"A4-{agent_id}",
                "h": f"a4-{agent_id}",
                "hash": AgentService.hash_api_key(key),
                "owner": owner,
            },
        )
        await deployment_session.execute(
            text(
                "INSERT INTO projects (id, title, creator_agent_id, repo_url, status) "
                "VALUES (:id, 'p', :a, :repo, 'deployed')"
            ),
            {"id": project_id, "a": agent_id, "repo": GITHUB + repo_name},
        )
        await deployment_session.commit()
        return {"id": project_id, "agent": agent_id, "key": key}

    return create


@pytest_asyncio.fixture(loop_scope="module")
async def seed(deployment_session):
    """Factory: deployments of a project whose created_at is `age` in the past."""

    async def put(project_id, count=1, status="failed", age=timedelta(0)):
        for _ in range(count):
            await deployment_session.execute(
                text(
                    "INSERT INTO project_deployments (project_id, idempotency_key, commit_sha, "
                    "repository_url, status, created_at, activated_at, creator_agent_id, "
                    "owner_user_id) "
                    "SELECT p.id, :k, :sha, 'https://x/y', :s, "
                    "clock_timestamp() - CAST(:age AS interval), "
                    "CASE WHEN :s = 'active' THEN clock_timestamp() END, p.creator_agent_id, "
                    "a.owner_user_id FROM projects p JOIN agents a ON a.id = p.creator_agent_id "
                    "WHERE p.id = :p"
                ),
                {"p": project_id, "k": uuid4(), "sha": SHA, "s": status, "age": age},
            )
        await deployment_session.commit()

    return put


async def deploy(client, project, key=None, sha=SHA):
    return await client.post(
        f"/api/v1/agents/projects/{project['id']}/deploy",
        headers={"X-API-Key": project["key"], "Idempotency-Key": str(key or uuid4())},
        json={"commit_sha": sha},
    )


async def test_deploy_disabled_flag_503(deployment_client, new_project):
    response = await deploy(deployment_client, await new_project("flag-off-app"))
    assert response.status_code == 503
    assert response.json()["detail"]["reason_code"] == "deploy_disabled"


async def test_non_author_agent_403(enabled, deployment_client, new_project, deployment_session):
    project, other = await new_project("author-app"), await new_project("other-app")
    response = await deploy(deployment_client, {**project, "key": other["key"]})
    assert response.status_code == 403
    assert response.json()["detail"]["reason_code"] == "not_project_author"
    count = await deployment_session.scalar(
        text("SELECT count(*) FROM project_deployments WHERE project_id = :p"),
        {"p": project["id"]},
    )
    assert count == 0


async def test_accepted_replay_and_conflicts(enabled, deployment_client, new_project):
    project, key = await new_project("replay-app"), uuid4()
    first = await deploy(deployment_client, project, key)
    assert first.status_code == 202
    assert first.json()["status"] == "queued"
    again = await deploy(deployment_client, project, key)
    assert again.json()["deployment_id"] == first.json()["deployment_id"]
    other_body = await deploy(deployment_client, project, key, sha="b" * 40)
    assert other_body.status_code == 409
    assert other_body.json()["detail"]["reason_code"] == "idempotency_conflict"
    busy = await deploy(deployment_client, project)
    assert busy.status_code == 409
    assert busy.json()["detail"]["reason_code"] == "deployment_in_progress"


async def test_hostname_slug_fallback_and_permanence(
    enabled, deployment_client, new_project, deployment_session
):
    taken = await new_project("shared-name")
    await deploy(deployment_client, taken)
    rival, denied = await new_project("shared-name"), await new_project("admin")
    await deploy(deployment_client, rival)
    await deploy(deployment_client, denied)
    rows = dict(
        (
            await deployment_session.execute(
                text("SELECT original_project_id, handle FROM service_hostnames")
            )
        ).all()
    )
    assert rows[taken["id"]] == "shared-name"
    assert rows[rival["id"]] == f"p-{rival['id'].hex}"
    assert rows[denied["id"]] == f"p-{denied['id'].hex}"
    await deployment_session.execute(text("DELETE FROM projects WHERE id = :p"), {"p": taken["id"]})
    await deployment_session.commit()
    kept = await deployment_session.scalar(
        text("SELECT count(*) FROM service_hostnames WHERE handle = 'shared-name'")
    )
    assert kept == 1


async def test_quota_agent_daily_429(enabled, deployment_client, new_project, seed):
    project = await new_project("daily-app")
    await seed(project["id"], 5)
    response = await deploy(deployment_client, project)
    assert response.status_code == 429
    assert response.json()["detail"]["reason_code"] == "agent_daily_quota"
    assert 0 < int(response.headers["Retry-After"]) <= 86400


async def test_quota_daily_counts_utc_day_only(enabled, deployment_client, new_project, seed):
    project = await new_project("yesterday-app")
    since_midnight = datetime.now(UTC) - datetime.now(UTC).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    await seed(project["id"], 5, age=since_midnight + timedelta(seconds=1))
    assert (await deploy(deployment_client, project)).status_code == 202


async def test_quota_agent_weekly_429(enabled, deployment_client, new_project, seed):
    project = await new_project("weekly-app")
    await seed(project["id"], 20, age=timedelta(days=2))
    response = await deploy(deployment_client, project)
    assert response.status_code == 429
    assert response.json()["detail"]["reason_code"] == "agent_weekly_quota"
    assert int(response.headers["Retry-After"]) > 0


async def test_quota_owner_active_429(
    enabled, deployment_client, new_project, seed, deployment_session
):
    owner = uuid4()
    await deployment_session.execute(
        text("INSERT INTO users (id, email, hashed_password, name) VALUES (:i, :e, 'x', 'o')"),
        {"i": owner, "e": f"{owner}@example.com"},
    )
    await deployment_session.commit()
    for index in range(3):
        await seed((await new_project(f"owned-{index}", owner))["id"], status="active")
    response = await deploy(deployment_client, await new_project("owned-new", owner))
    assert response.status_code == 429
    assert response.json()["detail"]["reason_code"] == "owner_active_quota"


async def test_owner_active_quota_counts_agents_claimed_after_deploy(
    enabled, deployment_client, new_project, seed, deployment_session
):
    """Snapshots of unowned agents stay NULL after a claim; the ceiling still counts them."""
    owner = uuid4()
    await deployment_session.execute(
        text("INSERT INTO users (id, email, hashed_password, name) VALUES (:i, :e, 'x', 'o')"),
        {"i": owner, "e": f"{owner}@example.com"},
    )
    for index in range(3):
        project = await new_project(f"claimed-late-{index}")
        await seed(project["id"], status="active")
        await deployment_session.execute(
            text("UPDATE agents SET owner_user_id = :o WHERE id = :a"),
            {"o": owner, "a": project["agent"]},
        )
    await deployment_session.commit()
    response = await deploy(deployment_client, await new_project("claimed-late-new", owner))
    assert response.status_code == 429
    assert response.json()["detail"]["reason_code"] == "owner_active_quota"


async def test_creator_agent_snapshot_is_immutable_once_set(
    enabled, new_project, seed, deployment_session
):
    project = await new_project("snapshot-app")
    await seed(project["id"])
    for value in (":other", "NULL"):
        with pytest.raises(DBAPIError) as error:
            await deployment_session.execute(
                text(f"UPDATE project_deployments SET creator_agent_id = {value}"),
                {"other": uuid4()} if value == ":other" else {},
            )
        assert getattr(error.value.orig, "sqlstate", None) == "23514"
        await deployment_session.rollback()


async def test_platform_capacity_429(enabled, deployment_client, new_project, seed):
    for index in range(10):
        await seed((await new_project(f"cap-{index}"))["id"], status="active")
    response = await deploy(deployment_client, await new_project("cap-new"))
    assert response.status_code == 429
    assert response.json()["detail"]["reason_code"] == "platform_capacity"
    assert "Retry-After" in response.headers


async def test_queue_full_429(enabled, deployment_client, new_project, seed):
    for index in range(10):
        await seed((await new_project(f"queued-{index}"))["id"], status="queued")
    response = await deploy(deployment_client, await new_project("queued-new"))
    assert response.status_code == 429
    assert response.json()["detail"]["reason_code"] == "queue_full"


async def test_status_lists_last_five(enabled, deployment_client, new_project, seed):
    project = await new_project("status-app")
    await seed(project["id"], 6)
    response = await deployment_client.get(
        f"/api/v1/agents/projects/{project['id']}/deploy", headers={"X-API-Key": project["key"]}
    )
    body = response.json()
    assert response.status_code == 200
    assert (body["status"], body["active_revision"], body["url"]) == ("failed", None, None)
    assert len(body["deployments"]) == 5


async def test_status_author_only(enabled, deployment_client, new_project):
    project, other = await new_project("status-own"), await new_project("status-other")
    response = await deployment_client.get(
        f"/api/v1/agents/projects/{project['id']}/deploy", headers={"X-API-Key": other["key"]}
    )
    assert response.status_code == 403


async def test_admission_waits_on_global_advisory_lock(
    enabled, deployment_client, new_project, deployment_postgres
):
    """A concurrent admission holds the lock, so quota counts cannot be read stale."""
    project = await new_project("lock-app")
    waiting = text(
        "SELECT count(*) FROM pg_stat_activity WHERE wait_event_type = 'Lock' "
        "AND wait_event = 'advisory'"
    )
    url = deployment_postgres.get_connection_url().replace("psycopg2", "asyncpg")
    side = create_async_engine(url, poolclass=NullPool)
    try:
        async with side.connect() as holder:
            await holder.execute(text("SELECT pg_advisory_xact_lock(hashtext('a4_admission'))"))
            pending = asyncio.ensure_future(deploy(deployment_client, project))
            for _ in range(100):
                if await holder.scalar(waiting):
                    break
                await asyncio.sleep(0.1)
            assert await holder.scalar(waiting) == 1
            assert not pending.done()
            await holder.rollback()
    finally:
        await side.dispose()
    assert (await pending).status_code == 202


async def test_unknown_project_404(enabled, deployment_client, new_project):
    ghost = {**await new_project("ghost-app"), "id": uuid4()}
    response = await deploy(deployment_client, ghost)
    assert response.status_code == 404
    assert response.json()["detail"]["reason_code"] == "project_not_found"


async def test_quota_survives_project_deletion(
    enabled, deployment_client, new_project, seed, deployment_session
):
    first = await new_project("deleted-app")
    await seed(first["id"], 5)
    await deployment_session.execute(text("DELETE FROM projects WHERE id = :p"), {"p": first["id"]})
    await deployment_session.commit()
    second_id = uuid4()
    await deployment_session.execute(
        text(
            "INSERT INTO projects (id, title, creator_agent_id, repo_url, status) "
            "VALUES (:id, 'p', :a, :repo, 'deployed')"
        ),
        {"id": second_id, "a": first["agent"], "repo": GITHUB + "replacement-app"},
    )
    await deployment_session.commit()
    response = await deploy(deployment_client, {**first, "id": second_id})
    assert response.status_code == 429
    assert response.json()["detail"]["reason_code"] == "agent_daily_quota"


async def test_replay_reports_stored_status(
    enabled, deployment_client, new_project, deployment_session
):
    project, key = await new_project("replay-status-app"), uuid4()
    first = await deploy(deployment_client, project, key)
    await deployment_session.execute(
        text("UPDATE project_deployments SET status = 'failed' WHERE id = :i"),
        {"i": UUID(first.json()["deployment_id"])},
    )
    await deployment_session.commit()
    again = await deploy(deployment_client, project, key)
    assert again.json() == {"deployment_id": first.json()["deployment_id"], "status": "failed"}
