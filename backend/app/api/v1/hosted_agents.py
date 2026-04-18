"""Hosted Agents API — create, manage, and chat with platform-hosted AI agents."""

import io
import secrets
import zipfile

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse

from app.api.deps import CurrentUser
from app.core.config import Settings, get_settings
from app.schemas.hosted_agents import (
    AgentActionResponse,
    AgentFileResponse,
    AgentFileWriteRequest,
    CronTaskCreateRequest,
    CronTaskResponse,
    CronTaskUpdateRequest,
    ForkAgentRequest,
    ForkableAgentItem,
    FreeModelInfo,
    FreeModelsResponse,
    HostedAgentCreateRequest,
    HostedAgentListItem,
    HostedAgentResponse,
    HostedAgentUpdateRequest,
    OwnerMessageRequest,
    OwnerMessageResponse,
)
from app.services.hosted_agent_service import HostedAgentService, get_hosted_agent_service
from app.services.openrouter_service import OpenRouterService, get_openrouter_service

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
    hosted_id: str,
    svc: HostedAgentService = Depends(get_hosted_agent_service),
    settings: Settings = Depends(get_settings),
    runner_key: str = Query(default="", alias="key"),
):
    """Callback from runner when an agent is auto-stopped due to inactivity."""
    if settings.agent_runner_key:
        if not runner_key or not secrets.compare_digest(runner_key, settings.agent_runner_key):
            raise HTTPException(403, "Unauthorized")
    await svc.repo.update_status(hosted_id, "stopped")
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
    hosted_id: str,
    body: ForkAgentRequest,
    current_user: CurrentUser,
    svc: HostedAgentService = Depends(get_hosted_agent_service),
):
    """Fork a public hosted agent — copies config, files, and memory."""
    result = await svc.fork_hosted_agent(
        source_hosted_id=hosted_id,
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
    hosted_id: str,
    current_user: CurrentUser,
    svc: HostedAgentService = Depends(get_hosted_agent_service),
):
    """Get details of a specific hosted agent."""
    return HostedAgentResponse.from_dict(await svc.get_hosted_agent(hosted_id, str(current_user.id)))


@router.patch("/{hosted_id}", response_model=HostedAgentResponse)
async def update_hosted_agent(
    hosted_id: str,
    body: HostedAgentUpdateRequest,
    current_user: CurrentUser,
    svc: HostedAgentService = Depends(get_hosted_agent_service),
):
    """Update hosted agent settings. Returns the full updated agent."""
    await svc.update_agent(hosted_id, str(current_user.id), body.model_dump(exclude_unset=True))
    return HostedAgentResponse.from_dict(await svc.get_hosted_agent(hosted_id, str(current_user.id)))


@router.delete("/{hosted_id}", response_model=AgentActionResponse)
async def delete_hosted_agent(
    hosted_id: str,
    current_user: CurrentUser,
    svc: HostedAgentService = Depends(get_hosted_agent_service),
):
    """Delete a hosted agent and its container."""
    await svc.delete_agent(hosted_id, str(current_user.id))
    return AgentActionResponse(status="deleted", message="Agent deleted")


# ── Container control ──


@router.post("/{hosted_id}/start", response_model=AgentActionResponse)
async def start_agent(
    hosted_id: str,
    current_user: CurrentUser,
    svc: HostedAgentService = Depends(get_hosted_agent_service),
):
    """Start the agent container."""
    return AgentActionResponse(**(await svc.start_agent(hosted_id, str(current_user.id))))


@router.post("/{hosted_id}/stop", response_model=AgentActionResponse)
async def stop_agent(
    hosted_id: str,
    current_user: CurrentUser,
    svc: HostedAgentService = Depends(get_hosted_agent_service),
):
    """Stop the agent container."""
    return AgentActionResponse(**(await svc.stop_agent(hosted_id, str(current_user.id))))


@router.post("/{hosted_id}/restart", response_model=AgentActionResponse)
async def restart_agent(
    hosted_id: str,
    current_user: CurrentUser,
    svc: HostedAgentService = Depends(get_hosted_agent_service),
):
    """Restart the agent container."""
    return AgentActionResponse(**(await svc.restart_agent(hosted_id, str(current_user.id))))


# ── Owner chat ──


@router.post("/{hosted_id}/chat", response_model=OwnerMessageResponse)
async def send_owner_message(
    hosted_id: str,
    body: OwnerMessageRequest,
    current_user: CurrentUser,
    svc: HostedAgentService = Depends(get_hosted_agent_service),
):
    """Send a private message to your hosted agent."""
    msg = await svc.send_owner_message(hosted_id, str(current_user.id), body.content)
    return OwnerMessageResponse(
        id=str(msg["id"]),
        sender_type=msg["sender_type"],
        content=msg["content"],
        created_at=str(msg["created_at"]),
    )


@router.post("/{hosted_id}/chat/stream")
async def stream_owner_message(
    hosted_id: str,
    body: OwnerMessageRequest,
    current_user: CurrentUser,
    svc: HostedAgentService = Depends(get_hosted_agent_service),
):
    """Stream chat response from the agent as ndjson events."""
    return StreamingResponse(
        svc.stream_owner_message(hosted_id, str(current_user.id), body.content),
        media_type="application/x-ndjson",
    )


@router.get("/{hosted_id}/chat", response_model=list[OwnerMessageResponse])
async def get_owner_chat(
    hosted_id: str,
    current_user: CurrentUser,
    svc: HostedAgentService = Depends(get_hosted_agent_service),
    limit: int = Query(default=50, ge=1, le=200),
):
    """Get private chat history with your hosted agent."""
    messages = await svc.get_owner_messages(hosted_id, str(current_user.id), limit)
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
    hosted_id: str,
    current_user: CurrentUser,
    svc: HostedAgentService = Depends(get_hosted_agent_service),
):
    await svc.get_hosted_agent(hosted_id, str(current_user.id))
    return await svc._call_runner("checkpoints", hosted_id, method="GET")


@router.post("/{hosted_id}/rewind")
async def rewind_checkpoint(
    hosted_id: str,
    body: dict,
    current_user: CurrentUser,
    svc: HostedAgentService = Depends(get_hosted_agent_service),
):
    await svc.get_hosted_agent(hosted_id, str(current_user.id))
    return await svc._call_runner("rewind", hosted_id, body)


@router.get("/{hosted_id}/todos")
async def get_todos(
    hosted_id: str,
    current_user: CurrentUser,
    svc: HostedAgentService = Depends(get_hosted_agent_service),
):
    await svc.get_hosted_agent(hosted_id, str(current_user.id))
    return await svc._call_runner("todos", hosted_id, method="GET")


# ── Files ──


@router.get("/{hosted_id}/files", response_model=list[AgentFileResponse])
async def list_agent_files(
    hosted_id: str,
    current_user: CurrentUser,
    svc: HostedAgentService = Depends(get_hosted_agent_service),
):
    """List all files in the agent's workspace."""
    files = await svc.list_files(hosted_id, str(current_user.id))
    return [
        AgentFileResponse(
            id=str(f["id"]),
            file_path=f["file_path"],
            file_type=f["file_type"],
            size_bytes=f["size_bytes"],
            updated_at=str(f["updated_at"]),
        )
        for f in files
    ]


@router.get("/{hosted_id}/files/download")
async def download_files_archive(
    hosted_id: str,
    current_user: CurrentUser,
    svc: HostedAgentService = Depends(get_hosted_agent_service),
):
    """Download all agent files as a zip archive (generated on-the-fly, not stored)."""
    hosted = await svc.get_hosted_agent(hosted_id, str(current_user.id))
    agent_name = hosted.get("agent_name", "agent") or "agent"
    raw_files = await svc.repo.list_files(hosted_id)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in raw_files:
            file_data = await svc.repo.get_file(hosted_id, f["file_path"])
            content = (file_data["content"] if file_data else "") or ""
            zf.writestr(f["file_path"], content.encode("utf-8"))
    buf.seek(0)
    archive_bytes = buf.getvalue()
    return StreamingResponse(
        iter([archive_bytes]),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{agent_name}-workspace.zip"'},
    )


@router.get("/{hosted_id}/files/{file_path:path}", response_model=AgentFileResponse)
async def read_agent_file(
    hosted_id: str,
    file_path: str,
    current_user: CurrentUser,
    svc: HostedAgentService = Depends(get_hosted_agent_service),
):
    """Read a specific file from the agent's workspace."""
    f = await svc.read_file(hosted_id, str(current_user.id), file_path)
    return AgentFileResponse(
        id=str(f["id"]),
        file_path=f["file_path"],
        file_type=f["file_type"],
        content=f["content"],
        size_bytes=f["size_bytes"],
        updated_at=str(f["updated_at"]),
    )


@router.put("/{hosted_id}/files", response_model=AgentFileResponse)
async def write_agent_file(
    hosted_id: str,
    body: AgentFileWriteRequest,
    current_user: CurrentUser,
    svc: HostedAgentService = Depends(get_hosted_agent_service),
):
    """Write or update a file in the agent's workspace."""
    f = await svc.write_file(hosted_id, str(current_user.id), body.file_path, body.content, body.file_type)
    return AgentFileResponse(
        id=str(f["id"]),
        file_path=f["file_path"],
        file_type=f["file_type"],
        content=f["content"],
        size_bytes=f["size_bytes"],
        updated_at=str(f["updated_at"]),
    )


@router.delete("/{hosted_id}/files/{file_path:path}", response_model=AgentActionResponse)
async def delete_agent_file(
    hosted_id: str,
    file_path: str,
    current_user: CurrentUser,
    svc: HostedAgentService = Depends(get_hosted_agent_service),
):
    """Delete a file from the agent's workspace."""
    await svc.delete_file(hosted_id, str(current_user.id), file_path)
    return AgentActionResponse(status="deleted", message=f"File {file_path} deleted")


# ── Cron tasks ──


@router.get("/{hosted_id}/cron", response_model=list[CronTaskResponse])
async def list_cron_tasks(
    hosted_id: str,
    current_user: CurrentUser,
    svc: HostedAgentService = Depends(get_hosted_agent_service),
):
    """List all cron tasks for a hosted agent."""
    tasks = await svc.list_cron_tasks(hosted_id, str(current_user.id))
    return [CronTaskResponse.from_dict(t) for t in tasks]


@router.post("/{hosted_id}/cron", response_model=CronTaskResponse, status_code=201)
async def create_cron_task(
    hosted_id: str,
    body: CronTaskCreateRequest,
    current_user: CurrentUser,
    svc: HostedAgentService = Depends(get_hosted_agent_service),
):
    """Create a scheduled task for a hosted agent."""
    result = await svc.create_cron_task(hosted_id, str(current_user.id), body.model_dump())
    return CronTaskResponse.from_dict(result)


@router.patch("/{hosted_id}/cron/{task_id}", response_model=CronTaskResponse)
async def update_cron_task(
    hosted_id: str,
    task_id: str,
    body: CronTaskUpdateRequest,
    current_user: CurrentUser,
    svc: HostedAgentService = Depends(get_hosted_agent_service),
):
    """Update a cron task."""
    result = await svc.update_cron_task(hosted_id, str(current_user.id), task_id, body.model_dump(exclude_unset=True))
    return CronTaskResponse.from_dict(result)


@router.delete("/{hosted_id}/cron/{task_id}", response_model=AgentActionResponse)
async def delete_cron_task(
    hosted_id: str,
    task_id: str,
    current_user: CurrentUser,
    svc: HostedAgentService = Depends(get_hosted_agent_service),
):
    """Delete a cron task."""
    await svc.delete_cron_task(hosted_id, str(current_user.id), task_id)
    return AgentActionResponse(status="deleted", message="Cron task deleted")
