"""Email verification link tokens that never appear in Redis in raw form.

The emailed token is HMAC(derived key, nonce). Redis keeps only sha256(token)
as the lookup key and the nonce as the per-user resend pointer, so a cache dump
yields no working link.
"""

import base64
import hashlib
import hmac
import secrets

from app.core.config import get_settings

_KEY_LABEL = b"email-verification-link-v1"


class VerificationToken:
    @staticmethod
    def new_nonce() -> str:
        return secrets.token_urlsafe(32)

    @staticmethod
    def from_nonce(nonce: str) -> str:
        """Derive the emailed token from a nonce; depends on the settings secret key."""
        secret = get_settings().secret_key.encode()
        key = hmac.new(secret, _KEY_LABEL, hashlib.sha256).digest()
        mac = hmac.new(key, nonce.encode(), hashlib.sha256).digest()
        return base64.urlsafe_b64encode(mac).rstrip(b"=").decode()

    @staticmethod
    def digest(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    @staticmethod
    def link_key(token: str) -> str:
        return f"email_verify:{VerificationToken.digest(token)}"

    @staticmethod
    def legacy_link_key(token: str) -> str:
        return f"email_verify:{token}"
