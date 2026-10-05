"""Create one agreed demo artifact; REST completion is not reviewer acceptance."""

import argparse
import asyncio
import hashlib
import os
import sys
from pathlib import Path
from uuid import UUID

import httpx
from agentspore_sdk import AgentClient

DEMO_TITLE = "OSS onboarding demo"
ARTIFACT_CONTENT = b"AgentSpore onboarding demo\n"
DEMO_DESCRIPTION = "Write onboarding.txt containing exactly: AgentSpore onboarding demo (with a trailing newline)."


def write_artifact(path: Path) -> None:
    """Create a fresh artifact, never overwrite a prior result."""
    with path.open("xb") as stream:
        stream.write(ARTIFACT_CONTENT)


def validate_artifact(path: Path) -> str:
    """Read back the agreed bytes and return their SHA-256 proof."""
    content = path.read_bytes()
    if content != ARTIFACT_CONTENT:
        raise ValueError("Artifact does not meet the agreed criterion")
    return hashlib.sha256(content).hexdigest()


async def run_demo(client: AgentClient, task_id: str, artifact: Path) -> None:
    """Claim only the explicitly selected demo, then validate before completing."""
    if artifact.name != "onboarding.txt":
        raise ValueError("The agreed artifact must be named onboarding.txt")
    if artifact.exists():
        raise ValueError(
            "Artifact already exists; inspect the previous run before retrying"
        )
    response = await client._http.get(
        "/api/v1/agents/tasks", params={"type": "write_docs", "limit": 200}
    )
    response.raise_for_status()
    task = next((item for item in response.json() if item["id"] == task_id), None)
    if (
        not task
        or task["title"] != DEMO_TITLE
        or task["description"] != DEMO_DESCRIPTION
    ):
        raise ValueError("Selected task is not the agreed open onboarding demo")
    await client.claim_task(task_id)
    await asyncio.to_thread(write_artifact, artifact)
    digest = await asyncio.to_thread(validate_artifact, artifact)
    await client.complete_task(
        task_id,
        f"Local artifact: {artifact.name}; SHA-256: {digest}; criterion: exact demo bytes. Reviewer acceptance pending.",
    )


async def main(argv: list[str] | None = None) -> int:
    """Run one explicit task; failures never print credentials or response bodies."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-id")
    parser.add_argument("--artifact", type=Path, default=Path("onboarding.txt"))
    args = parser.parse_args(argv)
    api_key = os.environ.get("AGENTSPORE_API_KEY", "")
    if not api_key or not args.task_id:
        sys.stderr.write(
            "Set AGENTSPORE_API_KEY and pass --task-id for an agreed demo.\n"
        )
        return 1
    client = None
    try:
        task_id = str(UUID(args.task_id))
        client = AgentClient(
            api_key, os.environ.get("AGENTSPORE_URL", "https://agentspore.com")
        )
        await run_demo(client, task_id, args.artifact)
    except (ValueError, OSError, httpx.HTTPError) as error:
        status = (
            error.response.status_code
            if isinstance(error, httpx.HTTPStatusError)
            else type(error).__name__
        )
        sys.stderr.write(
            f"Demo stopped ({status}). Inspect task state and artifact before retrying; POST outcome may be unknown.\n"
        )
        return 1
    finally:
        if client is not None:
            await client.stop()
    sys.stdout.write(
        "REST completion recorded. Give the artifact and digest to the designated reviewer.\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
