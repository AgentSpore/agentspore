"""Team membership grants push access through ``team_repo.is_team_member``.

A team member passes the access check (and reaches the VCS check, which
rejects a non-GitHub project with 400); a non-member is refused with 403.
"""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.services.agent_service import AgentService

CREATOR_ID = str(uuid4())
TEAM_ID = str(uuid4())


def _service() -> AgentService:
    svc = AgentService.__new__(AgentService)
    svc.db = MagicMock()
    svc.repo = MagicMock()
    svc.repo.get_project_basic = AsyncMock(return_value={
        "title": "demo", "repo_url": "https://gitlab.com/x/demo", "vcs_provider": "gitlab",
        "creator_agent_id": CREATOR_ID, "team_id": TEAM_ID,
    })
    return svc


@pytest.mark.asyncio
@pytest.mark.parametrize(("is_member", "status"), [(True, 400), (False, 403)])
async def test_team_membership_decides_push_access(is_member: bool, status: int) -> None:
    agent = {"id": str(uuid4()), "name": "outsider"}
    with patch(
        "app.repositories.team_repo.is_team_member", new=AsyncMock(return_value=is_member),
    ) as check:
        with pytest.raises(HTTPException) as exc:
            await _service().get_project_git_token(uuid4(), agent)
    assert exc.value.status_code == status
    check.assert_awaited_once()
    assert check.await_args.args[1:] == (TEAM_ID, agent["id"])
