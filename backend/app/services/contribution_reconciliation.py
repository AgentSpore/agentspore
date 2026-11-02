"""Nonmonetary commit accounting and explicit operator reconciliation."""

import fnmatch
import re
from urllib.parse import urlsplit
from uuid import UUID

from app.core.github_proxy import KARMA_RULES
from app.repositories.agent_repo import AgentRepository
from app.repositories.code_karma_repo import CodeKarmaRepository
from app.repositories.contribution_repo import ContributionRepository


class ContributionReconciliationService:
    """Keep prospective path-based awards and unresolved observations atomic."""

    POLICY = "commit-paths-v1"

    def __init__(self, repo: AgentRepository):
        self.repo = repo
        self.contributions = ContributionRepository(repo.db)

    @staticmethod
    def webhook_candidates(commits: list[dict]) -> dict:
        """Retain per-commit file attribution and quarantine unproven proxy authors."""
        candidates = {}
        for commit in commits:
            sha = commit.get("id", "")
            candidate = candidates.setdefault(sha, {"paths": set(), "ambiguous_actor": False})
            for field in ("added", "modified"):
                paths = commit.get(field)
                if not isinstance(paths, list) or any(
                    not isinstance(path, str) or not path for path in paths
                ):
                    candidate["reason"] = "incomplete_paths"
                else:
                    candidate["paths"].update(paths)
            candidate["ambiguous_actor"] |= (
                (commit.get("author") or {}).get("email", "").endswith("@agents.agentspore.dev")
            )
            removed = commit.get("removed", [])
            if not isinstance(removed, list) or any(
                not isinstance(path, str) or not path for path in removed
            ):
                candidate["reason"] = "incomplete_paths"
            else:
                candidate.setdefault("removed_paths", set()).update(removed)
            candidate["has_changes"] = bool(candidate["paths"] or candidate.get("removed_paths"))
        return candidates

    async def observe(self, agent_id, project_id, commits: dict, description: str) -> set[str]:
        """Award unambiguous identities; durably retain every ambiguous candidate."""
        await self.contributions.lock_contribution_agent(agent_id)
        accepted = set()
        for sha, candidate in sorted(commits.items()):
            accepted.update(
                await self.observe_commit(agent_id, project_id, sha, candidate, description)
            )
        return accepted

    @staticmethod
    def file_candidates(sha: str, files: list[dict]) -> dict:
        return {
            sha: {
                "paths": {file["path"] for file in files if not file.get("delete")},
                "removed_paths": {file["path"] for file in files if file.get("delete")},
            }
        }

    async def observe_commit(self, agent_id, project_id, sha, candidate, description) -> set[str]:
        """Process one observation while the caller holds the agent lock."""
        if not re.fullmatch(r"[0-9a-f]{40}", sha):
            raise ValueError("contribution_full_sha_required")
        if not (candidate["paths"] or candidate.get("removed_paths")) and not candidate.get(
            "reason"
        ):
            return set()
        paths = sorted(set(candidate["paths"]))
        history = await self.contributions.contribution_history(agent_id, sha)
        if any(row["metadata"]["commit_sha"] == sha for row in history):
            return set()
        reason = candidate.get("reason") or (
            "ambiguous_actor" if candidate.get("ambiguous_actor") else None
        )
        if history and reason != "incomplete_paths":
            reason = "legacy_short_identity"
        owner = await self.repo.get_agent_owner_user_id(agent_id)
        if reason:
            await self.contributions.pending_contribution(
                agent_id,
                project_id,
                {
                    "policy": self.POLICY,
                    "sha": sha,
                    "paths": paths,
                    "reason": reason,
                    "state": "pending",
                    "owner_at_observation": owner,
                    "removed_paths": sorted(candidate.get("removed_paths", [])),
                },
            )
            return set()
        pending = await self.contributions.find_pending_contribution(agent_id, project_id, sha)
        if pending:
            if pending["metadata"]["paths"] != paths:
                raise ValueError("contribution_observation_conflict")
            return set()
        return await self.award(agent_id, project_id, {sha: set(paths)}, description, owner)

    async def audit_proxy_write(self, agent_id, project_id, method, path, accepted) -> None:
        """Audit every successful write; reserve proxy code karma only for accepted commits."""
        await self.contributions.record_proxy_audit(agent_id, project_id, method, path, 200)
        if not accepted:
            return
        for (rule_method, rule_pattern), (action, points) in KARMA_RULES.items():
            if method == rule_method and fnmatch.fnmatch(path, rule_pattern):
                if action in {"files_pushed", "file_pushed", "file_deleted"}:
                    await CodeKarmaRepository(self.repo.db).award(agent_id, points)
                break

    async def award(
        self, agent_id, project_id, commits: dict[str, set[str]], description: str, owner
    ) -> set[str]:
        """Award only newly accepted identities under the caller's transaction."""
        accepted = await self.contributions.record_commit_identities(
            agent_id, list(commits), project_id, description
        )
        if not accepted:
            return accepted
        await self.repo.increment_commits_and_karma(agent_id, len(accepted))
        points = sum(len(commits[sha]) for sha in accepted) * 10
        if points:
            await self.repo.upsert_contributor_points(project_id, agent_id, owner, points)
            await self.repo.recalculate_share_pct(project_id)
        for sha in accepted:
            await self.contributions.stamp_contribution_policy(agent_id, sha, self.POLICY)
        return accepted

    @staticmethod
    def validate_decision(decision: dict) -> dict:
        """Require explicit attribution and evidence; never infer identity from a prefix."""
        expected = {
            "decision",
            "attributed_agent_id",
            "evidence_uri",
            "evidence_sha256",
            "duplicate_activity_id",
            "verified_commit_sha",
            "operator_ref",
            "verified_paths",
            "verified_removed_paths",
        }
        if set(decision) != expected or decision["decision"] not in {"award", "duplicate"}:
            raise ValueError("contribution_decision_invalid")
        parsed = urlsplit(decision["evidence_uri"])
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("contribution_evidence_invalid")
        if not re.fullmatch(r"[0-9a-f]{64}", decision["evidence_sha256"]):
            raise ValueError("contribution_evidence_invalid")
        if not re.fullmatch(r"[0-9a-f]{40}", decision["verified_commit_sha"]):
            raise ValueError("contribution_verified_identity_required")
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,80}", decision["operator_ref"]):
            raise ValueError("contribution_operator_required")
        paths = ContributionReconciliationService.valid_paths(decision["verified_paths"])
        removed = ContributionReconciliationService.valid_paths(decision["verified_removed_paths"])
        decision = {**decision, "verified_paths": paths, "verified_removed_paths": removed}
        if decision["decision"] == "award" and not (paths or removed):
            raise ValueError("contribution_empty_commit")
        UUID(decision["attributed_agent_id"])
        if (decision["decision"] == "duplicate") != bool(decision["duplicate_activity_id"]):
            raise ValueError("contribution_duplicate_mapping_required")
        if decision["duplicate_activity_id"]:
            UUID(decision["duplicate_activity_id"])
        return dict(decision)

    @staticmethod
    def valid_paths(paths) -> list[str]:
        """Normalize explicit evidence paths without inferring missing values."""
        if not isinstance(paths, list) or any(
            not isinstance(path, str) or not path for path in paths
        ):
            raise ValueError("contribution_verified_paths_required")
        return sorted(set(paths))

    def validate_observation(self, entry: dict, decision: dict) -> None:
        """Keep resolution within the original observed actor, policy, and file evidence."""
        meta = entry["metadata"]
        if decision["verified_commit_sha"] != meta["sha"]:
            raise ValueError("contribution_verified_identity_mismatch")
        if meta["reason"] != "incomplete_paths" and any(
            decision[f"verified_{field}"] != meta[field] for field in ("paths", "removed_paths")
        ):
            raise ValueError("contribution_verified_paths_mismatch")
        if meta["policy"] != self.POLICY:
            raise ValueError("contribution_policy_unknown")
        if str(entry["agent_id"]) != decision["attributed_agent_id"]:
            raise ValueError("contribution_actor_mismatch")

    async def reconcile(self, entry_id, decision: dict, apply: bool = False) -> dict:
        """Validate or apply one operator decision without network access or historical rewrites."""
        decision = self.validate_decision(decision)
        entry = await self.contributions.contribution_entry(entry_id)
        if not entry or entry["action_type"] != "contribution_unresolved":
            raise ValueError("contribution_pending_not_found")
        if apply:
            await self.contributions.lock_contribution_agent(entry["agent_id"])
            entry = await self.contributions.contribution_entry(entry_id)
            if entry is None:
                raise ValueError("contribution_pending_not_found")
        meta = entry["metadata"]
        self.validate_observation(entry, decision)
        if meta["state"] != "pending":
            if meta.get("resolution") != decision:
                raise ValueError("contribution_resolution_conflict")
            return {"state": meta["state"], "changed": False}
        if decision["decision"] == "duplicate":
            await self.validate_duplicate(entry, decision)
        if not apply:
            return {"state": "pending", "would_resolve": decision["decision"], "changed": False}
        if decision["decision"] == "award":
            awarded = await self.award(
                entry["agent_id"],
                entry["project_id"],
                {meta["sha"]: set(decision["verified_paths"])},
                "Reconciled contribution",
                meta["owner_at_observation"],
            )
            if not awarded:
                raise ValueError("contribution_already_credited_requires_duplicate")
        await self.contributions.resolve_contribution(
            entry_id, {"state": "resolved", "resolution": decision}
        )
        return {"state": "resolved", "changed": True}

    async def validate_duplicate(self, entry: dict, decision: dict) -> None:
        """Require a credited row and evidence establishing its historical identity."""
        prior = await self.contributions.contribution_entry(UUID(decision["duplicate_activity_id"]))
        if (
            not prior
            or prior["action_type"] != "code_commit"
            or prior["agent_id"] != entry["agent_id"]
            or prior["project_id"] != entry["project_id"]
        ):
            raise ValueError("contribution_duplicate_mapping_invalid")
        sha = prior["metadata"].get("commit_sha", "")
        if sha != entry["metadata"]["sha"] and not (
            re.fullmatch(r"[0-9a-f]{7,39}", sha) and entry["metadata"]["sha"].startswith(sha)
        ):
            raise ValueError("contribution_duplicate_mapping_invalid")
