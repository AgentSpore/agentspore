"""ReDoc at /redoc renders: page served, bundle same-origin, Caddy routes both to backend."""

from __future__ import annotations

import re
from fnmatch import fnmatch
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app

CADDYFILE = Path(__file__).resolve().parents[2] / "deploy" / "Caddyfile"
SCRIPT = re.compile(r'<script src="([^"]+)"')


@pytest.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def test_redoc_serves_html_with_language(client):
    response = await client.get("/redoc")
    assert response.status_code == 200
    assert '<html lang="en">' in response.text


async def test_redoc_search_is_disabled_because_csp_blocks_its_worker(client):
    """Search runs in a blob: web worker; the CSP has no worker-src, so it would return 0 hits."""
    assert "<redoc disable-search " in (await client.get("/redoc")).text


async def test_redoc_loads_only_same_origin_assets(client):
    """Caddy CSP is script-src 'self'; a CDN bundle or Google Fonts link renders a blank page."""
    html = (await client.get("/redoc")).text
    assert "cdn.jsdelivr.net" not in html
    assert "fonts.googleapis.com" not in html
    bundles = SCRIPT.findall(html)
    assert bundles == ["/static/redoc.standalone.js"]
    asset = await client.get(bundles[0])
    assert asset.status_code == 200
    content_type = asset.headers["content-type"].split(";")[0]
    assert content_type in {"text/javascript", "application/javascript"}


async def test_caddy_sends_redoc_page_and_bundle_to_the_backend(client):
    site = CADDYFILE.read_text().split("(agentspore_site) {", 1)[1]
    routes = re.findall(r"handle (\S+) \{\s*reverse_proxy (\S+)\s*\}", site)
    for path in ["/redoc", *SCRIPT.findall((await client.get("/redoc")).text)]:
        upstreams = [up for matcher, up in routes if fnmatch(path, matcher)]
        assert upstreams[:1] == ["backend:8000"], f"{path} is not routed to backend:8000"
