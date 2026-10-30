"""Swagger UI at /docs renders: assets reach the backend, page declares a language."""

from __future__ import annotations

import re
from fnmatch import fnmatch
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app

CADDYFILE = Path(__file__).resolve().parents[2] / "deploy" / "Caddyfile"
ASSET = re.compile(r'(?:href|src)="(/static/[^"]+)"')


@pytest.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def _docs_html(client: AsyncClient) -> str:
    response = await client.get("/docs")
    assert response.status_code == 200
    return response.text


async def test_docs_html_declares_language(client):
    assert '<html lang="en">' in await _docs_html(client)


async def test_docs_favicon_is_not_a_cross_origin_image(client):
    assert "fastapi.tiangolo.com" not in await _docs_html(client)


async def test_docs_assets_have_script_and_style_content_types(client):
    assets = ASSET.findall(await _docs_html(client))
    assert len(assets) == 2
    types = {}
    for path in assets:
        response = await client.get(path)
        assert response.status_code == 200
        types[path] = response.headers["content-type"].split(";")[0]
    assert {t for p, t in types.items() if p.endswith(".css")} == {"text/css"}
    js = {t for p, t in types.items() if p.endswith(".js")}
    assert js <= {"text/javascript", "application/javascript"}


async def test_caddy_sends_every_docs_asset_to_the_backend(client):
    """Without this the frontend answers /static/* with its own 404 and /docs renders blank."""
    site = CADDYFILE.read_text().split("(agentspore_site) {", 1)[1]
    routes = re.findall(r"handle (\S+) \{\s*reverse_proxy (\S+)\s*\}", site)
    for path in ASSET.findall(await _docs_html(client)):
        upstreams = [up for matcher, up in routes if fnmatch(path, matcher)]
        assert upstreams[:1] == ["backend:8000"], f"{path} is not routed to backend:8000"
