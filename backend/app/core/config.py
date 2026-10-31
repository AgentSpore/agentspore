"""Конфигурация приложения."""

import json
from functools import lru_cache
from typing import Annotated
from uuid import UUID

from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    """Настройки приложения."""

    # App
    app_name: str = "AgentSpore API"
    debug: bool = False
    api_v1_prefix: str = "/api/v1"

    # Database
    database_url: str = "postgresql+asyncpg://postgres:postgres@db:5432/sporeai"

    otkrytka_usage_project_id: UUID | None = None
    otkrytka_usage_observer_key: SecretStr | None = None

    # A4 agent deployments (admission only; the flag defaults to off)
    a4_deploy_enabled: bool = False
    a4_max_active: int = 10
    a4_queue_max: int = 10

    # Redis
    redis_url: str = "redis://redis:6379"

    # JWT
    secret_key: str = "super-secret-key-change-in-production"
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 7

    # LLM Provider (OpenRouter)
    llm_api_key: str = ""
    llm_base_url: str = "https://openrouter.ai/api/v1"
    llm_model: str = "anthropic/claude-3.5-sonnet"

    # Extra free LLM providers (OpenAI-compatible APIs)
    cerebras_api_key: str = ""
    groq_api_key: str = ""
    gemini_api_key: str = ""
    mistral_api_key: str = ""
    nebius_api_key: str = ""
    sambanova_api_key: str = ""
    nvidia_api_key: str = ""
    together_api_key: str = ""
    zai_api_key: str = ""
    moonshot_api_key: str = ""  # kimi-k3 judge (Moonshot, OpenAI-compatible)
    cloudflare_api_key: str = ""
    cloudflare_account_id: str = ""
    deepseek_api_key: str = ""  # paid (escalation fallback) — DeepSeek direct API, OpenAI-compatible
    llm7_api_key: str = ""  # optional — llm7.io works keyless; a token only raises the rate limit

    # CORS
    cors_origins: list[str] = ["http://localhost:3000", "http://localhost:8080"]

    # Mirror domains serving the same site and database. The OAuth callback host is
    # the canonical one set by DOMAIN / OAUTH_REDIRECT_BASE_URL, not the list order.
    # Env DOMAINS: comma-separated or a JSON list.
    domains: Annotated[list[str], NoDecode] = ["agentspore.com"]

    # GitHub Configuration
    github_org: str = "AgentSpore"
    github_app_id: str = ""
    github_app_private_key: str = ""
    github_app_installation_id: str = ""
    github_pat: str = ""  # Alternative: Personal Access Token

    # GitHub OAuth (for agent authentication)
    github_oauth_client_id: str = ""
    github_oauth_client_secret: str = ""
    github_oauth_redirect_uri: str = "http://localhost:8000/api/v1/agents/github/callback"

    # User OAuth (Google + GitHub for humans — separate from agent GitHub OAuth)
    google_oauth_client_id: str = ""
    google_oauth_client_secret: str = ""
    user_github_oauth_client_id: str = ""
    user_github_oauth_client_secret: str = ""
    oauth_redirect_base_url: str = "http://localhost:8000"

    # GitHub Webhooks
    github_webhook_secret: str = ""
    github_app_bot_login: str = "agentspore[bot]"

    # GitLab Configuration
    gitlab_api_url: str = "https://gitlab.com/api/v4"
    gitlab_group: str = "AgentSpore"
    gitlab_pat: str = ""  # Personal Access Token с owner правами на группу

    # GitLab OAuth (for agent authentication)
    gitlab_oauth_client_id: str = ""
    gitlab_oauth_client_secret: str = ""
    gitlab_oauth_redirect_uri: str = "http://localhost:8000/api/v1/agents/gitlab/callback"

    # GitLab Webhooks
    gitlab_webhook_secret: str = ""

    # Frontend URL (used to build email links pointing at the frontend, not the API)
    frontend_url: str = "http://localhost:3000"

    # Email (Resend)
    resend_api_key: str = ""
    resend_from_email: str = "noreply@agentspore.com"

    # Password reset
    password_reset_ttl_seconds: int = 3600  # 1 hour
    password_reset_rate_limit: int = 3  # max per hour per email

    # Email verification
    email_verification_ttl_seconds: int = 86400  # 24 hours
    email_verification_resend_cooldown_seconds: int = 60  # 1 request/min per email

    # Auth rate limits (Redis-backed, per IP)
    register_rate_limit: int = 3        # attempts per window
    register_rate_window_seconds: int = 3600  # 1 hour
    login_fail_rate_limit: int = 5      # failed attempts per window
    login_fail_rate_window_seconds: int = 900  # 15 minutes

    # OpenViking (shared agent memory)
    openviking_url: str = ""
    openviking_api_key: str = ""

    @field_validator("domains", mode="before")
    @classmethod
    def _parse_domains(cls, v: object) -> object:
        """Accept ``a.com,b.ru`` or a JSON list; empty falls back to the default."""
        if isinstance(v, str):
            raw = v.strip()
            parsed = json.loads(raw) if raw.startswith("[") else raw.split(",")
            v = [str(d).strip().lower() for d in parsed if str(d).strip()]
        return v or cls.model_fields["domains"].default

    @model_validator(mode="after")
    def _cors_origins_cover_domains(self) -> "Settings":
        """Every mirror domain (and its www) is a legitimate browser origin."""
        for domain in self.domains:
            for origin in (f"https://{domain}", f"https://www.{domain}"):
                if origin not in self.cors_origins:
                    self.cors_origins = [*self.cors_origins, origin]
        return self

    # Reverse proxy trust — IPs/CIDRs whose X-Forwarded-For header is honoured.
    # Default covers local Caddy (127.0.0.1) and Docker bridge (172.16.0.0/12).
    # Override in prod: TRUSTED_PROXY_IPS=172.18.0.0/16 (or exact Caddy container IP).
    trusted_proxy_ips: list[str] = ["127.0.0.1", "172.16.0.0/12", "::1"]

    # extra="ignore": an operator's .env may still hold settings of removed features
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    """Получить настройки (кэшированные)."""
    s = Settings()
    if not s.debug and s.secret_key == "super-secret-key-change-in-production":
        raise RuntimeError(
            "FATAL: SECRET_KEY is set to default value in production. "
            "Set a secure SECRET_KEY environment variable."
        )
    return s
