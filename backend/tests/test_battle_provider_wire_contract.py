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
    JUDGE_MODEL,
    JUDGE_TEMPERATURE,
    call_judge_model,
    judge_temperature_for,
    replicate_seed,
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
    """Records the JSON body of the single POST the code under test makes."""

    def __init__(self) -> None:
        self.body: dict | None = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc) -> bool:
        return False

    async def post(self, _url, **kwargs):
        self.body = kwargs["json"]
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
    """
    (primary,) = runner._resolve_judge_roster("https://stub.invalid/v1", "unused")

    assert "/" in primary.model_id
    assert primary.model_id == JUDGE_MODEL
    assert "/" not in primary.wire_model
    assert primary.wire_model == wire_model_name(JUDGE_MODEL)
    assert primary.provider == JUDGE_MODEL.split("/", 1)[0] == "moonshot"


def test_every_extra_roster_entry_is_stripped_too(monkeypatch, runner):
    """The dormant multi-provider path carries the same mine, so it is covered.

    An extra id only enters the roster when a key resolves for it, which never
    happens today (RU-ASN geo-block). Stubbing the resolver is the only way to
    reach the second constructor at all — and an untested constructor is exactly
    where the prefixed id survived the first time.
    """
    extra = "openrouter/some-vendor/some-model"

    monkeypatch.setattr(
        battle_runner_module,
        "get_settings",
        lambda: SimpleNamespace(battle_judge_models=[JUDGE_MODEL, extra]),
    )

    class _StubService:
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


def test_kimi_the_primary_overrides_to_one_glm_keeps_the_default():
    """kimi-k3 (now the PRIMARY judge, JUDGE_MODEL) was measured to only parse at
    temperature 1.0; the glm second model stays at the 0.7 default."""
    assert JUDGE_MODEL == MOONSHOT_MODEL
    assert judge_temperature_for(JUDGE_MODEL) == 1.0
    assert judge_temperature_for(GLM_MODEL) == JUDGE_TEMPERATURE == 0.7


def test_the_roster_carries_each_models_temperature(monkeypatch, runner):
    """The roster builder stamps the per-model temperature onto every JudgeModel,
    so kimi (primary) is called at 1.0 and glm at 0.7 without any per-call
    branching."""
    monkeypatch.setattr(
        battle_runner_module,
        "get_settings",
        lambda: SimpleNamespace(battle_judge_models=[JUDGE_MODEL, GLM_MODEL]),
    )

    class _StubService:
        @staticmethod
        def resolve_provider(_model_id):
            return {"base_url": "https://glm.invalid/v1", "api_key": "unused"}

    monkeypatch.setattr(openrouter_service, "OpenRouterService", _StubService)

    roster = runner._resolve_judge_roster("https://stub.invalid/v1", "unused")
    by_id = {m.model_id: m for m in roster}
    assert by_id[JUDGE_MODEL].temperature == 1.0
    assert by_id[GLM_MODEL].temperature == 0.7


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
