"""Exercise claim identity prerequisites through real HTTP, PostgreSQL and Redis."""

import asyncio
import hashlib
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, MockTransport, Response
from sqlalchemy import select, text
from test_agent_registration_limits import a3_redis as a3_redis
from test_agent_registration_limits import a3_redis_container as a3_redis_container
from test_service_usage import usage_pg as usage_pg
from test_service_usage import usage_world as usage_world

from app.api.v1 import auth, oauth
from app.core.database import get_db
from app.core.redis_client import get_redis
from app.core.security import get_password_hash
from app.models import User

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
EMAIL = "claim-owner@example.com"
PASSWORD = "SyntheticPassword200"


@pytest.fixture
def provider_http(monkeypatch):
    """Replace only outbound provider and email HTTP with documented payload shapes."""
    payloads = SimpleNamespace(
        google={"sub": "synthetic-google-subject", "email": EMAIL, "email_verified": True},
        github={"id": 200, "login": "synthetic-agent-owner", "email": EMAIL},
        emails=[{"email": EMAIL, "primary": True, "verified": True}],
        email_status=200,
        seen=[],
    )

    def respond(request):
        payloads.seen.append(request.url.path)
        if request.url.path in ("/token", "/login/oauth/access_token"):
            return Response(200, json={"access_token": "synthetic-provider-response"})
        if request.url.path == "/oauth2/v3/userinfo":
            return Response(200, json=payloads.google)
        if request.url.path == "/user":
            return Response(200, json=payloads.github)
        if request.url.path == "/user/emails":
            return Response(payloads.email_status, json=payloads.emails)
        if request.url.host == "api.resend.com":
            return Response(200, json={"id": "synthetic-email-response"})
        raise AssertionError("Unexpected outbound HTTP route")

    monkeypatch.setattr(
        oauth.httpx, "AsyncClient", lambda **kw: AsyncClient(transport=MockTransport(respond), **kw)
    )
    for name in (
        "google_oauth_client_id",
        "google_oauth_client_secret",
        "user_github_oauth_client_id",
        "user_github_oauth_client_secret",
        "resend_api_key",
    ):
        monkeypatch.setattr(oauth.settings, name, "synthetic-test-only")
    monkeypatch.setattr(oauth.settings, "oauth_redirect_base_url", "https://test")
    monkeypatch.setattr(oauth.settings, "debug", False)
    monkeypatch.setattr(oauth.settings, "domains", ["test", "mirror.example"])
    return payloads


@pytest.fixture
async def identity_case(usage_world, a3_redis, provider_http):
    """Keep identity queries, transaction commits and Redis state consumption real."""
    app = FastAPI()
    app.include_router(auth.router, prefix="/api/v1")
    app.include_router(oauth.router, prefix="/api/v1")

    async def request_db():
        async with usage_world.maker.begin() as db:
            yield db

    app.dependency_overrides[get_db] = request_db
    app.dependency_overrides[get_redis] = lambda: a3_redis
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as client:
        yield SimpleNamespace(
            app=app, client=client, world=usage_world, redis=a3_redis, provider=provider_http
        )
    app.dependency_overrides.clear()


async def callback(case, provider):
    """Redeem an actual single-use state through the mounted callback."""
    start = await case.client.get(f"/api/v1/oauth/{provider}")
    state = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]
    return await case.client.get(
        f"/api/v1/oauth/{provider}/callback", params={"code": "synthetic", "state": state}
    )


@pytest.mark.parametrize("provider", ["google", "github"])
async def test_verified_oauth_discards_preregistrant_password(identity_case, provider):
    case = identity_case
    signup = await case.client.post(
        "/api/v1/auth/register", json={"email": EMAIL, "password": PASSWORD, "name": "Claim"}
    )
    assert signup.status_code == 202
    blocked = await case.client.post(
        "/api/v1/auth/login", json={"email": EMAIL, "password": PASSWORD}
    )
    assert blocked.status_code == 403
    async with case.world.maker() as db:
        user_id = await db.scalar(select(User.id).where(User.email == EMAIL))
    response = await callback(case, provider)
    assert response.status_code == 302
    async with case.world.maker() as db:
        user = await db.get(User, user_id)
        assert user is not None and user.is_verified
        assert user.hashed_password is None
        assert user.verification_token is None and user.verification_expires_at is None
        assert user.oauth_provider == provider
    replay_password = await case.client.post(
        "/api/v1/auth/login", json={"email": EMAIL, "password": PASSWORD}
    )
    assert replay_password.status_code == 401


@pytest.mark.parametrize("provider", ["google", "github"])
async def test_verified_account_keeps_password(identity_case, provider):
    case = identity_case
    password_hash = get_password_hash(PASSWORD)
    async with case.world.maker.begin() as db:
        user = User(email=EMAIL, name="Verified", hashed_password=password_hash, is_verified=True)
        db.add(user)
        await db.flush()
        user_id = user.id
    assert (await callback(case, provider)).status_code == 302
    async with case.world.maker() as db:
        user = await db.get(User, user_id)
        assert user is not None and user.hashed_password == password_hash
    response = await case.client.post(
        "/api/v1/auth/login", json={"email": EMAIL, "password": PASSWORD}
    )
    assert response.status_code == 200


@pytest.mark.parametrize("verified", [False, None, "true", 1, "missing"])
async def test_google_rejects_unverified_email_without_identity_changes(identity_case, verified):
    case = identity_case
    case.provider.google["email_verified"] = verified
    if verified == "missing":
        del case.provider.google["email_verified"]
    async with case.world.maker.begin() as db:
        user = User(
            email=EMAIL,
            name="Unverified",
            hashed_password=get_password_hash(PASSWORD),
            is_verified=False,
            verification_token="synthetic-verification-digest",
            verification_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        db.add(user)
        await db.flush()
        user_id, original_hash = user.id, user.hashed_password
    response = await callback(case, "google")
    assert response.status_code == 400
    async with case.world.maker() as db:
        user = await db.get(User, user_id)
        assert user is not None and not user.is_verified
        assert user.hashed_password == original_hash
        assert user.oauth_provider is None and user.oauth_id is None
        assert user.verification_token == "synthetic-verification-digest"


@pytest.mark.parametrize(
    ("primary", "verified", "status"),
    [(True, False, 200), (False, True, 200), (True, True, 403)],
)
async def test_github_public_email_cannot_bypass_verified_primary(
    identity_case, primary, verified, status
):
    case = identity_case
    case.provider.emails = [{"email": EMAIL, "primary": primary, "verified": verified}]
    case.provider.email_status = status
    response = await callback(case, "github")
    assert response.status_code == 400
    assert "/user/emails" in case.provider.seen
    async with case.world.maker() as db:
        assert await db.scalar(select(User.id).where(User.email == EMAIL)) is None


async def test_github_uses_verified_primary_instead_of_public_email(identity_case):
    case = identity_case
    case.provider.github["email"] = "public-profile@example.com"
    assert (await callback(case, "github")).status_code == 302
    assert "/user/emails" in case.provider.seen
    async with case.world.maker() as db:
        assert await db.scalar(select(User.id).where(User.email == EMAIL)) is not None
        assert (
            await db.scalar(select(User.id).where(User.email == "public-profile@example.com"))
            is None
        )


@pytest.mark.parametrize("action", ["register", "login"])
async def test_signup_and_login_do_not_claim_matching_email(identity_case, action):
    case = identity_case
    async with case.world.maker.begin() as db:
        await db.execute(
            text("UPDATE agents SET owner_email = :email WHERE id IN (:first, :second)"),
            {"email": EMAIL, "first": case.world.agents[0], "second": case.world.agents[1]},
        )
        await db.execute(
            text("UPDATE agents SET owner_user_id = NULL WHERE id = :agent"),
            {"agent": case.world.agents[1]},
        )
        await db.execute(
            text(
                "INSERT INTO project_contributors (project_id, agent_id, owner_user_id) "
                "VALUES (:first, :agent, :owner), (:second, :agent, NULL)"
            ),
            {
                "first": case.world.projects[0],
                "second": case.world.projects[1],
                "agent": case.world.agents[1],
                "owner": case.world.owner,
            },
        )
        if action == "login":
            db.add(
                User(
                    email=EMAIL,
                    name="Existing",
                    hashed_password=get_password_hash(PASSWORD),
                    is_verified=True,
                )
            )
    response = await case.client.post(
        f"/api/v1/auth/{action}", json={"email": EMAIL, "password": PASSWORD, "name": "Claim"}
    )
    assert response.status_code == (202 if action == "register" else 200)
    async with case.world.maker() as db:
        owners = (
            await db.execute(
                text("SELECT id, owner_user_id FROM agents WHERE id IN (:first, :second)"),
                {"first": case.world.agents[0], "second": case.world.agents[1]},
            )
        ).all()
        assert dict(owners) == {case.world.agents[0]: case.world.owner, case.world.agents[1]: None}

        contributions = (
            await db.execute(
                text(
                    "SELECT project_id, owner_user_id FROM project_contributors "
                    "WHERE project_id IN (:first, :second)"
                ),
                {"first": case.world.projects[0], "second": case.world.projects[1]},
            )
        ).all()
        assert dict(contributions) == {
            case.world.projects[0]: case.world.owner,
            case.world.projects[1]: None,
        }


@pytest.fixture
async def unverified_identity(identity_case):
    """Persist a live verification token using the real signup storage shape."""
    case = identity_case
    token = "synthetic-verification-proof"
    async with case.world.maker.begin() as db:
        user = User(
            email=EMAIL,
            name="Unverified",
            hashed_password=get_password_hash(PASSWORD),
            is_verified=False,
            verification_token=hashlib.sha256(token.encode()).hexdigest(),
            verification_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        db.add(user)
        await db.flush()
        user_id = user.id
    await case.redis.setex(f"email_verify:{token}", 3600, str(user_id))
    return SimpleNamespace(case=case, token=token, user_id=user_id)


async def test_oauth_invalidates_old_email_verification(unverified_identity):
    identity = unverified_identity
    assert (await callback(identity.case, "google")).status_code == 302
    response = await identity.case.client.get(
        "/api/v1/auth/verify-email", params={"token": identity.token}
    )
    assert response.status_code == 400
    assert "access_token" not in response.json() and "refresh_token" not in response.json()


async def test_email_verification_succeeds_once(unverified_identity):
    identity = unverified_identity
    for expected in (200, 400):
        response = await identity.case.client.get(
            "/api/v1/auth/verify-email", params={"token": identity.token}
        )
        assert response.status_code == expected
    async with identity.case.world.maker() as db:
        user = await db.get(User, identity.user_id)
        assert user is not None and user.is_verified
        assert user.verification_token is None and user.verification_expires_at is None


@pytest.mark.parametrize("invalid", ["hash", "expired", "verified"])
async def test_email_verification_requires_live_database_proof(unverified_identity, invalid):
    identity = unverified_identity
    async with identity.case.world.maker.begin() as db:
        user = await db.get(User, identity.user_id)
        assert user is not None
        if invalid == "hash":
            user.verification_token = "superseded-synthetic-digest"
        elif invalid == "expired":
            user.verification_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        else:
            user.is_verified = True
    response = await identity.case.client.get(
        "/api/v1/auth/verify-email", params={"token": identity.token}
    )
    assert response.status_code == 400
    assert "access_token" not in response.json() and "refresh_token" not in response.json()


async def test_concurrent_email_verification_has_one_winner(unverified_identity):
    identity = unverified_identity
    async with asyncio.TaskGroup() as group:
        responses = [
            group.create_task(
                identity.case.client.get(
                    "/api/v1/auth/verify-email", params={"token": identity.token}
                )
            )
            for _ in range(2)
        ]
    assert sorted(response.result().status_code for response in responses) == [200, 400]


@pytest.mark.parametrize("provider", ["google", "github"])
@pytest.mark.parametrize("victim_started", [False, True])
async def test_oauth_callback_requires_initiating_browser(identity_case, provider, victim_started):
    case = identity_case
    start = await case.client.get(f"/api/v1/oauth/{provider}")
    params = {
        "code": "synthetic",
        "state": parse_qs(urlsplit(start.headers["location"]).query)["state"][0],
    }
    async with AsyncClient(transport=ASGITransport(app=case.app), base_url="https://test") as other:
        if victim_started:
            await other.get(f"/api/v1/oauth/{provider}")
        response = await other.get(f"/api/v1/oauth/{provider}/callback", params=params)
    assert response.status_code == 400
    assert not case.provider.seen
    assert (
        await case.client.get(f"/api/v1/oauth/{provider}/callback", params=params)
    ).status_code == 302
    calls = len(case.provider.seen)
    assert (
        await case.client.get(f"/api/v1/oauth/{provider}/callback", params=params)
    ).status_code == 400
    assert len(case.provider.seen) == calls


@pytest.mark.parametrize("cause", ["provider", "expired", "unicode"])
async def test_oauth_state_requires_provider_and_live_ttl(identity_case, cause):
    case = identity_case
    start = await case.client.get("/api/v1/oauth/google")
    state = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]
    provider = "github" if cause == "provider" else "google"
    if cause == "unicode":
        state = "é"
    if cause == "expired":
        await case.redis.expire(f"oauth_state:google:{state}", 0)
    response = await case.client.get(
        f"/api/v1/oauth/{provider}/callback",
        params={"code": "synthetic", "state": state},
        headers={} if cause == "unicode" else {"Cookie": f"__Host-oauth_state_{provider}={state}"},
    )
    assert response.status_code == 400
    assert not case.provider.seen


async def test_oauth_binding_cookie_is_host_only_and_protected(identity_case):
    response = await identity_case.client.get("/api/v1/oauth/google")
    cookie = response.headers.get("set-cookie", "").lower()
    assert "httponly" in cookie and "samesite=lax" in cookie and "secure" in cookie
    assert "max-age=300" in cookie and "domain=" not in cookie
    assert cookie.startswith("__host-oauth_state_google=")


async def test_mirror_login_normalizes_before_minting_browser_state(identity_case):
    case = identity_case
    response = await case.client.get("https://mirror.example/api/v1/oauth/google")
    assert response.headers["location"] == "https://test/api/v1/oauth/google?origin=mirror.example"
    assert "set-cookie" not in response.headers
    assert not await case.redis.keys("oauth_state:*")
    start = await case.client.get(response.headers["location"])
    state = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]
    response = await case.client.get(
        "/api/v1/oauth/google/callback", params={"code": "synthetic", "state": state}
    )
    assert response.status_code == 302
    assert response.headers["location"].startswith("https://mirror.example/auth/callback?")
