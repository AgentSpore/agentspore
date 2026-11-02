"""Private single-host publication reservations, without network retries or credentials."""

import copy
import importlib
import json
import os
import re
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from uuid import UUID

from .publication_manifest import canonical_digest, validate_path

LIMIT = 65536
STEPS = {"create_project", "create_branch", "commit_files", "open_draft_pr"}
REQUIRED = {"operation_id", "actor_id", "step", "request_digest"}
OPTIONAL = {
    "project_id",
    "repository",
    "base_sha",
    "head_sha",
    "manifest_digest",
    "blobs",
}


def validate_uuid(value: str) -> None:
    """Require canonical UUID strings, including for journal lookup keys."""
    if not isinstance(value, str) or str(UUID(value)) != value:
        raise ValueError("publication_uuid_invalid")


def validate_hex(value: str, length: int) -> None:
    """Require exact lower-case digest strings."""
    if (
        not isinstance(value, str)
        or re.fullmatch("[0-9a-f]{" + str(length) + "}", value) is None
    ):
        raise ValueError("publication_digest_invalid")


def validate_intent(intent: dict[str, Any]) -> dict[str, Any]:
    """Accept closed metadata only; body and credential fields cannot be persisted."""
    if (
        not isinstance(intent, dict)
        or not REQUIRED <= intent.keys()
        or intent.keys() - REQUIRED - OPTIONAL
    ):
        raise ValueError("publication_intent_invalid")
    for field in ("operation_id", "actor_id", "project_id"):
        if field in intent:
            validate_uuid(intent[field])
    if intent["step"] not in STEPS:
        raise ValueError("publication_step_invalid")
    validate_hex(intent["request_digest"], 64)
    for field in ("base_sha", "head_sha", "manifest_digest"):
        if field in intent:
            validate_hex(intent[field], 64 if field == "manifest_digest" else 40)
    if "repository" in intent:
        validate_repository(intent["repository"])
    if "blobs" in intent:
        validate_blobs(intent["blobs"])
    return copy.deepcopy(intent)


def validate_repository(repository: str) -> None:
    """Require an opaque owner/name binding without URL credentials."""
    if (
        not isinstance(repository, str)
        or len(repository) > 200
        or re.fullmatch(r"[A-Za-z0-9_-]+/[A-Za-z0-9_.-]+", repository) is None
    ):
        raise ValueError("publication_repository_invalid")


def validate_blobs(blobs: dict[str, str]) -> None:
    """Bound expected Git object identities without storing content."""
    if not isinstance(blobs, dict) or not 1 <= len(blobs) <= 32:
        raise ValueError("publication_blobs_invalid")
    for path, digest in blobs.items():
        validate_path(path)
        validate_hex(digest, 40)


class PublicationState:
    """Keep reservation and verification ordering inside an exclusive session."""

    def __init__(self, directory: Path):
        if not isinstance(directory, Path) or not directory.is_absolute():
            raise ValueError("publication_directory_must_be_explicit_absolute")
        self.directory = directory
        self._active = False
        self._records: list[dict[str, Any]] = []
        self._storage: Any = None

    @contextmanager
    def locked(self) -> Generator["PublicationState"]:
        """Hold the process lock across transitions and caller's bounded async work."""
        if self._active:
            raise RuntimeError("publication_session_already_active")
        # operator_controller imports POSIX-only fcntl/pwd; read-only SDK imports stay portable.
        self._storage = importlib.import_module(".operator_controller", __package__)
        with self._storage.controller_lock(self.directory) as acquired:
            if not acquired:
                raise BlockingIOError("publication_busy")
            self._records = self._load()
            self._active = True
            try:
                yield self
            finally:
                self._active = False
                self._records = []

    def _load(self) -> list[dict[str, Any]]:
        try:
            descriptor = self._storage.private_file(
                self.directory / "publication.json", os.O_RDONLY
            )
        except FileNotFoundError:
            return []
        with os.fdopen(descriptor, "rb") as stream:
            raw = stream.read(LIMIT + 1)
        if len(raw) > LIMIT:
            raise ValueError("publication_state_too_large")
        value = json.loads(raw, object_pairs_hook=self._storage.unique_object)
        if (
            not isinstance(value, dict)
            or set(value) != {"version", "records"}
            or type(value["version"]) is not int
            or value["version"] != 1
            or not isinstance(value["records"], list)
        ):
            raise ValueError("publication_state_invalid")
        keys = set()
        for record in value["records"]:
            self._validate_record(record)
            key = (record["intent"]["operation_id"], record["intent"]["step"])
            if key in keys:
                raise ValueError("publication_state_invalid")
            keys.add(key)
        return value["records"]

    def _validate_record(self, record: dict[str, Any]) -> None:
        if not isinstance(record, dict) or set(record) != {"intent", "phase", "result"}:
            raise ValueError("publication_state_invalid")
        validate_intent(record["intent"])
        if record["phase"] not in {"reserved", "verified"}:
            raise ValueError("publication_state_invalid")
        if record["phase"] == "reserved" and record["result"] is not None:
            raise ValueError("publication_state_invalid")
        if record["phase"] == "verified":
            self._validate_result(record["intent"], record["result"])

    def _save(self, records: list[dict[str, Any]]) -> None:
        self._active = False
        self._storage.write_state(
            self.directory / "publication.json",
            {"version": 1, "records": records},
            max_bytes=LIMIT,
        )
        self._records = records
        self._active = True

    def _check_session(self) -> None:
        if not self._active:
            raise RuntimeError("publication_session_required")

    def snapshot(self, operation_id: str, actor_id: str) -> list[dict[str, Any]]:
        """Read one actor's operation inside the lock, without exposing journal storage."""
        self._check_session()
        validate_uuid(operation_id)
        validate_uuid(actor_id)
        records = [
            r for r in self._records if r["intent"]["operation_id"] == operation_id
        ]
        if any(r["intent"]["actor_id"] != actor_id for r in records):
            raise ValueError("publication_identity_conflict")
        return [self._view(record, "readback_required") for record in records]

    def reserve(self, intent: dict[str, Any]) -> dict[str, Any]:
        """Fsync intent before granting one attempt; every replay requires readback."""
        self._check_session()
        intent = validate_intent(intent)
        for record in self._records:
            replay = self._check_previous(record, intent)
            if replay is not None:
                return replay
        record = {"intent": intent, "phase": "reserved", "result": None}
        self._save([*self._records, record])
        return self._view(record, "attempt")

    def _check_previous(
        self, record: dict[str, Any], intent: dict[str, Any]
    ) -> dict[str, Any] | None:
        previous = record["intent"]
        if previous["operation_id"] != intent["operation_id"]:
            if record["phase"] == "reserved" and all(
                previous.get(k) == intent.get(k)
                for k in ("actor_id", "step", "request_digest")
            ):
                raise ValueError("publication_pending")
            return None
        if previous["step"] == intent["step"]:
            if previous != intent:
                raise ValueError("publication_intent_conflict")
            return self._view(record, "readback_required")
        binding = {**previous, **(record["result"] or {})}
        if any(
            field in binding and binding[field] != intent.get(field)
            for field in ("actor_id", "project_id", "repository")
        ):
            raise ValueError("publication_identity_conflict")
        if record["phase"] == "reserved":
            raise ValueError("publication_pending")
        return None

    def reconcile(
        self, intent: dict[str, Any], verified: dict[str, Any]
    ) -> dict[str, Any]:
        """Record caller-verified exact outcome; no reset, absence proof, or retry."""
        self._check_session()
        intent = validate_intent(intent)
        self._validate_result(intent, verified)
        for index, record in enumerate(self._records):
            if record["intent"] != intent:
                continue
            if record["phase"] == "verified" and record["result"] != verified:
                raise ValueError("publication_result_conflict")
            completed = {
                "intent": intent,
                "phase": "verified",
                "result": copy.deepcopy(verified),
            }
            records = self._records.copy()
            records[index] = completed
            self._save(records)
            return self._view(completed, "verified")
        raise ValueError("publication_reservation_required")

    @staticmethod
    def _validate_result(intent: dict[str, Any], result: dict[str, Any]) -> None:
        if not isinstance(result, dict) or set(result) != {
            "project_id",
            "repository",
            "head_sha",
            "pr_number",
        }:
            raise ValueError("publication_result_invalid")
        validate_uuid(result["project_id"])
        validate_repository(result["repository"])
        if result["head_sha"] is not None:
            validate_hex(result["head_sha"], 40)
        if result["pr_number"] is not None and (
            type(result["pr_number"]) is not int
            or not 1 <= result["pr_number"] <= 2147483647
        ):
            raise ValueError("publication_result_invalid")
        for field in ("project_id", "repository"):
            if field in intent and intent[field] != result[field]:
                raise ValueError("publication_result_conflict")
        if intent["step"] != "create_project" and result["head_sha"] is None:
            raise ValueError("publication_result_invalid")
        if intent["step"] == "open_draft_pr" and result["pr_number"] is None:
            raise ValueError("publication_result_invalid")

    @staticmethod
    def _view(record: dict[str, Any], disposition: str) -> dict[str, Any]:
        view = copy.deepcopy(record)
        view["disposition"] = (
            "verified" if record["phase"] == "verified" else disposition
        )
        view["branch"] = "feature/agentspore-" + record["intent"]["operation_id"]
        view["intent_digest"] = canonical_digest(record["intent"])
        return view
