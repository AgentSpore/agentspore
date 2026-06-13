"""Unit tests for the /health endpoint Docker-daemon liveness probe.

Covers:
  - docker_daemon == "ok" when the daemon answers a ping
  - docker_daemon starts with "error" when docker.from_env()/ping() raises
  - HTTP 200 in both cases (the value is the field, not the status code)

Scope: unit (mocked docker.from_env, no real daemon, no testcontainers).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
import routes.health as health_mod
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(health_mod.router)
    return TestClient(app, raise_server_exceptions=False)


def test_health_docker_daemon_ok(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """A daemon that answers ping → docker_daemon == "ok", status 200."""
    fake_client = MagicMock()
    fake_client.ping.return_value = True
    monkeypatch.setattr(health_mod.docker, "from_env", lambda *a, **k: fake_client)

    resp = client.get("/health")

    assert resp.status_code == 200
    assert resp.json()["docker_daemon"] == "ok"


def test_health_docker_daemon_error(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """A wedged daemon (from_env raises) → docker_daemon error field, still 200."""
    def _boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("cannot connect to daemon socket")

    monkeypatch.setattr(health_mod.docker, "from_env", _boom)

    resp = client.get("/health")

    assert resp.status_code == 200
    assert resp.json()["docker_daemon"].startswith("error")
