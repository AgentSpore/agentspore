"""Unit tests for SubmitTurnRequest.seq_no bounds (review fix F8).

No database and no Docker: this is a pure pydantic validation contract. The
upper bound exists so a client can never post the sequence the reconciler
reserves for a silent fighter's synthetic final — a client that reached
SILENT_FIGHTER_SEQ_NO (9999) could take that slot first and make a battle
decided on silence permanently unjudgeable.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.battles import SubmitTurnRequest


def test_seq_no_at_the_upper_bound_is_accepted() -> None:
    assert SubmitTurnRequest(content="x", seq_no=9000).seq_no == 9000


def test_seq_no_above_the_bound_is_rejected() -> None:
    with pytest.raises(ValidationError):
        SubmitTurnRequest(content="x", seq_no=9001)
    # 9999 is the reconciler's SILENT_FIGHTER_SEQ_NO — the exact slot a client
    # must never be able to preempt.
    with pytest.raises(ValidationError):
        SubmitTurnRequest(content="x", seq_no=9999)


def test_seq_no_below_one_is_still_rejected() -> None:
    with pytest.raises(ValidationError):
        SubmitTurnRequest(content="x", seq_no=0)
