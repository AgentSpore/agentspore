"""D2 telemetry acceptance through the actual gateway, routes and PostgreSQL."""

import asyncio
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import text
from test_service_gateway import gateway_api as gateway_api
from test_service_gateway import gateway_boundaries as gateway_boundaries
from test_service_gateway import gateway_redis as gateway_redis
from test_service_gateway import gateway_redis_container as gateway_redis_container
from test_service_gateway import gateway_route as gateway_route
from test_service_usage import TODAY, ZERO
from test_service_usage import usage_api as usage_api
from test_service_usage import usage_clock as usage_clock
from test_service_usage import usage_pg as usage_pg
from test_service_usage import usage_world as usage_world

from app.core.security import create_access_token

EVENTS = "/_agentspore/events"
OPEN = {"event": "form_open", "model": "iphone-x", "lang": "en"}
PRIVATE = "D2-SYNTHETIC-CONTACT-DO-NOT-RETAIN"

COUNTS = [
    ("form_open", "iphone-x", "en", 0, 2),
    ("png_download", "iphone-x", "en", 0, 5),
    ("png_download", "iphone-x", "en", 6, 3),
    ("png_download", "iphone-x", "en", 7, 99),
    ("png_download", "iphone-x", "en", -1, 99),
    ("png_download", "pixel-8", "ru", 0, 1),
]
EXPECTED_STATS = {
    "period_days": 7,
    "groups": [
        {
            "model": "iphone-x",
            "lang": "en",
            "form_open": 2,
            "png_download": 8,
            "download_open_ratio": 4.0,
        },
        {
            "model": "pixel-8",
            "lang": "ru",
            "form_open": 0,
            "png_download": 1,
            "download_open_ratio": None,
        },
    ],
}


async def test_reserved_events_exclude_owner_bots_without_forwarding_or_visit_identity(
    gateway_api, usage_world, gateway_redis
):
    """A registered origin accepts enums locally; ordinary /stats remains upstream-owned."""
    client, upstream = gateway_api
    upstream["status"], upstream["chunks"] = 404, [b""]
    response = await client.post(EVENTS, json=OPEN)
    assert response.status_code == 204
    assert response.content == b"" and "set-cookie" not in response.headers
    excluded = [
        {"user-agent": "Mozilla/5.0 HeadlessChrome/129"},
        {"user-agent": "curl/8.1"},
        {"authorization": "Bearer " + create_access_token(str(usage_world.owner))},
    ]
    for headers in excluded:
        response = await client.post(EVENTS, json=OPEN, headers=headers)
        assert response.status_code == 204 and "set-cookie" not in response.headers
    assert not upstream["requests"]
    assert await gateway_redis.dbsize() == 0
    async with usage_world.maker() as db:
        assert await db.scalar(text("SELECT sum(count) FROM service_event_counts")) == 1
        assert await db.scalar(text("SELECT count(*) FROM service_visits")) == 0
        columns = await db.scalars(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name='service_event_counts'"
            )
        )
        assert set(columns) == {"project_id", "event_day", "event", "model", "lang", "count"}
    unknown = await client.post(EVENTS, json=OPEN, headers={"host": "unknown.agentspore.com"})
    assert unknown.status_code == 404 and not upstream["requests"]
    response = await client.get("/stats")
    assert response.status_code == 404
    assert len(upstream["requests"]) == 1 and upstream["requests"][0].url.path == "/stats"


async def test_parallel_events_persist_exact_counts_and_invalid_input_never_leaks(
    gateway_api, usage_world
):
    """Real concurrent HTTP inserts accumulate; validation rejects text before storage."""
    client, upstream = gateway_api
    upstream["status"], upstream["chunks"] = 404, [b""]
    assert (await client.post(EVENTS, json=OPEN)).status_code == 204
    async with asyncio.TaskGroup() as group:
        jobs = [
            group.create_task(client.post(EVENTS, json={**OPEN, "event": "png_download"}))
            for _ in range(20)
        ]
    assert [job.result().status_code for job in jobs] == [204] * 20
    invalid = [
        {**OPEN, "contact": PRIVATE},
        {**OPEN, "event": PRIVATE},
        {**OPEN, "model": PRIVATE},
        {**OPEN, "lang": PRIVATE},
    ]
    for payload in invalid:
        response = await client.post(EVENTS, json=payload)
        assert response.status_code == 422 and PRIVATE not in response.text
    response = await client.post(
        EVENTS, content=b"x" * 513, headers={"content-type": "application/json"}
    )
    assert response.status_code == 413
    response = await client.post(EVENTS, content=b"{", headers={"content-type": "application/json"})
    assert response.status_code == 400
    async with usage_world.maker() as db:
        rows = (
            await db.execute(
                text("SELECT event,model,lang,count FROM service_event_counts ORDER BY event")
            )
        ).all()
        assert [tuple(row) for row in rows] == [
            ("form_open", "iphone-x", "en", 1),
            ("png_download", "iphone-x", "en", 20),
        ]
    assert not upstream["requests"]


async def test_creator_event_stats_use_seven_days_without_changing_a2_usage_or_heartbeat(
    usage_api, usage_world, usage_clock
):
    """Counts are project-scoped actions, not uniques or a capped conversion percentage."""
    project = usage_world.projects[0]
    path = f"/api/v1/agents/projects/{project}/events"
    headers = {"X-API-Key": usage_world.keys[0]}
    response = await usage_api.get(path, headers=headers)
    assert response.status_code == 200
    assert response.json() == {"period_days": 7, "groups": []}
    assert (await usage_api.get(path, headers={"X-API-Key": "invalid"})).status_code == 401
    foreign = await usage_api.get(path, headers={"X-API-Key": usage_world.keys[1]})
    assert foreign.status_code == 403
    assert (
        await usage_api.get(f"/api/v1/agents/projects/{uuid4()}/events", headers=headers)
    ).status_code == 404
    async with usage_world.maker.begin() as db:
        await db.execute(
            text(
                "INSERT INTO service_event_counts (project_id,event_day,event,model,lang,count) "
                "VALUES (:project,:day,:event,:model,:lang,:count)"
            ),
            [
                {
                    "project": project,
                    "day": TODAY - timedelta(days=age),
                    "event": event,
                    "model": model,
                    "lang": lang,
                    "count": count,
                }
                for event, model, lang, age, count in COUNTS
            ],
        )
    response = await usage_api.get(path, headers=headers)
    assert response.status_code == 200
    assert response.json() == EXPECTED_STATS
    usage = await usage_api.get(f"/api/v1/projects/{project}/usage")
    assert {key: usage.json()[key] for key in ZERO} == ZERO
    assert usage.json()["measurement"]["coverage"] == {"status": "unverified", "since": None}
    assert usage.json()["measurement"]["d7"]["value"] is None
    heartbeat = await usage_api.post(
        "/api/v1/agents/heartbeat", json={"status": "idle"}, headers=headers
    )
    assert heartbeat.status_code == 200
    assert heartbeat.json()["project_usage"] == [
        {"project_id": str(project), **ZERO} for project in sorted(usage_world.projects[:2])
    ]
