"""SecureDockerSandbox, sandbox orphan reaping, BLOCKED_COMMANDS, is_command_safe."""

from collections.abc import Container
from datetime import datetime, timezone
from pathlib import Path

import docker
from loguru import logger
from pydantic_ai_backends import DockerSandbox

from ssrf_guard import extract_urls, is_safe_url

# Labels stamped on every sandbox container so a restarted runner can find and
# remove the containers its previous (crashed / SIGKILLed) incarnation left behind.
LABEL_MARKER = "com.agentspore.sandbox"
LABEL_HOSTED_ID = "com.agentspore.hosted-id"
LABEL_RUNNER_ID = "com.agentspore.runner-id"


def _sandbox_labels(runner_instance_id: str, volumes: dict[str, str], work_dir: str) -> dict[str, str]:
    """Ownership markers — the only way a restarted runner recognises its own leaked containers.

    The hosted-agent id is read off the workspace directory bound at ``work_dir``,
    which is named after it.
    """
    hosted_id = ""
    for host_path, container_path in volumes.items():
        if container_path == work_dir:
            hosted_id = Path(host_path).name
    return {
        LABEL_MARKER: "true",
        LABEL_HOSTED_ID: hosted_id,
        LABEL_RUNNER_ID: runner_instance_id,
    }


def _container_age_seconds(container) -> float:
    """Age of a container in seconds; 0.0 when Docker's timestamp is unusable."""
    created = (getattr(container, "attrs", None) or {}).get("Created", "")
    if not created:
        return 0.0
    # Docker reports nanoseconds; datetime accepts at most microseconds.
    head, _, fraction = created.rstrip("Z").partition(".")
    stamp = f"{head}.{fraction[:6]}" if fraction else head
    try:
        parsed = datetime.fromisoformat(stamp).replace(tzinfo=timezone.utc)
    except ValueError:
        return 0.0
    return (datetime.now(timezone.utc) - parsed).total_seconds()


def reap_orphan_sandboxes(live_hosted_ids: Container[str]) -> int:
    """Remove sandbox containers orphaned by a previous runner incarnation.

    Only containers carrying this runner instance's own labels are considered,
    so a second runner sharing the host keeps its sandboxes. Never raises —
    startup must proceed even when the Docker daemon misbehaves.

    Args:
        live_hosted_ids: hosted-agent ids whose sandboxes must be kept.

    Returns:
        Number of containers actually removed.
    """
    import main as _main  # noqa: PLC0415 — see _ensure_container for the rationale
    settings = _main.settings

    try:
        client = docker.from_env()
        # INVARIANT(sandbox-reaper): the label filter is the ONLY thing keeping this
        # scoped to our own containers. Removing it (or matching on image name /
        # name pattern instead) turns the reaper into a host-wide container killer —
        # this host also runs Harbor, the observability stack and customer services.
        containers = client.containers.list(
            all=True, filters={"label": f"{LABEL_MARKER}=true"}
        )
    except Exception as exc:
        logger.warning("Sandbox reaper: cannot list containers: {}", exc)
        return 0

    removed = 0
    for container in containers:
        labels = getattr(container, "labels", None) or {}
        if labels.get(LABEL_RUNNER_ID) != settings.runner_instance_id:
            continue
        if labels.get(LABEL_HOSTED_ID) in live_hosted_ids:
            continue
        if _container_age_seconds(container) < settings.sandbox_reap_grace_seconds:
            continue
        try:
            container.remove(force=True)
            removed += 1
        except Exception as exc:
            logger.warning("Sandbox reaper: cannot remove {}: {}", container, exc)

    logger.info("Sandbox reaper removed {} orphan container(s)", removed)
    return removed


class SecureDockerSandbox(DockerSandbox):
    """DockerSandbox with security hardening: resource limits, non-root user, capability drops."""

    def _ensure_container(self) -> None:
        if self._container is not None:
            return

        # Circular-import exception: tests patch `main.settings` to inject
        # per-test configuration overrides. To honour those patches,
        # `settings` must be resolved from `main`'s namespace at call time
        # rather than from a module-level import. Importing `main` at the
        # top of this file would create a circular dependency
        # (main → sandbox → main). This is the only structurally unavoidable
        # local import in the codebase.
        import main as _main  # noqa: PLC0415
        settings = _main.settings

        client = docker.from_env()

        image = self._ensure_runtime_image(client)

        # pip --user writes to ~/.local, and the root filesystem is mounted
        # read-only (see read_only=True below), so an agent asking for a library
        # it needs — python-docx to write the report it was asked for, pandas to
        # read the file it was given — fails on "Read-only file system" with no
        # way around it. /workspace is the one writable, persistent place it has,
        # so user installs are pointed there. Measured: with this set, pip
        # installs and the module imports; without it, neither.
        env_vars = {"PYTHONUSERBASE": f"{self._work_dir}/.local"}
        if self._runtime and self._runtime.env_vars:
            env_vars.update(self._runtime.env_vars)

        docker_volumes: dict[str, dict[str, str]] = {}
        for host_path, container_path in self._volumes.items():
            docker_volumes[host_path] = {"bind": container_path, "mode": "rw"}

        self._container = client.containers.run(
            image,
            command="sleep infinity",
            detach=True,
            working_dir=self._work_dir,
            auto_remove=self._auto_remove,
            environment=env_vars,
            labels=_sandbox_labels(settings.runner_instance_id, self._volumes, self._work_dir),
            volumes=docker_volumes if docker_volumes else None,
            # Network isolation (C3): spawn in dedicated sandbox network.
            # That network has iptables rules on the host dropping traffic to
            # RFC1918 ranges (10/8, 172.16/12, 192.168/16) so the container
            # cannot reach the host gateway, backend, or DB, but can still
            # call public LLM APIs (OpenRouter, NVIDIA, Groq, Cerebras).
            # Deploy: see docs/runbook-sandbox-network.md.
            network=settings.sandbox_network_name,
            # Resource limits
            mem_limit=settings.container_mem_limit,
            cpu_period=settings.container_cpu_period,
            cpu_quota=settings.container_cpu_quota,
            pids_limit=settings.container_pids_limit,
            # Non-root user (L3): sandbox user uid=1000 created in Dockerfile.sandbox.
            user=settings.container_user,
            # Read-only root FS; /tmp writable via tmpfs, /workspace via bind mount.
            read_only=True,
            tmpfs={"/tmp": "size=100m,mode=1777"},
            # Capability hardening: drop ALL, add nothing.
            # NET_RAW removed — DNS still works via container resolver without it.
            cap_drop=["ALL"],
            # Prevent privilege escalation via setuid binaries.
            security_opt=["no-new-privileges:true"],
        )


# NOTE (C4): This substring check is NOT a security control.
# Trivially bypassed via: tabs, base64, $(), bash -c "..." etc.
# Real isolation is enforced by the container boundary:
#   read_only FS, cap_drop=ALL, no-new-privileges, non-root user,
#   sandbox_net with iptables RFC1918 drop rules.
# This list exists only as a UX hint — it blocks obviously dangerous
# commands submitted by confused or copy-pasting users so they get a
# clear error message instead of a confusing sandbox refusal later.
BLOCKED_COMMANDS = [
    "rm -rf /", "rm -rf /*", "mkfs", "dd if=", ":()", "fork",
    "shutdown", "reboot", "halt", "poweroff",
    "chmod 777 /", "chown root",
    "/etc/shadow", "/etc/passwd",
]


def get_blocked_urls(text: str) -> list[str]:
    """Return list of URLs in text that are blocked (redirect/paste domains or private IPs).

    Intended for UX hints and logging — NOT a security boundary. Container
    network isolation remains the actual defence.
    """
    return [url for url in extract_urls(text) if not is_safe_url(url)]


def is_command_safe(command: str) -> tuple[bool, str]:
    """UX hint check — surface obviously dangerous commands with a clear message.

    NOT a security boundary. Container isolation (read_only, cap_drop=ALL,
    no-new-privileges, non-root user, isolated network) is the actual defence.
    """
    cmd_lower = command.lower().strip()
    for blocked in BLOCKED_COMMANDS:
        if blocked in cmd_lower:
            return False, f"Blocked command pattern: {blocked}"
    return True, ""
