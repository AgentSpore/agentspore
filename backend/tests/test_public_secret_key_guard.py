"""get_settings() must refuse a publicly known or weak SECRET_KEY outside debug mode."""

import hashlib
import secrets

import pytest

from app.core import config

SAMPLE_LEAKED = "leaked-sample-" + "a" * 50


@pytest.fixture(autouse=True)
def _isolated_settings(monkeypatch):
    monkeypatch.setattr(
        config,
        "_PUBLIC_SECRET_KEY_SHA256",
        frozenset({hashlib.sha256(SAMPLE_LEAKED.encode()).hexdigest()}),
    )
    monkeypatch.setenv("DEBUG", "false")
    config.get_settings.cache_clear()
    yield
    config.get_settings.cache_clear()


@pytest.mark.parametrize(
    "key",
    [
        "super-secret-key-change-in-production",
        "CHANGE_ME",
        "CHANGE_ME_generate_with_openssl_rand_hex_32",
        "your-super-secret-key-change-in-production",
        "short-but-unique-123",
        SAMPLE_LEAKED,
    ],
    ids=[
        "code_default",
        "deploy_placeholder",
        "root_placeholder",
        "backend_placeholder",
        "too_short",
        "leaked_hash",
    ],
)
def test_public_or_weak_key_is_refused(monkeypatch, key):
    monkeypatch.setenv("SECRET_KEY", key)
    with pytest.raises(RuntimeError, match="public example value or too short"):
        config.get_settings()


def test_random_key_is_accepted(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", secrets.token_hex(32))
    assert config.get_settings().secret_key


def test_debug_skips_the_guard(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "CHANGE_ME")
    monkeypatch.setenv("DEBUG", "true")
    assert config.get_settings().secret_key == "CHANGE_ME"
