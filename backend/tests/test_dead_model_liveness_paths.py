"""Every platform-owned provider call resolves its model by LIVENESS, not by a constant.

A hardcoded model id is a claim about the world that nothing re-checks. Three
releases in one day each removed one more site pointing at an account that had
started answering 402, and each time the symptom was identical: the call raised
permanent and the user-visible flow died with no verdict.

What these tests falsify is the BEHAVIOUR, never the identity of today's model:
each one makes the head of the candidate list dead and asserts the call still
reaches a live provider. Asserting a model id string instead would pass against
the broken code the moment the string was updated, and would go red on a routine
roster edit that broke nothing.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.services import provider_health
from app.services.battle_runner import DEMO_ANSWER_MODEL_CANDIDATES, BattleRunner
from app.services.battle_service import BattleService
from app.services.battle_task_validator import VALIDATION_MODEL_CANDIDATES
from app.services.provider_health import Verdict, pick_live_model

pytestmark = pytest.mark.asyncio


def _probe_verdicts(verdicts: dict[str, Verdict]):
    """Patch the network probe so each candidate answers a scripted verdict."""

    async def _fake_probe(_base_url: str, _api_key: str, model_id: str) -> Verdict:
        return verdicts.get(model_id, Verdict.DEAD)

    return patch("app.services.provider_health._probe", _fake_probe)


def _all_keyed():
    """Every candidate resolves credentials, so the probe decides, not the key."""
    return patch(
        "app.services.provider_health.OpenRouterService.resolve_provider",
        lambda _self, model: {"api_key": "k", "base_url": f"https://{model}.invalid/v1"},
    )


@pytest.fixture(autouse=True)
def _clear_liveness_cache():
    provider_health._cache.clear()
    yield
    provider_health._cache.clear()


class TestCandidateListsAreUsable:
    """A liveness list of one is a hardcoded constant with extra steps."""

    @pytest.mark.parametrize(
        "candidates",
        [DEMO_ANSWER_MODEL_CANDIDATES, VALIDATION_MODEL_CANDIDATES],
        ids=["demo_answer", "validation"],
    )
    async def test_more_than_one_provider_is_offered(self, candidates) -> None:
        """Two ids on ONE account fail together — the fallback must cross providers.

        MUTATION: collapse either list to a single entry, or to two ids sharing a
        provider prefix, and this goes red.
        """
        assert len(candidates) >= 2, "a single candidate cannot degrade"
        providers = {model.split("/")[0] for model in candidates}
        assert len(providers) >= 2, (
            f"all candidates share provider(s) {providers}: one billing failure "
            "kills the whole list"
        )


class TestDeadHeadIsSkipped:
    @pytest.mark.parametrize(
        "candidates",
        [DEMO_ANSWER_MODEL_CANDIDATES, VALIDATION_MODEL_CANDIDATES],
        ids=["demo_answer", "validation"],
    )
    async def test_a_dead_head_falls_through_to_a_live_candidate(self, candidates) -> None:
        """The first candidate answers 402; the pick must be a LATER one.

        This is the exact production failure: the head of the list keeps its
        credentials (so 'has a key' says nothing) and refuses every completion.
        """
        head, *rest = list(candidates)
        verdicts = {head: Verdict.DEAD} | {model: Verdict.ALIVE for model in rest}

        with _all_keyed(), _probe_verdicts(verdicts):
            picked = await pick_live_model(list(candidates))

        assert picked != head, "a dead head must not be picked"
        assert picked in rest


class TestDemoAnswerResolvesByLiveness:
    async def test_demo_answer_model_is_picked_per_call(self) -> None:
        """_generate_demo_answer asks pick_live_model, and the answer call is
        routed to whatever it returned — not to the module constant.

        Only the outward HTTP is mocked. Model resolution runs for real through
        the shared helper.

        MUTATION: hardcode DEMO_ANSWER_MODEL_CANDIDATES[0] as the AnswerSpec
        model and this goes red on the spec.model assertion.
        """
        runner = BattleRunner.__new__(BattleRunner)
        seen: list[str] = []

        async def _capture(_self, _battle, spec):
            seen.append(spec.model)
            return "answer"

        # A picked id deliberately OUTSIDE the candidate list: if the code used
        # the constant instead of the resolved value, seen[0] would still be a
        # candidate and the assertion below would catch it.
        with (
            patch(
                "app.services.battle_runner.pick_live_model",
                AsyncMock(return_value="live/picked-answer-model"),
            ),
            patch.object(BattleRunner, "_answer_with_retry", _capture),
        ):
            answer = await runner._generate_demo_answer({"id": "b"})

        assert answer == "answer"
        assert seen == ["live/picked-answer-model"], (
            "the demo answer must be routed to the model liveness picked"
        )


class TestValidationResolvesByLiveness:
    async def test_submission_validation_resolves_the_provider_from_a_live_pick(
        self,
    ) -> None:
        """battle_service.validate_task must probe liveness before spending the
        submitter's validation call, and must resolve credentials from the SAME
        id it picked — pairing one provider's base_url with another's wire name
        is HTTP 400 'Invalid model'.

        MUTATION: restore the direct `resolve_provider(VALIDATION_MODEL)` call
        and this goes red — `seen` holds the constant, not the picked id.
        """
        service = BattleService.__new__(BattleService)
        service._session_factory = None
        seen: list[str] = []

        with (
            patch("app.services.battle_service.breaker_is_open", AsyncMock(return_value=False)),
            patch(
                "app.services.battle_service.pick_live_model",
                AsyncMock(return_value="live/picked-validation-model"),
            ),
            patch(
                "app.services.battle_service.OpenRouterService.resolve_provider",
                lambda _self, model: seen.append(model) or None,
            ),
        ):
            result = await service.validate_submission(
                task_id="t1",
                user_id="u1",
                title="t",
                prompt="p",
                rubric=[{"criterion": "c", "weight": 1}],
                category="general",
                difficulty="medium",
                time_limit_seconds=600,
            )

        assert seen == ["live/picked-validation-model"]
        # No provider for the picked id -> the submission stays pending, which is
        # the documented soft refusal: an outage is not a verdict about the task.
        assert result["status"] == "pending_validation"
        assert result["reason"] is None
