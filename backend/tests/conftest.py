"""
Конфигурация pytest и общие фикстуры.

Для unit-тестов мокируем БД и внешние сервисы.
Для интеграционных тестов (требуют Docker) используй маркер @pytest.mark.integration.
"""
from unittest.mock import AsyncMock, MagicMock

import pytest


def split_sql_statements(sql: str) -> list[str]:
    """Split a migration script into statements, respecting $$ dollar-quoting.

    The battle test harnesses apply the real migration files against
    testcontainers Postgres by splitting on ';' — but V67 carries a
    ``DO $$ ... $$;`` precondition guard whose body contains its own semicolons.
    A naive ``sql.split(';')`` shreds that block into invalid fragments, so this
    tokeniser tracks whether the cursor is inside a ``$tag$ ... $tag$`` quoted
    region OR a ``--`` line comment, and only breaks on a semicolon at the top
    level (a ``;`` inside a comment — e.g. "nullable in V66; the ..." — must not
    split either).
    """
    statements: list[str] = []
    buf: list[str] = []
    i = 0
    dollar_tag: str | None = None
    n = len(sql)
    while i < n:
        ch = sql[i]
        # A -- line comment runs to end of line; copy it verbatim, ignore its ;.
        if dollar_tag is None and ch == "-" and sql.startswith("--", i):
            eol = sql.find("\n", i)
            if eol == -1:
                eol = n
            buf.append(sql[i:eol])
            i = eol
            continue
        if dollar_tag is None and ch == "$":
            # Possible start of a $tag$ / $$ dollar quote.
            j = sql.find("$", i + 1)
            if j != -1 and sql[i + 1 : j].isidentifier() or (j == i + 1):
                tag = sql[i : j + 1]
                dollar_tag = tag
                buf.append(tag)
                i = j + 1
                continue
        elif dollar_tag is not None and sql.startswith(dollar_tag, i):
            buf.append(dollar_tag)
            i += len(dollar_tag)
            dollar_tag = None
            continue
        if ch == ";" and dollar_tag is None:
            statements.append("".join(buf))
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    if "".join(buf).strip():
        statements.append("".join(buf))
    return [s for s in statements if s.strip()]


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
    from app.core.database import get_db
    from app.main import app

    async def override_get_db():
        yield mock_db

    app.dependency_overrides[get_db] = override_get_db
    yield app
    app.dependency_overrides.clear()
