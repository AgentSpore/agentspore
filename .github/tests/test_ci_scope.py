"""Route real Git histories, including unsafe comparison boundaries."""

import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "scripts/ci_scope.py"
SPEC = importlib.util.spec_from_file_location("ci_scope", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
scope = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(scope)


class ScopeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.repo = Path(self.temporary.name)
        self.git("init", "-q")
        self.git("config", "user.name", "CI fixture")
        self.git("config", "user.email", "fixture@example.invalid")
        self.write("sdk/module.py")
        self.base = self.commit()

    def git(self, *arguments):
        return subprocess.check_output(["git", "-C", str(self.repo), *arguments], text=True).strip()

    def write(self, name):
        path = self.repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("public fixture\n")

    def commit(self):
        self.git("add", "-A")
        self.git("commit", "-qm", "fixture")
        return self.git("rev-parse", "HEAD")

    def changed(self, event=None):
        event = event or {"before": self.base, "after": self.git("rev-parse", "HEAD")}
        return scope.changed_files(self.repo, event, "push")

    def test_component_routes(self):
        for path, expected in [("sdk/new file.py", {"sdk"}), ("frontend/page.tsx", {"frontend", "frontend_image"}), ("backend/app.py", {"backend", "telemetry", "backend_image"}), ("agent-runner/app.py", {"backend", "telemetry", "runner_image"})]:
            with self.subTest(path=path):
                self.git("checkout", "-q", self.base)
                self.write(path)
                self.commit()
                actual = scope.classify(self.changed())
                self.assertEqual({key for key, value in actual.items() if value}, expected)

    def test_unknown_ci_and_database(self):
        for path in ("unknown/shared.py", ".github/scripts/ci_scope.py", "db/migrations/new.sql"):
            with self.subTest(path=path):
                self.git("checkout", "-q", self.base)
                self.write(path)
                self.commit()
                result = scope.classify(self.changed())
                self.assertTrue(result["backend"])
                if not path.startswith("db/"):
                    self.assertTrue(all(result.values()))

    def test_missing_invalid_and_zero_base_fail_closed(self):
        for base in ("f" * 40, "0" * 40, "--not-a-ref", None):
            with self.subTest(base=base):
                self.assertTrue(all(scope.classify(self.changed({"before": base, "after": self.base})).values()))

    def test_deletion_and_rename_fail_closed(self):
        self.git("mv", "sdk/module.py", "sdk/renamed.py")
        self.commit()
        self.assertTrue(all(scope.classify(self.changed()).values()))
        self.base = self.git("rev-parse", "HEAD")
        self.git("rm", "-q", "sdk/renamed.py")
        self.commit()
        self.assertTrue(all(scope.classify(self.changed()).values()))

    def test_pr_uses_merge_base_not_unrelated_base_branch_changes(self):
        self.write("frontend/page.tsx")
        head = self.commit()
        self.git("checkout", "-q", self.base)
        self.write("backend/app.py")
        base = self.commit()
        event = {"pull_request": {"base": {"sha": base}, "head": {"sha": head}}}
        result = scope.classify(scope.changed_files(self.repo, event, "pull_request"))
        self.assertTrue(result["frontend"])
        self.assertFalse(result["backend"])
        self.assertTrue(all(scope.classify(self.changed({"before": base, "after": head})).values()))

    def test_cli_receipts_and_empty_diff(self):
        event = self.repo / "event.json"
        event.write_text(json.dumps({"before": self.base, "after": self.base}))
        result = subprocess.check_output(["python3", str(SCRIPT.resolve()), str(event), "push"], cwd=self.repo, text=True)
        self.assertFalse(any(json.loads(result).values()))
        self.assertIn("sdk/agentspore_sdk/mcp_server.py", scope.sdk_python_files(None))
        self.assertIn("sdk/agentspore_sdk/mcp_server.py", scope.sdk_python_files([("M", "sdk/agentspore_sdk/mcp_server.py")]))
