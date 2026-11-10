"""Start-failure evidence for PostgreSQL test containers (issue #186)."""

from docker.errors import DockerException
from requests.exceptions import RequestException


def container_exit_evidence(container, tail: int = 20) -> str:
    """Exit state and last log lines of a container, read before it is removed.

    Never reads `Config.Env`: only `State` and the log tail are returned.
    """
    try:
        wrapped = container.get_wrapped_container()
        wrapped.reload()
        state = wrapped.attrs["State"]
        logs = wrapped.logs(tail=tail).decode(errors="replace")
    except (DockerException, RequestException, AttributeError, KeyError) as exc:
        return f"unavailable ({type(exc).__name__}: {exc})"
    return (
        f"status={state.get('Status')} exit_code={state.get('ExitCode')} "
        f"oom_killed={state.get('OOMKilled')} error={state.get('Error')!r} "
        f"started_at={state.get('StartedAt')} finished_at={state.get('FinishedAt')} "
        f"last_logs={logs!r}"
    )


def start_with_evidence(container) -> None:
    """Start the container; on failure stop it and raise with its exit evidence."""
    try:
        container.start()
    except Exception as exc:
        evidence = container_exit_evidence(container)
        container.stop()
        # The readiness probe retries `docker exec` errors until its timeout, so a container
        # that died early surfaces only as "not running" after the exit state is cleaned up.
        raise RuntimeError(f"PostgreSQL fixture failed to start: {evidence}") from exc
