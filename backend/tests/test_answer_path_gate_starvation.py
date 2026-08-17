"""Two fighters on one paced, capacity-1 account must BOTH get their answer through.

Measured on production (v1.28.2, two-hour window): 12 battles ended, 2 reached a
verdict, 9 recorded "provider unreachable". Not the demo path — 0 demo battles in
the window. The decisive log line:

    23:35:03 side a unreachable provider (gate saturated): battle will be void
    23:35:09 side b answered as Mistral Nemo (llm7)
    23:39:45 settled without judging: empty side(s) a=provider unreachable

Side b answered; side a never got a slot. Root cause: llm7 is capacity 1 with an
8s pace interval, and ``_spawn_contender_drives`` fires both sides as detached
tasks, so they contend for that one slot. The loser waited against
DEFAULT_WAIT_SECONDS (20s) — a ceiling sized for the SEQUENTIAL judge panel,
where each acquire waits at most one 8s interval. On the answer path the second
claimant must outwait the first side's whole in-flight call, which is bounded by
DEMO_ANSWER_TIMEOUT_SECONDS (240s), not by the pace interval.

Drives the real LLMGate against a real Redis, like test_llm_gate.py: the
admission decision is Lua, so a fake gate would only prove the fake behaves as
written. Nothing here calls a provider.
"""

from __future__ import annotations

import asyncio
import time

import pytest
import pytest_asyncio
from redis.asyncio import Redis
from testcontainers.redis import RedisContainer

from app.services.battle_judges import LEASE_MARGIN_SECONDS
from app.services.battle_runner import (
    ANSWER_DRIVE_BUDGET_SECONDS,
    ANSWER_GATE_WAIT_SECONDS,
    DEMO_ANSWER_TIMEOUT_SECONDS,
)
from app.services.llm_gate import (
    DEFAULT_WAIT_SECONDS,
    LLMGate,
    LLMGateTimeoutError,
    provider_capacity,
    provider_min_interval,
)

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def redis_container():
    with RedisContainer("redis:7-alpine") as rc:
        yield rc


@pytest_asyncio.fixture
async def gate(redis_container):
    client = Redis(
        host=redis_container.get_container_host_ip(),
        port=int(redis_container.get_exposed_port(6379)),
        decode_responses=False,
    )
    await client.flushall()
    yield LLMGate(client)
    await client.flushall()
    await client.aclose()


def _llm7_account(gate: LLMGate):
    """The answer path's own account handle: llm7, leased for a full answer."""
    return gate.for_provider(
        "llm7", lease_seconds=int(DEMO_ANSWER_TIMEOUT_SECONDS) + LEASE_MARGIN_SECONDS
    )


async def _race_two_sides(account, wait_seconds: float, hold_seconds: float) -> dict:
    """Side a takes the slot and holds it; side b must outwait it. Both outcomes."""
    outcomes: dict[str, str] = {}

    async def side(name: str, hold: float) -> None:
        try:
            async with account.slot(wait_seconds=wait_seconds):
                outcomes[name] = "answered"
                await asyncio.sleep(hold)
        except LLMGateTimeoutError:
            outcomes[name] = "gate saturated"

    await asyncio.gather(side("a", hold_seconds), side("b", 0.0))
    return outcomes


class TestLlm7IsThePacedCapacityOneAccount:
    """The premise the rest of the file rests on. If llm7 stops being capacity-1
    or unpaced, these tests measure nothing and must be re-read."""

    def test_llm7_is_paced_and_single_slot(self) -> None:
        assert provider_capacity("llm7") == 1
        assert provider_min_interval("llm7") == 8.0


class TestSecondClaimantIsNotStarved:
    # Just past DEFAULT_WAIT_SECONDS: the smallest hold that proves the 20s
    # ceiling is the binding constraint, while keeping the test fast. A real
    # answer runs far longer (up to DEMO_ANSWER_TIMEOUT_SECONDS), so this
    # UNDERSTATES production contention rather than exaggerating it.
    HELD_SECONDS = 25.0

    async def test_two_concurrent_sides_both_get_a_slot(self, gate) -> None:
        """The production failure, reproduced: two answer calls on one llm7
        account, started together, one holding the slot for a realistic duration.

        MUTATION: pass DEFAULT_WAIT_SECONDS instead of ANSWER_GATE_WAIT_SECONDS
        as the answer path's wait and this goes red — side b raises
        LLMGateTimeoutError, exactly as production did.
        """
        outcomes = await _race_two_sides(
            _llm7_account(gate), ANSWER_GATE_WAIT_SECONDS, self.HELD_SECONDS
        )

        assert outcomes == {"a": "answered", "b": "answered"}, (
            "both sides must reach the provider; a bounced side voids the battle"
        )

    async def test_the_judge_ceiling_alone_would_have_bounced_it(self, gate) -> None:
        """The control. Same contention, judge ceiling: the second side IS
        bounced. Without it the test above could pass for reasons unrelated to
        the wait (a hold too short to contend at all), and would keep passing if
        the fix were reverted to a value that merely looks bigger.
        """
        outcomes = await _race_two_sides(
            _llm7_account(gate), DEFAULT_WAIT_SECONDS, self.HELD_SECONDS
        )

        assert outcomes["a"] == "answered"
        assert outcomes["b"] == "gate saturated", (
            "this is the production defect; if it no longer reproduces, the "
            "gate's contention behaviour changed and the fix needs re-deriving"
        )

    async def test_the_pace_interval_is_still_enforced_between_departures(
        self, gate
    ) -> None:
        """The widened wait must NOT become a way around the account's real
        limit. Two sequential departures on llm7 stay >= one pace interval apart.

        MUTATION: drop llm7 from PROVIDER_MIN_INTERVAL_SECONDS and this goes red.
        """
        account = gate.for_provider("llm7")
        started = time.monotonic()

        for _ in range(2):
            async with account.slot(wait_seconds=ANSWER_GATE_WAIT_SECONDS):
                pass

        elapsed = time.monotonic() - started
        assert elapsed >= provider_min_interval("llm7"), (
            f"two departures {elapsed:.1f}s apart — the 8s account pace was bypassed"
        )


class TestAnswerGateWaitArithmetic:
    """The step v1.28.2 got wrong: the numbers were checked against the judge
    path and never re-checked for the answer path."""

    def test_wait_covers_a_full_in_flight_answer_plus_one_pace_interval(self) -> None:
        """The second claimant's worst case is the first side's whole call
        (DEMO_ANSWER_TIMEOUT_SECONDS) plus one pace interval before it may
        depart. A wait shorter than that sum is a battle that voids under load.

        MUTATION: set ANSWER_GATE_WAIT_SECONDS back to DEFAULT_WAIT_SECONDS (20)
        and this goes red on the lower bound.
        """
        required = DEMO_ANSWER_TIMEOUT_SECONDS + provider_min_interval("llm7")
        assert ANSWER_GATE_WAIT_SECONDS >= required, (
            f"{ANSWER_GATE_WAIT_SECONDS}s cannot outwait a {DEMO_ANSWER_TIMEOUT_SECONDS}s "
            f"call plus an {provider_min_interval('llm7')}s pace interval"
        )

    def test_wait_still_fits_inside_one_detached_drive_budget(self) -> None:
        """The upper bound, and why widening is safe: each side's drive is its
        own asyncio task with its own ANSWER_DRIVE_BUDGET_SECONDS wait_for, so
        the two sides' budgets run in PARALLEL and never sum. One attempt is gate
        wait + HTTP, and it must still fit one drive budget.

        MUTATION: raise ANSWER_GATE_WAIT_SECONDS past what the budget admits
        (e.g. 400) and this goes red — one attempt would exceed the drive's own
        wait_for, so the drive is killed mid-call and the side never answers.
        """
        one_attempt = ANSWER_GATE_WAIT_SECONDS + DEMO_ANSWER_TIMEOUT_SECONDS
        assert one_attempt <= ANSWER_DRIVE_BUDGET_SECONDS, (
            "a single attempt must fit its drive budget"
        )
