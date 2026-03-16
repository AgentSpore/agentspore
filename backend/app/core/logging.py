"""Loguru-based logging setup for AgentSpore.

Call `setup_logging()` once at startup (in main.py).
All other modules just do: `from loguru import logger`.
"""

import logging
import sys
from pathlib import Path

from loguru import logger


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
        logger.opt(depth=depth, exception=record.exc_info).log(level, record.getMessage())


def setup_logging() -> None:
    """Configure loguru sinks: stderr + rotating file."""
    logger.remove()

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
