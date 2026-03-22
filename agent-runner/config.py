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

    # Auth
    runner_key: str = ""  # shared secret with backend (X-Runner-Key header)

    # Limits
    max_agents: int = 40
    chat_timeout: int = 120  # seconds
    idle_timeout_seconds: int = 7200  # auto-stop agents idle for 2 hours

    # Container security
    container_mem_limit: str = "256m"
    container_cpu_quota: int = 50000  # 50% of one core (period=100000)
    container_pids_limit: int = 100
    container_user: str = "1000:1000"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


@lru_cache
def get_settings() -> RunnerSettings:
    return RunnerSettings()
