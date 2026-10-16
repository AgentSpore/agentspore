"""Historical fork fields remain readable on agent profiles."""

from app.schemas.agents import AgentProfile


def test_agent_profile_has_fork_fields():
    profile = AgentProfile(
        id="1",
        name="Bot",
        handle="bot",
        agent_type="external",
        model_provider="x",
        model_name="y",
        specialization="programmer",
        skills=[],
        karma=0,
        projects_created=0,
        code_commits=0,
        reviews_done=0,
        last_heartbeat=None,
        is_active=True,
        created_at="2026-01-01",
        fork_count=5,
        is_hosted=True,
    )
    assert profile.fork_count == 5
    assert profile.is_hosted is True


def test_agent_profile_defaults():
    profile = AgentProfile(
        id="1",
        name="Bot",
        handle="bot",
        agent_type="external",
        model_provider="x",
        model_name="y",
        specialization="programmer",
        skills=[],
        karma=0,
        projects_created=0,
        code_commits=0,
        reviews_done=0,
        last_heartbeat=None,
        is_active=True,
        created_at="2026-01-01",
    )
    assert profile.fork_count == 0
    assert profile.is_hosted is False
