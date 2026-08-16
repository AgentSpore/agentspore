"""What actually goes out on the wire to the LLM provider.

Both defects covered here were invisible to the rest of the suite because every
other test asserts on the RESULT of a provider call against a stub that accepts
anything. The provider does not: it rejects a prefixed model name with
``400 {"code":"1211","message":"Unknown Model"}`` and a seed above 2**31-1 with
``400 ... Numeric value (...) out of range of int``. So these tests assert on the
REQUEST BODY, and on the body only — a constant can be right while the field
built from it is wrong, which is exactly what happened.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services import battle_runner as battle_runner_module
from app.services import battle_task_validator, openrouter_service
from app.services.battle_judges import (
    JUDGE_HTTP_TIMEOUT_SECONDS,
    JUDGE_MODEL,
    JUDGE_TEMPERATURE,
    JudgeTransportError,
    auth_headers,
    call_judge_model,
    judge_temperature_for,
    replicate_seed,
    seed_field_for,
    seed_int32,
    wire_model_name,
)
from app.services.battle_runner import BattleRunner
from app.services.battle_task_validator import VALIDATION_MODEL

# The provider's signed-int32 ceiling. Anything above it is a 400, not a clamp.
INT32_MAX = 2**31 - 1

# The exact value the provider rejected on a live judging pass. Kept as the raw
# hex a replicate seed carries, so the regression is expressed the way the bug
# arrived rather than as a post-hoc integer.
LIVE_REJECTED_SEED_HEX = "d0368aa3fedcba98"


class _CapturingResponse:
    status_code = 200
    text = "unused"

    @staticmethod
    def json():
        return {"choices": [{"message": {"content": "ok"}}]}


class _CapturingClient:
    """Records the JSON body and headers of the single POST under test."""

    def __init__(self) -> None:
        self.body: dict | None = None
        self.headers: dict | None = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc) -> bool:
        return False

    async def post(self, _url, **kwargs):
        self.body = kwargs["json"]
        self.headers = kwargs.get("headers")
        return _CapturingResponse()


class _OpenGate:
    """A gate that never blocks: concurrency is not what these tests measure."""

    def slot(self):
        return _OpenGate._Slot()

    class _Slot:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc) -> bool:
            return False


@pytest.fixture
def capturing_client() -> _CapturingClient:
    return _CapturingClient()


# -- the model field ---------------------------------------------------------


def test_the_platform_model_id_is_prefixed_and_its_wire_name_is_not():
    """The premise of both fixes below, asserted rather than assumed.

    If the platform ids ever lose their provider prefix, the assertions further
    down would still pass while proving nothing.
    """
    assert "/" in VALIDATION_MODEL
    assert "/" in JUDGE_MODEL
    assert "/" not in wire_model_name(VALIDATION_MODEL)
    assert "/" not in wire_model_name(JUDGE_MODEL)


def test_wire_model_name_strips_only_the_platform_prefix_from_static_models():
    """wire_model_name splits on the LAST '/' (rsplit('/', 1)) — the provider
    prefix is everything BEFORE that split point. Asserted over the actual
    llm7 static_models (openrouter_service.py), none of which carry a colon:
    gpt-oss:20b was deliberately excluded from that list (finish_reason=
    'length', empty content at the judging token cap) and must not be pinned
    here as if it were a live id."""
    for model_id in openrouter_service.OpenRouterService.EXTRA_PROVIDERS["llm7"][
        "static_models"
    ]:
        platform_id = f"llm7/{model_id}"
        assert wire_model_name(platform_id) == model_id
        assert "/" not in wire_model_name(platform_id)


@pytest.mark.asyncio
async def test_validator_sends_the_wire_name_not_the_platform_id(
    monkeypatch, capturing_client
):
    """The 1211 regression: ``model`` on the request carries no provider prefix."""
    monkeypatch.setattr(
        battle_task_validator.httpx,
        "AsyncClient",
        lambda *a, **k: capturing_client,
    )
    await battle_task_validator.call_validation_model(
        base_url="https://stub.invalid/v1", api_key="unused", messages=[]
    )

    assert capturing_client.body is not None
    sent = capturing_client.body["model"]
    assert sent == wire_model_name(VALIDATION_MODEL)
    assert "/" not in sent
    assert sent != VALIDATION_MODEL


@pytest.mark.asyncio
async def test_validator_sends_no_authorization_header_for_a_blank_key(
    monkeypatch, capturing_client
):
    """Same call site, same bug class: call_validation_model builds its own
    headers dict, so it needed the same auth_headers fix independently."""
    monkeypatch.setattr(
        battle_task_validator.httpx,
        "AsyncClient",
        lambda *a, **k: capturing_client,
    )
    await battle_task_validator.call_validation_model(
        base_url="https://api.llm7.io/v1", api_key="", messages=[]
    )
    assert "Authorization" not in (capturing_client.headers or {})


def test_the_stored_verdict_keeps_the_platform_id():
    """Storage and the ledger keep the PREFIXED id — only the wire is stripped.

    The two representations must not collapse into one: the ledger's ``model``
    column and the verdict document identify which provider's model produced a
    decision, and a bare ``glm-4.5-flash`` no longer says that.
    """
    verdict = battle_task_validator.ValidationVerdict(
        verdict=battle_task_validator.VERDICT_ACCEPT, reasons=[]
    )
    assert verdict.as_document()["model"] == VALIDATION_MODEL


@pytest.mark.asyncio
async def test_judge_sends_the_wire_name_it_was_given(capturing_client):
    """The judge takes its wire name from the roster and sends it verbatim."""
    await call_judge_model(
        client=capturing_client,
        base_url="https://stub.invalid/v1",
        api_key="unused",
        messages=[],
        seed=replicate_seed("battle-1", 0),
        gate=_OpenGate(),
        wire_model="glm-4.5-flash",
    )
    assert capturing_client.body["model"] == "glm-4.5-flash"


# -- the roster that supplies the wire name ----------------------------------


@pytest.fixture
def runner() -> BattleRunner:
    """A runner built only far enough to resolve a roster.

    ``_resolve_judge_roster`` reads config and credentials, never the session:
    passing None keeps this a pure-function test instead of dragging in a
    database for a decision the database has no part in.
    """
    return BattleRunner(None, gate=None)


def test_roster_primary_carries_a_platform_id_and_a_bare_wire_name(runner):
    """Asserted on the BUILT roster entry, not on the constant it derives from.

    This is the seam the 1211 came through: ``JudgeModel`` documents the two
    fields as different things, and the roster used to fill both with the same
    prefixed id — so the type looked correct while the request was not.

    Only the primary (roster[0]) is asserted on: since llm7 needs no key,
    settings.battle_judge_models now resolves several extra entries in this
    real-service test, not just the primary.
    """
    primary = runner._resolve_judge_roster("https://stub.invalid/v1", "unused")[0]

    assert "/" in primary.model_id
    assert primary.model_id == JUDGE_MODEL
    assert "/" not in primary.wire_model
    assert primary.wire_model == wire_model_name(JUDGE_MODEL)
    assert primary.provider == JUDGE_MODEL.split("/", 1)[0]


def test_every_extra_roster_entry_is_stripped_too(monkeypatch, runner):
    """The extra-roster path carries the same mine, so it is covered too.

    Four ids resolve in production, one per reachable provider, so this
    constructor is live rather than dormant. The resolver is stubbed here for
    hermeticity — the test must not depend on which provider keys the
    environment happens to hold — and an untested constructor is exactly where
    the prefixed id survived the first time.
    """
    extra = "openrouter/some-vendor/some-model"

    monkeypatch.setattr(
        battle_runner_module,
        "get_settings",
        lambda: SimpleNamespace(battle_judge_models=[JUDGE_MODEL, extra]),
    )

    class _StubService:
        # The real service's provider table, because the roster now reads the
        # per-provider seed field off it (seed_field_for). A double that omits
        # it is a double of an interface that no longer exists.
        EXTRA_PROVIDERS = openrouter_service.OpenRouterService.EXTRA_PROVIDERS

        @staticmethod
        def resolve_provider(_model_id):
            return {"base_url": "https://other.invalid/v1", "api_key": "unused"}

    monkeypatch.setattr(openrouter_service, "OpenRouterService", _StubService)

    roster = runner._resolve_judge_roster("https://stub.invalid/v1", "unused")

    assert len(roster) == 2
    assert [m.model_id for m in roster] == [JUDGE_MODEL, extra]
    for model in roster:
        assert "/" not in model.wire_model, model.model_id
    # A multi-segment id keeps only its LAST segment: the provider names the
    # model, everything before it is platform routing.
    assert roster[1].wire_model == "some-model"


# -- the moonshot judge provider (kimi-k3) -----------------------------------

MOONSHOT_MODEL = "moonshot/kimi-k3"


def test_moonshot_resolves_kimi_to_its_own_base_url_and_key(monkeypatch):
    """kimi-k3 is the second reachable judge model: it must prefix-route to the
    Moonshot endpoint with the moonshot key, exactly like zai does with its own."""
    monkeypatch.setattr(
        openrouter_service,
        "get_settings",
        lambda: SimpleNamespace(moonshot_api_key="sk-moonshot-test"),
    )
    creds = openrouter_service.OpenRouterService().resolve_provider(MOONSHOT_MODEL)
    assert creds is not None
    assert creds["base_url"] == "https://api.moonshot.ai/v1"
    assert creds["api_key"] == "sk-moonshot-test"


def test_moonshot_is_unresolved_without_a_key(monkeypatch):
    """No key -> the roster builder drops kimi and the panel stays single-model,
    never a JudgeModel with an empty api_key."""
    monkeypatch.setattr(
        openrouter_service,
        "get_settings",
        lambda: SimpleNamespace(moonshot_api_key=""),
    )
    assert openrouter_service.OpenRouterService().resolve_provider(MOONSHOT_MODEL) is None


def test_kimis_wire_name_drops_the_provider_prefix():
    """The provider takes ``kimi-k3``, not the platform id ``moonshot/kimi-k3`` —
    the same 1211 mine the zai path already documents."""
    assert wire_model_name(MOONSHOT_MODEL) == "kimi-k3"
    assert "/" not in wire_model_name(MOONSHOT_MODEL)


# -- per-model judge temperature ---------------------------------------------


GLM_MODEL = "zai/glm-4.5-flash"


def test_kimi_overrides_to_one_glm_keeps_the_default():
    """kimi-k3 was measured to only parse at temperature 1.0 and stays pinned in
    JUDGE_MODEL_TEMPERATURE_OVERRIDES even though it is no longer the primary
    judge (moonshot is suspended); a model absent from the override table, like
    glm, falls through to JUDGE_TEMPERATURE."""
    assert judge_temperature_for(MOONSHOT_MODEL) == 1.0
    assert judge_temperature_for(GLM_MODEL) == JUDGE_TEMPERATURE == 0.7


def test_the_roster_carries_each_models_temperature(monkeypatch, runner):
    """The roster builder stamps the per-model temperature onto every JudgeModel
    via judge_temperature_for, so an overridden model (kimi, at 1.0) and a
    default-temperature model (JUDGE_MODEL itself, at 0.7) both come out right
    without any per-call branching."""
    monkeypatch.setattr(
        battle_runner_module,
        "get_settings",
        lambda: SimpleNamespace(battle_judge_models=[JUDGE_MODEL, MOONSHOT_MODEL]),
    )

    class _StubService:
        # The real service's provider table, because the roster now reads the
        # per-provider seed field off it (seed_field_for). A double that omits
        # it is a double of an interface that no longer exists.
        EXTRA_PROVIDERS = openrouter_service.OpenRouterService.EXTRA_PROVIDERS

        @staticmethod
        def resolve_provider(_model_id):
            return {"base_url": "https://moonshot.invalid/v1", "api_key": "unused"}

    monkeypatch.setattr(openrouter_service, "OpenRouterService", _StubService)

    roster = runner._resolve_judge_roster("https://stub.invalid/v1", "unused")
    by_id = {m.model_id: m for m in roster}
    assert by_id[JUDGE_MODEL].temperature == judge_temperature_for(JUDGE_MODEL) == 0.7
    assert by_id[MOONSHOT_MODEL].temperature == 1.0


@pytest.mark.asyncio
async def test_call_judge_model_sends_the_temperature_it_was_given(capturing_client):
    """The temperature on the request body is the model's, not a hardcoded 0.7."""
    await call_judge_model(
        client=capturing_client,
        base_url="https://stub.invalid/v1",
        api_key="unused",
        messages=[],
        seed=replicate_seed("battle-1", 0),
        gate=_OpenGate(),
        wire_model="kimi-k3",
        temperature=1.0,
    )
    assert capturing_client.body["temperature"] == 1.0


# -- the seed field ----------------------------------------------------------


@pytest.mark.parametrize(
    "seed_hex",
    ["ffffffff", "80000000", "7fffffff", "00000000", "d0368aa3"],
    ids=["all_ones", "sign_bit_only", "max_int32", "zero", "live_failure"],
)
def test_seed_int32_stays_inside_the_provider_range(seed_hex):
    value = seed_int32(seed_hex)
    assert 0 <= value <= INT32_MAX


def test_the_seed_the_provider_rejected_is_now_in_range():
    """Regression for the live 400: ``Numeric value (3493235363) out of range``."""
    assert int(LIVE_REJECTED_SEED_HEX[:8], 16) == 3493235363
    assert seed_int32(LIVE_REJECTED_SEED_HEX) == 1345751715
    assert seed_int32(LIVE_REJECTED_SEED_HEX) <= INT32_MAX


def test_seed_is_deterministic_across_recomputation():
    """A restarted reconciler must land on the SAME provider seed, not a new one."""
    seed = replicate_seed("battle-42", 2)
    assert replicate_seed("battle-42", 2) == seed
    assert seed_int32(seed) == seed_int32(seed)


def test_replicates_of_one_battle_get_distinct_seeds():
    """Masking must not fold the three replicates onto one provider seed."""
    values = [seed_int32(replicate_seed("battle-42", n)) for n in range(3)]
    assert len(set(values)) == 3


@pytest.mark.asyncio
async def test_judge_request_carries_an_int32_seed(capturing_client):
    """End of the chain: the value in the BODY is what the provider parses."""
    await call_judge_model(
        client=capturing_client,
        base_url="https://stub.invalid/v1",
        api_key="unused",
        messages=[],
        seed=LIVE_REJECTED_SEED_HEX,
        gate=_OpenGate(),
        wire_model="glm-4.5-flash",
    )
    sent = capturing_client.body["seed"]
    assert isinstance(sent, int)
    assert 0 <= sent <= INT32_MAX


# -- the seed field ----------------------------------------------------------
#
# The third wire defect of the same family, and the most expensive: the seed key
# was hardcoded to the OpenAI name with a comment saying it was "passed in case
# the provider honours it". Mistral does not ignore an unknown body field — it
# answers 422 `extra_forbidden` and produces nothing, so all three Mistral
# contenders lost every single request while the rest of the suite stayed green
# on stubs that accept any body.


class _MistralShapedResponse:
    """Mistral's real answer to an unknown body field, reduced to its shape."""

    status_code = 422
    text = (
        '{"object":"error","message":{"detail":[{"type":"extra_forbidden",'
        '"loc":["body","seed"],"msg":"Extra inputs are not permitted"}]}}'
    )

    @staticmethod
    def json():  # pragma: no cover - never reached on a 422
        raise AssertionError("a 422 body is never parsed for content")


class _StrictMistralClient(_CapturingClient):
    """Accepts `random_seed`, rejects `seed` — exactly as the live API does."""

    async def post(self, _url, **kwargs):
        self.body = kwargs["json"]
        if "seed" in self.body:
            return _MistralShapedResponse()
        return _CapturingResponse()


def test_the_seed_field_is_resolved_per_provider():
    """Mistral's key is `random_seed`; everyone else keeps the OpenAI name.

    JUDGE_MODEL is itself a mistral model now, so it is asserted alongside its
    siblings rather than as the "everyone else" example — GLM (zai) plays that
    role instead.
    """
    assert seed_field_for("mistral/mistral-small-latest") == "random_seed"
    assert seed_field_for("mistral/mistral-medium-2508") == "random_seed"
    assert seed_field_for(JUDGE_MODEL) == "random_seed"
    assert seed_field_for("zai/glm-4.5-flash") == "seed"


@pytest.mark.asyncio
async def test_a_mistral_call_sends_random_seed_and_never_seed(capturing_client):
    """The body key follows the provider, and the wrong key is ABSENT.

    Asserting the absence matters as much as the presence: sending both would
    still be a 422, and a test that only checked for `random_seed` would pass.
    """
    await call_judge_model(
        client=capturing_client,
        base_url="https://stub.invalid/v1",
        api_key="unused",
        messages=[],
        seed=replicate_seed("battle-1", 0),
        gate=_OpenGate(),
        wire_model="mistral-small-latest",
        seed_field=seed_field_for("mistral/mistral-small-latest"),
    )
    assert capturing_client.body["random_seed"] == seed_int32(replicate_seed("battle-1", 0))
    assert "seed" not in capturing_client.body


@pytest.mark.asyncio
async def test_a_live_shaped_mistral_endpoint_accepts_the_call():
    """The regression itself: against a client that enforces Mistral's rule, the
    call succeeds — and fails loudly with the old hardcoded key.

    MUTATION: put `"seed": seed_int32(seed)` back in the body unconditionally.
    This raises JudgeTransportError carrying the verbatim 422.
    """
    client = _StrictMistralClient()
    answer = await call_judge_model(
        client=client,
        base_url="https://stub.invalid/v1",
        api_key="unused",
        messages=[],
        seed=replicate_seed("battle-1", 0),
        gate=_OpenGate(),
        wire_model="mistral-small-latest",
        seed_field=seed_field_for("mistral/mistral-small-latest"),
    )
    assert answer == "ok"


@pytest.mark.asyncio
async def test_a_provider_that_takes_no_seed_gets_no_seed_field(capturing_client):
    """`seed_field=None` omits the field entirely rather than sending a null."""
    await call_judge_model(
        client=capturing_client,
        base_url="https://stub.invalid/v1",
        api_key="unused",
        messages=[],
        seed=replicate_seed("battle-1", 0),
        gate=_OpenGate(),
        wire_model="whatever",
        seed_field=None,
    )
    assert "seed" not in capturing_client.body
    assert "random_seed" not in capturing_client.body


@pytest.mark.asyncio
async def test_the_judge_roster_carries_each_model_s_seed_field(runner):
    """The panel is on the same code path, so it inherits the same fix.

    The roster (settings.battle_judge_models) now carries five mistral entries
    plus zai/glm-4.5-flash, so this exercises the real production mix: every
    mistral entry must resolve `random_seed`, glm must resolve `seed`.
    """
    roster = runner._resolve_judge_roster("https://stub.invalid/v1", "unused")
    assert roster, "the roster must resolve at least the primary"
    for entry in roster:
        assert entry.seed_field == seed_field_for(entry.model_id)


# -- the account the call is gated on ----------------------------------------


class _RecordingGate(_OpenGate):
    """Records which provider the call scoped the gate to."""

    def __init__(self) -> None:
        self.scoped_to: list[str] = []
        self.leases: list[int | None] = []

    def for_provider(self, provider: str, lease_seconds: int | None = None):
        self.scoped_to.append(provider)
        self.leases.append(lease_seconds)
        return self


@pytest.mark.asyncio
async def test_the_call_is_gated_on_its_own_provider_account(capturing_client):
    """Each provider is a separate account, so each gets its own gate.

    MUTATION: drop the `gate.for_provider(provider)` scoping in call_judge_model.
    `scoped_to` stays empty and this goes red — which is the state that made a
    Mistral call fail with `no slot on llm_gate:zai:platform`.
    """
    gate = _RecordingGate()
    await call_judge_model(
        client=capturing_client,
        base_url="https://stub.invalid/v1",
        api_key="unused",
        messages=[],
        seed=replicate_seed("battle-1", 0),
        gate=gate,
        wire_model="mistral-small-latest",
        provider="mistral",
    )
    assert gate.scoped_to == ["mistral"]
    # The lease must outlast this call's HTTP timeout, or the reaper frees a live
    # call's slot and the account goes over its cap.
    assert gate.leases[0] > JUDGE_HTTP_TIMEOUT_SECONDS


@pytest.mark.asyncio
async def test_a_call_with_no_provider_keeps_the_gate_it_was_given(capturing_client):
    """Back-compat: an unscoped caller is not silently re-keyed."""
    gate = _RecordingGate()
    await call_judge_model(
        client=capturing_client,
        base_url="https://stub.invalid/v1",
        api_key="unused",
        messages=[],
        seed=replicate_seed("battle-1", 0),
        gate=gate,
        wire_model="glm-4.5-flash",
    )
    assert gate.scoped_to == []


# -- an HTTP-200 body that is actually a rate-limit error (llm7) -------------
#
# llm7's keyless rate limit (~1 req/8s) returns HTTP 200 with an error-shaped
# JSON body instead of a 429: {"error": {"message": "Rate limit exceeded. Retry
# after 1 seconds."}}. call_judge_model's 200 branch reads
# response.json()["choices"][0]["message"]["content"] unconditionally, so this
# body raises an unhandled KeyError instead of JudgeTransportError — the one
# exception type every caller (battle_runner's fallback loop, the reclaim loop)
# knows how to retry. An uncaught KeyError crashes the judge run instead of
# being treated as retryable.


class _RateLimitedShapedResponse:
    """llm7's real reply to a burst, reduced to its shape: HTTP 200, error body."""

    status_code = 200
    text = '{"error":{"message":"Rate limit exceeded. Retry after 1 seconds."}}'

    @staticmethod
    def json():
        return {"error": {"message": "Rate limit exceeded. Retry after 1 seconds."}}


class _RateLimitedClient(_CapturingClient):
    async def post(self, _url, **kwargs):
        self.body = kwargs["json"]
        return _RateLimitedShapedResponse()


@pytest.mark.asyncio
async def test_a_200_shaped_rate_limit_error_is_retryable_not_a_crash():
    """MUTATION: read content unconditionally on status_code == 200 (no 'error'
    key check). This test raises KeyError instead of failing an assertion —
    itself proof the current code has no seam to catch this shape.
    """
    with pytest.raises(JudgeTransportError) as exc_info:
        await call_judge_model(
            client=_RateLimitedClient(),
            base_url="https://stub.invalid/v1",
            api_key="",
            messages=[],
            seed=replicate_seed("battle-1", 0),
            gate=_OpenGate(),
            wire_model="DeepSeek-V4-Flash-0731",
        )
    assert exc_info.value.permanent is False
    assert "Rate limit exceeded" in str(exc_info.value)


class _BenignErrorKeyResponse:
    """A REAL completion that also happens to carry a benign 'error': null/{}
    alongside real choices — must NOT be discarded as transient (review
    finding 5: the guard must key on absence of a completion, not presence of
    an 'error' key)."""

    status_code = 200
    text = "unused"

    @staticmethod
    def json():
        return {
            # Non-null but empty — a provider that always includes this key.
            # The old guard (`error is not None`) would discard this as
            # transient even though a real completion sits right next to it.
            "error": {},
            "choices": [{"message": {"content": "a real answer"}}],
        }


class _BenignErrorClient(_CapturingClient):
    async def post(self, _url, **kwargs):
        self.body = kwargs["json"]
        return _BenignErrorKeyResponse()


@pytest.mark.asyncio
async def test_a_benign_error_key_alongside_real_choices_is_not_discarded():
    """MUTATION: guard on `payload.get("error") is not None` (the old,
    over-broad check) instead of gating on the ABSENCE of choices. Against
    this payload — a non-null empty "error" beside a real completion — the
    old guard fires and discards a valid answer as transient; the positive
    signal (`not payload.get("choices")`) does not.
    """
    answer = await call_judge_model(
        client=_BenignErrorClient(),
        base_url="https://stub.invalid/v1",
        api_key="",
        messages=[],
        seed=replicate_seed("battle-1", 0),
        gate=_OpenGate(),
        wire_model="DeepSeek-V4-Flash-0731",
    )
    assert answer == "a real answer"


class _PermanentErrorShapedResponse:
    """A 200-shaped body carrying a genuine balance/auth failure, not a rate
    limit — must be classified permanent so it is never retried forever."""

    status_code = 200
    text = "unused"

    @staticmethod
    def json():
        return {"error": {"message": "Insufficient balance", "code": "1113"}}


class _PermanentErrorClient(_CapturingClient):
    async def post(self, _url, **kwargs):
        self.body = kwargs["json"]
        return _PermanentErrorShapedResponse()


@pytest.mark.asyncio
async def test_a_permanent_error_in_a_200_body_is_never_retried():
    with pytest.raises(JudgeTransportError) as exc_info:
        await call_judge_model(
            client=_PermanentErrorClient(),
            base_url="https://stub.invalid/v1",
            api_key="",
            messages=[],
            seed=replicate_seed("battle-1", 0),
            gate=_OpenGate(),
            wire_model="DeepSeek-V4-Flash-0731",
        )
    assert exc_info.value.permanent is True


# -- a keyless provider must send NO Authorization header at all -------------
#
# api_key="" produced `Bearer ` — h11/httpx REJECT that as an illegal header
# value before any network I/O, so the request never left the process
# (production: every llm7 battle voided "provider unreachable"). Asserting
# `api_key == ""` would pass on the broken code; the only real check is the
# HEADERS DICT itself.


def test_auth_headers_omits_the_key_entirely_when_blank():
    """The unit-level version of the same guard: {} not {"Authorization": "Bearer "}."""
    assert auth_headers("") == {}
    assert auth_headers("real-key") == {"Authorization": "Bearer real-key"}


@pytest.mark.asyncio
async def test_keyless_provider_sends_no_authorization_header(capturing_client):
    await call_judge_model(
        client=capturing_client,
        base_url="https://api.llm7.io/v1",
        api_key="",
        messages=[],
        seed=replicate_seed("battle-1", 0),
        gate=_OpenGate(),
        wire_model="DeepSeek-V4-Flash-0731",
    )
    assert "Authorization" not in (capturing_client.headers or {})


@pytest.mark.asyncio
async def test_a_real_key_still_sends_the_bearer_header(capturing_client):
    await call_judge_model(
        client=capturing_client,
        base_url="https://api.z.ai/api/paas/v4",
        api_key="zai-secret",
        messages=[],
        seed=replicate_seed("battle-1", 0),
        gate=_OpenGate(),
        wire_model="glm-4.5-flash",
    )
    assert capturing_client.headers == {"Authorization": "Bearer zai-secret"}
