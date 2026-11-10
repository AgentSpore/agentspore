"""Crash-safe record of the last unpersisted signing request per intent; never holds bytes."""

import json
import os
from pathlib import Path


class Journal:
    """`{intent: {epoch, lvbh, at}}`, rewritten atomically (temp file, fsync, rename)."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def _load(self) -> dict[str, dict]:
        try:
            return json.loads(self.path.read_text())
        except FileNotFoundError:
            return {}

    def _store(self, body: dict[str, dict]) -> None:
        temp = self.path.with_suffix(".tmp")
        descriptor = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w") as handle:
            handle.write(json.dumps(body))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, self.path)
        directory = os.open(self.path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)

    def get(self, intent: str) -> dict | None:
        return self._load().get(intent)

    def put(self, intent: str, epoch: int, lvbh: int, at: float) -> None:
        body = self._load()
        body[intent] = {"epoch": epoch, "lvbh": lvbh, "at": at}
        self._store(body)

    def hold(self, intent: str, reason: str, at: float) -> None:
        """Mark the intent held; take()/handle skip it until an operator removes the entry."""
        body = self._load()
        body.setdefault(intent, {"epoch": 0, "lvbh": 0, "at": at})["hold"] = reason
        self._store(body)

    def drop(self, intent: str) -> None:
        body = self._load()
        if body.pop(intent, None) is not None:
            self._store(body)
