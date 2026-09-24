"""
SagaMind Centralized Logging Configuration
===========================================

Structured logging for production (JSON lines) and development (colorized console).
All modules must call ``configure_logging()`` at startup instead of creating ad-hoc loggers.

Usage::

    from src.logging_config import configure_logging, get_logger
    configure_logging()
    logger = get_logger("SagaMind.MyModule")
"""

import json
import logging
import logging.config
import sys
from datetime import datetime, timezone

from src.config import settings
from src.request_context import request_id


class RequestContextFilter(logging.Filter):
    """Attach the current request correlation ID to every log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id.get() or "-"
        return True


class JSONFormatter(logging.Formatter):
    """
    Structured JSON log formatter for production environments.
    Emits one JSON object per line — compatible with ELK, Datadog, CloudWatch.
    """

    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
            "request_id": getattr(record, "request_id", "-"),
        }
        if record.exc_info and record.exc_info[0] is not None:
            log_entry["exception"] = self.formatException(record.exc_info)
        if hasattr(record, "saga_id"):
            log_entry["saga_id"] = record.saga_id
        if hasattr(record, "tenant_id"):
            log_entry["tenant_id"] = record.tenant_id
        return json.dumps(log_entry)


class ColorizedFormatter(logging.Formatter):
    """ANSI-colorized console formatter for development environments."""

    COLORS = {
        "DEBUG": "\033[36m",  # Cyan
        "INFO": "\033[32m",  # Green
        "WARNING": "\033[33m",  # Yellow
        "ERROR": "\033[31m",  # Red
        "CRITICAL": "\033[1;31m",  # Bold Red
    }
    RESET = "\033[0m"

    def format(self, record: logging.LogRecord) -> str:
        color = self.COLORS.get(record.levelname, self.RESET)
        record.levelname = f"{color}{record.levelname:<8}{self.RESET}"
        return super().format(record)


def configure_logging(env: str | None = None) -> None:
    """
    Initialize the global logging configuration.

    Args:
        env: Override environment setting. Defaults to ``settings.env``.
    """
    environment = env or settings.env

    if environment == "production":
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(JSONFormatter())
        level = logging.INFO
    else:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(
            ColorizedFormatter(
                fmt="%(asctime)s │ %(levelname)s │ %(name)-32s │ rid=%(request_id)s │ %(message)s",
                datefmt="%H:%M:%S",
            )
        )
        level = logging.DEBUG

    handler.addFilter(RequestContextFilter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

    # Silence noisy third-party loggers
    for noisy in ("urllib3", "asyncio", "neo4j", "wasmtime"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """
    Retrieve a named logger under the SagaMind hierarchy.

    Args:
        name: Dot-separated logger name (e.g. "SagaMind.Orchestrator").

    Returns:
        Configured logging.Logger instance.
    """
    return logging.getLogger(name)
