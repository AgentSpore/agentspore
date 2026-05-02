"""Tests for auth schema validation: C2 (name length) and C3 (password complexity).

C2 — UserCreate crashes server on 10000-char name. Fix: Field(max_length=128).
C3 — UserCreate accepts weak password "123". Fix: min_length=8 + complexity validator.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.auth import UserCreate


class TestNameValidation:
    """C2: name field must be bounded."""

    def test_normal_name_accepted(self):
        u = UserCreate(email="user@example.com", password="Pass1234", name="Alice")
        assert u.name == "Alice"

    def test_name_max_128_chars_accepted(self):
        u = UserCreate(email="user@example.com", password="Pass1234", name="a" * 128)
        assert len(u.name) == 128

    def test_name_10000_chars_rejected(self):
        with pytest.raises(ValidationError) as exc_info:
            UserCreate(email="user@example.com", password="Pass1234", name="a" * 10000)
        errors = exc_info.value.errors()
        assert any(e["loc"] == ("name",) for e in errors)

    def test_name_129_chars_rejected(self):
        with pytest.raises(ValidationError):
            UserCreate(email="user@example.com", password="Pass1234", name="x" * 129)

    def test_empty_name_rejected(self):
        with pytest.raises(ValidationError):
            UserCreate(email="user@example.com", password="Pass1234", name="")


class TestPasswordValidation:
    """C3: password must be at least 8 chars and contain letter + digit."""

    def test_valid_password_accepted(self):
        u = UserCreate(email="u@x.com", password="SecurePass1", name="Bob")
        assert u.password == "SecurePass1"

    def test_password_too_short_rejected(self):
        with pytest.raises(ValidationError) as exc_info:
            UserCreate(email="u@x.com", password="abc1", name="Bob")
        errors = exc_info.value.errors()
        assert any(e["loc"] == ("password",) for e in errors)

    def test_password_123_rejected(self):
        """The exact case from the bug report."""
        with pytest.raises(ValidationError):
            UserCreate(email="u@x.com", password="123", name="Bob")

    def test_password_no_letter_rejected(self):
        with pytest.raises(ValidationError) as exc_info:
            UserCreate(email="u@x.com", password="12345678", name="Bob")
        errors = exc_info.value.errors()
        assert any(e["loc"] == ("password",) for e in errors)

    def test_password_no_digit_rejected(self):
        with pytest.raises(ValidationError) as exc_info:
            UserCreate(email="u@x.com", password="abcdefgh", name="Bob")
        errors = exc_info.value.errors()
        assert any(e["loc"] == ("password",) for e in errors)

    def test_password_max_128_chars_accepted(self):
        long_pass = "aB3" + "x" * 125  # 128 chars, has letter + digit
        u = UserCreate(email="u@x.com", password=long_pass, name="Bob")
        assert len(u.password) == 128

    def test_password_129_chars_rejected(self):
        long_pass = "aB3" + "x" * 126  # 129 chars
        with pytest.raises(ValidationError):
            UserCreate(email="u@x.com", password=long_pass, name="Bob")

    def test_email_normalized_to_lowercase(self):
        u = UserCreate(email="User@EXAMPLE.COM", password="Pass1234", name="Bob")
        assert u.email == "user@example.com"
