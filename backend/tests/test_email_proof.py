"""Email ownership proof cannot be inferred from legacy account verification."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

from httpx import ASGITransport, AsyncClient

from app.api.v1.oauth import _upsert_oauth_user
from app.main import app
from app.models.user import User
from app.services import email_service
from app.services.email_proof_service import EmailProofService


def test_legacy_verification_is_not_current_email_proof():
    user = User(
        is_verified=True,
        email="owner@example.com",
        email_proof_email=None,
        email_proof_at=None,
        email_proof_source=None,
    )
    assert EmailProofService.has_current_proof(user) is False


def test_proof_requires_current_email_and_known_source():
    user = User(
        is_verified=True,
        email="owner@example.com",
        email_proof_email="old@example.com",
        email_proof_at=datetime.now(timezone.utc),
        email_proof_source="email_link",
    )
    assert EmailProofService.has_current_proof(user) is False
    user.email_proof_email = user.email
    assert EmailProofService.has_current_proof(user) is True
    user.email_proof_source = "legacy"
    assert EmailProofService.has_current_proof(user) is False


async def test_oauth_existing_identity_only_proves_matching_email():

    user = User(
        email="owner@example.com",
        is_verified=True,
        email_proof_email=None,
        email_proof_at=None,
        email_proof_source=None,
    )
    db = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = user
    db.execute.return_value = result
    await _upsert_oauth_user(
        db,
        email="else@example.com",
        provider="google",
        oauth_id="synthetic-id",
        name="Owner",
        avatar_url=None,
    )
    assert user.email_proof_at is None
    await _upsert_oauth_user(
        db,
        email="owner@example.com",
        provider="google",
        oauth_id="synthetic-id",
        name="Owner",
        avatar_url=None,
    )
    assert EmailProofService.has_current_proof(user)


async def test_email_proof_routes_require_login():

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        for path in ("request", "confirm"):
            response = await client.post(
                f"/api/v1/auth/email-proof/{path}", json={"code": "x" * 43}
            )
            assert response.status_code in (401, 403)


async def test_email_delivery_logs_exclude_recipient_and_provider_payload(monkeypatch):

    service = email_service.EmailService()
    service.settings = service.settings.model_copy(
        update={"resend_api_key": "synthetic", "resend_from_email": "test@example.com"}
    )
    response = MagicMock(status_code=400, text="synthetic-private-payload")
    transport = AsyncMock()
    transport.__aenter__.return_value.post.return_value = response
    monkeypatch.setattr(email_service.httpx, "AsyncClient", lambda **kw: transport)
    logs = MagicMock()
    monkeypatch.setattr(email_service, "logger", logs)
    assert not await service.send_email("private@example.com", "private-subject", "secret-code")
    captured = str(logs.mock_calls)
    assert "private@example.com" not in captured
    assert "synthetic-private-payload" not in captured
    assert "secret-code" not in captured
