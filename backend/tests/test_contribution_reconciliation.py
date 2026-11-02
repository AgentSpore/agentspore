"""Durable unresolved contributions retain attribution without consuming commit identities."""

import asyncio
import json
from types import SimpleNamespace
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from test_contribution_dedupe import accounting as accounting
from test_contribution_dedupe import commit, push
from test_contribution_dedupe import pg_url as pg_url

from app.cli import contribution_reconciliation as cli
from app.repositories.activity_repo import get_activity_events
from app.repositories.agent_repo import AgentRepository
from app.repositories.contribution_repo import ContributionRepository
from app.services.contribution_reconciliation import ContributionReconciliationService


async def seed_legacy(session, agent, project, short="a" * 7):
    return await session.scalar(
        text("""
        INSERT INTO agent_activity (agent_id, project_id, action_type, description, metadata)
        VALUES (:agent, :project, 'code_commit', 'historical', CAST(:meta AS jsonb)) RETURNING id
    """),
        {"agent": agent, "project": project, "meta": json.dumps({"commit_sha": short})},
    )


async def required_entry(repo, entry_id):
    row = await ContributionRepository(repo.db).contribution_entry(entry_id)
    assert row is not None
    return row


async def pending(session):
    return await session.scalar(
        text("SELECT id FROM agent_activity WHERE action_type='contribution_unresolved'")
    )


def decision(agent, *, kind="award", duplicate=None):
    return {
        "decision": kind,
        "attributed_agent_id": str(agent),
        "evidence_uri": "https://example.org/evidence/fixture",
        "evidence_sha256": "1" * 64,
        "duplicate_activity_id": str(duplicate) if duplicate else None,
        "verified_commit_sha": "a" * 40,
        "operator_ref": "synthetic-reviewer",
        "verified_paths": ["new"],
        "verified_removed_paths": [],
    }


@pytest.mark.asyncio
async def test_legacy_overlap_is_durable_deduplicated_and_publicly_redacted(accounting):
    maker, agent, project = accounting
    async with maker() as session:
        legacy = await seed_legacy(session, agent, project)
        await session.commit()
        await push(session, project, [commit("a" * 40, "private/path")])
        await push(session, project, [commit("a" * 40, "private/path")])
        assert await session.scalar(text("SELECT COUNT(*) FROM agent_activity")) == 2
        assert await session.scalar(text("SELECT code_commits FROM agents")) == 0
        assert await session.scalar(text("SELECT COUNT(*) FROM project_contributors")) == 0
        repo = AgentRepository(session)
        row = await required_entry(repo, await pending(session))
        assert row["metadata"]["paths"] == ["private/path"]
        public = await repo.get_github_activity(
            "aa.agent_id = :agent", {"agent": agent, "limit": 50}
        )
        exposed = next(row for row in public if row["action_type"] == "contribution_unresolved")
        assert set(exposed["metadata"]) == {"state", "reason", "policy"}
        assert (await required_entry(repo, legacy))["metadata"] == {"commit_sha": "a" * 7}


@pytest.mark.asyncio
async def test_two_full_identities_with_same_legacy_prefix_are_not_merged(accounting):
    maker, agent, project = accounting
    async with maker() as session:
        await seed_legacy(session, agent, project)
        await session.commit()
        await push(
            session, project, [commit("a" * 40, "first"), commit("a" * 7 + "b" * 33, "second")]
        )
        assert (
            await session.scalar(
                text(
                    "SELECT COUNT(*) FROM agent_activity "
                    "WHERE action_type='contribution_unresolved'"
                )
            )
            == 2
        )
        assert await session.scalar(text("SELECT code_commits FROM agents")) == 0


@pytest.mark.asyncio
async def test_mixed_proxy_author_quarantines_only_ambiguous_commit(accounting):
    maker, _, project = accounting
    ambiguous = {
        **commit("a" * 40, "uncertain"),
        "author": {"email": "untrusted@agents.agentspore.dev"},
    }
    async with maker() as session:
        await push(session, project, [ambiguous, commit("b" * 40, "normal")])
        assert (
            await session.scalar(text("SELECT contribution_points FROM project_contributors")) == 10
        )
        assert await session.scalar(text("SELECT code_commits FROM agents")) == 1
        row = await required_entry(AgentRepository(session), await pending(session))
        assert row["metadata"]["reason"] == "ambiguous_actor"
        await push(session, project, [commit("a" * 40, "uncertain")])
        assert await session.scalar(text("SELECT code_commits FROM agents")) == 1


@pytest.mark.asyncio
async def test_dry_run_is_read_only_then_apply_is_idempotent(accounting):
    maker, agent, project = accounting
    async with maker() as session:
        await seed_legacy(session, agent, project)
        await session.commit()
        await push(session, project, [commit("a" * 40, "new")])
        entry = await pending(session)
        await session.commit()
        await session.execute(text("SET TRANSACTION READ ONLY"))
        svc = ContributionReconciliationService(AgentRepository(session))
        result = await svc.reconcile(entry, decision(agent))
        assert not result["changed"]
        await session.rollback()
        assert (await svc.reconcile(entry, decision(agent), True))["changed"]
        await session.commit()
        assert not (await svc.reconcile(entry, decision(agent), True))["changed"]
        assert (
            await session.scalar(text("SELECT contribution_points FROM project_contributors")) == 10
        )
        altered = {**decision(agent), "evidence_sha256": "2" * 64}
        with pytest.raises(ValueError, match="contribution_resolution_conflict"):
            await svc.reconcile(entry, altered, True)


@pytest.mark.asyncio
async def test_duplicate_resolution_preserves_historical_row_and_evidence(accounting):
    maker, agent, project = accounting
    async with maker() as session:
        legacy = await seed_legacy(session, agent, project)
        await session.commit()
        await push(session, project, [commit("a" * 40, "new")])
        repo = AgentRepository(session)
        entry = await pending(session)
        attestation = decision(agent, kind="duplicate", duplicate=legacy)
        await ContributionReconciliationService(repo).reconcile(entry, attestation, True)
        await session.commit()
        assert (await required_entry(repo, entry))["metadata"]["resolution"] == attestation
        assert (await required_entry(repo, legacy))["metadata"] == {"commit_sha": "a" * 7}
        assert await session.scalar(text("SELECT code_commits FROM agents")) == 0
        await push(session, project, [commit("a" * 40, "new")])
        assert await session.scalar(text("SELECT code_commits FROM agents")) == 0


@pytest.mark.asyncio
async def test_concurrent_resolution_awards_once(accounting):
    maker, agent, project = accounting
    async with maker() as session:
        await seed_legacy(session, agent, project)
        await session.commit()
        await push(session, project, [commit("a" * 40, "new")])
        entry = await pending(session)

    async def resolve():
        async with maker() as session:
            result = await ContributionReconciliationService(AgentRepository(session)).reconcile(
                entry, decision(agent), True
            )
            await session.commit()
            return result["changed"]

    results = await asyncio.wait_for(asyncio.gather(resolve(), resolve()), 10)
    assert sorted(results) == [False, True]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value,error",
    [
        ("attributed_agent_id", str(UUID(int=1)), "contribution_actor_mismatch"),
        ("verified_commit_sha", "b" * 40, "contribution_verified_identity_mismatch"),
        ("evidence_uri", "http://example.org/proof", "contribution_evidence_invalid"),
        ("evidence_sha256", "prefix-proof", "contribution_evidence_invalid"),
    ],
)
async def test_invalid_resolution_keeps_pending(accounting, field, value, error):
    maker, agent, project = accounting
    async with maker() as session:
        await seed_legacy(session, agent, project)
        await session.commit()
        await push(session, project, [commit("a" * 40, "new")])
        entry = await pending(session)
        with pytest.raises(ValueError, match=error):
            await ContributionReconciliationService(AgentRepository(session)).reconcile(
                entry, {**decision(agent), field: value}, True
            )
        await session.rollback()
        assert (await required_entry(AgentRepository(session), entry))["metadata"][
            "state"
        ] == "pending"
        assert await session.scalar(text("SELECT code_commits FROM agents")) == 0
        assert await session.scalar(text("SELECT COUNT(*) FROM project_contributors")) == 0


@pytest.mark.asyncio
async def test_pending_owner_retained_when_owner_changes(accounting):
    maker, agent, project = accounting
    async with maker() as session:
        owner = await session.scalar(text("INSERT INTO users DEFAULT VALUES RETURNING id"))
        later = await session.scalar(text("INSERT INTO users DEFAULT VALUES RETURNING id"))
        await session.execute(text("UPDATE agents SET owner_user_id=:owner"), {"owner": owner})
        await seed_legacy(session, agent, project)
        await session.commit()
        await push(session, project, [commit("a" * 40, "new")])
        entry = await pending(session)
        await session.execute(text("UPDATE agents SET owner_user_id=:owner"), {"owner": later})
        await session.commit()
        await ContributionReconciliationService(AgentRepository(session)).reconcile(
            entry, decision(agent), True
        )
        await session.commit()
        assert await session.scalar(text("SELECT owner_user_id FROM project_contributors")) == owner


@pytest.mark.asyncio
@pytest.mark.parametrize("mismatch", ["project", "agent", "identity"])
async def test_duplicate_mapping_rejects_unrelated_accounted_rows(accounting, mismatch):
    maker, agent, project = accounting
    async with maker() as session:
        await seed_legacy(session, agent, project)
        other_agent, other_project, short = agent, project, "a" * 7
        if mismatch == "agent":
            other_agent = await session.scalar(
                text("INSERT INTO agents DEFAULT VALUES RETURNING id")
            )
        elif mismatch == "project":
            short = "a" * 8
            other_project = await session.scalar(
                text("INSERT INTO projects DEFAULT VALUES RETURNING id")
            )
        else:
            short = "b" * 7
        unrelated = await seed_legacy(session, other_agent, other_project, short)
        await session.commit()
        await push(session, project, [commit("a" * 40, "new")])
        with pytest.raises(ValueError, match="contribution_duplicate_mapping_invalid"):
            await ContributionReconciliationService(AgentRepository(session)).reconcile(
                await pending(session),
                decision(agent, kind="duplicate", duplicate=unrelated),
                True,
            )
        await session.rollback()
        assert await session.scalar(text("SELECT SUM(code_commits) FROM agents")) == 0


@pytest.mark.asyncio
async def test_resolution_failure_rolls_back_credit_and_state(accounting):
    maker, agent, project = accounting
    async with maker() as session:
        await seed_legacy(session, agent, project)
        await session.commit()
        await push(session, project, [commit("a" * 40, "new")])
        entry = await pending(session)
        await session.execute(
            text(
                "ALTER TABLE agent_activity ADD CHECK "
                "(metadata->>'state' IS DISTINCT FROM 'resolved')"
            )
        )
        await session.commit()
        with pytest.raises(IntegrityError):
            await ContributionReconciliationService(AgentRepository(session)).reconcile(
                entry, decision(agent), True
            )
        await session.rollback()
        assert (await required_entry(AgentRepository(session), entry))["metadata"][
            "state"
        ] == "pending"
        assert await session.scalar(text("SELECT code_commits FROM agents")) == 0
        assert await session.scalar(text("SELECT COUNT(*) FROM project_contributors")) == 0


@pytest.mark.asyncio
async def test_conflicting_observation_retains_original_pending(accounting):
    maker, agent, project = accounting
    async with maker() as session:
        await seed_legacy(session, agent, project)
        await session.commit()
        await push(session, project, [commit("a" * 40, "original")])
        with pytest.raises(ValueError, match="contribution_observation_conflict"):
            await push(session, project, [commit("a" * 40, "different")])
        await session.rollback()
        assert (await required_entry(AgentRepository(session), await pending(session)))["metadata"][
            "paths"
        ] == ["original"]


@pytest.mark.asyncio
async def test_cli_dry_run_then_explicit_apply(accounting, monkeypatch, tmp_path):
    maker, agent, project = accounting
    async with maker() as session:
        await seed_legacy(session, agent, project)
        await session.commit()
        await push(session, project, [commit("a" * 40, "new")])
        entry = await pending(session)
    decision_file = tmp_path / "decision.json"
    decision_file.write_text(json.dumps(decision(agent)))
    monkeypatch.setattr(cli, "async_session_maker", maker)
    args = SimpleNamespace(action="resolve", entry=entry, decision=decision_file, apply=False)
    result = await cli.run(args)
    assert result["evidence"] == "operator_attestation_not_automatically_verified"
    async with maker() as session:
        assert await session.scalar(text("SELECT code_commits FROM agents")) == 0
    args.apply = True
    assert (await cli.run(args))["changed"]
    async with maker() as session:
        assert await session.scalar(text("SELECT code_commits FROM agents")) == 1


@pytest.mark.asyncio
async def test_incomplete_paths_require_explicit_operator_path_evidence(accounting):
    maker, agent, project = accounting
    async with maker() as session:
        await push(session, project, [{"id": "a" * 40, "added": ["known"]}])
        repo = AgentRepository(session)
        entry = await pending(session)
        assert (await required_entry(repo, entry))["metadata"]["reason"] == "incomplete_paths"
        assert await session.scalar(text("SELECT code_commits FROM agents")) == 0
        attestation = {**decision(agent), "verified_paths": ["known", "recovered"]}
        await ContributionReconciliationService(repo).reconcile(entry, attestation, True)
        await session.commit()
        assert (
            await session.scalar(text("SELECT contribution_points FROM project_contributors")) == 20
        )
        assert (await required_entry(repo, entry))["metadata"]["paths"] == ["known"]


@pytest.mark.asyncio
async def test_global_feed_redacts_unknown_unresolved_metadata(accounting):
    maker, agent, project = accounting
    async with maker() as session:
        await AgentRepository(session).insert_activity(
            agent,
            "contribution_unresolved",
            "review",
            project,
            {
                "state": "private-state",
                "reason": "private-reason",
                "policy": "private-policy",
                "paths": ["private"],
            },
        )
        await session.commit()
        events = await get_activity_events(session)
        assert events[0]["metadata"] == {
            "state": "unknown",
            "reason": "review_required",
            "policy": "unknown",
        }


@pytest.mark.asyncio
async def test_concurrent_delivery_after_winner_rollback_earns_once(accounting):
    maker, agent, project = accounting
    async with maker() as winner, maker() as observer:
        svc = ContributionReconciliationService(AgentRepository(winner))
        await svc.observe(agent, project, {"a" * 40: {"paths": {"new"}}}, "fixture")
        started = asyncio.Event()
        waiter_pid = []

        async def deliver():
            async with maker() as waiter:
                waiter_pid.append(await waiter.scalar(text("SELECT pg_backend_pid()")))
                started.set()
                await push(waiter, project, [commit("a" * 40, "new")])

        task = asyncio.create_task(deliver())
        try:
            await asyncio.wait_for(started.wait(), 10)
            async with asyncio.timeout(10):
                while not await observer.scalar(
                    text("SELECT cardinality(pg_blocking_pids(:pid)) > 0"), {"pid": waiter_pid[0]}
                ):
                    await asyncio.sleep(0)
            assert not task.done()
            await winner.rollback()
            await asyncio.wait_for(task, 10)
            assert await observer.scalar(text("SELECT code_commits FROM agents")) == 1
            assert (
                await observer.scalar(text("SELECT contribution_points FROM project_contributors"))
                == 10
            )
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_legacy_overlap_with_incomplete_paths_accepts_explicit_evidence(accounting):
    maker, agent, project = accounting
    async with maker() as session:
        legacy = await seed_legacy(session, agent, project)
        await session.commit()
        await push(session, project, [{"id": "a" * 40, "added": ["known"]}])
        repo = AgentRepository(session)
        entry = await pending(session)
        assert await session.scalar(text("SELECT code_commits FROM agents")) == 0
        assert await session.scalar(text("SELECT COUNT(*) FROM project_contributors")) == 0
        await session.commit()
        await session.execute(text("SET TRANSACTION READ ONLY"))
        svc = ContributionReconciliationService(repo)
        attestation = {**decision(agent), "verified_paths": ["known", "recovered"]}
        assert not (await svc.reconcile(entry, attestation))["changed"]
        await session.rollback()
        assert (await svc.reconcile(entry, attestation, True))["changed"]
        await session.commit()
        assert not (await svc.reconcile(entry, attestation, True))["changed"]
        assert await session.scalar(text("SELECT code_commits FROM agents")) == 1
        assert (
            await session.scalar(text("SELECT contribution_points FROM project_contributors")) == 20
        )
        assert (await required_entry(repo, legacy))["metadata"] == {"commit_sha": "a" * 7}
        retained = (await required_entry(repo, entry))["metadata"]
        assert retained["reason"] == "incomplete_paths"
        assert retained["paths"] == ["known"]
        assert retained["resolution"] == attestation
