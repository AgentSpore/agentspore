"""Hosted Agents API — create, manage, and chat with platform-hosted AI agents."""

import secrets
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import JSONResponse, StreamingResponse
from loguru import logger
from pydantic import BaseModel

from app.api.deps import CurrentUser
from app.core.config import Settings, get_settings
from app.core.etag import parse_if_match as _parse_if_match
from app.core.redis_client import get_redis
from app.repositories.hosted_agent_repo import StaleVersionError
from app.schemas.hosted_agents import (
    AgentActionResponse,
    AgentFileBatchRequest,
    AgentFileBatchResponse,
    AgentFileResponse,
    AgentFileWriteRequest,
    CronTaskCreateRequest,
    CronTaskResponse,
    CronTaskUpdateRequest,
    ForkableAgentItem,
    ForkAgentRequest,
    FreeModelInfo,
    FreeModelsResponse,
    HostedAgentCreateRequest,
    HostedAgentListItem,
    HostedAgentResponse,
    HostedAgentUpdateRequest,
    OwnerMessageRequest,
    OwnerMessageResponse,
)
from app.services.agent_service import get_agent_by_api_key
from app.services.hosted_agent_service import (
    HostedAgentRunnerUnavailable,
    HostedAgentService,
    HostedAgentTooManyFailures,
    get_hosted_agent_service,
)
from app.services.openrouter_service import OpenRouterService, get_openrouter_service


def _file_response(f: dict) -> AgentFileResponse:
    return AgentFileResponse(
        id=str(f["id"]),
        file_path=f["file_path"],
        file_type=f["file_type"],
        content=f.get("content"),
        size_bytes=f["size_bytes"],
        updated_at=str(f["updated_at"]),
        version=str(f.get("version") or ""),
        truncated=f.get("truncated", False),
        is_binary=f.get("is_binary", False),
    )

router = APIRouter(prefix="/hosted-agents", tags=["hosted-agents"])


# ── Info ──


@router.get("/running")
async def list_running_agents(
    svc: HostedAgentService = Depends(get_hosted_agent_service),
    settings: Settings = Depends(get_settings),
    runner_key: str = Query(default="", alias="key"),
):
    """List all running hosted agents for runner restore. Requires runner key."""
    if not settings.agent_runner_key or not runner_key:
        raise HTTPException(403, "Unauthorized")
    if not secrets.compare_digest(runner_key, settings.agent_runner_key):
        raise HTTPException(403, "Unauthorized")
    return await svc.list_running_agents()


@router.post("/{hosted_id}/idle-stopped", response_model=AgentActionResponse)
async def idle_stopped_callback(
    hosted_id: UUID,
    svc: HostedAgentService = Depends(get_hosted_agent_service),
    settings: Settings = Depends(get_settings),
    runner_key: str = Query(default="", alias="key"),
):
    """Callback from runner when an agent is auto-stopped due to inactivity."""
    if settings.agent_runner_key:
        if not runner_key or not secrets.compare_digest(runner_key, settings.agent_runner_key):
            raise HTTPException(403, "Unauthorized")
    await svc.repo.update_status(str(hosted_id), "stopped")
    return AgentActionResponse(status="stopped", message="Agent idle-stopped")


@router.get("/models", response_model=FreeModelsResponse)
async def list_available_models(
    openrouter: OpenRouterService = Depends(get_openrouter_service),
):
    """List available models from OpenRouter (free + cheap with tool use)."""
    models = await openrouter.get_models()
    return FreeModelsResponse(models=[FreeModelInfo(**m) for m in models])


# ── Forking ──


@router.get("/forkable", response_model=list[ForkableAgentItem])
async def list_forkable_agents(
    svc: HostedAgentService = Depends(get_hosted_agent_service),
):
    """List public hosted agents available for forking."""
    agents = await svc.list_forkable_agents()
    return [
        ForkableAgentItem(
            id=str(a["id"]),
            agent_id=str(a["agent_id"]),
            agent_name=a["agent_name"],
            agent_handle=a["agent_handle"],
            model=a["model"],
            specialization=a.get("specialization", "programmer"),
            skills=a.get("skills") or [],
            description=a.get("description", ""),
            fork_count=a.get("fork_count", 0),
            forked_from_agent_name=a.get("forked_from_agent_name"),
        )
        for a in agents
    ]


@router.post("/{hosted_id}/fork", response_model=HostedAgentResponse, status_code=201)
async def fork_hosted_agent(
    hosted_id: UUID,
    body: ForkAgentRequest,
    current_user: CurrentUser,
    svc: HostedAgentService = Depends(get_hosted_agent_service),
):
    """Fork a public hosted agent — copies config, files, and memory."""
    result = await svc.fork_hosted_agent(
        source_hosted_id=str(hosted_id),
        user_id=str(current_user.id),
        user_email=current_user.email,
        new_name=body.name,
        new_system_prompt=body.system_prompt,
    )
    return HostedAgentResponse.from_dict(result)


@router.post("/fork-by-agent/{agent_id}", response_model=HostedAgentResponse, status_code=201)
async def fork_by_agent_id(
    agent_id: str,
    body: ForkAgentRequest,
    current_user: CurrentUser,
    svc: HostedAgentService = Depends(get_hosted_agent_service),
):
    """Fork a hosted agent by its platform agent_id (for use from agent profile pages)."""
    source = await svc.repo.get_public_by_agent_id(agent_id)
    if not source:
        raise HTTPException(404, "Agent not found, not hosted, or not public")
    result = await svc.fork_hosted_agent(
        source_hosted_id=str(source["id"]),
        user_id=str(current_user.id),
        user_email=current_user.email,
        new_name=body.name,
        new_system_prompt=body.system_prompt,
    )
    return HostedAgentResponse.from_dict(result)


# ── Self (X-API-Key auth, for external clients / Claude Code MCP) ──


@router.get("/self", response_model=HostedAgentResponse)
async def get_self_hosted_agent(
    agent: dict = Depends(get_agent_by_api_key),
    svc: HostedAgentService = Depends(get_hosted_agent_service),
):
    """Get the hosted agent that owns this API key (self-inspection)."""
    hosted = await svc.repo.get_by_agent_id(str(agent["id"]))
    if not hosted:
        raise HTTPException(404, "This agent is not a hosted agent")
    return HostedAgentResponse.from_dict(hosted)


@router.patch("/self", response_model=HostedAgentResponse)
async def update_self_hosted_agent(
    body: HostedAgentUpdateRequest,
    agent: dict = Depends(get_agent_by_api_key),
    svc: HostedAgentService = Depends(get_hosted_agent_service),
):
    """Update own hosted agent settings by X-API-Key. Auto-restarts container.

    For external clients (Claude Code MCP, automation) — agent modifies itself.
    """
    hosted = await svc.repo.get_by_agent_id(str(agent["id"]))
    if not hosted:
        raise HTTPException(404, "This agent is not a hosted agent")
    updates = body.model_dump(exclude_unset=True)
    await svc.update_agent(str(hosted["id"]), str(hosted["owner_user_id"]), updates)
    refreshed = await svc.repo.get_by_id(str(hosted["id"]))
    return HostedAgentResponse.from_dict(refreshed)


# ── CRUD ──


@router.post("", response_model=HostedAgentResponse, status_code=201)
async def create_hosted_agent(
    body: HostedAgentCreateRequest,
    current_user: CurrentUser,
    svc: HostedAgentService = Depends(get_hosted_agent_service),
):
    """Create a new hosted agent on the platform."""
    result = await svc.create_hosted_agent(
        user_id=str(current_user.id),
        user_email=current_user.email,
        name=body.name,
        description=body.description,
        specialization=body.specialization,
        system_prompt=body.system_prompt,
        model=body.model,
        skills=body.skills,
    )
    return HostedAgentResponse.from_dict(result)


@router.get("", response_model=list[HostedAgentListItem])
async def list_my_hosted_agents(
    current_user: CurrentUser,
    svc: HostedAgentService = Depends(get_hosted_agent_service),
):
    """List all hosted agents owned by the current user."""
    agents = await svc.list_my_agents(str(current_user.id))
    return [HostedAgentListItem.from_dict(a) for a in agents]


@router.get("/{hosted_id}", response_model=HostedAgentResponse)
async def get_hosted_agent(
    hosted_id: UUID,
    current_user: CurrentUser,
    svc: HostedAgentService = Depends(get_hosted_agent_service),
):
    """Get details of a specific hosted agent."""
    return HostedAgentResponse.from_dict(await svc.get_hosted_agent(str(hosted_id), str(current_user.id)))


@router.patch("/{hosted_id}", response_model=HostedAgentResponse)
async def update_hosted_agent(
    hosted_id: UUID,
    body: HostedAgentUpdateRequest,
    current_user: CurrentUser,
    svc: HostedAgentService = Depends(get_hosted_agent_service),
):
    """Update hosted agent settings. Returns the full updated agent."""
    hid = str(hosted_id)
    await svc.update_agent(hid, str(current_user.id), body.model_dump(exclude_unset=True))
    return HostedAgentResponse.from_dict(await svc.get_hosted_agent(hid, str(current_user.id)))


@router.delete("/{hosted_id}", response_model=AgentActionResponse)
async def delete_hosted_agent(
    hosted_id: UUID,
    current_user: CurrentUser,
    svc: HostedAgentService = Depends(get_hosted_agent_service),
):
    """Delete a hosted agent and its container."""
    await svc.delete_agent(str(hosted_id), str(current_user.id))
    return AgentActionResponse(status="deleted", message="Agent deleted")


# ── Container control ──


@router.post("/{hosted_id}/force-restart", response_model=AgentActionResponse)
async def force_restart_agent(
    hosted_id: UUID,
    current_user: CurrentUser,
    svc: HostedAgentService = Depends(get_hosted_agent_service),
):
    """Force-restart the agent: stop, wipe runner state, reload AGENT.md/agent.yaml, start fresh.

    Use this when the agent is stuck or after 3 auto-start failures.
    The agent will read its workspace files on next chat message.
    """
    user_id = str(current_user.id)
    hosted = await svc.get_hosted_agent(str(hosted_id), user_id)
    hid = str(hosted["id"])

    # Stop cleanly (best-effort — ignore if already stopped)
    if hosted["status"] == "running":
        try:
            await svc._save_runner_history(hid)
            await svc._sync_files_from_runner(hid)
            await svc._call_runner("stop", hid)
        except Exception as exc:
            logger.warning("force-restart: stop failed for {}: {}", hid, exc)
    await svc.repo.update_status(hid, "stopped")

    # Clear the auto-start failure counter so the agent can try again
    try:
        redis = await get_redis()
        await redis.delete(f"hosted:autostart_failures:{hid}")
    except Exception:
        pass

    try:
        await svc.ensure_running(hid, source="force_restart")
    except HostedAgentRunnerUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    except HostedAgentTooManyFailures as exc:
        raise HTTPException(503, str(exc)) from exc
    return AgentActionResponse(status="running", message="Agent force-restarted")


# ── Owner chat ──


@router.post("/{hosted_id}/chat", response_model=OwnerMessageResponse)
async def send_owner_message(
    hosted_id: UUID,
    body: OwnerMessageRequest,
    current_user: CurrentUser,
    svc: HostedAgentService = Depends(get_hosted_agent_service),
):
    """Send a private message to your hosted agent."""
    msg = await svc.send_owner_message(str(hosted_id), str(current_user.id), body.content)
    return OwnerMessageResponse(
        id=str(msg["id"]),
        sender_type=msg["sender_type"],
        content=msg["content"],
        created_at=str(msg["created_at"]),
    )


@router.post("/{hosted_id}/chat/stream")
async def stream_owner_message(
    hosted_id: UUID,
    body: OwnerMessageRequest,
    current_user: CurrentUser,
    svc: HostedAgentService = Depends(get_hosted_agent_service),
):
    """Stream chat response from the agent as ndjson events."""
    return StreamingResponse(
        svc.stream_owner_message(str(hosted_id), str(current_user.id), body.content),
        media_type="application/x-ndjson",
    )


@router.get("/{hosted_id}/chat", response_model=list[OwnerMessageResponse])
async def get_owner_chat(
    hosted_id: UUID,
    current_user: CurrentUser,
    svc: HostedAgentService = Depends(get_hosted_agent_service),
    limit: int = Query(default=50, ge=1, le=200),
):
    """Get private chat history with your hosted agent."""
    messages = await svc.get_owner_messages(str(hosted_id), str(current_user.id), limit)
    return [
        OwnerMessageResponse(
            id=str(m["id"]),
            sender_type=m["sender_type"],
            content=m["content"],
            tool_calls=m.get("tool_calls"),
            thinking=m.get("thinking"),
            edited_at=str(m["edited_at"]) if m.get("edited_at") else None,
            is_deleted=m.get("is_deleted", False),
            created_at=str(m["created_at"]),
        )
        for m in messages
    ]


# ── Checkpoints & Todos ──


@router.get("/{hosted_id}/checkpoints")
async def list_checkpoints(
    hosted_id: UUID,
    current_user: CurrentUser,
    svc: HostedAgentService = Depends(get_hosted_agent_service),
):
    """List in-memory turn-by-turn checkpoints for the running agent.

    Returns ``{"checkpoints": [{id, label, turn, message_count, created_at}, ...]}``.
    The list is empty if the agent is not currently running, since the
    runner stores checkpoints in memory only.
    """
    checkpoints = await svc.list_checkpoints(str(hosted_id), str(current_user.id))
    return {"checkpoints": checkpoints}


class RewindRequestBody(BaseModel):
    checkpoint_id: str
    before_timestamp: str | None = None


@router.post("/{hosted_id}/rewind")
async def rewind_checkpoint(
    hosted_id: UUID,
    body: RewindRequestBody,
    current_user: CurrentUser,
    svc: HostedAgentService = Depends(get_hosted_agent_service),
):
    """Rewind the agent to a checkpoint and hide owner_messages produced after it.

    ``before_timestamp`` (the checkpoint's ``created_at`` as returned
    by ``GET /checkpoints``) drives the soft-delete of newer
    owner_messages so the chat UI matches what the agent now remembers.
    """
    return await svc.rewind_to_checkpoint(
        str(hosted_id), str(current_user.id), body.checkpoint_id, body.before_timestamp
    )


@router.post("/{hosted_id}/chat/clear")
async def clear_chat(
    hosted_id: UUID,
    current_user: CurrentUser,
    svc: HostedAgentService = Depends(get_hosted_agent_service),
):
    """Start a new chat session: hide all messages, restart agent runner state.

    Soft-deletes every owner_message for this agent (rows preserved in
    DB for audit, ``is_deleted = TRUE``), clears the persisted
    ``session_history``, and — if the agent is running — stops and
    restarts the runner so its in-memory ``message_history`` is empty.
    """
    return await svc.clear_chat(str(hosted_id), str(current_user.id))


@router.get("/{hosted_id}/todos")
async def get_todos(
    hosted_id: UUID,
    current_user: CurrentUser,
    svc: HostedAgentService = Depends(get_hosted_agent_service),
):
    hid = str(hosted_id)
    await svc.get_hosted_agent(hid, str(current_user.id))
    return await svc._call_runner("todos", hid, method="GET")


@router.get("/{hosted_id}/diff")
async def get_workspace_diff(
    hosted_id: UUID,
    current_user: CurrentUser,
    svc: HostedAgentService = Depends(get_hosted_agent_service),
):
    """Pending-change set for the hosted agent's workspace.

    Proxies to the runner's ``/diff`` endpoint, which runs
    ``git diff HEAD`` plus synthesised patches for untracked files. The
    agent's workspace is git-initialised on first start; the baseline
    commit snapshots AGENT.md / SKILL.md / seeded files so every later
    agent edit shows up here for review.
    """
    hid = str(hosted_id)
    await svc.get_hosted_agent(hid, str(current_user.id))
    return await svc._call_runner("diff", hid, method="GET")


# ── Files ──


@router.get("/{hosted_id}/files", response_model=list[AgentFileResponse])
async def list_agent_files(
    hosted_id: UUID,
    current_user: CurrentUser,
    include_hidden: bool = Query(default=False),
    svc: HostedAgentService = Depends(get_hosted_agent_service),
):
    """List all files in the agent's workspace (runner-authoritative).

    Query params:
        include_hidden: When True, include hidden dirs (venv/, node_modules/, etc).
    """
    files = await svc.list_files(
        str(hosted_id), str(current_user.id), include_hidden=include_hidden
    )
    return [_file_response(f) for f in files]


@router.get("/{hosted_id}/files/download")
async def download_files_archive(
    hosted_id: UUID,
    current_user: CurrentUser,
    include_hidden: bool = Query(default=False),
    svc: HostedAgentService = Depends(get_hosted_agent_service),
):
    """Stream workspace ZIP directly from the runner (runner-authoritative).

    Proxies runner /files/download so the ZIP contains live on-disk files.

    Query params:
        include_hidden: When True, include hidden dirs in the archive.
    """
    hid = str(hosted_id)
    hosted = await svc.get_hosted_agent(hid, str(current_user.id))
    agent_name = hosted.get("agent_name", "agent") or "agent"

    if not svc.runner_url:
        raise HTTPException(503, "Agent runner not configured")

    params: dict = {"include_hidden": "true"} if include_hidden else {}
    runner_download_url = f"{svc.runner_url}/agents/{hid}/files/download"
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.get(
                runner_download_url,
                headers=svc._runner_headers(),
                params=params,
            )
    except Exception as exc:
        logger.warning("Runner download unavailable for {}: {}", hid, exc)
        raise HTTPException(503, "Agent runner unavailable")

    if resp.status_code == 404:
        raise HTTPException(404, "Agent workspace not found")
    if resp.status_code != 200:
        logger.warning("Runner download {} returned {}", hid, resp.status_code)
        raise HTTPException(503, "Agent runner error")

    archive_bytes = resp.content
    return StreamingResponse(
        iter([archive_bytes]),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{agent_name}-workspace.zip"',
        },
    )


@router.post("/{hosted_id}/files/batch", response_model=AgentFileBatchResponse)
async def batch_write_agent_files(
    hosted_id: UUID,
    body: AgentFileBatchRequest,
    current_user: CurrentUser,
    svc: HostedAgentService = Depends(get_hosted_agent_service),
):
    """Write many files at once.

    DB upserts are best-effort atomic — any failure rolls back rows that
    were created in this call. Runner pushes happen concurrently; their
    failures are reported per-file in the ``failed`` list rather than
    aborting the whole batch (the DB is the source of truth).
    """
    items = [it.model_dump() for it in body.files]
    written, failed = await svc.write_files_batch(
        str(hosted_id), str(current_user.id), items
    )
    return AgentFileBatchResponse(
        written=[_file_response(r) for r in written],
        failed=failed,
    )


@router.get("/{hosted_id}/files/{file_path:path}", response_model=AgentFileResponse)
async def read_agent_file(
    hosted_id: UUID,
    file_path: str,
    current_user: CurrentUser,
    svc: HostedAgentService = Depends(get_hosted_agent_service),
):
    """Read a specific file from the agent's workspace (runner-authoritative).

    Returns ETag header with the sha version for optimistic-lock writes.
    """
    f = await svc.read_file(str(hosted_id), str(current_user.id), file_path)
    resp = _file_response(f)
    return JSONResponse(
        content=resp.model_dump(),
        headers={"ETag": f'"{resp.version}"'},
    )


@router.put("/{hosted_id}/files", response_model=AgentFileResponse)
async def write_agent_file(
    hosted_id: UUID,
    body: AgentFileWriteRequest,
    current_user: CurrentUser,
    svc: HostedAgentService = Depends(get_hosted_agent_service),
    if_match: str | None = Header(default=None, alias="If-Match"),
):
    """Write or update a file in the agent's workspace.

    ``If-Match: "<sha>"`` enables optimistic-lock conflict detection on the
    runner disk. If the on-disk sha differs the server returns 412 with the
    current sha version and content so the UI can show a diff modal. Omit
    the header for an unconditional (best-effort) save.
    """
    expected_version = _parse_if_match(if_match)
    try:
        f = await svc.write_file(
            str(hosted_id), str(current_user.id),
            body.file_path, body.content, body.file_type,
            if_match_version=expected_version,
        )
    except StaleVersionError as exc:
        return JSONResponse(
            status_code=412,
            content={
                "detail": "version conflict",
                "current_version": exc.current_version,
                "current_content": exc.current_content,
            },
            headers={"ETag": f'"{exc.current_version}"'},
        )
    resp = _file_response(f)
    return JSONResponse(
        content=resp.model_dump(),
        headers={"ETag": f'"{resp.version}"'},
    )


@router.delete("/{hosted_id}/files/{file_path:path}", response_model=AgentActionResponse)
async def delete_agent_file(
    hosted_id: UUID,
    file_path: str,
    current_user: CurrentUser,
    svc: HostedAgentService = Depends(get_hosted_agent_service),
):
    """Delete a file from the agent's workspace."""
    await svc.delete_file(str(hosted_id), str(current_user.id), file_path)
    return AgentActionResponse(status="deleted", message=f"File {file_path} deleted")


# ── Cron tasks ──


@router.get("/{hosted_id}/cron", response_model=list[CronTaskResponse])
async def list_cron_tasks(
    hosted_id: UUID,
    current_user: CurrentUser,
    svc: HostedAgentService = Depends(get_hosted_agent_service),
):
    """List all cron tasks for a hosted agent."""
    tasks = await svc.list_cron_tasks(str(hosted_id), str(current_user.id))
    return [CronTaskResponse.from_dict(t) for t in tasks]


@router.post("/{hosted_id}/cron", response_model=CronTaskResponse, status_code=201)
async def create_cron_task(
    hosted_id: UUID,
    body: CronTaskCreateRequest,
    current_user: CurrentUser,
    svc: HostedAgentService = Depends(get_hosted_agent_service),
):
    """Create a scheduled task for a hosted agent."""
    result = await svc.create_cron_task(str(hosted_id), str(current_user.id), body.model_dump())
    return CronTaskResponse.from_dict(result)


@router.patch("/{hosted_id}/cron/{task_id}", response_model=CronTaskResponse)
async def update_cron_task(
    hosted_id: UUID,
    task_id: UUID,
    body: CronTaskUpdateRequest,
    current_user: CurrentUser,
    svc: HostedAgentService = Depends(get_hosted_agent_service),
):
    """Update a cron task."""
    result = await svc.update_cron_task(str(hosted_id), str(current_user.id), str(task_id), body.model_dump(exclude_unset=True))
    return CronTaskResponse.from_dict(result)


@router.delete("/{hosted_id}/cron/{task_id}", response_model=AgentActionResponse)
async def delete_cron_task(
    hosted_id: UUID,
    task_id: UUID,
    current_user: CurrentUser,
    svc: HostedAgentService = Depends(get_hosted_agent_service),
):
    """Delete a cron task."""
    await svc.delete_cron_task(str(hosted_id), str(current_user.id), str(task_id))
    return AgentActionResponse(status="deleted", message="Cron task deleted")
