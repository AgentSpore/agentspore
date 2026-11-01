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

# Dependency manifest AND the lockfile: without uv.lock in the context every
# build re-resolves against the network and can pin different versions.
COPY pyproject.toml uv.lock ./

# --locked: install exactly what uv.lock pins, and FAIL if the lockfile has
# drifted from pyproject.toml. --frozen would use a stale lock silently; a bare
# `uv sync` would rewrite it in-image, which is the non-reproducible behaviour.
RUN uv sync --locked --no-dev

# buildctl for the A4 worker: moby/buildkit v0.23.2 (static binary), pinned by index digest.
# Keep in step with the buildkitd image in deploy/a4-builder.compose.yml (v0.23.2-rootless).
COPY --from=moby/buildkit@sha256:ddd1ca44b21eda906e81ab14a3d467fa6c39cd73b9a39df1196210edcb8db59e /usr/bin/buildctl /usr/local/bin/buildctl

# Install reviewed scanner assets for the target, including native Apple Silicon builds.
ARG TARGETARCH
COPY scripts/install_a4_scanners.py /tmp/install_a4_scanners.py
RUN python /tmp/install_a4_scanners.py "$TARGETARCH" \
    && rm /tmp/install_a4_scanners.py

# Копируем код приложения
COPY app/ ./app/

# Validates UVICORN_WORKERS (default 4) and execs the CMD. Tests: tests/test_docker_entrypoint.py.
COPY docker-entrypoint.sh /app/docker-entrypoint.sh
RUN chmod 0755 /app/docker-entrypoint.sh

# Создаём директорию для Alembic миграций
RUN mkdir -p alembic/versions

# Открываем порт
EXPOSE 8000

# Healthcheck
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Start from the baked venv directly. `uv run` would re-check the environment on
# every container start — a network-touching step on the boot path.
#
# Worker count comes from UVICORN_WORKERS (uvicorn reads it natively; the
# entrypoint validates it, default 4). No literal --workers here: a CLI flag
# would beat the env var.
ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["/app/.venv/bin/python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
