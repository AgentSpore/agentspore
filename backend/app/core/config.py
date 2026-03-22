"""Конфигурация приложения."""

from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Настройки приложения."""

    # App
    app_name: str = "AgentSpore API"
    debug: bool = False
    api_v1_prefix: str = "/api/v1"

    # Database
    database_url: str = "postgresql+asyncpg://postgres:postgres@db:5432/sporeai"

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

    # CORS
    cors_origins: list[str] = ["http://localhost:3000", "http://localhost:8080"]

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

    # Web3 / Base (mainnet)
    oracle_private_key: str = ""
    base_rpc_url: str = "https://mainnet.base.org"
    factory_contract_address: str = ""

    # Email (Resend)
    resend_api_key: str = ""
    resend_from_email: str = "noreply@agentspore.com"

    # Password reset
    password_reset_ttl_seconds: int = 3600  # 1 hour
    password_reset_rate_limit: int = 3  # max per hour per email

    # Rentals
    rental_payment_enabled: bool = False
    rental_platform_fee_pct: float = 0.01  # 1%

    # OpenViking (shared agent memory)
    openviking_url: str = ""
    openviking_api_key: str = ""

    # Agent Runner (hosted agents on infra server)
    agent_runner_url: str = ""
    agent_runner_key: str = ""

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


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
