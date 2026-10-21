"""Classify verified Git comparisons; uncertainty always selects every CI component."""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

COMPONENTS = ("backend", "telemetry", "frontend", "sdk", "backend_image", "frontend_image", "runner_image", "caddy_image")
ROUTES = {"sdk": ("sdk",), "frontend": ("frontend", "frontend_image"), "backend": ("backend", "telemetry", "backend_image"), "db": ("backend", "telemetry", "backend_image"), "agent-runner": ("backend", "telemetry", "runner_image")}


def git(repo, *arguments):
    return subprocess.run(["git", "-C", str(repo), *arguments], check=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=15).stdout


def changed_files(repo, event, event_name):
    """Return status/path pairs, including both rename endpoints, or unknown."""
    try:
        if event_name == "pull_request":
            base, head = event["pull_request"]["base"]["sha"], event["pull_request"]["head"]["sha"]
        elif event_name == "push":
            base, head = event["before"], event["after"]
        else:
            return None
        if not all(isinstance(ref, str) and re.fullmatch(r"[0-9a-f]{40}", ref) and ref != "0" * 40 for ref in (base, head)):
            return None
        for ref in (base, head):
            git(repo, "cat-file", "-e", ref + "^{commit}")
        if event_name == "pull_request":
            base = git(repo, "merge-base", base, head).decode().strip()
        else:
            git(repo, "merge-base", "--is-ancestor", base, head)
        fields = iter(git(repo, "diff", "--no-ext-diff", "--no-textconv", "--name-status", "-z", "--find-renames", base, head, "--").decode().split("\0")[:-1])
        changes = []
        for status in fields:
            changes.append((status, next(fields)))
            if status.startswith(("R", "C")):
                changes.append((status, next(fields)))
        return changes
    except (OSError, ValueError, KeyError, TypeError, StopIteration, subprocess.SubprocessError):
        return None


def classify(changes):
    """Permit selective CI only for known regular additions/modifications."""
    selected = set()
    if changes is None:
        return dict.fromkeys(COMPONENTS, True)
    for status, path in changes:
        parts = Path(path).parts
        route = ROUTES.get(parts[0]) if len(parts) > 1 else None
        if status not in {"A", "M"} or route is None or ".." in parts or Path(path).is_absolute():
            return dict.fromkeys(COMPONENTS, True)
        selected.update(route)
    return {component: component in selected for component in COMPONENTS}


def sdk_python_files(changes):
    """Type supported core plus every changed SDK Python file; unknown means all."""
    files = {str(path) for path in Path("sdk/agentspore_sdk").glob("*.py") if path.name != "mcp_server.py"}
    if changes is None:
        files.add("sdk/agentspore_sdk/mcp_server.py")
    else:
        files.update(path for _, path in changes if path.startswith("sdk/") and path.endswith(".py") and Path(path).is_file())
    return sorted(files)


def main():
    try:
        event_path = Path(sys.argv[1] if len(sys.argv) > 2 else os.environ["GITHUB_EVENT_PATH"])
        event_name = sys.argv[2] if len(sys.argv) > 2 else os.environ["GITHUB_EVENT_NAME"]
        event = json.loads(event_path.read_text())
        changes = changed_files(Path.cwd(), event, event_name)
    except (OSError, ValueError, KeyError):
        changes = None
    if sys.argv[1:] == ["--sdk-types"]:
        sys.stdout.write("\0".join(sdk_python_files(changes)) + "\0")
        return
    result = classify(changes)
    if "GITHUB_OUTPUT" in os.environ:
        with Path(os.environ["GITHUB_OUTPUT"]).open("a") as output:
            output.writelines(f"{name}={str(value).lower()}\n" for name, value in result.items())
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
