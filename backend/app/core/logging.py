"""Loguru-based logging setup for AgentSpore.

Call `setup_logging()` once at startup (in main.py).
All other modules just do: `from loguru import logger`.
"""

import logging
import re
import sys
from pathlib import Path

from loguru import logger

# Query parameters whose VALUE is a credential.
#
# INVARIANT(ws-key-leak): this redaction is the only thing between an agent
# credential and every log sink. Uvicorn formats the leaking line itself
# (websockets_impl, via get_path_with_query_string), so it cannot be fixed at
# the route — it must be scrubbed here, where stdlib records enter loguru.
# Removing it re-opens the leak silently: the endpoint works either way.
_SECRET_QUERY_PARAMS = (
    "api_key",
    "apikey",
    "access_token",
    "refresh_token",
    "token",
    "secret",
    "password",
    "key",
)

_QUERY_SECRET_RE = re.compile(
    r"(?i)\b(" + "|".join(_SECRET_QUERY_PARAMS) + r")=([^&\s\"']+)"
)

# Agent keys are self-identifying by prefix, so they are masked even outside a
# query string — an exception message or a repr of a connect URL leaks just as
# effectively as an access log line.
_AGENT_KEY_RE = re.compile(r"\baf_[A-Za-z0-9_\-]{8,}")

_REDACTED = "<redacted>"


def redact_secrets(message: str) -> str:
    """Mask credential values in a log line, keeping the line diagnostic.

    The parameter NAME survives so the line still says what was scrubbed; only
    the value is replaced.
    """
    message = _QUERY_SECRET_RE.sub(lambda m: f"{m.group(1)}={_REDACTED}", message)
    return _AGENT_KEY_RE.sub(_REDACTED, message)


class _InterceptHandler(logging.Handler):
    """Route stdlib logging (uvicorn, sqlalchemy, httpx) into loguru."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno
        frame, depth = logging.currentframe(), 2
        while frame and frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1
        # getMessage() first: uvicorn logs lazily ('%s' + args), so the
        # credential does not exist until the args are interpolated.
        message = redact_secrets(record.getMessage())
        logger.opt(depth=depth, exception=record.exc_info).log(level, message)


def _scrub_patcher(record: dict) -> None:
    """Redact every record on its way to a sink, whatever produced it.

    _InterceptHandler covers stdlib records (uvicorn). This covers the direct
    `from loguru import logger` calls all over the app, so redaction is a
    property of the SINK rather than of one code path — a hand-rolled log line
    that interpolates a key cannot bypass it.
    """
    record["message"] = redact_secrets(record["message"])


def setup_logging() -> None:
    """Configure loguru sinks: stderr + rotating file."""
    logger.remove()
    logger.configure(patcher=_scrub_patcher)

    fmt = "{time:YYYY-MM-DD HH:mm:ss} {level:<7} [{name}] {message}"

    # Console (stderr → docker logs)
    logger.add(sys.stderr, format=fmt, level="INFO", colorize=True)

    # File: 5 MB × 3 rotation, max 15 MB
    log_dir = Path("/app/logs")
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        log_dir = Path(__file__).parent.parent.parent / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)

    logger.add(
        log_dir / "app.log",
        format=fmt,
        level="INFO",
        rotation="5 MB",
        retention=3,
        encoding="utf-8",
    )

    # Intercept stdlib loggers
    logging.basicConfig(handlers=[_InterceptHandler()], level=0, force=True)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "sqlalchemy.engine", "httpx"):
        logging.getLogger(name).handlers = [_InterceptHandler()]
