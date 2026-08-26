"""Agent Runner configuration via Pydantic Settings."""

import socket
import uuid
from functools import lru_cache
from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings

INSTANCE_ID_FILENAME = ".runner-instance-id"


def resolve_runner_instance_id(workspace_root: Path) -> str:
    """Stable identity for this runner deployment, read from (or written to) its workspace.

    The workspace root is a host bind mount, so the identity survives
    ``docker compose up --force-recreate``. The container hostname does not: it
    defaults to the container id, which changes on every redeploy — an identity
    based on it makes the sandbox reaper skip the very containers the previous
    deployment orphaned. Two runner deployments own two workspace roots and so
    get two identities, which is what keeps them from reaping each other.
    """
    marker = workspace_root / INSTANCE_ID_FILENAME
    try:
        stored = marker.read_text().strip()
        if stored:
            return stored
    except OSError:
        pass

    generated = uuid.uuid4().hex
    try:
        workspace_root.mkdir(parents=True, exist_ok=True)
        marker.write_text(generated)
    except OSError:
        # Unwritable workspace: fall back to the hostname. The reaper then only
        # recognises containers from this process's own lifetime — it under-reaps
        # rather than touching a container it cannot prove is its own.
        return socket.gethostname()
    return generated


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

    # Extra free LLM providers (OpenAI-compatible APIs)
    cerebras_api_key: str = ""
    groq_api_key: str = ""
    gemini_api_key: str = ""
    mistral_api_key: str = ""
    nebius_api_key: str = ""
    sambanova_api_key: str = ""
    # Z.AI (GLM) — the only LLM provider reachable from our hosts; every other
    # one geo-blocks Russian ASNs with HTTP 403 (verified 2026-07-15).
    zai_api_key: str = ""

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
    chat_timeout: int = 600  # seconds — llm7 serves ~1 req/8s; a tool-calling loop does not fit in 120s (measured 2026-08-23)
    chat_queue_timeout: int = 120  # seconds to wait for busy agent before 429
    idle_timeout_seconds: int = 1800  # auto-stop agents idle for 30 minutes

    # Prod-trace replay sampling (closes prod→eval feedback loop, Phil Hetzel / AIE London 2026)
    replay_enabled: bool = True
    replay_sample_rate: float = 0.01  # fraction of completed runs to sample (1%)

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

    # Sandbox orphan reaping — containers left behind when the runner is SIGKILLed.
    # Identifies this runner deployment; the startup reaper only ever touches
    # containers stamped with this value. Left empty it is resolved from the
    # workspace volume (see resolve_runner_instance_id), which is what a
    # deployment owns; set RUNNER_INSTANCE_ID only to override that.
    runner_instance_id: str = ""
    # Containers younger than this are left alone, so a sandbox being created
    # concurrently by another process is never removed mid-flight.
    sandbox_reap_grace_seconds: int = 300

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"

    @model_validator(mode="after")
    def _resolve_runner_instance_id(self) -> "RunnerSettings":
        if not self.runner_instance_id:
            self.runner_instance_id = resolve_runner_instance_id(self.workspace_root)
        return self


@lru_cache
def get_settings() -> RunnerSettings:
    return RunnerSettings()
