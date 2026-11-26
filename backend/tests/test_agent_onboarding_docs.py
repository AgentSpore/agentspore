"""Agent onboarding: what skill.md promises must match what the backend does."""

import json
import re
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

import app.main as main_module
from app.main import app
from app.schemas.agents import AgentRegisterRequest
from app.schemas.chat import AgentDMReply

REPO = Path(__file__).resolve().parents[2]
SKILL = (REPO / "skill.md").read_text(encoding="utf-8")
CURL = re.compile(r"curl[^\n]*?(https?://\S+)(.*?)(?=\n```)", re.S)
BODY = re.compile(r"-d '(\{.*?\})'\s*$", re.S)
MODELS = {"/agents/register": AgentRegisterRequest, "/chat/dm/reply": AgentDMReply}


def _examples() -> list[tuple[str, dict]]:
    found = []
    for url, rest in CURL.findall(SKILL):
        path = url.split("/api/v1", 1)[-1].split("?")[0]
        body = BODY.search(rest.rstrip())
        if path in MODELS and body:
            found.append((path, json.loads(body.group(1))))
    return found


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def test_skill_md_has_register_and_dm_examples():
    assert {path for path, _ in _examples()} == set(MODELS)


@pytest.mark.parametrize(("path", "body"), _examples())
def test_every_skill_md_example_body_validates(path, body):
    model = MODELS[path]
    assert set(body) <= set(model.model_fields), "example uses fields the API does not have"
    model.model_validate(body)


def test_model_name_is_optional_with_derived_default():
    base = {"name": "Agent-One", "model_provider": "anthropic"}
    assert AgentRegisterRequest(**base).model_name == "anthropic"
    assert AgentRegisterRequest(name="Agent-One", model_provider="").model_name == "unknown"
    assert AgentRegisterRequest(**base, model_name="m1").model_name == "m1"


def test_skill_md_documents_register_errors():
    section = SKILL.split("### Step 1:", 1)[1].split("### Step 2:", 1)[0]
    for status in ("400", "409", "422", "429", "503"):
        assert re.search(rf"\b{status}\b", section), status
    assert "suggested_name" in section
    assert re.search(r"422.*list", section, re.S)


def test_skill_md_uses_real_chat_and_single_push_path():
    assert "/chat/dms/" not in SKILL
    paths = {r.path.removeprefix("/api/v1") for r in app.routes if hasattr(r, "path")}
    assert "/chat/dm/reply" in paths
    assert not re.search(r"/projects/[^\s`]*/push\b", SKILL)


def test_a3_limit_text_matches_name_conflict_behaviour():
    flat = " ".join(SKILL.split())
    assert "Duplicate names and later database failures consume" not in flat
    assert "taken name does not consume" in flat


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["heartbeat", "rules"])
async def test_heartbeat_and_rules_serve_real_documents(name):
    async with _client() as client:
        response = await client.get(f"/{name}.md")
    assert response.status_code == 200
    assert response.text.startswith("# AgentSpore — ")
    assert len(response.content) > 1024


@pytest.mark.parametrize("name", ["HEARTBEAT.md", "RULES.md"])
def test_doc_files_live_inside_the_image_copied_tree(name):
    found = main_module._find_doc_file(name)
    assert found is not None
    assert Path(main_module.__file__).parent in found.resolve().parents
    published = (REPO / "docs" / name).read_text(encoding="utf-8")
    assert found.resolve().read_text(encoding="utf-8") == published


@pytest.mark.asyncio
async def test_head_skill_md_is_ok():
    async with _client() as client:
        response = await client.head("/skill.md")
    assert response.status_code == 200
    assert response.content == b""


@pytest.mark.asyncio
async def test_get_register_is_method_not_allowed():
    async with _client() as client:
        response = await client.get("/api/v1/agents/register")
    assert response.status_code == 405
    assert response.headers["allow"] == "POST"


def test_register_openapi_description_is_english():
    operation = app.openapi()["paths"]["/api/v1/agents/register"]["post"]
    text = operation["summary"] + operation.get("description", "")
    assert operation["summary"] != "Register Agent"
    assert not re.search("[А-Яа-яЁё]", text)
    assert {"409", "429", "503"} <= set(operation["responses"])
