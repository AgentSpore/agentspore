"""SecureDockerSandbox, BLOCKED_COMMANDS, and is_command_safe."""

import docker
from pydantic_ai_backends import DockerSandbox


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

        env_vars = {}
        if self._runtime and self._runtime.env_vars:
            env_vars = self._runtime.env_vars

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
