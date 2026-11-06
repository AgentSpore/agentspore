"""Bounded virtual files for mocked eval tools; never access the host filesystem."""

import json
import shlex
from pathlib import PurePosixPath


class EvalWorkspace:
    def __init__(self):
        self.files: dict[str, str] = {}

    @staticmethod
    def path(value: str) -> str:
        path = PurePosixPath(value)
        if not value.startswith("/tmp/") or ".." in path.parts or "\x00" in value:
            raise ValueError("path must be inside virtual /tmp")
        return str(path)

    def write(self, path: str, content: str) -> str:
        try:
            path = self.path(path)
            content = content if isinstance(content, str) else json.dumps(content)
            if len(content.encode()) > 1024 * 1024 or (
                path not in self.files and len(self.files) >= 256
            ):
                return "ERROR: virtual workspace limit reached"
            self.files[path] = content
            return "ok"
        except ValueError:
            return "ERROR: path must be inside virtual /tmp"

    def read(self, path: str) -> str:
        try:
            return self.files.get(self.path(path), "[not found]")
        except ValueError:
            return "ERROR: path must be inside virtual /tmp"

    def list_files(self, path: str) -> list[str]:
        try:
            prefix = self.path(path).rstrip("/") + "/"
            return sorted(
                name.removeprefix(prefix)
                for name in self.files
                if name.startswith(prefix)
            )
        except ValueError:
            return []

    def execute(self, command: str) -> str | None:
        try:
            args = shlex.split(command)
        except ValueError:
            return "ERROR: command not executed"
        if not args:
            return "ERROR: command not executed"
        if args[0] == "curl":
            return None  # The caller handles synthetic platform HTTP fixtures.
        if any(char in command for char in ";|&`$<>\n\r"):
            return "ERROR: command not executed"
        if args[:2] == ["mkdir", "-p"]:
            args = ["mkdir", *args[2:]]
        if len(args) != 2 or args[0] not in {"mkdir", "cat", "ls", "find"}:
            return "ERROR: command not executed; use write_file/read_file"
        try:
            self.path(args[1])
        except ValueError:
            return "ERROR: path must be inside virtual /tmp"
        if args[0] == "mkdir":
            return ""
        if args[0] == "cat":
            return self.read(args[1])
        return "\n".join(self.list_files(args[1]))
