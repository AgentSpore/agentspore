"""Exercise webhook authentication through real HTTP routes on synthetic inputs."""

from collections.abc import AsyncIterator
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1 import webhooks


@pytest.fixture
def webhook_db() -> AsyncMock:
    return AsyncMock(spec=AsyncSession)


@pytest.fixture
async def webhook_client(
    webhook_db: AsyncMock, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[AsyncClient]:
    monkeypatch.setattr(webhooks, "GITLAB_WEBHOOK_SECRET", "synthetic-gitlab-secret")
    monkeypatch.setattr(webhooks, "GITHUB_WEBHOOK_SECRET", "synthetic-github-secret")
    app = FastAPI()
    app.include_router(webhooks.router, prefix="/api/v1")

    async def request_db() -> AsyncIterator[AsyncMock]:
        yield webhook_db

    app.dependency_overrides[webhooks.get_db] = request_db
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test"
    ) as client:
        yield client


@pytest.mark.parametrize("payload", [b"not-json", b"{}"], ids=["invalid-json", "valid-json"])
@pytest.mark.parametrize(
    "token",
    [None, b"", b"wrong", b"synthetic-gitlab-secret"],
    ids=["missing", "empty", "wrong", "configured-value"],
)
async def test_unconfigured_gitlab_rejects_before_json(
    webhook_client: AsyncClient,
    webhook_db: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
    token: bytes | None,
    payload: bytes,
) -> None:
    monkeypatch.setattr(webhooks, "GITLAB_WEBHOOK_SECRET", "")
    headers = [(b"X-Gitlab-Event", b"Push Hook")]
    if token is not None:
        headers.append((b"X-Gitlab-Token", token))
    response = await webhook_client.post(
        "/api/v1/webhooks/gitlab", content=payload, headers=headers
    )
    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid GitLab webhook token"}
    assert webhook_db.mock_calls == []


@pytest.mark.parametrize("payload", [b"not-json", b"{}"], ids=["invalid-json", "valid-json"])
@pytest.mark.parametrize(
    "token",
    [None, b"", b"wrong", b"synthetic-gitlab-secret-extra", b"\xff"],
    ids=["missing", "empty", "wrong", "prefix", "non_ascii"],
)
async def test_configured_gitlab_rejects_invalid_token(
    webhook_client: AsyncClient, webhook_db: AsyncMock, token: bytes | None, payload: bytes
) -> None:
    headers = [(b"X-Gitlab-Event", b"Push Hook")]
    if token is not None:
        headers.append((b"X-Gitlab-Token", token))
    response = await webhook_client.post(
        "/api/v1/webhooks/gitlab", content=payload, headers=headers
    )
    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid GitLab webhook token"}
    assert webhook_db.mock_calls == []


async def test_matching_gitlab_token_reaches_real_service(
    webhook_client: AsyncClient, webhook_db: AsyncMock
) -> None:
    response = await webhook_client.post(
        "/api/v1/webhooks/gitlab",
        json={},
        headers={"X-Gitlab-Event": "Push Hook", "X-Gitlab-Token": "synthetic-gitlab-secret"},
    )
    assert response.status_code == 200
    assert response.json() == {"status": "ignored", "reason": "no repo"}
    webhook_db.commit.assert_awaited_once_with()
    webhook_db.execute.assert_not_awaited()


async def test_matching_gitlab_token_rejects_invalid_json(
    webhook_client: AsyncClient, webhook_db: AsyncMock
) -> None:
    response = await webhook_client.post(
        "/api/v1/webhooks/gitlab",
        content=b"not-json",
        headers={"X-Gitlab-Event": "Push Hook", "X-Gitlab-Token": "synthetic-gitlab-secret"},
    )
    assert response.status_code == 400
    assert response.json() == {"detail": "Invalid JSON payload"}
    assert webhook_db.mock_calls == []


@pytest.mark.parametrize(
    "token", [None, "synthetic-gitlab-secret"], ids=["missing-token", "matching-token"]
)
async def test_gitlab_auth_precedes_missing_event(
    webhook_client: AsyncClient, webhook_db: AsyncMock, token: str | None
) -> None:
    headers = {"X-Gitlab-Token": token} if token is not None else {}
    response = await webhook_client.post("/api/v1/webhooks/gitlab", headers=headers)
    assert response.status_code == (200 if token is not None else 401)
    assert webhook_db.mock_calls == []


async def test_signing_only_gitlab_request_has_no_legacy_fallback(
    webhook_client: AsyncClient, webhook_db: AsyncMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(webhooks, "GITLAB_WEBHOOK_SECRET", "")
    response = await webhook_client.post(
        "/api/v1/webhooks/gitlab",
        json={},
        headers={
            "webhook-id": "synthetic-id",
            "webhook-timestamp": "1",
            "webhook-signature": "v1,c3ludGhldGlj",
            "X-Gitlab-Event": "Push Hook",
        },
    )
    assert response.status_code == 401
    assert webhook_db.mock_calls == []


@pytest.mark.parametrize("signature", [None, "", "sha256=wrong", "sha1=wrong"])
async def test_github_rejects_invalid_signature(
    webhook_client: AsyncClient, webhook_db: AsyncMock, signature: str | None
) -> None:
    headers = {"X-Hub-Signature-256": signature} if signature is not None else {}
    response = await webhook_client.post("/api/v1/webhooks/github", content=b"{}", headers=headers)
    assert response.status_code == 401
    assert webhook_db.mock_calls == []


@pytest.mark.parametrize("configured", [False, True], ids=["unconfigured", "configured"])
async def test_github_known_signature_requires_configured_secret(
    webhook_client: AsyncClient,
    webhook_db: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
    configured: bool,
) -> None:
    if not configured:
        monkeypatch.setattr(webhooks, "GITHUB_WEBHOOK_SECRET", "")
    # Independent fixture: printf '{}' | openssl dgst -sha256 -hmac synthetic-github-secret
    signature = "sha256=a9e4db1a083aad99f45a0db5157a9849ece2432c1f8a6e2d37b5d7bd0af1d4c8"
    response = await webhook_client.post(
        "/api/v1/webhooks/github",
        content=b"{}",
        headers={"X-Hub-Signature-256": signature, "X-Github-Event": "push"},
    )
    assert response.status_code == (200 if configured else 401)
    if configured:
        assert response.json() == {"status": "ignored", "reason": "no repo"}
        webhook_db.commit.assert_awaited_once_with()
    else:
        assert webhook_db.mock_calls == []
