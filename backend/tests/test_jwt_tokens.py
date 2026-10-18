"""Unit tests for JWT creation and decoding in app.core.security (no Docker)."""

from datetime import datetime, timedelta, timezone

import jwt
import pytest

from app.core import security

SECRET = "unit-test-key-0123456789abcdef0123456789"
OTHER_SECRET = "another-key-0123456789abcdef0123456789"


@pytest.fixture(autouse=True)
def fixed_settings(monkeypatch):
    monkeypatch.setattr(security.settings, "secret_key", SECRET)
    monkeypatch.setattr(security.settings, "algorithm", "HS256")


def _claims(delta: timedelta) -> dict:
    return {"sub": "u1", "exp": datetime.now(timezone.utc) + delta, "type": "access"}


def test_access_token_round_trip():
    payload = security.decode_token(security.create_access_token("u1"))
    assert payload is not None
    assert (payload.sub, payload.type) == ("u1", "access")


def test_refresh_token_round_trip():
    payload = security.decode_token(security.create_refresh_token("u1"))
    assert payload is not None
    assert payload.type == "refresh"


def test_expired_token_rejected():
    token = jwt.encode(_claims(timedelta(seconds=-5)), SECRET, algorithm="HS256")
    assert security.decode_token(token) is None


def test_wrong_signature_rejected():
    token = jwt.encode(_claims(timedelta(minutes=5)), OTHER_SECRET, algorithm="HS256")
    assert security.decode_token(token) is None


def test_alg_none_rejected():
    token = jwt.encode(_claims(timedelta(minutes=5)), None, algorithm="none")
    assert security.decode_token(token) is None


def test_different_hmac_alg_rejected():
    token = jwt.encode(_claims(timedelta(minutes=5)), SECRET, algorithm="HS512")
    assert security.decode_token(token) is None


def test_garbage_token_rejected():
    assert security.decode_token("not.a.jwt") is None
