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
    cloudflare_api_key: str = ""
    cloudflare_account_id: str = ""
    deepseek_api_key: str = ""  # paid (escalation fallback) — DeepSeek direct API, OpenAI-compatible

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

    # Rentals
    rental_payment_enabled: bool = False
    rental_platform_fee_pct: float = 0.01  # 1%

    # OpenViking (shared agent memory)
    openviking_url: str = ""
    openviking_api_key: str = ""

    # Agent Runner (hosted agents on infra server)
    agent_runner_url: str = ""
    agent_runner_key: str = ""

    # Hosted agents
    max_hosted_agents_per_user: int = 1
    max_cron_tasks_per_agent: int = 10

    # Battle rated-track anti-abuse (Track 3). All limits are enforced against
    # the verified owner (users.id), not the agent, so a Sybil second agent
    # cannot multiply an owner's budget or rated slots.
    # Judge-panel model roster (Track 2 diversity). Ordered candidate model ids;
    # the FIRST is the primary (must be battle_judges.JUDGE_MODEL). Each is kept
    # only if OpenRouterService.resolve_provider finds a usable key, so the panel
    # picks models from what is actually enabled — never a hardcoded list. When
    # only one resolves the panel degrades to prompt-diversity-only (the real
    # case today: RU-ASN geo-blocks every US provider, leaving z.ai). To enable
    # real model diversity, add a reachable id (e.g. "mistral/mistral-small") AND
    # set its provider key; do NOT assume a rich model zoo.
    battle_judge_models: list[str] = ["zai/glm-4.5-flash"]
    battle_judge_owner_daily_call_limit: int = 60
    battle_judge_global_daily_call_limit: int = 10_000
    battle_judge_max_attempts_per_battle: int = 12
    battle_owner_hourly_challenge_limit: int = 20
    battle_owner_concurrent_rated_limit: int = 2
    battle_owner_daily_rated_limit: int = 10
    battle_rated_min_account_age_days: int = 7
    battle_breaker_failure_threshold: int = 20
    battle_breaker_failure_window_seconds: int = 300
    battle_breaker_spike_threshold: int = 100
    battle_breaker_spike_window_seconds: int = 60
    battle_breaker_ttl_seconds: int = 900

    # Reverse proxy trust — IPs/CIDRs whose X-Forwarded-For header is honoured.
    # Default covers local Caddy (127.0.0.1) and Docker bridge (172.16.0.0/12).
    # Override in prod: TRUSTED_PROXY_IPS=172.18.0.0/16 (or exact Caddy container IP).
    trusted_proxy_ips: list[str] = ["127.0.0.1", "172.16.0.0/12", "::1"]

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
