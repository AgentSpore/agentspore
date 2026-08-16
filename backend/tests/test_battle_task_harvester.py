"""TaskHarvesterService: turning a source topic into a validated battle task.

No network and no database: sources, the LLM rewrite call, and the validator
call are all doubles here. This proves the harvester's OWN control flow — skip
a dead source, stop at the per-pass cap, reuse the platform-wide dedup check,
insert only what the validator accepted — not the sources or the validator
themselves, which already have their own suites.
"""

from __future__ import annotations

from collections import Counter
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.schemas.battles import TaskStatus
from app.services.battle_task_harvester import (
    DRAFT_LANGUAGES,
    TaskHarvesterService,
    TopicSource,
    _language_for,
)
from app.services.battle_task_validator import (
    REASON_DUPLICATE_CONTENT,
    REASON_PROMPT_TOO_SHORT,
    CheapFilterVerdict,
    ValidationTransportError,
    ValidationVerdict,
)
from app.services.openrouter_service import OpenRouterService

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
    repo.count_pooled_generated_tasks = AsyncMock(return_value=0)
    repo.count_ready_generated_tasks = AsyncMock(return_value=0)
    repo.create_task = AsyncMock(return_value="task-1")
    return repo


@pytest.fixture
def source():
    src = AsyncMock(spec=TopicSource)
    src.name = "github"
    src.fetch_topics = AsyncMock(return_value=[_topic()])
    return src


@pytest.fixture(autouse=True)
def closed_breaker():
    """Default: provider healthy. The open-breaker path is its own test."""
    with patch(
        "app.services.battle_task_harvester.breaker_is_open",
        AsyncMock(return_value=False),
    ):
        yield


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

    async def test_accepted_task_lands_in_quarantine_not_ready(self, harvester, repo):
        """Same status an accepted HUMAN submission gets (battle_service).

        A harvested topic is attacker-influenceable public text and the
        validator's keyword scanner cannot see semantic injection, so READY
        here would be a lighter bar for untrusted input than for a trusted
        submitter — the opposite of what the module claims.
        """
        await harvester.harvest(pool_target=5, max_per_pass=3)

        assert repo.create_task.await_args.kwargs["status"] == TaskStatus.QUARANTINE

    async def test_open_breaker_skips_the_pass(self, harvester, source):
        with patch(
            "app.services.battle_task_harvester.breaker_is_open",
            AsyncMock(return_value=True),
        ):
            result = await harvester.harvest(pool_target=5, max_per_pass=3)

        assert result.created == 0
        source.fetch_topics.assert_not_awaited()

    async def test_validator_transport_failure_drops_only_that_topic(
        self, harvester, repo, source
    ):
        """A provider outage on one topic must not discard the topics after it."""
        source.fetch_topics = AsyncMock(return_value=[_topic("A"), _topic("B")])
        harvester.draft_task = AsyncMock(
            side_effect=[_drafted_task("A"), _drafted_task("B")]
        )
        harvester.run_validator = AsyncMock(
            side_effect=[
                ValidationTransportError("provider 502"),
                (
                    CheapFilterVerdict(passed=True),
                    ValidationVerdict(verdict="accept", reasons=[]),
                ),
            ]
        )

        result = await harvester.harvest(pool_target=5, max_per_pass=3)

        assert result.created == 1
        assert result.dropped == 1

    async def test_skips_when_pool_already_at_target(self, harvester, repo, source):
        repo.count_ready_generated_tasks = AsyncMock(return_value=5)

        result = await harvester.harvest(pool_target=5, max_per_pass=3)

        assert result.created == 0
        source.fetch_topics.assert_not_awaited()

    async def test_drafts_when_ready_short_despite_large_quarantine_backlog(
        self, harvester, repo, source
    ) -> None:
        """A big quarantine backlog must not jam refill: only READY gates it."""
        repo.count_ready_generated_tasks = AsyncMock(return_value=1)
        repo.count_pooled_generated_tasks = AsyncMock(return_value=500)
        harvester.draft_task = AsyncMock(return_value=_drafted_task())
        harvester.run_validator = AsyncMock(
            return_value=(
                CheapFilterVerdict(passed=True),
                ValidationVerdict(verdict="accept", reasons=[]),
            )
        )

        result = await harvester.harvest(pool_target=5, max_per_pass=3)

        assert result.created == 1
        source.fetch_topics.assert_awaited()

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
        """The dedup flag must reach the cheap filters and stop the insert.

        Restoring the real run_validator here (the previous shape of this test)
        proved nothing: with no provider configured it returns
        'no_validation_provider' and created == 0 whether content_key_exists is
        True or False, so deleting the dedup check entirely left it green.
        Asserting on the reason ties the outcome to dedup specifically.
        """
        repo.content_key_exists = AsyncMock(return_value=True)
        harvester.run_validator = TaskHarvesterService.run_validator.__get__(harvester)

        outcome = await harvester._process_topic(_topic())

        assert outcome == "rejected"
        repo.create_task.assert_not_awaited()
        repo.content_key_exists.assert_awaited_once()
        cheap, verdict = await harvester.run_validator(
            _drafted_task(), duplicate_exists=True
        )
        assert not cheap.passed
        assert cheap.reason == REASON_DUPLICATE_CONTENT
        assert verdict is None

    async def test_validator_provider_is_resolved_from_the_model_it_sends(self, harvester):
        """The base_url and the wire model name must come from the SAME id.

        validate_with_llm now sends whichever model pick_live_model resolved,
        so resolving the provider from anything else pairs one provider's
        endpoint with another's model name. That stays silent while both ids
        share a prefix and becomes HTTP 400 'Invalid model' the moment they
        diverge, which is how it reached production originally.
        """
        harvester.run_validator = TaskHarvesterService.run_validator.__get__(harvester)
        seen: list[str] = []
        # The picked id is deliberately NOT VALIDATION_MODEL, which would make
        # this test pass against the broken code too. Forcing them apart is
        # what gives it teeth.
        with (
            patch(
                "app.services.battle_task_harvester.pick_live_model",
                AsyncMock(return_value="other/picked-model"),
            ),
            patch.object(
                OpenRouterService,
                "resolve_provider",
                lambda _self, model: seen.append(model) or None,
            ),
        ):
            cheap, verdict = await harvester.run_validator(
                _drafted_task(), duplicate_exists=False
            )

        assert seen == ["other/picked-model"]
        assert cheap.reason == "no_validation_provider"
        assert verdict is None

    async def test_the_pool_is_not_all_one_language(self):
        """A battle follows its task's language, so a one-language pool is a
        one-language feed. Asserting the SHARE, not just the spread: a test
        that only counts distinct values passes against any weighting, which
        leaves the documented 'English keeps the largest share' unverified."""
        counted = Counter(_language_for(_topic(f"topic {i}")) for i in range(400))

        assert set(counted) == {"English", "Russian"}
        assert counted["English"] > counted["Russian"]
        # Neither language may collapse to a rounding error.
        assert min(counted.values()) > 400 * 0.25

    async def test_only_languages_the_validator_can_screen_are_drafted(self):
        """The deterministic filters in battle_task_validator key on English
        and Russian alternations. A third language would pass both by
        construction, leaving only the LLM verdict between untrusted source
        text and the pool — so the tuple itself is the safety boundary."""
        assert set(DRAFT_LANGUAGES) == {"English", "Russian"}

    async def test_a_topic_always_drafts_into_the_same_language(self):
        """Keyed on the topic, not drawn at random: a rerun after a transport
        failure must not produce a second, differently-worded version."""
        topic = _topic("Race condition in the scheduler")

        assert _language_for(topic) == _language_for(topic)

    async def test_the_drafting_call_names_the_language(self, harvester):
        """The rule is useless if the model is never told which language."""
        topic = _topic("Cache stampede on cold start")
        captured: dict = {}

        class _Resp:
            status_code = 200

            @staticmethod
            def json():
                return {"choices": [{"message": {"content": '{"title": null}'}}]}

        class _Client:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                return False

            async def post(self, _url, **kwargs):
                captured.update(kwargs["json"])
                return _Resp()

        with (
            patch(
                "app.services.battle_task_harvester.pick_live_model",
                AsyncMock(return_value="zai/glm-4.5-flash"),
            ),
            patch.object(
                OpenRouterService,
                "resolve_provider",
                lambda _s, _m: {"base_url": "http://x", "api_key": "k"},
            ),
            patch("app.services.battle_task_harvester.httpx.AsyncClient", _Client),
        ):
            await TaskHarvesterService.draft_task(harvester, topic)

        user_message = captured["messages"][-1]["content"]
        assert f"Language for this task: {_language_for(topic)}" in user_message

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


class TestLedgerNamesTheModelActuallyUsed:
    """The ledger row is the only record of which account was charged."""

    @pytest.mark.asyncio
    async def test_reservation_and_request_use_the_same_picked_model(
        self, harvester, repo, source
    ):
        # zai is dead today, so the pick falls back to mistral. Reserving under
        # the static constant would bill the ledger to zai for a call mistral
        # served — the next outage investigation would read the wrong account.
        picked = "mistral/mistral-small-latest"
        with patch(
            "app.services.battle_task_harvester.pick_live_model",
            AsyncMock(return_value=picked),
        ):
            await harvester.harvest(pool_target=5, max_per_pass=1)

        harvester.reserve_budget.assert_awaited()
        assert harvester.reserve_budget.await_args.args[0] == picked
        assert harvester.draft_task.await_args.args[1] == picked
