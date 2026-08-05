# Dockerfile для SporeAI Backend
FROM python:3.12-slim

# Переменные окружения
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

# Рабочая директория
WORKDIR /app

# Установка системных зависимостей
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Установка uv через pip (ghcr.io заблокирован в России)
RUN pip install --no-cache-dir uv

# Копируем файлы зависимостей
COPY pyproject.toml ./

# Устанавливаем зависимости (без --frozen, так как uv.lock может не существовать)
RUN uv sync --no-dev

# Копируем код приложения
COPY app/ ./app/

# Operator one-shots run inside the container. The target is NOT ./scripts:
# deploy/docker-compose.prod.yml bind-mounts the repo-root scripts/ over
# /app/scripts and would shadow it. Guarded by tests/test_image_manifest.py.
COPY scripts/backfill_contender_elo.py ./ops_scripts/

# Создаём директорию для Alembic миграций
RUN mkdir -p alembic/versions

# Открываем порт
EXPOSE 8000

# Healthcheck
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Запуск через uv
CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
