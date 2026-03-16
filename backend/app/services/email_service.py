"""EmailService — transactional emails via Resend API."""

from functools import lru_cache

import httpx

from app.core.config import get_settings

from loguru import logger


class EmailService:
    """Send transactional emails via Resend (https://resend.com/docs/api-reference)."""

    API_URL = "https://api.resend.com/emails"

    def __init__(self):
        self.settings = get_settings()

    async def send_email(self, to: str, subject: str, html: str) -> bool:
        if not self.settings.resend_api_key:
            logger.warning("RESEND_API_KEY not configured, email not sent to %s", to)
            return False

        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                self.API_URL,
                headers={"Authorization": f"Bearer {self.settings.resend_api_key}"},
                json={
                    "from": self.settings.resend_from_email,
                    "to": [to],
                    "subject": subject,
                    "html": html,
                },
            )

        if resp.status_code in (200, 201):
            logger.info("Email sent to %s: %s", to, subject)
            return True

        logger.warning("Email send failed (%d): %s", resp.status_code, resp.text)
        return False

    async def send_password_reset(self, to: str, token: str) -> bool:
        base_url = self.settings.oauth_redirect_base_url
        reset_url = f"{base_url}/reset-password?token={token}"

        html = f"""
        <div style="font-family: -apple-system, system-ui, sans-serif; max-width: 480px; margin: 0 auto; padding: 40px 20px;">
            <h2 style="color: #fff; font-size: 20px; margin-bottom: 8px;">Password Reset</h2>
            <p style="color: #a3a3a3; font-size: 14px; line-height: 1.6;">
                Click the button below to reset your password. This link expires in 1 hour.
            </p>
            <a href="{reset_url}"
               style="display: inline-block; margin: 24px 0; padding: 12px 32px; background: #fff; color: #000; text-decoration: none; border-radius: 8px; font-size: 14px; font-weight: 600;">
                Reset Password
            </a>
            <p style="color: #525252; font-size: 12px; line-height: 1.5;">
                If you didn't request this, ignore this email.<br>
                Link: <a href="{reset_url}" style="color: #737373;">{reset_url}</a>
            </p>
        </div>
        """

        return await self.send_email(to, "Reset your AgentSpore password", html)


@lru_cache
def get_email_service() -> EmailService:
    return EmailService()
