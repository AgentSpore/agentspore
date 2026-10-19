"""GET /oauth/providers reports which human-login providers are configured, booleans only."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1 import oauth as oauth_module


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(oauth_module.router, prefix="/api/v1")
    return TestClient(app)


def _configure(monkeypatch, **values: str) -> None:
    for key in (
        "user_github_oauth_client_id",
        "user_github_oauth_client_secret",
        "google_oauth_client_id",
        "google_oauth_client_secret",
    ):
        monkeypatch.setattr(oauth_module.settings, key, values.get(key, ""))


def test_nothing_configured(client, monkeypatch):
    _configure(monkeypatch)
    res = client.get("/api/v1/oauth/providers")
    assert res.status_code == 200
    assert res.json() == {"github": False, "google": False}


@pytest.mark.parametrize(
    "half",
    [
        "user_github_oauth_client_id",
        "user_github_oauth_client_secret",
        "google_oauth_client_id",
        "google_oauth_client_secret",
    ],
)
def test_half_configured_provider_is_off(client, monkeypatch, half):
    _configure(monkeypatch, **{half: "only-one-half"})
    assert client.get("/api/v1/oauth/providers").json() == {"github": False, "google": False}


def test_configured_providers_listed_without_values(client, monkeypatch):
    _configure(
        monkeypatch,
        user_github_oauth_client_id="gh-id",
        user_github_oauth_client_secret="gh-secret",
        google_oauth_client_id="g-id",
        google_oauth_client_secret="g-secret",
    )
    res = client.get("/api/v1/oauth/providers")
    assert res.json() == {"github": True, "google": True}
    for leaked in ("gh-id", "gh-secret", "g-id", "g-secret"):
        assert leaked not in res.text
