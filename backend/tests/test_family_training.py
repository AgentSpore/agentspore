"""Family training snapshots, privacy and UTC retention through real PG and ASGI."""

import asyncio
from datetime import timedelta
from uuid import uuid1, uuid4

import pytest
from sqlalchemy import text
from test_service_gateway import gateway_api as gateway_api
from test_service_gateway import gateway_boundaries as gateway_boundaries
from test_service_gateway import gateway_redis as gateway_redis
from test_service_gateway import gateway_redis_container as gateway_redis_container
from test_service_gateway import gateway_route as gateway_route
from test_service_usage import TODAY
from test_service_usage import usage_clock as usage_clock
from test_service_usage import usage_pg as usage_pg
from test_service_usage import usage_world as usage_world

from app.core.security import create_access_token
from app.services.usage_service import UsageService

pytestmark = pytest.mark.integration
EVENTS = "/_agentspore/events"
STATS = "/stats?curriculum_version=1"


@pytest.fixture(autouse=True)
async def family_profile(usage_world):
    async with usage_world.maker.begin() as db:
        await db.execute(
            text("UPDATE projects SET metrics_profile='family_safety' WHERE id=:id"),
            {"id": usage_world.projects[0]},
        )


@pytest.fixture
def progress():
    return {
        "event": "training_progress",
        "attempt_id": str(uuid4()),
        "curriculum_version": 1,
        "lang": "en",
        "answers": [],
        "completed": False,
    }


async def test_snapshots_preserve_prefix_and_count_concurrent_completion_once(
    gateway_api, usage_world, gateway_redis, progress
):
    client, upstream = gateway_api
    assert (await client.post(EVENTS, json=progress)).status_code == 204
    first = {**progress, "answers": [True]}
    assert (await client.post(EVENTS, json=first)).status_code == 204
    partial = {**progress, "answers": [True, False]}
    assert (await client.post(EVENTS, json=partial)).status_code == 204
    response = await client.get(STATS)
    assert response.status_code == 200
    stats = response.json()
    assert (stats["starts"], stats["completions"], stats["completion_ratio"]) == (1, 0, 0)
    assert stats["rounds"][1] == {"round_id": 2, "answered": 1, "wrong": 1, "error_ratio": 1}
    ignored = [
        partial,
        {**partial, "answers": [True]},
        {**partial, "answers": [False, False, True]},
        {**partial, "answers": [True, False, True], "lang": "ru"},
        {**partial, "answers": [True, False, True], "curriculum_version": 2},
    ]
    for snapshot in ignored:
        assert (await client.post(EVENTS, json=snapshot)).status_code == 204
    assert (await client.get(STATS)).json() == stats
    full = {**partial, "answers": [True, False] + [True] * 8}
    for size in range(3, 11):
        snapshot = {**full, "answers": full["answers"][:size]}
        assert (await client.post(EVENTS, json=snapshot)).status_code == 204
    final = {**full, "completed": True}
    responses = await asyncio.gather(
        *(client.post(EVENTS, json=snapshot) for snapshot in [final] * 20 + [partial, full])
    )
    assert [response.status_code for response in responses] == [204] * 22
    for snapshot in [partial, full, {**final, "answers": [True] * 10}]:
        assert (await client.post(EVENTS, json=snapshot)).status_code == 204
    response = await client.get(STATS)
    assert response.status_code == 200
    assert response.json() == {
        "curriculum_version": 1,
        "period_days": 7,
        "starts": 1,
        "completions": 1,
        "completion_ratio": 1,
        "rounds": [
            {"round_id": i, "answered": 1, "wrong": int(i == 2), "error_ratio": int(i == 2)}
            for i in range(1, 11)
        ],
    }
    async with usage_world.maker() as db:
        rows = (
            await db.execute(
                text(
                    "SELECT answers, completed, lang, curriculum_version "
                    "FROM family_training_attempts"
                )
            )
        ).all()
        assert [tuple(row) for row in rows] == [(final["answers"], True, "en", 1)]
        assert await db.scalar(text("SELECT count(*) FROM service_visits")) == 0
    assert not upstream["requests"] and await gateway_redis.dbsize() == 0
    assert all("set-cookie" not in response.headers for response in responses)
    assert not client.cookies


async def test_invalid_progress_is_generic_and_never_creates_attempt(
    gateway_api, usage_world, progress
):
    client, upstream = gateway_api
    marker = "D1-SYNTHETIC-PHRASE-DO-NOT-RETAIN"
    invalid = [
        {**progress, "phrase": marker},
        {**progress, "answers": [1]},
        {**progress, "answers": ["true"]},
        {**progress, "completed": 1},
        {**progress, "completed": True},
        {**progress, "answers": [True] * 11},
        {**progress, "curriculum_version": True},
        {**progress, "curriculum_version": "1"},
        {**progress, "curriculum_version": 0},
        {**progress, "curriculum_version": 1000001},
        {**progress, "attempt_id": str(uuid1())},
        {**progress, "lang": "xx"},
    ]
    errors = []
    for payload in invalid:
        response = await client.post(EVENTS, json=payload)
        assert response.status_code == 422
        assert marker not in response.text and '"input"' not in response.text
        assert '"ctx"' not in response.text and "set-cookie" not in response.headers
        errors.append(response.text)
    assert len(set(errors)) == 1
    for body, status in [(b"{", 400), (b"x" * 513, 413)]:
        response = await client.post(
            EVENTS, content=body, headers={"content-type": "application/json"}
        )
        assert response.status_code == status
    async with usage_world.maker() as db:
        assert await db.scalar(text("SELECT count(*) FROM family_training_attempts")) == 0
    assert not upstream["requests"]


async def test_owner_and_bot_progress_are_excluded_and_empty_stats_are_public(
    gateway_api, usage_world, gateway_redis, progress
):
    client, upstream = gateway_api
    for headers in [
        {"user-agent": "Mozilla/5.0 HeadlessChrome/129"},
        {"user-agent": "curl/8.1"},
        {"authorization": "Bearer " + create_access_token(str(usage_world.owner))},
    ]:
        response = await client.post(EVENTS, json=progress, headers=headers)
        assert response.status_code == 204 and "set-cookie" not in response.headers
    response = await client.get("/stats?curriculum_version=2")
    assert response.status_code == 200
    assert response.json() == {
        "curriculum_version": 2,
        "period_days": 7,
        "starts": 0,
        "completions": 0,
        "completion_ratio": None,
        "rounds": [
            {"round_id": i, "answered": 0, "wrong": 0, "error_ratio": None} for i in range(1, 11)
        ],
    }
    for version in ["invalid", "0", "1000001", "1.5"]:
        response = await client.get("/stats", params={"curriculum_version": version})
        assert response.status_code == 422
    async with usage_world.maker() as db:
        assert await db.scalar(text("SELECT count(*) FROM family_training_attempts")) == 0
        assert await db.scalar(text("SELECT count(*) FROM service_visits")) == 0
    assert not upstream["requests"] and await gateway_redis.dbsize() == 0


async def test_stats_cohort_and_ttl_use_distinct_utc_windows_without_changing_visit_count(
    gateway_api, usage_world, usage_clock
):
    client, upstream = gateway_api
    async with usage_world.maker.begin() as db:
        await db.execute(
            text(
                "INSERT INTO family_training_attempts "
                "(project_id,attempt_id,first_seen_day,curriculum_version,lang,answers,completed) "
                "VALUES (:project,:attempt,:day,1,'en',:answers,:completed)"
            ),
            [
                {
                    "project": usage_world.projects[0],
                    "attempt": uuid4(),
                    "day": TODAY - timedelta(days=age),
                    "answers": [False] if age == 6 else [True] * 10,
                    "completed": age != 6,
                }
                for age in [8, 7, 6, 0, -1]
            ],
        )
    response = await client.get(STATS)
    assert response.status_code == 200
    assert response.json() == {
        "curriculum_version": 1,
        "period_days": 7,
        "starts": 2,
        "completions": 1,
        "completion_ratio": 0.5,
        "rounds": [
            {
                "round_id": i,
                "answered": 2 if i == 1 else 1,
                "wrong": int(i == 1),
                "error_ratio": 0.5 if i == 1 else 0,
            }
            for i in range(1, 11)
        ],
    }
    async with usage_world.maker.begin() as db:
        assert await UsageService(db).purge_expired() == 0
    async with usage_world.maker() as db:
        days = list(
            await db.scalars(
                text("SELECT first_seen_day FROM family_training_attempts ORDER BY first_seen_day")
            )
        )
        assert days == [TODAY - timedelta(days=age) for age in [7, 6, 0, -1]]
        columns = await db.scalars(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name='family_training_attempts' AND table_schema='public'"
            )
        )
        assert set(columns) == {
            "project_id",
            "attempt_id",
            "first_seen_day",
            "curriculum_version",
            "lang",
            "answers",
            "completed",
        }
    assert not upstream["requests"]
