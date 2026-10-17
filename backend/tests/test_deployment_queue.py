"""Durable A4 queue behavior on PostgreSQL; no builds or sandbox execution."""

import asyncio
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker
from test_deployment_control_plane import deployment_engine as deployment_engine
from test_deployment_control_plane import deployment_postgres as deployment_postgres
from test_deployment_control_plane import deployment_project as deployment_project
from test_deployment_control_plane import deployment_session as deployment_session

from app.repositories.deployment_repo import DeploymentConflictError, DeploymentRepository
from app.schemas.deployments import (
    DeploymentEnqueue,
    SandboxNamespaceAbsenceProof,
    SandboxStopProof,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]


@pytest_asyncio.fixture(loop_scope="module")
async def queue_world(deployment_engine, deployment_project):
    """Reset only this owned test database's queue; retain hostname reservations."""
    maker = async_sessionmaker(deployment_engine, expire_on_commit=False)
    first, second = UUID(deployment_project["id"]), uuid4()
    async with maker.begin() as db:
        await db.execute(text("TRUNCATE deployment_worker_lease, project_deployments"))
        await db.execute(text("INSERT INTO deployment_worker_lease (slot) VALUES (1)"))
        await db.execute(
            text(
                "INSERT INTO projects (id, title, creator_agent_id) "
                "SELECT :second, 'A4 second', creator_agent_id FROM projects WHERE id = :first"
            ),
            {"first": first, "second": second},
        )
    requests = tuple(
        DeploymentEnqueue(
            project_id=project,
            idempotency_key=uuid4(),
            commit_sha="a" * 40,
            repository_url="https://github.com/AgentSpore/a4-trusted-fixture",
        )
        for project in (first, second)
    )
    return maker, requests


async def test_twenty_identical_requests_share_one_durable_job(queue_world):
    maker, (request, _) = queue_world

    async def enqueue():
        async with maker.begin() as db:
            return await DeploymentRepository(db).enqueue(request)

    results = await asyncio.gather(*(enqueue() for _ in range(20)))
    assert len(set(results)) == 1
    async with maker() as db:
        assert await db.scalar(text("SELECT count(*) FROM project_deployments")) == 1
        assert await db.scalar(text("SELECT status FROM project_deployments")) == "queued"
        assert (
            await db.scalar(
                text("SELECT handle FROM service_hostnames WHERE original_project_id = :project"),
                {"project": request.project_id},
            )
            == f"p-{request.project_id.hex}"
        )


@pytest.mark.parametrize(
    "change,reason",
    [
        ({"commit_sha": "b" * 40}, "idempotency_conflict"),
        ({"idempotency_key": uuid4()}, "deployment_in_progress"),
    ],
)
async def test_conflicting_request_preserves_existing_operation(queue_world, change, reason):
    maker, (request, _) = queue_world
    async with maker.begin() as db:
        original = await DeploymentRepository(db).enqueue(request)
    with pytest.raises(DeploymentConflictError, match=reason):
        async with maker.begin() as db:
            await DeploymentRepository(db).enqueue(request.model_copy(update=change))
    async with maker() as db:
        assert await db.scalar(text("SELECT id FROM project_deployments")) == original
        assert await db.scalar(text("SELECT commit_sha FROM project_deployments")) == "a" * 40


async def test_global_claim_expiry_requires_owned_stop_and_fences_old_worker(queue_world):
    maker, (first, second) = queue_world
    async with maker.begin() as db:
        first_id = await DeploymentRepository(db).enqueue(first)
        second_id = await DeploymentRepository(db).enqueue(second)

    async def claim():
        async with maker.begin() as db:
            return await DeploymentRepository(db).claim(uuid4())

    claims = await asyncio.gather(claim(), claim())
    assert sum(lease is not None for lease in claims) == 1
    old = next(lease for lease in claims if lease is not None)
    assert old.deployment_id == first_id
    async with maker.begin() as db:
        repo = DeploymentRepository(db)
        assert await repo.record_sandbox(old, "a4-owned-sandbox")
        await db.execute(
            text(
                "UPDATE deployment_worker_lease "
                "SET expires_at = clock_timestamp() - interval '1 second'"
            )
        )
    async with maker.begin() as db:
        repo = DeploymentRepository(db)
        assert await repo.claim(uuid4()) is None
        assert not await repo.record_sandbox(old, "late-overwrite")
        assert (
            await db.scalar(
                text("SELECT status FROM project_deployments WHERE id = :id"), {"id": first_id}
            )
            == "recovering"
        )
        wrong = old.model_copy(update={"worker_id": uuid4()})
        assert not await repo.confirm_stopped(
            SandboxStopProof(lease=wrong, sandbox_id="a4-owned-sandbox")
        )
        assert not await repo.confirm_stopped(
            SandboxStopProof(lease=old, sandbox_id="different-sandbox")
        )
    async with maker.begin() as db:
        assert await DeploymentRepository(db).confirm_stopped(
            SandboxStopProof(lease=old, sandbox_id="a4-owned-sandbox")
        )
    async with maker.begin() as db:
        fresh = await DeploymentRepository(db).claim(uuid4())
        assert fresh is not None and fresh.deployment_id == second_id
        assert fresh.generation > old.generation
        assert not await DeploymentRepository(db).record_sandbox(old, "stale")


async def test_project_deletion_preserves_job_identity_and_sandbox_ownership(queue_world):
    maker, (request, _) = queue_world
    async with maker.begin() as db:
        job = await DeploymentRepository(db).enqueue(request)
        lease = await DeploymentRepository(db).claim(uuid4())
        assert lease is not None
        assert await DeploymentRepository(db).record_sandbox(lease, "a4-retained-sandbox")
        await db.execute(text("DELETE FROM projects WHERE id = :id"), {"id": request.project_id})
    async with maker() as db:
        row = (
            (
                await db.execute(
                    text("SELECT * FROM project_deployments WHERE id = :id"), {"id": job}
                )
            )
            .mappings()
            .one()
        )
        assert row["project_id"] == request.project_id
        assert row["worker_id"] == lease.worker_id
        assert row["generation"] == lease.generation
        assert row["sandbox_id"] == "a4-retained-sandbox"
        assert (
            await db.scalar(
                text(
                    "SELECT original_project_id FROM service_hostnames "
                    "WHERE original_project_id = :id"
                ),
                {"id": request.project_id},
            )
            == request.project_id
        )


@pytest.mark.parametrize(
    "statement,replacement",
    [
        ("UPDATE project_deployments SET project_id = :value", uuid4()),
        ("UPDATE project_deployments SET idempotency_key = :value", uuid4()),
        ("UPDATE project_deployments SET commit_sha = :value", "b" * 40),
        (
            "UPDATE project_deployments SET repository_url = :value",
            "https://example.invalid/changed",
        ),
    ],
)
async def test_job_identity_cannot_be_rewritten(queue_world, statement, replacement):
    maker, (request, _) = queue_world
    async with maker.begin() as db:
        await DeploymentRepository(db).enqueue(request)
    with pytest.raises(DBAPIError) as error:
        async with maker.begin() as db:
            await db.execute(text(statement), {"value": replacement})
    assert getattr(error.value.orig, "sqlstate", None) == "23514"


async def test_claim_persists_namespace_before_sandbox_creation(queue_world):
    """A pre-creation crash requires fenced trusted namespace inventory to recover."""
    maker, (request, second) = queue_world
    async with maker.begin() as db:
        job = await DeploymentRepository(db).enqueue(request)
        next_job = await DeploymentRepository(db).enqueue(second)
        lease = await DeploymentRepository(db).claim(uuid4())
        assert lease is not None
    async with maker() as db:
        row = (
            (
                await db.execute(
                    text("SELECT * FROM project_deployments WHERE id = :id"), {"id": job}
                )
            )
            .mappings()
            .one()
        )
    assert row["sandbox_id"] is None
    assert row.get("sandbox_namespace") == f"a4-{lease.deployment_id.hex}-{lease.generation}"
    proof = SandboxNamespaceAbsenceProof(lease=lease, namespace=lease.sandbox_namespace)
    async with maker.begin() as db:
        repo = DeploymentRepository(db)
        assert not await repo.confirm_namespace_absent(proof)
        await db.execute(
            text(
                "UPDATE deployment_worker_lease "
                "SET expires_at = clock_timestamp() - interval '1 second'"
            )
        )
        assert not await repo.confirm_namespace_absent(proof)
        assert await repo.claim(uuid4()) is None
        assert not await repo.confirm_namespace_absent(
            proof.model_copy(update={"namespace": "a4-unowned"})
        )
        wrong = lease.model_copy(update={"generation": lease.generation + 1})
        assert not await repo.confirm_namespace_absent(
            proof.model_copy(update={"lease": wrong, "namespace": wrong.sandbox_namespace})
        )
        assert await repo.claim(uuid4()) is None
        assert await repo.confirm_namespace_absent(proof)
    async with maker.begin() as db:
        repo = DeploymentRepository(db)
        fresh = await repo.claim(uuid4())
        assert fresh is not None and fresh.deployment_id == next_job
        assert fresh.generation > lease.generation
        assert not await repo.confirm_namespace_absent(proof)
