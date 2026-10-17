"""Additional public training and SQL-constraint acceptance through existing real fixtures."""

from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from test_family_training import EVENTS, STATS
from test_family_training import family_profile as family_profile
from test_family_training import progress as progress
from test_service_gateway import gateway_api as gateway_api
from test_service_gateway import gateway_boundaries as gateway_boundaries
from test_service_gateway import gateway_redis as gateway_redis
from test_service_gateway import gateway_redis_container as gateway_redis_container
from test_service_gateway import gateway_route as gateway_route
from test_service_usage import TODAY
from test_service_usage import usage_clock as usage_clock
from test_service_usage import usage_pg as usage_pg
from test_service_usage import usage_world as usage_world

pytestmark = pytest.mark.integration
LANGUAGES = (
    "en",
    "ru",
    "es",
    "pt",
    "fr",
    "de",
    "it",
    "nl",
    "pl",
    "uk",
    "cs",
    "sk",
    "ro",
    "hu",
    "el",
    "tr",
    "ar",
    "he",
    "fa",
    "hi",
    "bn",
    "ur",
    "id",
    "ms",
    "vi",
    "th",
    "ko",
    "ja",
    "zh-Hans",
    "sw",
    "tl",
)


async def test_lost_start_final_snapshot_counts_one_completed_attempt(gateway_api, progress):
    client, upstream = gateway_api
    final = {**progress, "answers": [True] * 10, "completed": True}
    for snapshot in [final, final, {**progress, "answers": [True]}]:
        response = await client.post(EVENTS, json=snapshot)
        assert response.status_code == 204 and "set-cookie" not in response.headers
    response = await client.get(STATS)
    assert response.status_code == 200
    stats = response.json()
    assert (stats["starts"], stats["completions"], stats["completion_ratio"]) == (1, 1, 1)
    assert stats["rounds"][-1] == {
        "round_id": 10,
        "answered": 1,
        "wrong": 0,
        "error_ratio": 0,
    }
    assert response.headers["cache-control"] == "no-store"
    assert not client.cookies and not upstream["requests"]


async def test_all_spec_languages_accept_partial_snapshots_without_starts(
    gateway_api, usage_world, gateway_redis, progress
):
    client, upstream = gateway_api
    for lang in LANGUAGES:
        response = await client.post(
            EVENTS,
            json={
                **progress,
                "attempt_id": str(uuid4()),
                "lang": lang,
                "answers": [True, False, True],
            },
        )
        assert response.status_code == 204 and "set-cookie" not in response.headers
    response = await client.get(STATS)
    assert response.status_code == 200
    stats = response.json()
    assert (stats["starts"], stats["completions"]) == (31, 0)
    assert stats["rounds"][1] == {
        "round_id": 2,
        "answered": 31,
        "wrong": 31,
        "error_ratio": 1,
    }
    assert stats["rounds"][3] == {
        "round_id": 4,
        "answered": 0,
        "wrong": 0,
        "error_ratio": None,
    }
    async with usage_world.maker() as db:
        langs = await db.scalars(text("SELECT DISTINCT lang FROM family_training_attempts"))
        assert set(langs) == set(LANGUAGES)
        assert await db.scalar(text("SELECT count(*) FROM service_visits")) == 0
    assert not client.cookies and not upstream["requests"] and await gateway_redis.dbsize() == 0


async def test_stats_query_is_private_and_other_routes_remain_upstream(gateway_api, usage_world):
    client, upstream = gateway_api
    response = await client.get("/stats")
    assert response.status_code == 200 and response.json()["curriculum_version"] == 1
    marker = "D1-SYNTHETIC-QUERY-DO-NOT-REFLECT"
    queries = [
        "curriculum_version=1&curriculum_version=2",
        "unknown=" + marker,
        "curriculum_version=" + marker,
        "curriculum_version=1000001",
    ]
    errors = []
    for query in queries:
        response = await client.get("/stats?" + query)
        assert response.status_code == 422 and marker not in response.text
        errors.append(response.text)
    assert len(set(errors)) == 1
    response = await client.get("/stats", headers={"host": "unknown.agentspore.com"})
    assert response.status_code == 404 and not upstream["requests"]
    upstream["status"], upstream["chunks"] = 404, [b""]
    assert (await client.post("/stats")).status_code == 404
    async with usage_world.maker.begin() as db:
        await db.execute(
            text("UPDATE projects SET metrics_profile=NULL WHERE id=:id"),
            {"id": usage_world.projects[0]},
        )
    assert (await client.get("/stats")).status_code == 404
    assert [(request.method, request.url.path) for request in upstream["requests"]] == [
        ("POST", "/stats"),
        ("GET", "/stats"),
    ]


@pytest.mark.parametrize(
    ("answers", "completed", "version"),
    [
        pytest.param([True], True, 1, id="incomplete-final"),
        pytest.param([None], False, 1, id="null-answer"),
        pytest.param([], False, 0, id="invalid-version"),
    ],
)
async def test_sql_constraints_reject_invalid_attempts_without_persisting(
    usage_world, answers, completed, version
):
    with pytest.raises(IntegrityError) as caught:
        async with usage_world.maker.begin() as db:
            await db.execute(
                text(
                    "INSERT INTO family_training_attempts "
                    "(project_id,attempt_id,first_seen_day,"
                    "curriculum_version,lang,answers,completed) "
                    "VALUES (:project,:attempt,:day,:version,'en',"
                    "CAST(:answers AS boolean[]),:completed)"
                ),
                {
                    "project": usage_world.projects[0],
                    "attempt": uuid4(),
                    "day": TODAY,
                    "version": version,
                    "answers": answers,
                    "completed": completed,
                },
            )
    assert getattr(caught.value.orig, "sqlstate", None) == "23514"
    async with usage_world.maker() as db:
        assert await db.scalar(text("SELECT count(*) FROM family_training_attempts")) == 0
