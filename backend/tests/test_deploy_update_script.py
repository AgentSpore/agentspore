"""Exercise the deploy CLI with real Bash/Git and synthetic local repositories."""

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[2] / "deploy/update.sh"
LOG_ASSIGNMENT = 'LOG="/var/log/agentspore-deploy.log"'


@dataclass
class DeployRepository:
    """Keep all effects inside an isolated, synthetic Git fixture."""

    root: Path
    remote: Path
    env: dict[str, str]
    initial: str
    release: str

    def git(self, *args: str) -> str:
        """Run real Git against this fixture only."""
        return subprocess.run(
            ["git", "-C", str(self.root), *args],
            env=self.env,
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout.strip()

    def run(self, args: tuple[str, ...] = ("release/test",)) -> subprocess.CompletedProcess[str]:
        """Run the production script's fixture copy without a login shell."""
        return subprocess.run(
            ["bash", str(self.root / "deploy/update.sh"), *args],
            env=self.env,
            capture_output=True,
            text=True,
            timeout=10,
        )

    def commands(self) -> list[str]:
        """Read only the fixture's safe Docker/sleep command tokens."""
        record = self.root / "commands"
        return record.read_text().splitlines() if record.exists() else []

    def log(self) -> str:
        """Read only the fixture's synthetic deploy log."""
        log = self.root / "deploy.log"
        return log.read_text() if log.exists() else ""


@pytest.fixture
def repository(tmp_path: Path) -> DeployRepository:
    """Create divergent main/release histories without network or credentials."""
    root = tmp_path / "checkout"
    root.mkdir()
    remote = tmp_path / "origin.git"
    env = {
        "PATH": os.environ["PATH"],
        "LC_ALL": "C",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_AUTHOR_NAME": "Fixture",
        "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
        "GIT_COMMITTER_NAME": "Fixture",
        "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
        "GIT_TERMINAL_PROMPT": "0",
        "BUILD_EXIT": "0",
        "HEALTH_FAILURES": "0",
    }
    repo = DeployRepository(root, remote, env, "", "")
    repo.git("init", "--initial-branch=main")
    for name in ("ordinary.txt", "white space.txt", "line\nbreak.txt", "docker-compose.yml"):
        (root / name).write_text("initial\n")
    repo.git("add", ".")
    repo.git("commit", "-m", "fixture initial")
    repo.initial = repo.git("rev-parse", "HEAD")
    repo.git("init", "--bare", str(remote))
    repo.git("remote", "add", "origin", str(remote))
    repo.git("checkout", "-b", "release/test")
    for name in ("ordinary.txt", "white space.txt", "line\nbreak.txt", "docker-compose.yml"):
        (root / name).write_text("release\n")
    repo.git("commit", "-am", "fixture release")
    repo.release = repo.git("rev-parse", "HEAD")
    repo.git("checkout", "main")
    (root / "ordinary.txt").write_text("main\n")
    repo.git("commit", "-am", "fixture main")
    repo.git("push", "origin", "main", "release/test")
    repo.git("reset", "--hard", repo.initial)
    repo.git("config", "core.logAllRefUpdates", "true")
    return repo


@pytest.fixture
def deployment(repository: DeployRepository) -> DeployRepository:
    """Install a script copy and stub only outward Docker and sleep calls."""
    root = repository.root
    deploy = root / "deploy"
    deploy.mkdir()
    source = SOURCE.read_text()
    assert source.count(LOG_ASSIGNMENT) == 1
    source = source.replace(LOG_ASSIGNMENT, f'LOG="{root / "deploy.log"}"')
    (deploy / "update.sh").write_text(source)
    (deploy / ".env").touch()
    binaries = root / "bin"
    binaries.mkdir()
    docker = binaries / "docker"
    docker.write_text(
        "#!/bin/bash\nset -euo pipefail\n"
        'printf "%s\\n" "$*" >> "$COMMANDS"\n'
        'if [ "$1" = compose ]; then exit "$BUILD_EXIT"; fi\n'
        'count=0\nif [ -f "$HEALTH_COUNT" ]; then read -r count < "$HEALTH_COUNT"; fi\n'
        'count=$((count + 1))\nprintf "%s\\n" "$count" > "$HEALTH_COUNT"\n'
        '[ "$count" -gt "$HEALTH_FAILURES" ]\n'
    )
    sleep = binaries / "sleep"
    sleep.write_text('#!/bin/bash\nprintf "sleep %s\\n" "$*" >> "$COMMANDS"\n')
    for binary in (docker, sleep):
        binary.chmod(0o755)
    repository.env.update(
        PATH=f"{binaries}{os.pathsep}{os.environ['PATH']}",
        COMMANDS=str(root / "commands"),
        HEALTH_COUNT=str(root / "health-count"),
    )
    return repository


def test_selected_release_commit_is_deployed(deployment: DeployRepository) -> None:
    assert deployment.run().returncode == 0
    assert deployment.git("rev-parse", "HEAD") == deployment.release
    assert deployment.git("rev-parse", "origin/main") != deployment.release
    assert (deployment.root / "ordinary.txt").read_text() == "release\n"
    assert deployment.commands() == [
        "compose -f docker-compose.prod.yml --env-file .env up -d --build --remove-orphans",
        "exec agentspore-backend curl -sf http://localhost:8000/health",
    ]


def test_fetch_updates_only_selected_head_without_tags(deployment: DeployRepository) -> None:
    deployment.git("update-ref", "-d", "refs/remotes/origin/main")
    deployment.git(
        "--git-dir", str(deployment.remote), "update-ref", "refs/tags/fixture", deployment.release
    )
    assert deployment.run().returncode == 0
    assert deployment.git("branch", "--remotes").splitlines() == ["origin/release/test"]
    assert deployment.git("tag", "--list") == ""


@pytest.mark.parametrize(
    "args",
    [
        (),
        ("release/test", "extra"),
        ("main",),
        ("feature/test",),
        ("v1.0",),
        ("--help",),
        ("HEAD",),
        ("a" * 40,),
        ("release/",),
        ("release/a..b",),
        ("release/test~1",),
        ("release/test:main",),
        ("release/white space",),
        ("release/test\n",),
        ("refs/heads/release/test",),
    ],
)
def test_invalid_arguments_have_no_effects(
    deployment: DeployRepository, args: tuple[str, ...]
) -> None:
    tracking = deployment.git("rev-parse", "origin/release/test")
    (deployment.root / "deploy/.env").unlink()
    assert deployment.run(args).returncode == 1
    assert deployment.git("rev-parse", "HEAD") == deployment.initial
    assert deployment.git("rev-parse", "origin/release/test") == tracking
    assert not (deployment.root / ".git/FETCH_HEAD").exists()
    assert not (deployment.root / "deploy.log").exists()
    assert deployment.commands() == []


@pytest.mark.parametrize("failure", ["missing", "stale", "unavailable"])
def test_fetch_failure_never_uses_tracking_commit(
    deployment: DeployRepository, failure: str
) -> None:
    if failure == "missing":
        deployment.git("update-ref", "-d", "refs/remotes/origin/release/test")
    if failure in ("missing", "stale"):
        deployment.git(
            "--git-dir", str(deployment.remote), "update-ref", "-d", "refs/heads/release/test"
        )
    else:
        deployment.remote.rename(deployment.remote.with_name("unavailable.git"))
    assert deployment.run().returncode != 0
    assert deployment.git("rev-parse", "HEAD") == deployment.initial
    assert deployment.commands() == []


def test_same_head_exits_without_docker(deployment: DeployRepository) -> None:
    deployment.git("reset", "--hard", deployment.release)
    assert deployment.run().returncode == 0
    assert deployment.commands() == []
    assert "Already up to date" in deployment.log()


@pytest.mark.parametrize("filename", ["ordinary.txt", "white space.txt", "line\nbreak.txt"])
@pytest.mark.parametrize("matches_release", [False, True])
def test_dirty_guard_uses_nul_names_and_release_commit(
    deployment: DeployRepository,
    filename: str,
    matches_release: bool,
) -> None:
    content = "release\n" if matches_release else "local patch\n"
    (deployment.root / filename).write_text(content)
    assert deployment.run().returncode == (0 if matches_release else 1)
    assert deployment.git("rev-parse", "HEAD") == (
        deployment.release if matches_release else deployment.initial
    )
    assert (deployment.root / filename).read_text() == content
    assert bool(deployment.commands()) == matches_release


def test_failed_dirty_enumeration_aborts(deployment: DeployRepository) -> None:
    deployment.git("config", "diff.ignoreSubmodules", "invalid-fixture-value")
    assert deployment.run().returncode == 1
    assert deployment.git("rev-parse", "HEAD") == deployment.initial
    assert "ABORT: git diff --name-only HEAD failed" in deployment.log()
    assert deployment.commands() == []


@pytest.mark.parametrize("build_exit,health_failures,expected", [(0, 0, 0), (7, 0, 7), (0, 12, 1)])
def test_dirty_ignored_file_restored_with_exit_status(
    deployment: DeployRepository,
    build_exit: int,
    health_failures: int,
    expected: int,
) -> None:
    ignored = deployment.root / "docker-compose.yml"
    ignored.write_text("synthetic local configuration\n")
    deployment.env.update(BUILD_EXIT=str(build_exit), HEALTH_FAILURES=str(health_failures))
    assert deployment.run().returncode == expected
    assert deployment.git("rev-parse", "HEAD") == deployment.release
    assert ignored.read_text() == "synthetic local configuration\n"
    assert f"(exit {expected})" in deployment.log()
    assert deployment.commands()[0].startswith("compose ")
    assert sum(command.startswith("exec ") for command in deployment.commands()) == (
        0 if build_exit else 12 if health_failures else 1
    )


def test_clean_ignored_file_receives_release_content(deployment: DeployRepository) -> None:
    assert deployment.run().returncode == 0
    assert (deployment.root / "docker-compose.yml").read_text() == "release\n"


def test_health_retries_then_succeeds(deployment: DeployRepository) -> None:
    deployment.env["HEALTH_FAILURES"] = "2"
    assert deployment.run().returncode == 0
    assert sum(command.startswith("exec ") for command in deployment.commands()) == 3
    assert deployment.commands().count("sleep 5") == 2
    assert "Deploy OK" in deployment.log()
