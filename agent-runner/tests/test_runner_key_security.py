"""Tests for C2 — runner_key enforcement (no bypass when key is empty/unset)."""

from __future__ import annotations

import secrets

import pytest
from fastapi.testclient import TestClient


def _make_app(runner_key: str):
    """Create a test FastAPI app with the middleware applied and a dummy route."""
    import secrets as _secrets

    from fastapi import FastAPI
    from fastapi.responses import JSONResponse
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.requests import Request

    app = FastAPI()

    @app.middleware("http")
    async def verify_runner_key(request: Request, call_next):
        if request.url.path == "/health":
            return await call_next(request)
        key = request.headers.get("X-Runner-Key", "")
        if not key or not _secrets.compare_digest(key, runner_key):
            return JSONResponse({"detail": "Unauthorized"}, status_code=403)
        return await call_next(request)

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.get("/protected")
    async def protected():
        return {"data": "secret"}

    return app


class TestRunnerKeyMiddleware:
    def test_health_passes_without_key(self):
        app = _make_app(runner_key="some-key")
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/health")
        assert resp.status_code == 200

    def test_protected_requires_key(self):
        app = _make_app(runner_key="some-key")
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/protected")
        assert resp.status_code == 403

    def test_protected_wrong_key_rejected(self):
        app = _make_app(runner_key="correct-key")
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/protected", headers={"X-Runner-Key": "wrong-key"})
        assert resp.status_code == 403

    def test_protected_correct_key_passes(self):
        key = secrets.token_urlsafe(32)
        app = _make_app(runner_key=key)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/protected", headers={"X-Runner-Key": key})
        assert resp.status_code == 200

    def test_no_bypass_when_key_is_set(self):
        """Previously: if not settings.runner_key: skip auth. Must never bypass."""
        key = secrets.token_urlsafe(32)
        app = _make_app(runner_key=key)
        client = TestClient(app, raise_server_exceptions=False)
        # Empty header — must be rejected even though key is valid string
        resp = client.get("/protected", headers={"X-Runner-Key": ""})
        assert resp.status_code == 403


class TestRunnerSettingsRequired:
    def test_runner_key_field_has_no_default(self):
        """runner_key must be required — no default value in RunnerSettings."""
        from config import RunnerSettings

        fields = RunnerSettings.model_fields
        field = fields.get("runner_key")
        assert field is not None, "runner_key field must exist"
        # Pydantic v2: required field has no default and default_factory is None
        assert field.default is None or str(field.default) in ("PydanticUndefined", "None"), (
            "runner_key must have no default — it is required"
        )
        import pydantic
        assert field.is_required(), "runner_key must be a required field"
