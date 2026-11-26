"""heartbeat_logs.status is VARCHAR(30): a longer status must be a 422, not a database 500."""

import pytest
from pydantic import ValidationError

from app.schemas.agents import HeartbeatRequestBody


def test_status_at_column_limit_is_accepted():
    assert HeartbeatRequestBody(status="x" * 30).status == "x" * 30


def test_status_longer_than_column_is_rejected_before_the_database():
    # Observed in the onboarding eval: a status with a project id reached the insert, 500.
    with pytest.raises(ValidationError):
        HeartbeatRequestBody(status="idle (vcs_unavailable on project d9e6d61f-6b5e-427b)")
