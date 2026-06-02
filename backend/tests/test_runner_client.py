"""Tests for RunnerFileClient — behaviour-equivalence with the inline httpx blocks
it replaced in HostedAgentService.

Covers:
- put_file: 200 happy, RequestError→503, 412→StaleVersionError, non-2xx→503
- get_file: 200 happy, RequestError→503, 404→404, non-200→503
- list_files: 200 happy (include_hidden param forwarded), Exception→503
- delete_file: 200/204 happy, 404→404, Exception→503, non-2xx→503
- post_import: 200 happy (count returned), HTTPStatusError→503
- get_download: 200 happy (bytes returned), 404→404, Exception→503, non-200→503
- soft_put_file: 200 returns sha, Exception returns ''
- soft_list_files: 200 returns files, RequestError returns []
- soft_post_import: 200 returns count, RequestError returns -1
- soft_get_status: 200 returns dict, non-200 returns {}, Exception returns None
- soft_get_history: 200 returns list, Exception returns []
- headers(): key present / absent
- timeout passed to AsyncClient (asserted via mock kwargs)
- download_files_archive service method: delegates to _rc, returns bytes
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import HTTPException

from app.repositories.hosted_agent_repo import StaleVersionError
from app.services.hosted_agent_service import HostedAgentService
from app.services.runner_client import RunnerFileClient


# ── helpers ──────────────────────────────────────────────────────────────────


def make_client(url: str = "http://runner.test", key: str = "secret") -> RunnerFileClient:
    return RunnerFileClient(runner_url=url, runner_key=key)


def _mock_response(status: int, json_body: object = None, content: bytes | None = None) -> httpx.Response:
    if content is not None:
        return httpx.Response(status, content=content)
    if json_body is not None:
        return httpx.Response(status, json=json_body)
    return httpx.Response(status, content=b"")


def _patch_client(method: str, response: httpx.Response | Exception):
    """Return a context-manager patch for httpx.AsyncClient.<method>."""

    async def _fake(self, *args, **kwargs):
        if isinstance(response, Exception):
            raise response
        return response

    async def _aenter(self):
        return self

    async def _aexit(self, *args):
        return False

    return (
        patch.object(httpx.AsyncClient, method, _fake),
        patch.object(httpx.AsyncClient, "__aenter__", _aenter),
        patch.object(httpx.AsyncClient, "__aexit__", _aexit),
    )


# ── headers ──────────────────────────────────────────────────────────────────


def test_headers_with_key():
    rc = make_client(key="my-key")
    assert rc.headers() == {"X-Runner-Key": "my-key"}


def test_headers_without_key():
    rc = make_client(key="")
    assert rc.headers() == {}


def test_configured():
    assert make_client(url="http://x").configured is True
    assert RunnerFileClient(runner_url=None, runner_key=None).configured is False


# ── put_file ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_put_file_happy():
    rc = make_client()
    resp = _mock_response(200, json_body={"version": "abc123"})
    p1, p2, p3 = _patch_client("put", resp)
    with p1, p2, p3:
        data = await rc.put_file("agent1", "AGENT.md", "hello")
    assert data["version"] == "abc123"


@pytest.mark.asyncio
async def test_put_file_201():
    rc = make_client()
    resp = _mock_response(201, json_body={"version": "new123"})
    p1, p2, p3 = _patch_client("put", resp)
    with p1, p2, p3:
        data = await rc.put_file("agent1", "x.md", "content")
    assert data["version"] == "new123"


@pytest.mark.asyncio
async def test_put_file_request_error_raises_503():
    rc = make_client()
    exc = httpx.RequestError("connection refused", request=MagicMock())
    p1, p2, p3 = _patch_client("put", exc)
    with p1, p2, p3:
        with pytest.raises(HTTPException) as exc_info:
            await rc.put_file("agent1", "x.md", "content")
    assert exc_info.value.status_code == 503


@pytest.mark.asyncio
async def test_put_file_412_raises_stale_version_error():
    rc = make_client()
    resp = _mock_response(
        412,
        json_body={
            "detail": {
                "message": "Precondition Failed",
                "current_version": "deadbeef",
                "current_content": "old content",
            }
        },
    )
    p1, p2, p3 = _patch_client("put", resp)
    with p1, p2, p3:
        with pytest.raises(StaleVersionError) as exc_info:
            await rc.put_file("agent1", "x.md", "content", if_match="wrongsha")
    assert exc_info.value.current_version == "deadbeef"
    assert exc_info.value.current_content == "old content"


@pytest.mark.asyncio
async def test_put_file_non_2xx_raises_503():
    rc = make_client()
    resp = _mock_response(500)
    p1, p2, p3 = _patch_client("put", resp)
    with p1, p2, p3:
        with pytest.raises(HTTPException) as exc_info:
            await rc.put_file("agent1", "x.md", "content")
    assert exc_info.value.status_code == 503


@pytest.mark.asyncio
async def test_put_file_if_match_forwarded(monkeypatch):
    rc = make_client()
    captured: dict = {}

    async def _fake_put(self, url, json=None, headers=None, **kwargs):
        captured["headers"] = headers or {}
        return _mock_response(200, json_body={"version": "v1"})

    async def _aenter(self):
        return self

    async def _aexit(self, *args):
        return False

    monkeypatch.setattr(httpx.AsyncClient, "put", _fake_put)
    monkeypatch.setattr(httpx.AsyncClient, "__aenter__", _aenter)
    monkeypatch.setattr(httpx.AsyncClient, "__aexit__", _aexit)

    await rc.put_file("agent1", "x.md", "content", if_match="mysha")
    assert captured["headers"].get("If-Match") == "mysha"


# ── get_file ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_file_happy():
    rc = make_client()
    resp = _mock_response(200, json_body={"file_path": "x.md", "content": "hello", "version": "v1"})
    p1, p2, p3 = _patch_client("get", resp)
    with p1, p2, p3:
        data = await rc.get_file("agent1", "x.md")
    assert data["content"] == "hello"


@pytest.mark.asyncio
async def test_get_file_404():
    rc = make_client()
    resp = _mock_response(404)
    p1, p2, p3 = _patch_client("get", resp)
    with p1, p2, p3:
        with pytest.raises(HTTPException) as exc_info:
            await rc.get_file("agent1", "missing.md")
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_get_file_exception_raises_503():
    rc = make_client()
    p1, p2, p3 = _patch_client("get", Exception("timeout"))
    with p1, p2, p3:
        with pytest.raises(HTTPException) as exc_info:
            await rc.get_file("agent1", "x.md")
    assert exc_info.value.status_code == 503


@pytest.mark.asyncio
async def test_get_file_non_200_raises_503():
    rc = make_client()
    resp = _mock_response(502)
    p1, p2, p3 = _patch_client("get", resp)
    with p1, p2, p3:
        with pytest.raises(HTTPException) as exc_info:
            await rc.get_file("agent1", "x.md")
    assert exc_info.value.status_code == 503


# ── list_files ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_files_happy(monkeypatch):
    rc = make_client()
    captured: dict = {}

    async def _fake_get(self, url, headers=None, params=None, **kwargs):
        captured["params"] = params or {}
        return _mock_response(200, json_body={"files": [{"file_path": "a.md"}]})

    async def _aenter(self):
        return self

    async def _aexit(self, *args):
        return False

    monkeypatch.setattr(httpx.AsyncClient, "get", _fake_get)
    monkeypatch.setattr(httpx.AsyncClient, "__aenter__", _aenter)
    monkeypatch.setattr(httpx.AsyncClient, "__aexit__", _aexit)

    files = await rc.list_files("agent1")
    assert files == [{"file_path": "a.md"}]
    assert captured["params"] == {}


@pytest.mark.asyncio
async def test_list_files_include_hidden_forwarded(monkeypatch):
    rc = make_client()
    captured: dict = {}

    async def _fake_get(self, url, headers=None, params=None, **kwargs):
        captured["params"] = params or {}
        return _mock_response(200, json_body={"files": []})

    async def _aenter(self):
        return self

    async def _aexit(self, *args):
        return False

    monkeypatch.setattr(httpx.AsyncClient, "get", _fake_get)
    monkeypatch.setattr(httpx.AsyncClient, "__aenter__", _aenter)
    monkeypatch.setattr(httpx.AsyncClient, "__aexit__", _aexit)

    await rc.list_files("agent1", include_hidden=True)
    assert captured["params"].get("include_hidden") == "true"


@pytest.mark.asyncio
async def test_list_files_exception_raises_503():
    rc = make_client()
    p1, p2, p3 = _patch_client("get", Exception("net error"))
    with p1, p2, p3:
        with pytest.raises(HTTPException) as exc_info:
            await rc.list_files("agent1")
    assert exc_info.value.status_code == 503


@pytest.mark.asyncio
async def test_list_files_non_200_raises_503():
    rc = make_client()
    resp = _mock_response(503)
    p1, p2, p3 = _patch_client("get", resp)
    with p1, p2, p3:
        with pytest.raises(HTTPException) as exc_info:
            await rc.list_files("agent1")
    assert exc_info.value.status_code == 503


# ── delete_file ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_delete_file_200():
    rc = make_client()
    resp = _mock_response(200)
    p1, p2, p3 = _patch_client("delete", resp)
    with p1, p2, p3:
        await rc.delete_file("agent1", "x.md")  # no exception = success


@pytest.mark.asyncio
async def test_delete_file_204():
    rc = make_client()
    resp = _mock_response(204)
    p1, p2, p3 = _patch_client("delete", resp)
    with p1, p2, p3:
        await rc.delete_file("agent1", "x.md")


@pytest.mark.asyncio
async def test_delete_file_404():
    rc = make_client()
    resp = _mock_response(404)
    p1, p2, p3 = _patch_client("delete", resp)
    with p1, p2, p3:
        with pytest.raises(HTTPException) as exc_info:
            await rc.delete_file("agent1", "missing.md")
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_delete_file_exception_raises_503():
    rc = make_client()
    p1, p2, p3 = _patch_client("delete", Exception("timeout"))
    with p1, p2, p3:
        with pytest.raises(HTTPException) as exc_info:
            await rc.delete_file("agent1", "x.md")
    assert exc_info.value.status_code == 503


@pytest.mark.asyncio
async def test_delete_file_non_2xx_raises_503():
    rc = make_client()
    resp = _mock_response(500)
    p1, p2, p3 = _patch_client("delete", resp)
    with p1, p2, p3:
        with pytest.raises(HTTPException) as exc_info:
            await rc.delete_file("agent1", "x.md")
    assert exc_info.value.status_code == 503


# ── post_import ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_post_import_happy():
    rc = make_client()
    resp = _mock_response(200, json_body={"imported": 3})
    p1, p2, p3 = _patch_client("post", resp)
    with p1, p2, p3:
        data = await rc.post_import("agent1", [{"file_path": "a.md"}, {"file_path": "b.md"}, {"file_path": "c.md"}])
    assert data["imported"] == 3


@pytest.mark.asyncio
async def test_post_import_request_error_raises_503():
    rc = make_client()
    p1, p2, p3 = _patch_client("post", httpx.RequestError("conn", request=MagicMock()))
    with p1, p2, p3:
        with pytest.raises(HTTPException) as exc_info:
            await rc.post_import("agent1", [])
    assert exc_info.value.status_code == 503


# ── get_download ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_download_happy():
    rc = make_client()
    zip_bytes = b"PK\x03\x04fake_zip"
    resp = _mock_response(200, content=zip_bytes)
    p1, p2, p3 = _patch_client("get", resp)
    with p1, p2, p3:
        result = await rc.get_download("agent1")
    assert result == zip_bytes


@pytest.mark.asyncio
async def test_get_download_404():
    rc = make_client()
    resp = _mock_response(404)
    p1, p2, p3 = _patch_client("get", resp)
    with p1, p2, p3:
        with pytest.raises(HTTPException) as exc_info:
            await rc.get_download("agent1")
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_get_download_exception_raises_503():
    rc = make_client()
    p1, p2, p3 = _patch_client("get", Exception("timeout"))
    with p1, p2, p3:
        with pytest.raises(HTTPException) as exc_info:
            await rc.get_download("agent1")
    assert exc_info.value.status_code == 503


@pytest.mark.asyncio
async def test_get_download_non_200_raises_503():
    rc = make_client()
    resp = _mock_response(500)
    p1, p2, p3 = _patch_client("get", resp)
    with p1, p2, p3:
        with pytest.raises(HTTPException) as exc_info:
            await rc.get_download("agent1")
    assert exc_info.value.status_code == 503


# ── soft_put_file ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_soft_put_file_returns_sha():
    rc = make_client()
    resp = _mock_response(200, json_body={"version": "sha123"})
    p1, p2, p3 = _patch_client("put", resp)
    with p1, p2, p3:
        result = await rc.soft_put_file("agent1", "x.md", "content")
    assert result == "sha123"


@pytest.mark.asyncio
async def test_soft_put_file_exception_returns_empty():
    rc = make_client()
    p1, p2, p3 = _patch_client("put", Exception("net error"))
    with p1, p2, p3:
        result = await rc.soft_put_file("agent1", "x.md", "content")
    assert result == ""


@pytest.mark.asyncio
async def test_soft_put_file_non_2xx_returns_empty():
    rc = make_client()
    resp = _mock_response(503)
    p1, p2, p3 = _patch_client("put", resp)
    with p1, p2, p3:
        result = await rc.soft_put_file("agent1", "x.md", "content")
    assert result == ""


@pytest.mark.asyncio
async def test_soft_put_file_no_url_returns_empty():
    rc = RunnerFileClient(runner_url=None, runner_key=None)
    result = await rc.soft_put_file("agent1", "x.md", "content")
    assert result == ""


# ── soft_list_files ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_soft_list_files_happy():
    rc = make_client()
    resp = _mock_response(200, json_body={"files": [{"file_path": "a.md"}]})
    p1, p2, p3 = _patch_client("get", resp)
    with p1, p2, p3:
        files = await rc.soft_list_files("agent1")
    assert files == [{"file_path": "a.md"}]


@pytest.mark.asyncio
async def test_soft_list_files_request_error_returns_empty():
    rc = make_client()
    p1, p2, p3 = _patch_client("get", httpx.RequestError("conn", request=MagicMock()))
    with p1, p2, p3:
        files = await rc.soft_list_files("agent1")
    assert files == []


@pytest.mark.asyncio
async def test_soft_list_files_non_200_returns_empty():
    rc = make_client()
    resp = _mock_response(503)
    p1, p2, p3 = _patch_client("get", resp)
    with p1, p2, p3:
        files = await rc.soft_list_files("agent1")
    assert files == []


# ── soft_post_import ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_soft_post_import_happy():
    rc = make_client()
    resp = _mock_response(200, json_body={"imported": 2})
    p1, p2, p3 = _patch_client("post", resp)
    with p1, p2, p3:
        count = await rc.soft_post_import("agent1", [{"file_path": "a.md"}, {"file_path": "b.md"}])
    assert count == 2


@pytest.mark.asyncio
async def test_soft_post_import_request_error_returns_minus_one():
    rc = make_client()
    p1, p2, p3 = _patch_client("post", httpx.RequestError("conn", request=MagicMock()))
    with p1, p2, p3:
        count = await rc.soft_post_import("agent1", [])
    assert count == -1


@pytest.mark.asyncio
async def test_soft_post_import_non_200_returns_minus_one():
    rc = make_client()
    resp = _mock_response(503)
    p1, p2, p3 = _patch_client("post", resp)
    with p1, p2, p3:
        count = await rc.soft_post_import("agent1", [])
    assert count == -1


# ── soft_get_status ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_soft_get_status_running():
    rc = make_client()
    resp = _mock_response(200, json_body={"status": "running"})
    p1, p2, p3 = _patch_client("get", resp)
    with p1, p2, p3:
        data = await rc.soft_get_status("agent1")
    assert data == {"status": "running"}


@pytest.mark.asyncio
async def test_soft_get_status_non_200_returns_empty_dict():
    """Non-200 (e.g. 404 = agent not found on runner) → {} → caller triggers correction."""
    rc = make_client()
    resp = _mock_response(404)
    p1, p2, p3 = _patch_client("get", resp)
    with p1, p2, p3:
        data = await rc.soft_get_status("agent1")
    assert data == {}


@pytest.mark.asyncio
async def test_soft_get_status_network_error_returns_none():
    """Network error → None → caller does NOT change DB status."""
    rc = make_client()
    p1, p2, p3 = _patch_client("get", Exception("conn reset"))
    with p1, p2, p3:
        data = await rc.soft_get_status("agent1")
    assert data is None


@pytest.mark.asyncio
async def test_soft_get_status_timeout_3(monkeypatch):
    """status probe uses timeout=3."""
    rc = make_client()
    captured: dict = {}

    original_init = httpx.AsyncClient.__init__

    def _fake_init(self, *, timeout=None, **kwargs):
        captured["timeout"] = timeout
        original_init(self, timeout=timeout, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", _fake_init)
    resp = _mock_response(200, json_body={"status": "running"})
    p1, p2, p3 = _patch_client("get", resp)
    with p1, p2, p3:
        await rc.soft_get_status("agent1", timeout=3)
    assert captured["timeout"] == 3


# ── soft_get_history ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_soft_get_history_happy():
    rc = make_client()
    resp = _mock_response(200, json_body={"history": [{"role": "user", "content": "hi"}]})
    p1, p2, p3 = _patch_client("get", resp)
    with p1, p2, p3:
        history = await rc.soft_get_history("agent1")
    assert history == [{"role": "user", "content": "hi"}]


@pytest.mark.asyncio
async def test_soft_get_history_exception_returns_empty():
    rc = make_client()
    p1, p2, p3 = _patch_client("get", Exception("timeout"))
    with p1, p2, p3:
        history = await rc.soft_get_history("agent1")
    assert history == []


# ── download_files_archive service method ────────────────────────────────────


@pytest.mark.asyncio
async def test_download_files_archive_service_method():
    """Service.download_files_archive delegates to _rc.get_download and returns bytes."""
    zip_bytes = b"PK\x03\x04fake_zip_data"
    svc = HostedAgentService(
        repo=MagicMock(),
        agent_service=MagicMock(),
        openrouter=MagicMock(),
        openviking=MagicMock(),
    )
    svc.runner_url = "http://runner.test"
    svc.get_hosted_agent = AsyncMock(return_value={"id": "abc", "status": "stopped"})
    svc._rc = MagicMock()
    svc._rc.get_download = AsyncMock(return_value=zip_bytes)

    result = await svc.download_files_archive("abc", "user1")
    assert result == zip_bytes
    svc._rc.get_download.assert_awaited_once_with("abc", timeout=60, include_hidden=False)


@pytest.mark.asyncio
async def test_download_files_archive_include_hidden():
    zip_bytes = b"PK\x03\x04fake"
    svc = HostedAgentService(
        repo=MagicMock(),
        agent_service=MagicMock(),
        openrouter=MagicMock(),
        openviking=MagicMock(),
    )
    svc.runner_url = "http://runner.test"
    svc.get_hosted_agent = AsyncMock(return_value={"id": "abc", "status": "stopped"})
    svc._rc = MagicMock()
    svc._rc.get_download = AsyncMock(return_value=zip_bytes)

    await svc.download_files_archive("abc", "user1", include_hidden=True)
    svc._rc.get_download.assert_awaited_once_with("abc", timeout=60, include_hidden=True)


@pytest.mark.asyncio
async def test_download_files_archive_no_runner_url():
    svc = HostedAgentService(
        repo=MagicMock(),
        agent_service=MagicMock(),
        openrouter=MagicMock(),
        openviking=MagicMock(),
    )
    svc.runner_url = ""
    svc.get_hosted_agent = AsyncMock(return_value={"id": "abc", "status": "stopped"})

    with pytest.raises(HTTPException) as exc_info:
        await svc.download_files_archive("abc", "user1")
    assert exc_info.value.status_code == 503
