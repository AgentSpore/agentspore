"""Route real Git histories, including unsafe comparison boundaries."""

import importlib.util
import json
import subprocess
import tempfile
import unittest
from unittest.mock import patch
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

    def test_scanner_changes_select_native_backend_acceptance(self):
        for path in (
            "backend/scripts/install_a4_scanners.py",
            "scripts/ci/smoke_a4_scanners.py",
            "scripts/ci/check_a4_scanner_image.py",
        ):
            with self.subTest(path=path):
                self.git("checkout", "-q", self.base)
                self.write(path)
                self.commit()
                self.assertTrue(scope.classify(self.changed())["backend_image"])

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


SCANNER_SPEC = importlib.util.spec_from_file_location(
    "scanner_image", Path(__file__).parents[2] / "scripts/ci/check_a4_scanner_image.py"
)
assert SCANNER_SPEC is not None and SCANNER_SPEC.loader is not None
scanner_image = importlib.util.module_from_spec(SCANNER_SPEC)
SCANNER_SPEC.loader.exec_module(scanner_image)


class ScannerImageTests(unittest.TestCase):
    def test_probe_success_isolated_command_and_cleanup(self):
        result = subprocess.CompletedProcess([], 0, b'{"offline":"passed","online":"not_run"}')
        empty = subprocess.CompletedProcess([], 0, b'')
        with patch.object(scanner_image.subprocess, "run", side_effect=[result, empty, empty]) as run:
            self.assertEqual(scanner_image.probe("sha256:" + "a" * 64, b"script", False),
                             {"offline": "passed", "online": "not_run"})
        command = run.call_args_list[0].args[0]
        self.assertIn("--read-only", command)
        self.assertIn("10001:10001", command)
        self.assertEqual(command[command.index("--network") + 1], "none")
        self.assertNotIn("--volume", command)
        self.assertEqual(run.call_args_list[0].kwargs["timeout"], 330)
        name = command[command.index("--name") + 1]
        self.assertEqual(run.call_args_list[1].args[0], ["docker", "rm", "-f", name])

    def test_timeout_still_cleans_owned_container(self):
        empty = subprocess.CompletedProcess([], 0, b'')
        with patch.object(scanner_image.subprocess, "run", side_effect=[
            subprocess.TimeoutExpired("docker", 330), empty, empty,
        ]) as run:
            with self.assertRaises(subprocess.TimeoutExpired):
                scanner_image.probe("sha256:" + "a" * 64, b"script", True)
        self.assertEqual(run.call_args_list[1].args[0][:3], ["docker", "rm", "-f"])
        self.assertEqual(run.call_args_list[2].args[0][:3], ["docker", "ps", "-aq"])

    def test_unexpected_report_and_cleanup_failure_rejected(self):
        for stdout, remaining in [(b'{"offline":"passed"}', b''),
                                  (b'{"offline":"passed","online":"passed"}', b'container')]:
            with self.subTest(stdout=stdout):
                results = [subprocess.CompletedProcess([], 0, stdout),
                           subprocess.CompletedProcess([], 0, b''),
                           subprocess.CompletedProcess([], 0, remaining)]
                with patch.object(scanner_image.subprocess, "run", side_effect=results):
                    with self.assertRaises(ValueError):
                        scanner_image.probe("sha256:" + "a" * 64, b"script", True)

    def test_receipt_source_and_architecture_fail_closed(self):
        head = "b" * 40
        image = "sha256:" + "a" * 64
        with tempfile.TemporaryDirectory() as directory:
            receipt = Path(directory) / "backend.json"
            receipt.write_text(json.dumps({"image_id": image, "service": "backend",
                                           "architecture": "amd64", "source_sha": head}))
            with patch.object(scanner_image.subprocess, "check_output", return_value=head):
                wrong_arch = subprocess.CompletedProcess([], 0, f"{image} linux arm64")
                with patch.object(scanner_image.subprocess, "run", return_value=wrong_arch):
                    with self.assertRaisesRegex(ValueError, "scanner_image_architecture"):
                        scanner_image.image_from_receipt(receipt)
            with patch.object(scanner_image.subprocess, "check_output", return_value="c" * 40):
                with self.assertRaisesRegex(ValueError, "scanner_image_receipt"):
                    scanner_image.image_from_receipt(receipt)
