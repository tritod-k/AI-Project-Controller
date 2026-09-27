"""Minimal structured stderr logging, with optional correlation context."""

import json
import logging
from datetime import UTC, datetime
from typing import TextIO

_CONTEXT_FIELDS = (
    "project_id",
    "run_id",
    "phase_id",
    "attempt",
    "task_id",
    "agent_id",
    "provider",
    "event",
)


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            **{field: getattr(record, field, None) for field in _CONTEXT_FIELDS},
        }
        return json.dumps(payload)


def configure_logging(level: str = "INFO", *, stream: TextIO | None = None) -> logging.Logger:
    """Configure the owned `aipc` logger without modifying root logging.

    Repeated calls replace its handlers. Callers must not send secrets in
    messages or correlation fields; automatic redaction is not implemented.
    """
    handler = logging.StreamHandler(stream)
    handler.setFormatter(_JsonFormatter())
    logger = logging.getLogger("aipc")
    logger.setLevel(level)  # Validate before replacing a working configuration.
    logger.handlers.clear()
    logger.addHandler(handler)
    logger.propagate = False
    return logger
