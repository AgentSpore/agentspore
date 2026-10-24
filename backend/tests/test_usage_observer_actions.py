"""Committed, privacy-preserving action receipts on real PostgreSQL and Redis."""

import asyncio
import hashlib
import hmac
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
import test_usage_observer as observer_fixtures
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.core.security import create_access_token
from app.repositories.usage_repo import UsageRepository
from app.services.usage_service import UsageService

usage_pg = observer_fixtures.usage_pg
usage_world = observer_fixtures.usage_world
usage_clock = observer_fixtures.usage_clock
gateway_redis_container = observer_fixtures.gateway_redis_container
gateway_redis = observer_fixtures.gateway_redis
observer_app = observer_fixtures.observer_app
observer = observer_fixtures.observer
metadata = observer_fixtures.metadata
PATH, KEY = observer_fixtures.PATH, observer_fixtures.KEY
pytestmark = pytest.mark.usefixtures("usage_clock")


@pytest.fixture
def action_metadata(metadata):
    return {
        **metadata,
        "X-Usage-Kind": "board_created",
        "X-Usage-Method": "POST",
        "X-Usage-Content-Type": "application/json",
        "X-Usage-Path-Class": "board_creation",
    }


async def test_action_retry_concurrency_and_distinct_ingress(
    observer, action_metadata, usage_world
):
    client, state = observer
    replies = await asyncio.gather(*(client.post(PATH, headers=action_metadata) for _ in range(5)))
    assert all(r.status_code == 204 and "set-cookie" not in r.headers for r in replies)
    async with usage_world.maker() as db:
        rows = (await db.execute(text("SELECT * FROM service_action_receipts"))).mappings().all()
        assert len(rows) == 1
        assert set(rows[0]) == {"project_id", "action_day", "action", "receipt_hash"}
        receipt = rows[0]["receipt_hash"]
        assert len(receipt) == 32 and rows[0]["action"] == "board_created"
        assert rows[0]["action_day"] == UsageService.today()
        visitor = hmac.new(
            KEY.encode(),
            b"agentspore:otkrytka:visitor:v1\0" + UUID(action_metadata["X-Usage-Request-Id"]).bytes,
            hashlib.sha256,
        ).digest()
        assert receipt != visitor
        assert await db.scalar(text("SELECT count(*) FROM service_visits")) == 0
    action_metadata["X-Usage-Request-Id"] = str(uuid4())
    assert (await client.post(PATH, headers=action_metadata)).status_code == 204
    async with usage_world.maker() as db:
        assert await db.scalar(text("SELECT count(*) FROM service_action_receipts")) == 2
    assert state == {"opened": 6, "committed": 6}


@pytest.mark.parametrize(
    "change",
    [
        {"X-Usage-Kind": "other"},
        {"X-Usage-Kind": "visit"},
        {"X-Usage-Method": "GET"},
        {"X-Usage-Status": "201"},
        {"X-Usage-Status": "500"},
        {"X-Usage-Content-Type": "text/html"},
        {"X-Usage-Path-Class": "document"},
        {"X-Usage-Path-Class": "/boards/private-marker"},
        {"X-Usage-Verified-User-Id": "private-marker"},
        {"X-Usage-Project-Id": str(uuid4())},
        {"X-Usage-Kind": ""},
        {"X-Usage-Observer-Key": "wrong"},
    ],
)
async def test_action_invalid_metadata_precedes_database(observer, action_metadata, change):
    client, state = observer
    action_metadata.update(change)
    result = await client.post(PATH, headers=action_metadata)
    assert result.status_code in {400, 403} and state["opened"] == 0
    assert "private-marker" not in result.text and "set-cookie" not in result.headers


@pytest.mark.parametrize("identity", ["owner", "bot", "anonymous", "nonowner"])
async def test_action_identity_exclusions(observer, action_metadata, usage_world, identity):
    client, _ = observer
    if identity == "owner":
        action_metadata["Authorization"] = "Bearer " + create_access_token(str(usage_world.owner))
    if identity == "nonowner":
        stranger = uuid4()
        async with usage_world.maker.begin() as db:
            await db.execute(
                text(
                    "INSERT INTO users (id,email,hashed_password,name) "
                    "VALUES (:id,'stranger@example.invalid','synthetic','stranger')"
                ),
                {"id": stranger},
            )
        action_metadata["Authorization"] = "Bearer " + create_access_token(str(stranger))
    if identity == "bot":
        action_metadata["X-Usage-User-Agent"] = "Googlebot"
    result = await client.post(PATH, headers=action_metadata)
    assert result.status_code == 204 and "set-cookie" not in result.headers
    async with usage_world.maker() as db:
        assert await db.scalar(text("SELECT count(*) FROM service_action_receipts")) == int(
            identity in {"anonymous", "nonowner"}
        )
        assert await db.scalar(text("SELECT count(*) FROM service_visits")) == 0


async def test_action_deferred_failure_is_private(
    observer, action_metadata, usage_world, capsys, caplog
):
    client, state = observer
    async with usage_world.maker.begin() as db:
        await db.execute(
            text(
                "CREATE FUNCTION action_failure() RETURNS trigger LANGUAGE plpgsql AS $$ "
                "BEGIN RAISE EXCEPTION 'private-marker %', NEW.receipt_hash; "
                "RETURN NEW; END $$"
            )
        )
        await db.execute(
            text(
                "CREATE CONSTRAINT TRIGGER action_commit_failure AFTER INSERT "
                "ON service_action_receipts DEFERRABLE INITIALLY DEFERRED "
                "FOR EACH ROW EXECUTE FUNCTION action_failure()"
            )
        )
    result = await client.post(PATH, headers=action_metadata)
    assert result.status_code == 503 and "set-cookie" not in result.headers
    assert state == {"opened": 1, "committed": 0}
    captured = capsys.readouterr()
    output = result.text + captured.out + captured.err + caplog.text
    assert all(
        value not in output
        for value in ("private-marker", KEY, action_metadata["X-Usage-Request-Id"])
    )
    async with usage_world.maker() as db:
        assert await db.scalar(text("SELECT count(*) FROM service_action_receipts")) == 0


async def test_action_retention_preserves_visit_return_count(usage_world):
    today = UsageService.today()
    async with usage_world.maker.begin() as db:
        repo = UsageRepository(db)
        for age in (7, 8):
            await repo.record_action_receipt(
                usage_world.projects[0], bytes([age]) * 32, None, today - timedelta(days=age)
            )
        await db.execute(
            text("INSERT INTO service_visits VALUES (:project,:visitor,:day)"),
            {
                "project": usage_world.projects[0],
                "visitor": uuid4(),
                "day": today - timedelta(days=8),
            },
        )
        assert await repo.purge_expired(today) == 1
        assert list(await db.scalars(text("SELECT action_day FROM service_action_receipts"))) == [
            today - timedelta(days=7)
        ]


@pytest.mark.parametrize("invalid", ["project", "action", "short", "long"])
async def test_action_database_constraints(usage_world, invalid):
    async with usage_world.maker.begin() as db:
        with pytest.raises(IntegrityError):
            async with db.begin_nested():
                await db.execute(
                    text(
                        "INSERT INTO service_action_receipts "
                        "(project_id,action_day,action,receipt_hash) "
                        "VALUES (:project,:day,:action,:receipt)"
                    ),
                    {
                        "project": uuid4() if invalid == "project" else usage_world.projects[0],
                        "day": UsageService.today(),
                        "action": "other" if invalid == "action" else "board_created",
                        "receipt": b"x"
                        * (31 if invalid == "short" else 33 if invalid == "long" else 32),
                    },
                )
