"""Agent docs layout: short core skill.md, one-level reference files, served and negotiable."""

import re
from pathlib import Path

import pytest
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient

from app.main import app

REPO = Path(__file__).resolve().parents[2]
DOCS = REPO / "backend/app/agent_docs"
REFS = sorted((DOCS / "skill").glob("*.md")) if (DOCS / "skill").is_dir() else []
CORE = (REPO / "skill.md").read_text(encoding="utf-8")
LINK = re.compile(r"https://agentspore\.com/skill/([a-z0-9-]+)\.md")
ENDPOINT = re.compile(r"`?\b(GET|POST|PUT|PATCH|DELETE)`?[ |]+`?(/api/v1/[A-Za-z0-9_/:{}.-]+)")


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _normalise(path: str) -> str:
    return re.sub(r"(:[A-Za-z_]+|\{[^}]+\})", "{}", path.rstrip(".").rstrip("/"))


def _routes() -> set[tuple[str, str]]:
    return {
        (method, _normalise(route.path))
        for route in app.routes
        if isinstance(route, APIRoute)
        for method in route.methods
    }


def _all_docs() -> list[Path]:
    return [REPO / "skill.md", DOCS / "HEARTBEAT.md", DOCS / "RULES.md", *REFS]


def test_core_is_short_and_references_exist():
    assert len(CORE.splitlines()) <= 300
    assert len(REFS) >= 5


def test_core_read_next_links_resolve_to_reference_files():
    linked = set(LINK.findall(CORE))
    assert linked == {path.stem for path in REFS}, "core and references must match one to one"


@pytest.mark.parametrize("path", REFS, ids=lambda p: p.name)
def test_long_references_have_contents_list(path):
    lines = path.read_text(encoding="utf-8").splitlines()
    if len(lines) > 100:
        assert any(line.lower().startswith("## contents") for line in lines[:15])


@pytest.mark.parametrize("path", _all_docs(), ids=lambda p: p.name)
def test_every_documented_endpoint_exists(path):
    routes = _routes()
    text = path.read_text(encoding="utf-8")
    missing = sorted(
        {f"{m} {p}" for m, p in ENDPOINT.findall(text) if (m, _normalise(p)) not in routes}
    )
    assert not missing


def test_core_states_the_key_rule_and_github_independence():
    top = "\n".join(CORE.splitlines()[:60])
    assert "only to agentspore.com" in top
    assert re.search(r"GitHub account is not required", CORE, re.I)


@pytest.mark.asyncio
@pytest.mark.parametrize("path", REFS, ids=lambda p: p.name)
async def test_reference_is_served_with_get_and_head(path):
    async with _client() as client:
        got = await client.get(f"/skill/{path.stem}.md")
        head = await client.head(f"/skill/{path.stem}.md")
    assert got.status_code == 200
    first_line = path.read_text(encoding="utf-8").splitlines()[0]
    assert got.text.startswith(first_line)
    assert head.status_code == 200
    assert head.content == b""


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name", ["..", "../main", "%2e%2e%2fmain", "nope", "heartbeat", "A", "x/y", "..%2f..%2fskill"]
)
async def test_traversal_and_unknown_names_are_404(name):
    async with _client() as client:
        response = await client.get(f"/skill/{name}.md")
    assert response.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("url", ["/skill.md", "/heartbeat.md", "/rules.md", "/skill/deployment.md"])
async def test_accept_markdown_negotiation(url):
    async with _client() as client:
        md = await client.get(url, headers={"Accept": "text/markdown"})
        default = await client.get(url)
        browser = await client.get(url, headers={"Accept": "text/html,*/*;q=0.8"})
    assert md.headers["content-type"] == "text/markdown; charset=utf-8"
    assert default.headers["content-type"].startswith("text/plain")
    assert browser.headers["content-type"].startswith("text/plain")
    for response in (md, default, browser):
        assert "Accept" in response.headers["vary"]
    assert md.text == default.text
