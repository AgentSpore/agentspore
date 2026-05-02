"""Agent Runner configuration via Pydantic Settings."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings


class RunnerSettings(BaseSettings):
    """Agent Runner Service settings."""

    # Server
    host: str = "0.0.0.0"
    port: int = 8100

    # Workspace
    workspace_root: Path = Path("/data/agents")

    # Docker
    docker_image: str = "agentspore-sandbox:latest"
    docker_host: str = ""  # e.g. unix:///Users/exzent/.docker/run/docker.sock

    # AgentSpore platform
    agentspore_url: str = "https://agentspore.com"

    # LLM (OpenRouter via OpenAI-compatible API)
    openai_api_key: str = ""
    openai_base_url: str = "https://openrouter.ai/api/v1"

    # Agent defaults
    default_model: str = "mistralai/mistral-nemo"
    default_budget_usd: float = 1.0
    default_heartbeat_seconds: int = 3600  # 1 hour

    # Auth — REQUIRED: set RUNNER_KEY env var to a strong random secret.
    # Generate: python -c "import secrets; print(secrets.token_urlsafe(32))"
    # Startup fails if missing. The runner enforces this on every non-health request.
    runner_key: str

    # Limits
    max_agents: int = 40
    chat_timeout: int = 120  # seconds
    idle_timeout_seconds: int = 1800  # auto-stop agents idle for 30 minutes

    # Disk quota (per-agent workspace enforcement)
    # Set AGENT_DISK_QUOTA_ENABLED=true to activate.  Default OFF for safe deploy.
    agent_disk_quota_enabled: bool = False
    agent_disk_soft_mb: int = 150  # warn + emit event at this threshold
    agent_disk_hard_mb: int = 200  # block runner write_file calls above this

    # Container security
    container_mem_limit: str = "512m"
    container_cpu_quota: int = 50000  # 50% of one core (period=100000)
    container_cpu_period: int = 100000
    container_pids_limit: int = 200
    container_user: str = "sandbox"

    # Sandbox network isolation (C3)
    # Create with: docker network create --driver bridge --subnet=10.99.0.0/16 sandbox_net
    # Then add iptables rules to drop RFC1918 traffic from that subnet.
    # See docs/runbook-sandbox-network.md for full deploy steps.
    sandbox_network_name: str = "sandbox_net"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


@lru_cache
def get_settings() -> RunnerSettings:
    return RunnerSettings()
