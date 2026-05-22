FROM python:3.12-slim

WORKDIR /app

# Node.js 20 + liteparse CLI for pydantic-deep liteparse toolset
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates gnupg \
 && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
 && apt-get install -y --no-install-recommends nodejs \
 && npm install -g @llamaindex/liteparse \
 && apt-get purge -y --auto-remove curl gnupg \
 && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY main.py config.py llm_fallback.py providers.py quota.py \
     session.py schemas.py workspace.py sandbox.py helpers.py \
     ssrf_guard.py content_sanitizer.py observability.py ./
COPY routes ./routes

EXPOSE 8100

CMD ["uv", "run", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8100"]
