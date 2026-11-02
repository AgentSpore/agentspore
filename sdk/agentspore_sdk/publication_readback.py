"""Bound provider readback for exact branch, parent/tree/blob and draft PR outcomes."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from .publication_state import validate_hex

if TYPE_CHECKING:
    from .publication import PublicationClient


class PublicationReadback:
    """Keep provider verification separate from reservation and dispatch ordering."""

    def __init__(self, client: PublicationClient):
        self.client = client

    async def branch(self, project: str, branch: str) -> str:
        """Require the exact generated name and a complete immutable head SHA."""
        value = await self.client.proxy(
            project, "GET", "/branches/" + quote(branch, safe="")
        )
        if value["name"] != branch:
            raise ValueError("publication_branch_conflict")
        sha = value["commit"]["sha"]
        validate_hex(sha, 40)
        return sha

    async def verify(self, record: dict, response: dict | None = None) -> dict:
        """Prove one reserved outcome without inferring absence or issuing mutations."""
        intent = record["intent"]
        result = record["result"]
        if intent["step"] == "create_project":
            source = result or response
            if source is None:
                raise ValueError("publication_creation_unknown")
            project = source.get("project_id", source.get("id"))
            binding = await self.client.owned(project, intent["actor_id"])
            return {**binding, "head_sha": None, "pr_number": None}
        project = intent["project_id"]
        head = await self.branch(project, record["branch"])
        number = None
        if intent["step"] == "create_branch":
            if head != intent["base_sha"]:
                raise ValueError("publication_head_conflict")
        elif intent["step"] == "commit_files":
            if response is not None and response["sha"] != head:
                raise ValueError("publication_head_conflict")
            await self._commit(intent, head)
        else:
            if head != intent["head_sha"]:
                raise ValueError("publication_head_conflict")
            number = (result or response or {}).get(
                "pr_number", (response or {}).get("number")
            )
            number = await self._pr(record, number)
        return {
            "project_id": project,
            "repository": intent["repository"],
            "head_sha": head,
            "pr_number": number,
        }

    async def _tree(self, project: str, sha: str) -> tuple[dict, dict]:
        commit = await self.client.proxy(project, "GET", "/commits/" + sha)
        if commit["sha"] != sha:
            raise ValueError("publication_commit_conflict")
        tree_sha = commit["commit"]["tree"]["sha"]
        validate_hex(tree_sha, 40)
        tree = await self.client.proxy(
            project, "GET", "/git/trees/" + tree_sha + "?recursive=1"
        )
        if (
            tree["sha"] != tree_sha
            or tree["truncated"] is not False
            or not isinstance(tree["tree"], list)
        ):
            raise ValueError("publication_tree_unresolved")
        rows = tree["tree"]
        paths = [r["path"] for r in rows]
        if len(paths) != len(set(paths)):
            raise ValueError("publication_tree_unresolved")
        files = {
            r["path"]: (r["sha"], r["mode"], r["type"])
            for r in rows
            if r["type"] != "tree"
        }
        return commit, files

    async def _commit(self, intent: dict, head: str) -> None:
        commit, actual = await self._tree(intent["project_id"], head)
        if [p["sha"] for p in commit["parents"]] != [intent["head_sha"]]:
            raise ValueError("publication_parent_conflict")
        _, expected = await self._tree(intent["project_id"], intent["head_sha"])
        expected.update(
            {path: (sha, "100644", "blob") for path, sha in intent["blobs"].items()}
        )
        if actual != expected:
            raise ValueError("publication_manifest_conflict")

    async def _pr(self, record: dict, number: int | None) -> int:
        intent = record["intent"]
        if number is None:
            matches = await self._find_prs(record)
            if len(matches) != 1:
                raise ValueError("publication_pr_unresolved")
            number = matches[0]["number"]
        if type(number) is not int or not 1 <= number <= 2147483647:
            raise ValueError("publication_pr_unresolved")
        data = await self.client.proxy(
            intent["project_id"], "GET", "/pulls/" + str(number)
        )
        if (
            data["number"] != number
            or data["draft"] is not True
            or data["state"] != "open"
        ):
            raise ValueError("publication_pr_conflict")
        expected = {
            "head": (record["branch"], intent["head_sha"]),
            "base": ("main", None),
        }
        for side, (branch, sha) in expected.items():
            value = data[side]
            if (
                value["ref"] != branch
                or value["repo"]["full_name"] != intent["repository"]
                or (sha is not None and value["sha"] != sha)
            ):
                raise ValueError("publication_pr_conflict")
        return number

    async def _find_prs(self, record: dict) -> list[dict[str, Any]]:
        intent = record["intent"]
        owner = intent["repository"].split("/")[0]
        matches = []
        for page in range(1, 6):
            path = (
                "/pulls?state=all&base=main&head="
                + quote(owner + ":" + record["branch"], safe="")
                + f"&per_page=100&page={page}"
            )
            rows = await self.client.proxy(intent["project_id"], "GET", path)
            if not isinstance(rows, list) or len(rows) > 100:
                raise ValueError("publication_pr_unresolved")
            matches.extend(
                r
                for r in rows
                if r["head"]["ref"] == record["branch"]
                and r["head"]["sha"] == intent["head_sha"]
                and r["base"]["ref"] == "main"
            )
            if len(rows) < 100:
                return matches
        raise ValueError("publication_pr_unresolved")
