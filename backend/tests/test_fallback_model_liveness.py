"""The chat fallback resolves by availability, not by a constant.

Fifth site of one defect class. FALLBACK_MODEL was a single hardcoded id that
each time died with its account: nvidia/nemotron (privacy-blocked), then
zai/glm-4.5-flash (429 on every call), then mistral/mistral-small-latest (402
all day, 2026-08-17). Each fix swapped one dead constant for the next, and
`test_fallback_model_is_one_measured_answering` pinned the new id — so the
test that was meant to force a re-measurement instead froze the next corpse.

The fix is the shape the battle paths already use: a CANDIDATE LIST spanning
more than one provider/account, resolved at call time. What differs here is
WHICH resolver runs. `pick_live_model` sends a real `chat/completions` probe,
and `llm_gate.py:35` records a verified invariant that `openrouter_service`
never sends inference — plus `resolve_model` sits on the per-message chat path
(hosted_agent_service._start_agent_internal -> ensure_running), where an 8s
cold probe would be user-visible latency. So this path resolves by CONFIGURED
CREDENTIALS (`resolve_provider`, a pure config lookup, zero network), which is
the strongest signal available without sending inference.

These tests assert BEHAVIOUR, never today's model id: pinning a string is what
let the constant rot three times.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.services.openrouter_service import (
    FALLBACK_MODEL_CANDIDATES,
    OpenRouterService,
    _provider_prefix,
)


class TestCandidateListIsUsable:
    def test_more_than_one_candidate_is_offered(self) -> None:
        """MUTATION: collapse the tuple to one entry and this goes red."""
        assert len(FALLBACK_MODEL_CANDIDATES) >= 2, "a single candidate cannot degrade"

    def test_candidates_span_more_than_one_provider(self) -> None:
        """One account's billing failure must not take the whole list down.

        This is the judge roster's INVARIANT, and the exact reason every
        previous single-constant fallback died: the id and its replacement
        shared an account, so one 402 killed both.
        """
        providers = {_provider_prefix(m) for m in FALLBACK_MODEL_CANDIDATES}
        assert len(providers) >= 2, (
            f"all candidates share provider(s) {providers}: one billing "
            "failure kills the whole list"
        )

    def test_no_candidate_is_self_blocked(self) -> None:
        """A fallback that is itself in BLOCKED_MODELS is broken by construction."""
        for model in FALLBACK_MODEL_CANDIDATES:
            assert model not in OpenRouterService.BLOCKED_MODELS

    def test_every_candidate_routes_to_a_known_provider(self) -> None:
        """A prefix with no EXTRA_PROVIDERS entry resolves to no credentials at
        all, so it could never answer — a dead candidate by construction.
        """
        for model in FALLBACK_MODEL_CANDIDATES:
            prefix = _provider_prefix(model)
            assert prefix in OpenRouterService.EXTRA_PROVIDERS, (
                f"{model}: prefix {prefix!r} is not a configured provider"
            )

    def test_at_least_one_candidate_needs_no_api_key(self) -> None:
        """The last resort must survive a host with NO provider keys set.

        Measured: with no keys configured, zai and mistral resolve to None
        while llm7 resolves keyless. Without such an anchor the fallback chain
        dead-ends on an unconfigured deployment, which is precisely the
        "resolves to a provider we hold no key for" failure recorded in
        test_fallback_model_is_not_self_blocked.

        MUTATION: drop the llm7 entries and this goes red.
        """
        svc = OpenRouterService()
        keyless = [
            m
            for m in FALLBACK_MODEL_CANDIDATES
            if (info := svc.resolve_provider(m)) is not None and not info["api_key"]
        ]
        assert keyless, "no candidate works without configured credentials"


class TestResolveModelSkipsUnavailableCandidates:
    """The head of the list is not trusted just because it is first."""

    @pytest.mark.asyncio
    async def test_a_blocked_model_falls_back_to_an_available_candidate(self) -> None:
        """The real production path: a blocked OpenRouter id is downgraded, and
        the downgrade must land on a candidate that actually has credentials.

        MUTATION: return FALLBACK_MODEL_CANDIDATES[0] unconditionally and this
        goes red — the head resolves to no provider here.
        """
        svc = OpenRouterService()
        blocked = next(iter(OpenRouterService.BLOCKED_MODELS))
        head, *rest = list(FALLBACK_MODEL_CANDIDATES)

        # Head has no credentials; a later candidate does. This is the shape of
        # a dead account: resolve_provider is the only signal available here.
        def _fake_resolve(_self, model: str):
            if model == head:
                return None
            return {"base_url": "https://example.invalid/v1", "api_key": "k"}

        with patch.object(OpenRouterService, "resolve_provider", _fake_resolve):
            resolved = await svc.resolve_model(blocked)

        assert resolved != head, "an unavailable head must not be picked"
        assert resolved in rest

    @pytest.mark.asyncio
    async def test_the_first_available_candidate_wins(self) -> None:
        """Order is a preference ranking, so the earliest usable one is taken."""
        svc = OpenRouterService()
        blocked = next(iter(OpenRouterService.BLOCKED_MODELS))

        with patch.object(
            OpenRouterService,
            "resolve_provider",
            lambda _self, _m: {"base_url": "https://example.invalid/v1", "api_key": "k"},
        ):
            resolved = await svc.resolve_model(blocked)

        assert resolved == FALLBACK_MODEL_CANDIDATES[0]

    @pytest.mark.asyncio
    async def test_nothing_available_still_returns_a_model_id(self) -> None:
        """Degrade like pick_live_model does: return the first candidate so the
        caller makes a real request and produces a real upstream error, rather
        than returning None and failing in some unrelated place later.
        """
        svc = OpenRouterService()
        blocked = next(iter(OpenRouterService.BLOCKED_MODELS))

        with patch.object(OpenRouterService, "resolve_provider", lambda _self, _m: None):
            resolved = await svc.resolve_model(blocked)

        assert resolved == FALLBACK_MODEL_CANDIDATES[0]

    @pytest.mark.asyncio
    async def test_healthy_models_are_never_downgraded(self) -> None:
        """Regression guard: only genuinely blocked OpenRouter ids fall back."""
        svc = OpenRouterService()
        assert await svc.resolve_model("openai/gpt-oss-120b:free") == "openai/gpt-oss-120b:free"
        assert await svc.resolve_model("zai/glm-4.5-flash") == "zai/glm-4.5-flash"


class TestResolveModelSendsNoInference:
    """llm_gate.py:35 records a VERIFIED invariant: openrouter_service resolves
    config and never sends inference. A liveness probe here would be an ungated
    chat/completions POST on the per-message chat path.
    """

    @pytest.mark.asyncio
    async def test_resolving_the_fallback_makes_no_http_call(self) -> None:
        """MUTATION: call pick_live_model from resolve_model and this goes red.

        That is the whole reason this path resolves by credentials instead.
        """
        svc = OpenRouterService()
        blocked = next(iter(OpenRouterService.BLOCKED_MODELS))

        with patch("httpx.AsyncClient.post") as post, patch("httpx.AsyncClient.get") as get:
            await svc.resolve_model(blocked)

        assert not post.called, "resolve_model sent inference — llm_gate invariant broken"
        assert not get.called, "resolve_model made a network call on the chat path"


class TestCatalogueFallbackEntryIsCoherent:
    """get_models' offline entry describes the model it actually names."""

    @pytest.mark.asyncio
    async def test_offline_catalogue_entry_is_self_consistent(self) -> None:
        """The old entry hardcoded name='GLM 4.5 Flash — free', provider='zai'
        beside a mistral id — the label was not updated when the constant moved,
        so the catalogue advertised a model that was not there.

        MUTATION: restore a fixed provider/name string and this goes red.
        """
        svc = OpenRouterService()
        svc._cache = None

        with patch("httpx.AsyncClient.get", side_effect=RuntimeError("catalogue down")):
            models = await svc.get_models()

        offline = [m for m in models if m["id"] in FALLBACK_MODEL_CANDIDATES]
        assert offline, "no fallback entry offered when the catalogue is unreachable"
        for entry in offline:
            assert entry["provider"] == _provider_prefix(entry["id"]), (
                f"entry {entry['id']} claims provider {entry['provider']!r}"
            )
