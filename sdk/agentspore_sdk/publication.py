"""One-attempt publication orchestration over the existing authenticated workflow."""

import json
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .publication_manifest import build_manifest, canonical_digest
from .publication_readback import PublicationReadback
from .publication_state import (
    PublicationState,
    validate_hex,
    validate_repository,
    validate_uuid,
)
from .workflow import WorkflowClient

ACTIONS = {
    "create_project": "create_project",
    "create_feature_branch": "create_branch",
    "commit_feature_files": "commit_files",
    "open_draft_pr": "open_draft_pr",
}


class PublicationClient:
    """Enforce durable reservation and ownership across each bounded network attempt."""

    def __init__(self, workflow: WorkflowClient, directory: Path | None):
        self.workflow = workflow
        self.directory = directory

    async def request(self, method: str, path: str, body: dict | None = None) -> Any:
        """Use opaque existing authentication and refuse redirects or oversized responses."""
        async with self.workflow._http.stream(
            method, path, json=body, follow_redirects=False, timeout=30
        ) as response:
            response.raise_for_status()
            content = bytearray()
            async for chunk in response.aiter_bytes(chunk_size=65536):
                content.extend(chunk)
                if len(content) > 2097152:
                    raise ValueError("publication_response_too_large")
            return json.loads(content)

    async def proxy(
        self, project: str, method: str, path: str, body: dict | None = None
    ) -> Any:
        """Check provider status independently from the platform HTTP envelope."""
        value = await self.request(
            "POST",
            f"/api/v1/agents/projects/{project}/github",
            {"method": method, "path": path, "body": body},
        )
        if (
            type(value["status_code"]) is not int
            or not 200 <= value["status_code"] < 300
        ):
            raise ValueError("publication_provider_unavailable")
        return value["data"]

    async def owned(self, project: str, actor: str) -> dict[str, Any]:
        """Verify creator and a canonical GitHub repository binding before mutation."""
        validate_uuid(project)
        value = await self.request("GET", f"/api/v1/projects/{project}")
        if value["id"] != project or value["creator_agent_id"] != actor:
            raise ValueError("publication_not_creator")
        parsed = urlsplit(value["repo_url"])
        repository = parsed.path.removeprefix("/")
        validate_repository(repository)
        if value["repo_url"] != "https://github.com/" + repository:
            raise ValueError("publication_repository_invalid")
        return {"project_id": project, "repository": repository}

    async def run(self, action: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Execute a closed semantic action; pending operations are reconciled, never resent."""
        if self.directory is None:
            raise ValueError("publication_unconfigured")
        validate_uuid(arguments["operation_id"])
        with PublicationState(self.directory).locked() as session:
            identity = await self.workflow.identity()
            if identity["is_active"] is not True:
                raise ValueError("publication_actor_inactive")
            actor = identity["agent_id"]
            records = session.snapshot(arguments["operation_id"], actor)
            if action == "publication_status":
                return await self._status(session, records, actor)
            intent, payload = await self._prepare(action, arguments, records, actor)
            record = session.reserve(intent)
            if record["disposition"] != "attempt":
                return await self._status(session, [record], actor)
            await self._preflight(record)
            response = await self._send(record, payload, actor)
            result = await PublicationReadback(self).verify(record, response)
            return self._project(session.reconcile(intent, result))

    async def _prepare(
        self, action: str, args: dict, records: list[dict], actor: str
    ) -> tuple[dict, dict]:
        intent = {
            "operation_id": args["operation_id"],
            "actor_id": actor,
            "step": ACTIONS[action],
        }
        if action == "create_project":
            payload = {
                "title": args["title"],
                "description": args.get("description", ""),
                "category": args.get("category", "other"),
                "tech_stack": args.get("tech_stack", []),
                "vcs_provider": "github",
            }
        elif action == "create_feature_branch":
            validate_hex(args["base_sha"], 40)
            intent.update(await self.owned(args["project_id"], actor))
            intent["base_sha"] = args["base_sha"]
            payload = {
                "ref": "refs/heads/feature/agentspore-" + args["operation_id"],
                "sha": args["base_sha"],
            }
        else:
            intent, payload = self._prepare_feature(action, args, records, intent)
        intent["request_digest"] = canonical_digest(
            {
                "intent": {
                    k: v
                    for k, v in intent.items()
                    if k not in {"operation_id", "actor_id"}
                },
                "payload": {
                    k: v
                    for k, v in payload.items()
                    if k not in {"ref", "branch", "head", "files"}
                },
            }
        )
        return intent, payload

    @staticmethod
    def _prepare_feature(
        action: str, args: dict, records: list[dict], intent: dict
    ) -> tuple[dict, dict]:
        needed = "create_branch" if action == "commit_feature_files" else "commit_files"
        previous = next(
            (
                r
                for r in records
                if r["intent"]["step"] == needed and r["phase"] == "verified"
            ),
            None,
        )
        if previous is None:
            raise ValueError("publication_prerequisite_required")
        binding = previous["result"]
        intent.update({k: binding[k] for k in ("project_id", "repository")})
        expected = args["expected_sha"]
        validate_hex(expected, 40)
        if expected != binding["head_sha"]:
            raise ValueError("publication_head_conflict")
        intent["head_sha"] = expected
        branch = previous["branch"]
        if action == "commit_feature_files":
            manifest = build_manifest(args["files"])
            intent.update(
                manifest_digest=manifest["digest"],
                blobs={f["path"]: f["blob_sha"] for f in manifest["files"]},
            )
            payload = {
                "files": args["files"],
                "message": args["message"],
                "branch": branch,
            }
        else:
            payload = {
                "head": branch,
                "base": "main",
                "draft": True,
                "title": args["title"],
                "body": args.get("body", ""),
            }
        return intent, payload

    async def _preflight(self, record: dict) -> None:
        intent = record["intent"]
        if intent["step"] == "create_project":
            return
        branch = "main" if intent["step"] == "create_branch" else record["branch"]
        head = await PublicationReadback(self).branch(intent["project_id"], branch)
        expected = intent.get("base_sha", intent.get("head_sha"))
        if head != expected:
            raise ValueError("publication_head_conflict")

    async def _send(self, record: dict, payload: dict, actor: str) -> Any:
        intent = record["intent"]
        if intent["step"] == "create_project":
            return await self.request("POST", "/api/v1/agents/projects", payload)
        binding = await self.owned(intent["project_id"], actor)
        if binding["repository"] != intent["repository"]:
            raise ValueError("publication_repository_conflict")
        routes = {
            "create_branch": ("POST", "/git/refs"),
            "commit_files": ("PUT", "/contents"),
            "open_draft_pr": ("POST", "/pulls"),
        }
        method, path = routes[intent["step"]]
        return await self.proxy(intent["project_id"], method, path, payload)

    async def _status(
        self, session: PublicationState, records: list[dict], actor: str
    ) -> dict:
        if not records:
            return {"phase": "not_found", "automatic_retry": False}
        record = records[-1]
        intent = record["intent"]
        if intent["step"] == "create_project" and record["phase"] == "reserved":
            return self._project(record)
        project = (record["result"] or intent)["project_id"]
        binding = await self.owned(project, actor)
        if binding["repository"] != (record["result"] or intent)["repository"]:
            raise ValueError("publication_repository_conflict")
        result = await PublicationReadback(self).verify(record)
        return self._project(session.reconcile(intent, result))

    @staticmethod
    def _project(record: dict) -> dict:
        return {
            "operation_id": record["intent"]["operation_id"],
            "step": record["intent"]["step"],
            "phase": record["phase"],
            "branch": record["branch"],
            "automatic_retry": False,
            **(record["result"] or {}),
            **(
                {"draft": True, "state": "open"}
                if record["phase"] == "verified"
                and record["intent"]["step"] == "open_draft_pr"
                else {}
            ),
        }
