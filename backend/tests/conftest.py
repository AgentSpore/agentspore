"""
Конфигурация pytest и общие фикстуры.

Для unit-тестов мокируем БД и внешние сервисы.
Для интеграционных тестов (требуют Docker) используй маркер @pytest.mark.integration.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from httpx import AsyncClient, ASGITransport


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "integration: requires running Docker stack (DB + backend)"
    )


@pytest.fixture
def mock_db():
    """Mock AsyncSession — для тестов без реальной БД."""
    db = AsyncMock()
    result = MagicMock()
    result.mappings.return_value.first.return_value = None
    db.execute.return_value = result
    return db


@pytest.fixture
def app_with_mock_db(mock_db):
    """FastAPI app с замокированной БД."""
    from app.main import app
    from app.core.database import get_db

    async def override_get_db():
        yield mock_db

    app.dependency_overrides[get_db] = override_get_db
    yield app
    app.dependency_overrides.clear()
