"""Unit tests for AgentSpore backend API."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch



class TestModelImports:
    """
    Smoke-тесты импортов моделей и маппера SQLAlchemy.

    Ловит ошибки вида:
      InvalidRequestError: expression 'Vote' failed to locate a name ('Vote')
    которые возникают если в модели осталась relationship на удалённый класс.
    Без этих тестов ошибка проявляется только в рантайме при первом обращении к маппу.
    """

    def test_user_model_imports_without_error(self):
        """User модель импортируется и маппер инициализируется без ошибок."""
        from app.models.user import User
        assert User.__tablename__ == "users"

    def test_token_model_imports_without_error(self):
        """TokenTransaction модель импортируется без ошибок."""
        from app.models.token import TokenTransaction, TokenAction
        assert TokenTransaction.__tablename__ == "token_transactions"

    def test_all_models_import_together(self):
        """Все модели из __init__ импортируются вместе без конфликтов маппера."""
        import app.models  # noqa: F401 — проверяем что импорт не падает

    def test_user_model_has_no_stale_relationships(self):
        """User не имеет relationships на несуществующие модели (Vote, Idea и т.п.)."""
        from app.models.user import User
        from sqlalchemy import inspect as sa_inspect
        mapper = sa_inspect(User)
        rel_names = {r.key for r in mapper.relationships}
        # Если здесь есть Vote или Idea — маппер уже упал бы выше, но проверим явно
        assert "votes" not in rel_names, "User.votes relationship ссылается на удалённую модель Vote"
        assert "ideas" not in rel_names, "User.ideas relationship ссылается на удалённую модель Idea"
        assert "token_transactions" not in rel_names, (
            "User.token_transactions relationship ссылается на удалённую back_populates"
        )

    def test_main_app_creates_without_error(self):
        """FastAPI app создаётся без ошибок при импорте."""
        from app.main import app
        assert app is not None
