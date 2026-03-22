FROM python:3.12-slim

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY main.py config.py ./

EXPOSE 8100

CMD ["uv", "run", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8100"]
