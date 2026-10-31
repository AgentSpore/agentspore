"""A4 P2a: V108 builds the creator index CONCURRENTLY, and real Flyway accepts it outside a tx.

Measured with flyway/flyway:10 against PostgreSQL 16: with Flyway's default transactional advisory
lock its second connection stays "idle in transaction" and CREATE INDEX CONCURRENTLY waits for it
forever, so every compose Flyway command sets `-postgresql.transactional.lock=false` and the run
below takes that flag from docker-compose.yml.
"""

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml
from sqlalchemy import text
from test_deployment_control_plane import deployment_engine as deployment_engine
from test_deployment_control_plane import deployment_postgres as deployment_postgres

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]
MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "db/migrations/V108__project_deployments_creator_index.sql"
)
ROOT = MIGRATION.parents[2]
COMPOSE_FILES = (
    "docker-compose.yml",
    "deploy/docker-compose.prod.yml",
    "deploy/docker-compose.coolify.yml",
)
LOCK_FLAG = "-postgresql.transactional.lock=false"
INDEX = "idx_project_deployments_creator_created"
FLYWAY_IMAGE = "flyway/flyway:10"  # the image docker-compose.yml runs
BASELINE = (
    "CREATE TABLE project_deployments "
    "(id serial PRIMARY KEY, creator_agent_id uuid, created_at timestamptz);\n"
)


async def index_state(engine):
    async with engine.connect() as connection:
        return (
            await connection.execute(
                text(
                    "SELECT i.indisvalid, pg_get_indexdef(i.indexrelid) AS definition "
                    "FROM pg_index i "
                    "JOIN pg_class c ON c.oid = i.indexrelid WHERE c.relname = :name"
                ),
                {"name": INDEX},
            )
        ).one_or_none()


async def test_creator_index_is_valid_and_rerun_fails_loudly(deployment_engine):
    sql = MIGRATION.read_text()
    async with deployment_engine.connect() as connection:
        autocommit = await connection.execution_options(isolation_level="AUTOCOMMIT")
        await autocommit.execute(text(sql))
        with pytest.raises(Exception, match="already exists"):
            await autocommit.execute(text(sql))
    state = await index_state(deployment_engine)
    assert state.indisvalid is True
    assert "(creator_agent_id, created_at)" in state.definition


def compose_flyway_flags(compose_file: str) -> list[str]:
    """The `-postgresql.*` options of the Flyway service command in `compose_file`."""
    command = yaml.safe_load((ROOT / compose_file).read_text())["services"]["flyway"]["command"]
    return [arg for arg in command.split() if arg.startswith("-postgresql.")]


@pytest.mark.parametrize("compose_file", COMPOSE_FILES)
async def test_compose_flyway_disables_the_transactional_lock(compose_file):
    assert LOCK_FLAG in compose_flyway_flags(compose_file)


async def test_sdk_user_stand_flyway_disables_the_transactional_lock():
    stand = (ROOT / "sdk/tests/integration/user_stand.py").read_text()
    assert f'"{LOCK_FLAG}"' in stand


def flyway_migrate(postgres, database, migrations: Path) -> subprocess.CompletedProcess:
    """Run real Flyway on `database` with `migrations` copied in (no bind mount on colima)."""
    ip = postgres.get_docker_client().bridge_ip(postgres.get_wrapped_container().id)
    url = f"-url=jdbc:postgresql://{ip}:5432/{database}"
    created = subprocess.run(
        ["docker", "create", FLYWAY_IMAGE, url, f"-user={postgres.username}",
         f"-password={postgres.password}", *compose_flyway_flags("docker-compose.yml"), "migrate"],
        capture_output=True, text=True, check=True,
    )  # fmt: skip
    container = created.stdout.strip()
    try:
        subprocess.run(["docker", "cp", f"{migrations}/.", f"{container}:/flyway/sql"], check=True)
        return subprocess.run(
            ["docker", "start", "-a", container], capture_output=True, text=True, timeout=90
        )
    finally:
        subprocess.run(["docker", "rm", "-f", container], capture_output=True)


async def create_database(engine, name):
    async with engine.connect() as connection:
        autocommit = await connection.execution_options(isolation_level="AUTOCOMMIT")
        await autocommit.execute(text(f"CREATE DATABASE {name}"))


async def test_flyway_applies_v108_but_refuses_it_mixed_with_another_statement(
    deployment_engine, deployment_postgres, tmp_path
):
    await create_database(deployment_engine, "flyway_ok")
    await create_database(deployment_engine, "flyway_mixed")
    good = tmp_path / "good"
    good.mkdir()
    (good / "V1__baseline.sql").write_text(BASELINE)
    shutil.copy(MIGRATION, good / MIGRATION.name)
    ok = flyway_migrate(deployment_postgres, "flyway_ok", good)
    assert ok.returncode == 0, ok.stdout[-800:] + ok.stderr[-800:]
    assert "Successfully applied 2 migrations" in ok.stdout

    mixed = tmp_path / "mixed"
    mixed.mkdir()
    (mixed / "V1__baseline.sql").write_text(BASELINE)
    body = MIGRATION.read_text() + "SELECT 1;\n"
    (mixed / MIGRATION.name).write_text(body)
    refused = flyway_migrate(deployment_postgres, "flyway_mixed", mixed)
    assert refused.returncode != 0
    assert "transaction" in (refused.stdout + refused.stderr).lower()
