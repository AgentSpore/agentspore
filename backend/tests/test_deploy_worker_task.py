"""Kill switch and registration of the worker loop."""

from unittest.mock import AsyncMock

import pytest

from app.core import background
from app.core.config import get_settings


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled,calls", [(False, 0), (True, 1)])
async def test_flag_gates_every_cycle(monkeypatch, enabled, calls):
    monkeypatch.setattr(get_settings(), "a4_deploy_enabled", enabled)
    worker = AsyncMock()
    worker.run_once.return_value = False
    await background.DeploymentWorkerTask(worker).run_once()
    assert worker.run_once.await_count == calls


@pytest.mark.asyncio
async def test_cycle_drains_queue_but_stops_when_flag_flips(monkeypatch):
    monkeypatch.setattr(get_settings(), "a4_deploy_enabled", True)
    worker = AsyncMock()

    async def step():
        if worker.run_once.await_count == 2:
            monkeypatch.setattr(get_settings(), "a4_deploy_enabled", False)
        return True

    worker.run_once.side_effect = step
    await background.DeploymentWorkerTask(worker).run_once()
    assert worker.run_once.await_count == 2


def test_worker_loop_is_not_in_api_process_registry():
    assert background.DeploymentWorkerTask not in background.ALL_TASKS
