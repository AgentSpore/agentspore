"""D3 fixed action counters through real PostgreSQL, creator auth and gateway ASGI."""

import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

from sqlalchemy import text
from test_service_gateway import gateway_api as gateway_api
from test_service_gateway import gateway_boundaries as gateway_boundaries
from test_service_gateway import gateway_redis as gateway_redis
from test_service_gateway import gateway_redis_container as gateway_redis_container
from test_service_gateway import gateway_route as gateway_route
from test_service_usage import TODAY
from test_service_usage import usage_api as usage_api
from test_service_usage import usage_clock as usage_clock
from test_service_usage import usage_pg as usage_pg
from test_service_usage import usage_world as usage_world

from app.core.security import create_access_token
from app.services.github_service import GitHubService
from app.services.usage_service import UsageService

EVENTS = "/_agentspore/events"
OPEN = {"event": "folder_open", "section": "overview", "lang": "en"}
PROFILE = text("UPDATE projects SET metrics_profile='emergency_binder' WHERE id=:id")
COUNTERS = {
    "folder_open": 0,
    "section_open": 0,
    "section_complete": 0,
    "print_request": 0,
    "backup_download": 0,
}


async def test_creator_explicit_binder_profile_is_returned_and_persisted(
    usage_api, usage_world, monkeypatch
):
    monkeypatch.setattr(GitHubService, "create_repo", AsyncMock(return_value=None))
    response = await usage_api.post(
        "/api/v1/agents/projects",
        headers={"X-API-Key": usage_world.keys[1]},
        json={"title": "D3 synthetic folder", "metrics_profile": "emergency_binder"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["metrics_profile"] == "emergency_binder"
    assert data["creator_agent_id"] == str(usage_world.agents[1])
    async with usage_world.maker() as db:
        assert (
            await db.scalar(
                text("SELECT metrics_profile FROM projects WHERE id=:id"), {"id": UUID(data["id"])}
            )
            == "emergency_binder"
        )


async def test_all_26_fixed_combinations_and_parallel_retries_count_actions(
    gateway_api, usage_world, gateway_redis
):
    client, upstream = gateway_api
    async with usage_world.maker.begin() as db:
        await db.execute(PROFILE, {"id": usage_world.projects[0]})
    pairs = [(event, "overview") for event in ["folder_open", "print_request", "backup_download"]]
    pairs += [
        (event, section)
        for event in ["section_open", "section_complete"]
        for section in ["contacts", "home", "documents", "accounts", "care"]
    ]
    for event, section in pairs:
        for lang in ["en", "ru"]:
            response = await client.post(
                EVENTS, json={"event": event, "section": section, "lang": lang}
            )
            assert response.status_code == 204 and "set-cookie" not in response.headers
    responses = await asyncio.gather(*(client.post(EVENTS, json=OPEN) for _ in range(20)))
    assert [response.status_code for response in responses] == [204] * 20
    async with usage_world.maker() as db:
        rows = (
            await db.execute(
                text("SELECT event,section,lang,count FROM emergency_binder_event_counts")
            )
        ).all()
        assert len(rows) == 26 and sum(row[3] for row in rows) == 46
        assert (
            next(row[3] for row in rows if tuple(row[:3]) == ("folder_open", "overview", "en"))
            == 21
        )
        columns = await db.scalars(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name='emergency_binder_event_counts'"
            )
        )
        assert set(columns) == {"project_id", "event_day", "event", "section", "lang", "count"}
        assert await db.scalar(text("SELECT count(*) FROM service_visits")) == 0
    assert not client.cookies and not upstream["requests"] and await gateway_redis.dbsize() == 0


async def test_invalid_pairs_and_private_input_return_fixed_errors_without_storage(
    gateway_api, usage_world
):
    client, upstream = gateway_api
    async with usage_world.maker.begin() as db:
        await db.execute(PROFILE, {"id": usage_world.projects[0]})
    marker = "D3-SYNTHETIC-INPUT-DO-NOT-RETAIN"
    invalid = [
        {**OPEN, "section": "contacts"},
        {**OPEN, "event": "section_open"},
        {**OPEN, "event": "section_complete"},
        {**OPEN, "lang": "fr"},
        {**OPEN, "section": 1},
        {**OPEN, "event": True},
        {**OPEN, "lang": 1},
        {**OPEN, "section": "unknown"},
        {**OPEN, "country": "US"},
        {**OPEN, "notes": marker},
        {**OPEN, "attempt_id": str(uuid4())},
        {"event": "form_open", "model": "iphone-x", "lang": "en"},
    ]
    errors = []
    for payload in invalid:
        response = await client.post(EVENTS, json=payload)
        assert response.status_code == 422 and marker not in response.text
        assert '"input"' not in response.text and '"ctx"' not in response.text
        assert "set-cookie" not in response.headers
        errors.append(response.text)
    assert len(set(errors)) == 1
    for body, status in [(b"{", 400), (b"x" * 513, 413)]:
        response = await client.post(
            EVENTS, content=body, headers={"content-type": "application/json"}
        )
        assert response.status_code == status
    async with usage_world.maker() as db:
        assert await db.scalar(text("SELECT count(*) FROM emergency_binder_event_counts")) == 0
    assert not upstream["requests"]


async def test_owner_bots_and_unknown_hosts_cannot_record_and_stats_remains_upstream(
    gateway_api, usage_world, gateway_redis
):
    client, upstream = gateway_api
    async with usage_world.maker.begin() as db:
        await db.execute(PROFILE, {"id": usage_world.projects[0]})
    for headers in [
        {"user-agent": "Mozilla/5.0 HeadlessChrome/129"},
        {"user-agent": "curl/8.1"},
        {"authorization": "Bearer " + create_access_token(str(usage_world.owner))},
    ]:
        response = await client.post(EVENTS, json=OPEN, headers=headers)
        assert response.status_code == 204 and "set-cookie" not in response.headers
    response = await client.post(EVENTS, json=OPEN, headers={"host": "unknown.agentspore.com"})
    assert response.status_code == 404 and not upstream["requests"]
    async with usage_world.maker() as db:
        assert await db.scalar(text("SELECT count(*) FROM emergency_binder_event_counts")) == 0
        assert await db.scalar(text("SELECT count(*) FROM service_visits")) == 0
    assert await gateway_redis.dbsize() == 0 and not client.cookies
    upstream["status"], upstream["chunks"] = 404, [b""]
    assert (await client.get("/stats")).status_code == 404
    assert len(upstream["requests"]) == 1


async def test_creator_groups_have_action_ratios_above_one_and_null_without_open(
    usage_api, usage_world
):
    project = usage_world.projects[0]
    path = f"/api/v1/agents/projects/{project}/events"
    headers = {"X-API-Key": usage_world.keys[0]}
    async with usage_world.maker.begin() as db:
        await db.execute(PROFILE, {"id": project})
        await db.execute(
            text(
                "INSERT INTO emergency_binder_event_counts "
                "(project_id,event_day,event,section,lang,count) "
                "VALUES (:project,:day,:event,:section,:lang,:count)"
            ),
            [
                {
                    "project": project,
                    "day": TODAY,
                    "event": event,
                    "section": section,
                    "lang": lang,
                    "count": count,
                }
                for event, section, lang, count in [
                    ("section_open", "contacts", "en", 2),
                    ("section_complete", "contacts", "en", 5),
                    ("section_complete", "care", "ru", 3),
                    ("folder_open", "overview", "en", 4),
                    ("print_request", "overview", "en", 3),
                    ("backup_download", "overview", "en", 2),
                ]
            ],
        )
    assert (await usage_api.get(path, headers={"X-API-Key": "invalid"})).status_code == 401
    response = await usage_api.get(path, headers={"X-API-Key": usage_world.keys[1]})
    assert response.status_code == 403
    response = await usage_api.get(f"/api/v1/agents/projects/{uuid4()}/events", headers=headers)
    assert response.status_code == 404
    response = await usage_api.get(path, headers=headers)
    assert response.status_code == 200
    assert response.json() == {
        "period_days": 7,
        "groups": [
            {
                "section": "care",
                "lang": "ru",
                **COUNTERS,
                "section_complete": 3,
                "completion_open_ratio": None,
            },
            {
                "section": "contacts",
                "lang": "en",
                **COUNTERS,
                "section_open": 2,
                "section_complete": 5,
                "completion_open_ratio": 2.5,
            },
            {
                "section": "overview",
                "lang": "en",
                **COUNTERS,
                "folder_open": 4,
                "print_request": 3,
                "backup_download": 2,
                "completion_open_ratio": None,
            },
        ],
    }


async def test_utc_cohort_and_eight_day_purge_preserve_deleted_visit_return(
    usage_api, usage_world, usage_clock
):
    project = usage_world.projects[0]
    async with usage_world.maker.begin() as db:
        await db.execute(PROFILE, {"id": project})
        await db.execute(
            text(
                "INSERT INTO emergency_binder_event_counts "
                "(project_id,event_day,event,section,lang,count) "
                "VALUES (:project,:day,'folder_open','overview','en',:count)"
            ),
            [
                {"project": project, "day": TODAY - timedelta(days=age), "count": count}
                for age, count in [(8, 100), (7, 100), (6, 2), (0, 3), (-1, 100)]
            ],
        )
        await db.execute(
            text(
                "INSERT INTO service_visits (project_id,visitor_id,visit_day) VALUES (:p,:v,:day)"
            ),
            {"p": project, "v": uuid4(), "day": TODAY - timedelta(days=8)},
        )
    path = f"/api/v1/agents/projects/{project}/events"
    headers = {"X-API-Key": usage_world.keys[0]}
    response = await usage_api.get(path, headers=headers)
    assert response.status_code == 200
    assert response.json() == {
        "period_days": 7,
        "groups": [
            {
                "section": "overview",
                "lang": "en",
                **COUNTERS,
                "folder_open": 5,
                "completion_open_ratio": None,
            },
        ],
    }
    async with usage_world.maker.begin() as db:
        assert await UsageService(db).purge_expired() == 1
    async with usage_world.maker() as db:
        days = list(
            await db.scalars(
                text("SELECT event_day FROM emergency_binder_event_counts ORDER BY event_day")
            )
        )
        assert days == [TODAY - timedelta(days=age) for age in [7, 6, 0, -1]]
    assert (await usage_api.get(path, headers=headers)).json() == response.json()


async def test_unprofiled_service_keeps_d2_dto_and_creator_response(
    gateway_api, usage_api, usage_world
):
    client, upstream = gateway_api
    payload = {"event": "form_open", "model": "iphone-x", "lang": "en"}
    for event in ["form_open", "png_download"]:
        assert (await client.post(EVENTS, json={**payload, "event": event})).status_code == 204
    response = await usage_api.get(
        f"/api/v1/agents/projects/{usage_world.projects[0]}/events",
        headers={"X-API-Key": usage_world.keys[0]},
    )
    assert response.status_code == 200
    assert response.json() == {
        "period_days": 7,
        "groups": [
            {
                "model": "iphone-x",
                "lang": "en",
                "form_open": 1,
                "png_download": 1,
                "download_open_ratio": 1,
            },
        ],
    }
    assert not upstream["requests"]
