"""Settings parsing edge cases that env-var passthrough can trigger."""

from app.core.config import Settings


def test_empty_battle_judge_models_env_falls_back_to_the_default() -> None:
    """docker-compose passes BATTLE_JUDGE_MODELS='${BATTLE_JUDGE_MODELS:-}',
    so an unset var arrives as an empty string rather than a missing key.
    Settings() must not fail parsing it as JSON.
    """
    default = Settings.model_fields["battle_judge_models"].default
    settings = Settings(battle_judge_models="")
    assert settings.battle_judge_models == default


def test_battle_judge_models_env_accepts_a_json_list() -> None:
    settings = Settings(battle_judge_models='["zai/glm-4.5-flash"]')
    assert settings.battle_judge_models == ["zai/glm-4.5-flash"]
