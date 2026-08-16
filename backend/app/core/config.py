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
    moonshot_api_key: str = ""  # kimi-k3 judge (Moonshot, OpenAI-compatible)
    cloudflare_api_key: str = ""
    cloudflare_account_id: str = ""
    deepseek_api_key: str = ""  # paid (escalation fallback) — DeepSeek direct API, OpenAI-compatible
    llm7_api_key: str = ""  # optional — llm7.io works keyless; a token only raises the rate limit

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
    # picks models from what is actually enabled — never a hardcoded list. An id
    # whose provider key does not resolve is dropped with a WARNING; if that
    # leaves one model the panel degrades to prompt-diversity-only.
    #
    # EVERY roster model judges (owner decision, 2026-08-09), which is what keeps
    # a panel seatable at all: a judge whose model also fights is recused from
    # that battle, so a small roster empties the panel exactly when its own two
    # models meet. Six judges minus the two on the mat still leaves four for
    # three replicates — quorum by construction rather than by luck.
    #
    # moonshot and deepseek are GONE, and not for a reason a retry fixes: 429
    # "account suspended due to insufficient balance" and 402 respectively,
    # measured by generating a completion from the production host. Listing
    # models answers 200 for both, which is why the outage stayed invisible
    # until a battle needed a token — probe with a completion, never a catalogue
    # read.
    #
    # Each id below was verified live to complete a request AND to return strict
    # JSON, which is the judge contract.
    # INVARIANT(judge-roster): keep more than one PROVIDER here. An all-mistral
    # panel went silent on 2026-08-16 when that account hit 402 — zero verdicts
    # for five days and 144 finished battles nobody judged, while the roster
    # still looked healthy because every id in it was individually valid.
    #
    # zai/glm-4.5-flash was dropped 2026-08-10 for returning 429 on every
    # completion; measured again 2026-08-16 from the production host it answers
    # 200. A model verified once is not verified, and a single-provider panel
    # turns one billing failure into a total outage.
    # 2026-08-16: mistral now returns 402 on every completion (measured live from
    # production), leaving zai/glm-4.5-flash as the sole survivor of the prior
    # six-model roster — the exact single-provider outage the INVARIANT above
    # warns about. llm7 needs no key/signup/payment and was verified live
    # (strict-JSON judge contract) from the same host, so it becomes the second
    # provider. The mistral entries stay listed: a billing top-up brings them
    # back with no code change, and seatable_judges only drops what is actually
    # fighting.
    battle_judge_models: list[str] = [
        "zai/glm-4.5-flash",
        "llm7/DeepSeek-V4-Flash-0731",
        "llm7/codestral-latest",
        "llm7/gemini-3.1-flash-lite",
        "llm7/mistral-Nemo-Instruct-2407",
        "mistral/mistral-large-latest",
        "mistral/magistral-small-latest",
        "mistral/ministral-14b-latest",
        "mistral/mistral-medium-2508",
        "mistral/mistral-small-latest",
    ]
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
    # Auto-battle stream (V72). Conservative by default because the free z.ai
    # flash tier is the only one holding balance and tops out around three
    # in-flight requests, while one battle costs two answer calls plus a judge
    # panel: one new battle per 15 minutes, at most two live at once.
    #
    # OFF by default: merging must not start spending provider calls in every
    # environment without an operator saying so, and the frontend cannot render a
    # contender side yet. Turning it on is a deliberate act.
    battle_auto_enabled: bool = False
    # Sized for AGENTIC contenders (V75), which is a different cost shape from the
    # one-call contenders these defaults were first written for. An agentic side
    # spends a provider call per step, so one battle is now tens of calls rather
    # than two: a live run against Mistral hit HTTP 429 after a SINGLE battle.
    #
    # Hence one at a time. Throughput comes from ticking more often, not from
    # running battles in parallel — a second concurrent battle does not finish
    # sooner, it just makes both sides compete for the same rate limit and voids
    # them together. The worst case a tick must clear is two answer drives at
    # ANSWER_DRIVE_BUDGET_SECONDS (560s) plus the judge panel, so 600s leaves the
    # cadence just ahead of a battle that runs to its ceiling while staying far
    # below the 900s that was tuned for cheap single calls.
    battle_auto_interval_seconds: int = 600
    battle_auto_max_running: int = 1

    # Task harvester — pulls topics from open sources (GitHub/StackExchange/HN,
    # all reachable without a key) and drafts them into battle tasks. OFF by
    # default: it spends the SAME judge-panel budget as validation and judging,
    # so an operator must opt in deliberately, same posture as battle_auto_enabled.
    battle_harvester_enabled: bool = False
    battle_harvester_interval_seconds: int = 1800
    # Refill target for source='generated' READY tasks. Below MINIMUM_TASK_POOL
    # (20, battle_repo.py) a category/difficulty filter can fail to admit a
    # rated challenge at all, so the default sits comfortably above it.
    battle_harvester_pool_target: int = 40
    # Ceiling per cycle, independent of how far under target the pool is: one
    # drafting call costs the same judge-panel budget as one judging call, and a
    # pool crater must not let a single pass spend it all.
    battle_harvester_max_per_pass: int = 5

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
