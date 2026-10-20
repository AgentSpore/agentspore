"""Own a fresh local database, cache and copied native backend for one user proof."""

import asyncio
import hashlib
import json
import os
import re
import secrets
import shutil
import signal
import socket
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import httpx


def file_hash(path: Path) -> str:
    """Hash public source without loading private settings or runtime state."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


class OwnedStand:
    """Own exact resources created for this probe; never discover secrets from existing resources."""

    def __init__(self, source: Path, backend_python: Path, docker_host: str, recovery_dir: Path):
        if not docker_host.startswith("unix:///") or any(char in docker_host for char in "?#\n\r"):
            raise ValueError("Docker host must be an explicit local Unix socket")
        if not recovery_dir.is_dir():
            raise ValueError("Recovery directory must already exist")
        self.docker_host = docker_host
        self.source = source.resolve()
        self.backend_python = backend_python.absolute()
        self.root = Path(tempfile.mkdtemp(prefix="oss-new-user-", dir=recovery_dir))
        self.run_id = secrets.token_hex(6)
        self.network = "oss-user-" + self.run_id
        self.container_ids: list[str] = []
        self.baseline: set[str] = set()
        self.process: subprocess.Popen[bytes] | None = None
        self.email: socket.socket | None = None
        self.capture_buffer = bytearray()
        self.network_id: str | None = None
        self.intents: set[str] = set()
        self.pending: set[str] = set()
        self.journal = recovery_dir / (self.network + ".json")
        self.origin = ""
        self.manifest: dict[str, str] = {}
        self.schema_proof: dict[str, Any] = {}
        self.cleanup_proof: dict[str, Any] = {}
        self.env = {"PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin", "HOME": str(self.root)}

    def _docker(self, arguments: list[str], extra: dict[str, str] | None = None) -> str:
        command = ["docker", "--config", str(self.root / "docker"), "--host", self.docker_host]
        result = subprocess.run(
            [*command, *arguments],
            env={**self.env, **(extra or {})},
            capture_output=True,
            timeout=120,
            check=False,
        )
        if result.returncode:
            raise RuntimeError("Owned Docker operation failed")
        return result.stdout.decode().strip()

    def _ids(self) -> set[str]:
        return set(self._docker(["ps", "-aq", "--no-trunc"]).splitlines())

    def _journal(self, state: str) -> None:
        record = {
            "run_id": self.run_id,
            "state": state,
            "runtime_root": str(self.root),
            "docker_host": self.docker_host,
            "label": "oss.user-proof=" + self.run_id,
            "network": self.network,
            "network_id": self.network_id,
            "intents": sorted(self.intents),
            "pending": sorted(self.pending),
            "container_ids": self.container_ids,
            "baseline_ids": sorted(self.baseline),
            "backend_pid": self.process.pid if self.process else None,
        }
        temporary = self.journal.with_suffix(".tmp")
        with temporary.open("w") as handle:
            json.dump(record, handle, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(self.journal)

    def _container(self, arguments: list[str], extra: dict[str, str] | None = None) -> str:
        name = self.network + "-c" + str(len(self.intents) - 1)
        self.intents.add(name)
        self.pending.add(name)
        self._journal("container_create_intent")
        identifier = self._docker(
            [
                "run",
                "-d",
                "--pull=never",
                "--name",
                name,
                "--label",
                "oss.user-proof=" + self.run_id,
                "--network",
                self.network,
                *arguments,
            ],
            extra,
        )
        if len(identifier) != 64 or not all(char in "0123456789abcdef" for char in identifier):
            raise RuntimeError("Owned container ID unavailable")
        self.container_ids.append(identifier)
        self.pending.discard(name)
        self._journal("container_created")
        return identifier

    def _reconcile(self) -> tuple[set[str], set[str]]:
        label = "label=oss.user-proof=" + self.run_id
        rows = self._docker(["ps", "-a", "--no-trunc", "--filter", label, "--format", "{{.ID}} {{.Names}}"])
        live = set()
        for row in rows.splitlines():
            identifier, name = row.split()
            if name not in self.intents or identifier in self.baseline:
                raise RuntimeError("Ownership reconciliation mismatch")
            live.add(identifier)
            if identifier not in self.container_ids:
                self.container_ids.append(identifier)
            self.pending.discard(name)
        networks = self._docker(["network", "ls", "--no-trunc", "--filter", label, "--format", "{{.ID}} {{.Name}}"])
        found = set()
        for row in networks.splitlines():
            identifier, name = row.split()
            if name != self.network:
                raise RuntimeError("Network reconciliation mismatch")
            self.network_id = identifier
            found.add(identifier)
            self.pending.discard(name)
        self._journal("reconciled")
        return live, found

    def _copy_source(self) -> Path:
        runtime = self.root / "runtime"
        public = list((self.source / "backend/app").rglob("*.py"))
        public += [path for path in (self.source / "backend/app/static").rglob("*") if path.is_file()]
        public += list((self.source / "db/migrations").glob("*.sql"))
        public += [self.source / "skill.md", self.source / "docs/HEARTBEAT.md", self.source / "docs/RULES.md"]
        for path in public:
            if path.is_symlink() or any(part.startswith(".env") for part in path.parts):
                raise RuntimeError("Unsafe source copy input")
            relative = path.relative_to(self.source)
            target = runtime / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
            expected = file_hash(path)
            if file_hash(target) != expected:
                raise RuntimeError("Runtime source hash mismatch")
            self.manifest[str(relative)] = expected
        for name in ("HEARTBEAT.md", "RULES.md"):
            shutil.copyfile(runtime / "docs" / name, runtime / name)
        (runtime / "backend/app/static").mkdir(parents=True, exist_ok=True)
        return runtime

    def _database(self, runtime: Path) -> tuple[int, int, str]:
        password = secrets.token_urlsafe(32)
        database = self._container(
            [
                "--memory=384m",
                "--cpus=1",
                "--tmpfs=/var/lib/postgresql/data",
                "--publish=127.0.0.1::5432",
                "--env=POSTGRES_PASSWORD",
                "--env=POSTGRES_DB",
                "pgvector/pgvector:pg16",
            ],
            {"POSTGRES_PASSWORD": password, "POSTGRES_DB": "proof"},
        )
        cache_command = ["valkey-server", "--save", "", "--appendonly", "no"]
        cache = self._container(
            [
                "--memory=64m",
                "--cpus=0.5",
                "--publish=127.0.0.1::6379",
                "valkey/valkey:9.1-alpine",
                *cache_command,
            ]
        )
        self._migrate(runtime, password)
        self._schema(database, runtime)
        return self._port(database, "5432/tcp"), self._port(cache, "6379/tcp"), password

    def _migrate(self, runtime: Path, password: str) -> None:
        migration = self._container(
            [
                "--memory=256m",
                "--cpus=1",
                "--volume=" + str(runtime / "db/migrations") + ":/flyway/sql:ro",
                "--env=FLYWAY_URL",
                "--env=FLYWAY_USER",
                "--env=FLYWAY_PASSWORD",
                "flyway/flyway:13.8.1",
                "-connectRetries=30",
                "-locations=filesystem:/flyway/sql",
                "migrate",
            ],
            {
                "FLYWAY_URL": f"jdbc:postgresql://{self.network}-c0:5432/proof",
                "FLYWAY_USER": "postgres",
                "FLYWAY_PASSWORD": password,
            },
        )
        if self._docker(["wait", migration]) != "0":
            raise RuntimeError("Full migration chain failed")

    def _schema(self, database: str, runtime: Path) -> None:
        command = ["exec", database, "psql", "-U", "postgres", "-d", "proof", "-Atc"]
        files = (runtime / "db/migrations").glob("*.sql")
        expected = sorted(p.name for p in files if re.fullmatch(r"(?:V[0-9]+(?:[._][0-9]+)*|R)__.+\.sql", p.name))
        query = "SELECT to_regclass('public.users') IS NOT NULL,to_regclass('public.flyway_schema_history') IS NOT NULL"
        users, history = self._docker([*command, query]).split("|")
        self.schema_proof = {"users_present": users == "t", "history_present": history == "t"}
        self.schema_proof["expected_scripts"] = len(expected)
        if users != "t" or history != "t":
            raise RuntimeError("Fresh owned database schema absent")
        query = "SELECT COALESCE(json_agg(script ORDER BY script), '[]'::json) FROM public.flyway_schema_history "
        applied = json.loads(self._docker([*command, query + "WHERE success AND type='SQL'"]))
        self.schema_proof.update({"applied_scripts": len(applied), "scripts_match": applied == expected})
        if not expected or applied != expected:
            raise RuntimeError("Fresh owned migration history differs from source")

    def _port(self, identifier: str, container_port: str) -> int:
        address = self._docker(["port", identifier, container_port])
        host, port = address.rsplit(":", 1)
        if host != "127.0.0.1":
            raise RuntimeError("Owned service is not loopback-bound")
        return int(port)

    def _start(self) -> None:
        self.baseline = self._ids()
        self.intents.add(self.network)
        self.pending.add(self.network)
        self._journal("network_create_intent")
        self.network_id = self._docker(["network", "create", "--label", "oss.user-proof=" + self.run_id, self.network])
        if len(self.network_id) != 64:
            raise RuntimeError("Owned network ID unavailable")
        self.pending.discard(self.network)
        self._journal("network_created")
        runtime = self._copy_source()
        pg_port, redis_port, password = self._database(runtime)
        parent, child = socket.socketpair()
        self.email = parent
        parent.settimeout(15)
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        self.origin = f"http://127.0.0.1:{port}"
        environment = {
            **self.env,
            "PYTHONPATH": str(runtime / "backend"),
            "SECRET_KEY": secrets.token_urlsafe(48),
            "DATABASE_URL": f"postgresql+asyncpg://postgres:{password}@127.0.0.1:{pg_port}/proof",
            "REDIS_URL": f"redis://127.0.0.1:{redis_port}",
            "FRONTEND_URL": self.origin,
            "LOGFIRE_SEND_TO_LOGFIRE": "false",
            "PYTHONUNBUFFERED": "1",
            "UV_NO_ENV_FILE": "1",
        }
        self._journal("backend_start_intent")
        try:
            self.process = subprocess.Popen(
                [
                    str(self.backend_python),
                    str(Path(__file__).with_name("user_backend.py")),
                    "--port",
                    str(port),
                    "--email-fd",
                    str(child.fileno()),
                ],
                cwd=runtime / "backend",
                env=environment,
                pass_fds=(child.fileno(),),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        finally:
            child.close()
        self._journal("backend_started")

    async def start(self) -> None:
        """Create owned resources off the event loop, then await genuine backend health."""
        await asyncio.to_thread(self._start)
        async with httpx.AsyncClient(timeout=2, trust_env=False) as client:
            for _ in range(100):
                if self.process is None or self.process.poll() is not None:
                    raise RuntimeError("Owned backend stopped before readiness")
                try:
                    response = await client.get(self.origin + "/health")
                    if response.status_code == 200:
                        return
                except httpx.TransportError:
                    pass
                await asyncio.sleep(0.2)
        raise RuntimeError("Owned backend readiness deadline exceeded")

    def _capture(self) -> dict[str, Any]:
        if self.email is None:
            raise RuntimeError("Private capture channel missing")
        while b"\n" not in self.capture_buffer and len(self.capture_buffer) < 16384:
            part = self.email.recv(1024)
            if not part:
                raise RuntimeError("Private capture channel closed")
            self.capture_buffer.extend(part)
        payload, separator, trailing = self.capture_buffer.partition(b"\n")
        if not separator:
            raise RuntimeError("Private capture frame incomplete")
        self.capture_buffer = bytearray(trailing)
        data = json.loads(payload)
        if not isinstance(data, dict):
            raise RuntimeError("Private capture shape invalid")
        return data

    async def diagnostic(self, status: int | None) -> dict[str, Any]:
        """Consume only exception metadata after HTTP500; discard preceding private email capture."""
        if status != 500:
            return {"state": "diagnostic_not_requested"}
        try:
            for _ in range(3):
                data = await asyncio.to_thread(self._capture)
                if data.get("kind") == "exception":
                    return {key: data[key] for key in ("exception_type", "frames", "causes", "leaves", "truncated")}
        except (OSError, ValueError, RuntimeError, KeyError) as error:
            return {"state": "diagnostic_unavailable", "failure_type": type(error).__name__}
        return {"state": "diagnostic_not_received"}

    async def verification(self, email: str) -> str:
        """Read only the newly generated verification token from the inherited socket."""
        data = await asyncio.to_thread(self._capture)
        if data.get("to") != email or not isinstance(data.get("token"), str):
            raise RuntimeError("Private capture recipient mismatch")
        return data["token"]

    def _stop_backend(self) -> None:
        if self.process is not None and self.process.poll() is None:
            os.killpg(self.process.pid, signal.SIGTERM)
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(self.process.pid, signal.SIGKILL)
                self.process.wait(timeout=10)

    def _attempt(self, operation: Any, failures: list[str], stage: str) -> Any:
        try:
            return operation()
        except (RuntimeError, OSError, ValueError, subprocess.TimeoutExpired):
            failures.append(stage)
            return None

    def _cleanup(self) -> None:
        failures: list[str] = []
        self._attempt(self._stop_backend, failures, "backend_stop")
        if self.email is not None:
            self.email.close()
        self._attempt(self._reconcile, failures, "initial_reconcile")
        for identifier in reversed(self.container_ids):
            self._attempt(
                lambda owned=identifier: self._docker(["rm", "-f", "-v", owned]), failures, "container_remove"
            )
        network_id = self.network_id
        if network_id:
            self._attempt(lambda: self._docker(["network", "rm", network_id]), failures, "network_remove")
        remaining = self._attempt(self._ids, failures, "baseline_readback")
        scoped = self._attempt(self._reconcile, failures, "final_reconcile")
        preserved = remaining is not None and self.baseline <= remaining
        removed = scoped == (set(), set()) and not self.pending
        self.cleanup_proof = {
            "baseline_count": len(self.baseline),
            "baseline_preserved": preserved,
            "owned_count": len(self.container_ids),
            "owned_removed": removed,
            "unresolved_intents": sorted(self.pending),
            "failures": failures,
            "recovery_journal": str(self.journal),
        }
        failed = bool(failures) or not preserved or not removed
        self._journal("cleanup_failed" if failed else "cleaned")
        if failed:
            raise RuntimeError("Owned cleanup or baseline preservation failed; recovery journal retained")
        shutil.rmtree(self.root)

    async def close(self) -> None:
        """Stop exact owned process and container IDs; preserve every pre-existing resource."""
        await asyncio.to_thread(self._cleanup)
