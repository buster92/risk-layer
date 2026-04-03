"""
app/core/logging.py
Structured logging via structlog, properly integrated with stdlib logging.

Root cause of the original AttributeError:
  structlog.stdlib.add_logger_name calls logger.name, which only exists on
  stdlib logging.Logger objects. We were using PrintLoggerFactory which
  creates PrintLogger — a simple wrapper with no .name attribute.

Fix:
  Use structlog.stdlib.LoggerFactory() so structlog wraps real
  logging.Logger objects. These have .name, so add_logger_name works.
  We also wire a stdlib StreamHandler so the two systems share one output
  pipeline and there is no double-logging.
"""
import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

import structlog

from app.core.config import get_settings

_LOGS_DIR = Path("logs")


def configure_logging() -> None:
    settings = get_settings()
    level = getattr(logging, settings.log_level.upper(), logging.INFO)

    # ── Processors shared by structlog and the stdlib bridge ──────────────────
    shared_processors: list = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,      # now works — logger is a real Logger
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
    ]

    if settings.log_json:
        console_renderer: structlog.types.Processor = structlog.processors.JSONRenderer()
    else:
        console_renderer = structlog.dev.ConsoleRenderer(colors=True)

    # ── Configure structlog to use stdlib LoggerFactory ───────────────────────
    # This means structlog.get_logger("foo") returns a bound logger wrapping
    # logging.getLogger("foo"), which has a proper .name attribute.
    structlog.configure(
        processors=shared_processors + [
            # Prepare the event dict for stdlib — converts it to a LogRecord
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    # ── Console handler ───────────────────────────────────────────────────────
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=shared_processors,
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                console_renderer,
            ],
        )
    )

    # ── File handler — plain text, no ANSI, rotating at 10 MB ────────────────
    _LOGS_DIR.mkdir(exist_ok=True)
    file_handler = RotatingFileHandler(
        _LOGS_DIR / "app.log",
        maxBytes=10 * 1024 * 1024,  # 10 MB
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=shared_processors,
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                structlog.dev.ConsoleRenderer(colors=False),
            ],
        )
    )

    # Root logger — catches everything
    root = logging.getLogger()
    root.handlers = []          # clear any handlers added before configure_logging()
    root.addHandler(console_handler)
    root.addHandler(file_handler)
    root.setLevel(level)

    # ── Suppress noisy third-party loggers ────────────────────────────────────
    for noisy in ("yfinance", "urllib3", "httpx", "apscheduler", "peewee"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Return a structlog bound logger wrapping stdlib logging.getLogger(name)."""
    return structlog.get_logger(name)