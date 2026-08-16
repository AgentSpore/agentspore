"""BattleRunTask.run_once: the primary judge model must be picked LIVE.

Narrow by design: everything below reconcile_once (roster building, fallback,
recusal) already has its own suite in test_battle_runner.py. This file proves
only the ONE wiring fact that changed — the primary seat now comes from
pick_live_model(settings.battle_judge_models), not a hardcoded dead default —
without touching DB/Redis, by patching reconcile_once at its call site.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.core.background import BattleRunTask
from app.services.battle_judges import JUDGE_MODEL

pytestmark = pytest.mark.asyncio


def _settings(models: list[str]) -> SimpleNamespace:
    return SimpleNamespace(battle_judge_models=models)


async def _run_with(monkeypatch, *, candidates, live_model, creds):
    """Drive BattleRunTask.run_once with every external dependency stubbed,
    and return the ``provider`` dict it handed to reconcile_once."""
    captured: dict = {}

    async def _fake_reconcile(*, session_factory, gate, provider):
        captured["provider"] = provider
        return {"armed": 0, "queued": 0, "started": 0, "judged": 0, "settled": 0}

    monkeypatch.setattr(
        "app.core.background.get_settings", lambda: _settings(candidates)
    )
    monkeypatch.setattr("app.core.background.get_redis", AsyncMock(return_value=None))
    monkeypatch.setattr(
        "app.core.background.async_session_maker", AsyncMock()
    )
    monkeypatch.setattr(
        "app.services.battle_runner.reconcile_once", _fake_reconcile
    )
    monkeypatch.setattr(
        "app.services.provider_health.pick_live_model",
        AsyncMock(return_value=live_model),
    )
    monkeypatch.setattr(
        "app.services.openrouter_service.OpenRouterService.resolve_provider",
        lambda self, model_id: creds,
    )
    with patch("app.services.battle_budget.breaker_is_open", AsyncMock(return_value=False)):
        await BattleRunTask().run_once()
    return captured["provider"]


async def test_primary_seat_is_the_live_model_not_the_dead_default(monkeypatch):
    """settings lists mistral (dead) first and zai (alive) second; the provider
    handed to reconcile_once must carry the ALIVE model, not JUDGE_MODEL."""
    live_model = "zai/glm-4.5-flash"
    creds = {"api_key": "k", "base_url": "http://zai.example"}

    provider = await _run_with(
        monkeypatch,
        candidates=[JUDGE_MODEL, live_model],
        live_model=live_model,
        creds=creds,
    )

    assert provider is not None
    assert provider["model_id"] == live_model, (
        "the primary seat must be the model pick_live_model actually chose"
    )
    assert provider["api_key"] == "k"
    assert provider["base_url"] == "http://zai.example"


async def test_no_credentials_for_the_live_model_yields_no_provider(monkeypatch):
    """pick_live_model chose a model, but resolve_provider finds no key for it
    (key rotated out from under it mid-pass) — the judging phase must be
    skipped (provider=None), never handed half-built credentials."""
    provider = await _run_with(
        monkeypatch,
        candidates=[JUDGE_MODEL],
        live_model=JUDGE_MODEL,
        creds=None,
    )

    assert provider is None
