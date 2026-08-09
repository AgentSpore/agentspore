"""TaskHarvesterService: turning a source topic into a validated battle task.

No network and no database: sources, the LLM rewrite call, and the validator
call are all doubles here. This proves the harvester's OWN control flow — skip
a dead source, stop at the per-pass cap, reuse the platform-wide dedup check,
insert only what the validator accepted — not the sources or the validator
themselves, which already have their own suites.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.battle_task_harvester import TaskHarvesterService, TopicSource
from app.services.battle_task_validator import (
    REASON_PROMPT_TOO_SHORT,
    CheapFilterVerdict,
    ValidationVerdict,
)

GOOD_RUBRIC = [
    {"key": "correctness", "description": "The answer is correct.", "weight": 1.0},
    {"key": "completeness", "description": "Nothing required is missing.", "weight": 1.0},
    {"key": "clarity", "description": "The answer is easy to follow.", "weight": 0.5},
]


def _topic(title: str = "Off-by-one in the paginator") -> dict:
    return {"title": title, "summary": "Page 2 skips the first row of page 1."}


def _drafted_task(title: str = "Fix an off-by-one paginator bug") -> dict:
    return {
        "title": title,
        "prompt": "x" * 200,
        "rubric": GOOD_RUBRIC,
        "category": "backend",
        "difficulty": "medium",
        "time_limit_seconds": 600,
    }


@pytest.fixture
def repo():
    repo = AsyncMock()
    repo.content_key_exists = AsyncMock(return_value=False)
    repo.count_ready_generated_tasks = AsyncMock(return_value=0)
    repo.create_task = AsyncMock(return_value="task-1")
    return repo


@pytest.fixture
def source():
    src = AsyncMock(spec=TopicSource)
    src.name = "github"
    src.fetch_topics = AsyncMock(return_value=[_topic()])
    return src


@pytest.fixture
def harvester(repo, source):
    service = TaskHarvesterService(
        repo=repo,
        sources=[source],
        session_factory=AsyncMock(),
    )
    service.draft_task = AsyncMock(return_value=_drafted_task())
    service.reserve_budget = AsyncMock(
        return_value=SimpleNamespace(granted=True, ledger_id="ledger-1")
    )
    service.settle_budget = AsyncMock()
    service.run_validator = AsyncMock(
        return_value=(
            CheapFilterVerdict(passed=True),
            ValidationVerdict(verdict="accept", reasons=[]),
        )
    )
    return service


class TestHarvestPass:
    async def test_inserts_an_accepted_task(self, harvester, repo):
        result = await harvester.harvest(pool_target=5, max_per_pass=3)

        assert result.created == 1
        repo.create_task.assert_awaited_once()

    async def test_skips_when_pool_already_at_target(self, harvester, repo, source):
        repo.count_ready_generated_tasks = AsyncMock(return_value=5)

        result = await harvester.harvest(pool_target=5, max_per_pass=3)

        assert result.created == 0
        source.fetch_topics.assert_not_awaited()

    async def test_stops_at_max_per_pass(self, harvester, repo, source):
        source.fetch_topics = AsyncMock(
            return_value=[_topic("A"), _topic("B"), _topic("C")]
        )
        harvester.draft_task = AsyncMock(
            side_effect=[_drafted_task("A"), _drafted_task("B"), _drafted_task("C")]
        )

        result = await harvester.harvest(pool_target=100, max_per_pass=2)

        assert result.created == 2
        assert repo.create_task.await_count == 2

    async def test_dead_source_is_skipped_not_fatal(self, harvester, repo, source):
        source.fetch_topics = AsyncMock(side_effect=RuntimeError("github is down"))

        result = await harvester.harvest(pool_target=5, max_per_pass=3)

        assert result.created == 0
        assert result.source_failures == 1

    async def test_duplicate_content_is_dropped_not_inserted(self, harvester, repo):
        repo.content_key_exists = AsyncMock(return_value=True)
        harvester.run_validator = TaskHarvesterService.run_validator.__get__(harvester)

        result = await harvester.harvest(pool_target=5, max_per_pass=3)

        assert result.created == 0
        repo.create_task.assert_not_awaited()

    async def test_validator_rejection_is_dropped_not_inserted(self, harvester, repo):
        harvester.run_validator = AsyncMock(
            return_value=(
                CheapFilterVerdict(passed=False, reason=REASON_PROMPT_TOO_SHORT),
                None,
            )
        )

        result = await harvester.harvest(pool_target=5, max_per_pass=3)

        assert result.created == 0
        repo.create_task.assert_not_awaited()

    async def test_budget_refusal_stops_the_pass(self, harvester, repo, source):
        source.fetch_topics = AsyncMock(return_value=[_topic("A"), _topic("B")])
        harvester.reserve_budget = AsyncMock(
            return_value=SimpleNamespace(granted=False, ledger_id=None)
        )

        result = await harvester.harvest(pool_target=5, max_per_pass=3)

        assert result.created == 0
        harvester.draft_task.assert_not_awaited()

    async def test_budget_refusal_midway_abandons_the_remaining_topics(
        self, harvester, repo, source
    ):
        """A refusal on topic B must not let topic C ask for the same budget.

        The daily cap is global, so once it refuses, every later reservation in
        this pass is refused too. A test that refuses from the FIRST topic (the
        one above) cannot tell "stopped" apart from "spun through every topic
        anyway" — both end with created == 0.
        """
        source.fetch_topics = AsyncMock(
            return_value=[_topic("A"), _topic("B"), _topic("C")]
        )
        harvester.draft_task = AsyncMock(
            side_effect=[_drafted_task("A"), _drafted_task("B"), _drafted_task("C")]
        )
        harvester.reserve_budget = AsyncMock(
            side_effect=[
                SimpleNamespace(granted=True, ledger_id="ledger-1"),
                SimpleNamespace(granted=False, ledger_id=None),
            ]
        )

        result = await harvester.harvest(pool_target=100, max_per_pass=10)

        assert result.created == 1
        assert result.budget_exhausted is True
        # Two reservations attempted, not three: C was never reached. A third
        # call would also exhaust the side_effect list and raise StopIteration.
        assert harvester.reserve_budget.await_count == 2
        assert harvester.draft_task.await_count == 1
