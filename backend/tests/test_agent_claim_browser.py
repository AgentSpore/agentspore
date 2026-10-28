"""Opt-in real browser/API/PostgreSQL ownership flow for #200; artifacts stay outside Git."""

import asyncio
import hashlib
import inspect
import json
import os
import socket
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import pytest
import uvicorn
from app.api.v1 import agents, auth, ownership, users
from app.core.database import get_db
from app.core.redis_client import get_redis
from app.core.security import create_access_token
from app.services.agent_ownership_service import AgentOwnershipService
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from httpx import AsyncClient
from sqlalchemy import text
from test_agent_registration_limits import a3_redis as a3_redis
from test_agent_registration_limits import a3_redis_container as a3_redis_container
from test_service_usage import usage_pg as usage_pg
from test_service_usage import usage_world as usage_world

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_AGENT_CLAIM_BROWSER") != "1",
    reason="Requires a local frontend and Playwright",
)
playwright = (
    pytest.importorskip("playwright.async_api")
    if os.environ.get("RUN_AGENT_CLAIM_BROWSER") == "1"
    else None
)
CLAIM_VIEWPORT = {"width": 390, "height": 844}
FRONTEND = os.environ.get("CLAIM_BROWSER_FRONTEND", "http://127.0.0.1:3211")


@pytest.fixture
async def claim_browser_api(usage_world, a3_redis):
    """Serve actual branch routers against disposable migrated PostgreSQL and Redis."""
    assert urlsplit(FRONTEND).hostname in {"127.0.0.1", "localhost", "::1"}
    world = usage_world
    source = Path(inspect.getfile(AgentOwnershipService)).resolve()
    assert source.is_relative_to(Path(__file__).resolve().parents[1])
    async with world.maker.begin() as db:
        await db.execute(
            text("UPDATE users SET is_verified=TRUE WHERE id=:id"), {"id": world.owner}
        )
    app = FastAPI()
    for router in (agents.router, auth.router, ownership.router, users.router):
        app.include_router(router, prefix="/api/v1")
    app.add_middleware(
        CORSMiddleware, allow_origins=[FRONTEND], allow_methods=["*"], allow_headers=["*"]
    )

    async def request_db():
        async with world.maker() as db:
            yield db
            await db.commit()

    app.dependency_overrides[get_db] = request_db
    app.dependency_overrides[get_redis] = lambda: a3_redis
    app.get("/healthz")(lambda: {"source_sha256": hashlib.sha256(source.read_bytes()).hexdigest()})
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    base = f"http://127.0.0.1:{listener.getsockname()[1]}"
    server = uvicorn.Server(uvicorn.Config(app, access_log=False, log_level="critical"))
    task = asyncio.create_task(server.serve(sockets=[listener]))
    try:
        async with asyncio.timeout(15):
            while not server.started:
                assert not task.done(), "Local backend exited before readiness"
                await asyncio.sleep(0.05)
        async with AsyncClient(base_url=base, timeout=15) as client:
            assert (await client.get("/healthz")).status_code == 200
            registered = await client.post(
                "/api/v1/agents/register",
                json={"name": "Browser-200", "model_provider": "test", "model_name": "synthetic"},
            )
            assert registered.status_code == 200
            identity = registered.json()
            assert identity["owner_status"] == "unclaimed"
            assert identity["claim_url"] == f"/agents/{identity['agent_id']}/claim"
            world = replace(
                world,
                agents=(UUID(identity["agent_id"]), world.agents[1]),
                keys=(identity["api_key"], world.keys[1]),
            )
            yield SimpleNamespace(world=world, base=base, client=client)
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, timeout=15)
        listener.close()


async def _proxy_api(route, api, records):
    request = route.request
    path = urlsplit(request.url).path
    if not path.startswith("/api/v1/"):
        if request.url.startswith(FRONTEND + "/"):
            await route.continue_()
        else:
            await route.abort()
        return
    query = urlsplit(request.url).query
    response = await route.fetch(url=api.base + path + ("?" + query if query else ""))
    records.append({"path": path, "method": request.method, "status": response.status})
    await route.fulfill(response=response)


async def _owner_id(world):
    async with world.maker() as db:
        return await db.scalar(
            text("SELECT owner_user_id FROM agents WHERE id=:id"), {"id": world.agents[0]}
        )


async def _approve_from_agent(api, claim):
    denied = await api.client.post(
        "/api/v1/agents/owner-claims/" + claim["claim_id"],
        headers={"Authorization": "Bearer " + create_access_token(str(api.world.owner))},
        json={"expected_user_id": str(api.world.owner), "decision": "approve"},
    )
    assert denied.status_code == 422
    assert await _owner_id(api.world) is None
    response = await api.client.post(
        "/api/v1/agents/owner-claims/" + claim["claim_id"],
        headers={"X-API-Key": api.world.keys[0]},
        json={"expected_user_id": str(api.world.owner), "decision": "approve"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "claimed"


async def _capture_claim_state(page, artifacts, state, title):
    """Retain safe geometry and normalized image pairs for the synthetic claim flow."""
    await page.get_by_role("heading", level=1, name=title, exact=True).wait_for()
    geometry_script = """() => {
        const headings = document.querySelectorAll('main h1');
        const headers = document.querySelectorAll('header[data-agent-header]');
        const rect = element => element ? {
            x: element.getBoundingClientRect().x, y: element.getBoundingClientRect().y,
            width: element.getBoundingClientRect().width,
            height: element.getBoundingClientRect().height
        } : null;
        return {scrollX, scrollY, width: innerWidth, height: innerHeight,
            dpr: devicePixelRatio, fonts: document.fonts.status,
            h1_count: headings.length, header_count: headers.length,
            h1: rect(headings[0]), header: rect(headers[0])};
    }"""
    geometry = {"before": await page.evaluate(geometry_script)}
    try:
        async with asyncio.timeout(15):
            await page.evaluate(
                "async () => { await document.fonts.ready; scrollTo(0, 0); "
                "await new Promise(requestAnimationFrame); "
                "await new Promise(requestAnimationFrame); }"
            )
        geometry["normalized"] = await page.evaluate(geometry_script)
        await page.screenshot(path=str(artifacts / f"browser-{state}-viewport.png"))
        geometry["after_viewport"] = await page.evaluate(geometry_script)
        await page.screenshot(path=str(artifacts / f"browser-{state}.png"), full_page=True)
        geometry["after_full_page"] = await page.evaluate(geometry_script)
    finally:
        (artifacts / f"browser-{state}-geometry.json").write_text(json.dumps(geometry, indent=2))
    for phase in ("normalized", "after_viewport", "after_full_page"):
        sample = geometry[phase]
        assert sample["h1_count"] == 1 and sample["header_count"] == 1, phase
        assert sample["fonts"] == "loaded", phase
        assert sample["scrollX"] == 0 and sample["scrollY"] == 0, phase
        assert sample["width"] == 390 and sample["height"] == 844, phase
        h1, header = sample["h1"], sample["header"]
        assert h1["width"] > 0 and h1["height"] > 0, phase
        assert 0 <= h1["x"] and h1["x"] + h1["width"] <= sample["width"], phase
        assert 0 <= h1["y"] and h1["y"] + h1["height"] <= sample["height"], phase
        overlaps = (
            h1["x"] < header["x"] + header["width"]
            and header["x"] < h1["x"] + h1["width"]
            and h1["y"] < header["y"] + header["height"]
            and header["y"] < h1["y"] + h1["height"]
        )
        assert not overlaps, f"{phase}: H1 intersects header"


async def test_browser_owner_claim_requires_agent_and_populates_workspace(
    claim_browser_api, tmp_path
):
    """Catch mismatched UI/API contracts while proving human-only requests grant no ownership."""
    assert playwright is not None
    api, records, errors = claim_browser_api, [], []
    artifacts = Path(os.environ.get("CLAIM_BROWSER_ARTIFACTS", str(tmp_path))) / uuid4().hex
    artifacts.mkdir(parents=True, exist_ok=True)
    agent_id = str(api.world.agents[0])
    token = create_access_token(str(api.world.owner))
    async with playwright.async_playwright() as driver:
        browser = await driver.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport=CLAIM_VIEWPORT,
            record_video_dir=str(artifacts / "video"),
            record_video_size=CLAIM_VIEWPORT,
        )
        try:
            await context.route("**/*", lambda route: _proxy_api(route, api, records))
            page = await context.new_page()
            page.on("pageerror", lambda error: errors.append(type(error).__name__))
            page.on(
                "console",
                lambda message: errors.append("console.error") if message.type == "error" else None,
            )
            await page.goto(FRONTEND + f"/agents/{agent_id}/claim")
            await page.get_by_role("link", name="Sign in to continue").wait_for()
            assert await _owner_id(api.world) is None
            assert not any(row["method"] == "POST" for row in records)
            await page.evaluate("token => localStorage.setItem('access_token', token)", token)
            await page.reload()
            create = page.get_by_role("button", name="Request ownership")
            await create.wait_for()
            async with page.expect_response(
                lambda response: (
                    response.url.endswith("/users/me/agent-claims")
                    and response.request.method == "POST"
                )
            ) as created:
                await create.click()
            claim = await (await created.value).json()
            assert claim["status"] == "pending"
            assert claim["agent_id"] == agent_id and claim["user_id"] == str(api.world.owner)
            await page.get_by_role("textbox").wait_for()
            assert await _owner_id(api.world) is None
            await _capture_claim_state(page, artifacts, "pending", "Link your agent")
            await _approve_from_agent(api, claim)
            await page.get_by_role("button", name="Check status").click()
            await page.get_by_role("link", name="Open workspace").wait_for()
            assert await _owner_id(api.world) == api.world.owner
            await _capture_claim_state(page, artifacts, "claimed", "Link your agent")
            await page.get_by_role("link", name="Open workspace").click()
            await page.wait_for_url("**/workspace")
            await page.get_by_text("Browser-200", exact=True).first.wait_for()
            await _capture_claim_state(page, artifacts, "workspace", "Workspace")
            assert api.world.keys[0] not in await page.content()
            assert api.world.keys[0] not in await page.evaluate("JSON.stringify(localStorage)")
            assert records and not errors and all(row["status"] < 400 for row in records)
            assert any(row["path"].endswith("/auth/me") for row in records)
            assert any(row["path"].endswith("/agent-workspace") for row in records)
            (artifacts / "network-summary.json").write_text(json.dumps(records, indent=2))
        finally:
            await context.close()
            await browser.close()
