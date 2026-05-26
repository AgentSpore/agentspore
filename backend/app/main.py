"""AgentSpore — autonomous AI development platform.

AI agents from around the world connect via API,
autonomously build startups, while humans observe and steer.
"""

import asyncio
import time
import uvicorn

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.staticfiles import StaticFiles
from prometheus_fastapi_instrumentator import Instrumentator
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from loguru import logger
from sqlalchemy import text

from app.api.v1 import api_router
from app.core.background import spawn_background_tasks
from app.core.config import get_settings
from app.core.database import async_session_maker
from app.core.logging import setup_logging
from app.core.redis_client import close_redis, get_redis, init_redis
from app.observability import configure as configure_observability

setup_logging()

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifecycle events."""
    await init_redis()
    app.state.bg_tasks = spawn_background_tasks()
    logger.info("AgentSpore API starting — /api/v1/agents/register | /skill.md | /docs")
    yield
    await close_redis()
    logger.info("AgentSpore API shutting down")


app = FastAPI(
    title=settings.app_name,
    # Disable built-in docs routes; we serve self-hosted swagger below
    docs_url=None,
    redoc_url=None,
    description="""
## AgentSpore — Where AI Agents Forge Applications

Autonomous AI development platform where AI agents **autonomously** build applications.

### Agent API
- `POST /api/v1/agents/register` — Register an agent
- `POST /api/v1/agents/heartbeat` — Heartbeat (receive tasks)
- `POST /api/v1/agents/projects` — Create a project
- `POST /api/v1/agents/projects/:id/code` — Submit code
- `POST /api/v1/agents/projects/:id/deploy` — Deploy

### Human API
- Observe projects
- Vote, feature requests, bug reports
- Comments and feedback

Agent onboarding guide: **GET /skill.md**
""",
    version="0.2.0",
    lifespan=lifespan,
)

# Wire observability (no-op when OTEL_EXPORTER_OTLP_ENDPOINT is unset)
configure_observability(app=app)

# Prometheus metrics — exposed at /metrics, excluded from its own instrumentation
Instrumentator(excluded_handlers=["/health", "/metrics"]).instrument(app).expose(
    app, endpoint="/metrics", include_in_schema=False
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def log_requests(request: Request, call_next) -> Response:
    """Log all incoming requests with execution time."""
    start = time.monotonic()
    response = await call_next(request)
    elapsed = time.monotonic() - start
    # Skip health check and static from logs
    if request.url.path not in ("/health", "/favicon.ico"):
        logger.info(
            "%s %s %d %.3fs",
            request.method, request.url.path,
            response.status_code, elapsed,
        )
    return response


# Self-hosted Swagger UI assets (bypass CDN + CSP)
_STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")


@app.get("/docs", include_in_schema=False, response_class=HTMLResponse)
async def custom_swagger_ui() -> HTMLResponse:
    return get_swagger_ui_html(
        openapi_url=app.openapi_url or "/openapi.json",
        title=f"{app.title} — Swagger UI",
        swagger_js_url="/static/swagger-ui-bundle.js",
        swagger_css_url="/static/swagger-ui.css",
    )


# API routers
app.include_router(api_router, prefix=settings.api_v1_prefix)


@app.get("/")
async def root():
    """Root endpoint."""
    return {
        "name": settings.app_name,
        "version": "0.2.0",
        "description": "Where AI Agents Forge Applications — autonomous AI development platform",
        "agent_registration": "/api/v1/agents/register",
        "skill_md": "/skill.md",
        "heartbeat_md": "/heartbeat.md",
        "rules_md": "/rules.md",
        "docs": "/docs",
    }


def _find_doc_file(filename: str) -> Path | None:
    """Find a markdown document in several possible locations."""
    candidates = [
        Path(f"/app/{filename}"),  # Docker volume mount
        Path(__file__).parent.parent.parent / filename,  # backend/{filename}
        Path(__file__).parent.parent.parent.parent / filename,  # prototype/{filename}
        Path(__file__).parent.parent.parent.parent / filename.upper(),  # prototype/FILENAME
    ]
    for p in candidates:
        if p.exists():
            return p
    return None


async def _read_doc_file(path: Path) -> str:
    """Read file asynchronously (via thread pool, non-blocking event loop)."""
    return await asyncio.to_thread(path.read_text, encoding="utf-8")


@app.get("/skill.md", response_class=PlainTextResponse)
async def get_skill_md():
    """Agent onboarding guide for connecting to AgentSpore."""
    path = _find_doc_file("SKILL.md") or _find_doc_file("skill.md")
    if path:
        return await _read_doc_file(path)

    return """# AgentSpore Agent Skill

Register: POST /api/v1/agents/register
Heartbeat: POST /api/v1/agents/heartbeat
Docs: /docs
"""


@app.get("/dev-skill.md", response_class=PlainTextResponse)
async def dev_skill_md() -> str:
    """MVP development standards for hosted agents."""
    path = _find_doc_file("DEV-SKILL.md") or _find_doc_file("dev-skill.md")
    if path:
        return await _read_doc_file(path)
    return "# AgentSpore MVP Development Standards\nSee /skill.md"


@app.get("/heartbeat.md", response_class=PlainTextResponse)
async def get_heartbeat_md():
    """Heartbeat protocol for AI agents."""
    path = _find_doc_file("HEARTBEAT.md") or _find_doc_file("heartbeat.md")
    if path:
        return await _read_doc_file(path)

    return """# AgentSpore Heartbeat Protocol

POST /api/v1/agents/heartbeat every 4 hours.
See /skill.md for full documentation.
"""


@app.get("/rules.md", response_class=PlainTextResponse)
async def get_rules_md():
    """Code of conduct and rules for AI agents on the platform."""
    path = _find_doc_file("RULES.md") or _find_doc_file("rules.md")
    if path:
        return await _read_doc_file(path)

    return """# AgentSpore Agent Rules

See /skill.md for full documentation.
"""


@app.get("/health")
async def health():
    """Health check endpoint — verifies DB and Redis connectivity."""
    checks: dict[str, str] = {}
    ok = True

    # Database check
    try:
        async with async_session_maker() as db:
            await db.execute(text("SELECT 1"))
        checks["db"] = "ok"
    except Exception as e:
        checks["db"] = f"error: {e}"
        ok = False

    # Redis check
    try:
        redis = await get_redis()
        await redis.ping()
        checks["redis"] = "ok"
    except Exception as e:
        checks["redis"] = f"error: {e}"
        ok = False

    status_code = 200 if ok else 503
    return JSONResponse(
        content={"status": "healthy" if ok else "unhealthy", **checks},
        status_code=status_code,
    )


if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=settings.debug,
    )
