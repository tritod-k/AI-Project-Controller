"""Minimal structured stderr logging, with optional correlation context and secret redaction."""

import json
import logging
import re
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

_BEARER_PATTERN = re.compile(r"Bearer\s+[A-Za-z0-9_.\-]+", re.IGNORECASE)
_SK_PATTERN = re.compile(r"sk-[A-Za-z0-9_\-]{8,}")
_AIZA_PATTERN = re.compile(r"AIza[0-9A-Za-z_\-]{35}")
_KV_PATTERN = re.compile(
    r"""(?i)(api[_-]?key|token|password|secret)\s*([:=])\s*['"]?([^\s'"]+)['"]?"""
)
_AUTH_HEADER_PATTERN = re.compile(
    r"""(?i)(authorization)\s*([:=])\s*['"]?(?!Bearer\s)([^\s'"]+)['"]?"""
)


def _redact_text(text: str) -> str:
    """Scrub sensitive credentials from a string using standard library regex."""
    if not isinstance(text, str) or not text:
        return text
    redacted = _BEARER_PATTERN.sub("Bearer [REDACTED]", text)
    redacted = _SK_PATTERN.sub("[REDACTED]", redacted)
    redacted = _AIZA_PATTERN.sub("[REDACTED]", redacted)
    redacted = _KV_PATTERN.sub(r"\1\2[REDACTED]", redacted)
    redacted = _AUTH_HEADER_PATTERN.sub(r"\1\2[REDACTED]", redacted)
    return redacted


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        msg = _redact_text(record.getMessage())
        context = {}
        for field in _CONTEXT_FIELDS:
            val = getattr(record, field, None)
            if isinstance(val, str):
                val = _redact_text(val)
            context[field] = val

        payload = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": msg,
            **context,
        }
        return json.dumps(payload)


def configure_logging(level: str = "INFO", *, stream: TextIO | None = None) -> logging.Logger:
    """Configure the owned `aipc` logger without modifying root logging.

    Repeated calls replace its handlers. Automatic redaction scrubs Bearer tokens,
    API keys, passwords, and sensitive credentials from log messages and correlation context.
    """
    handler = logging.StreamHandler(stream)
    handler.setFormatter(_JsonFormatter())
    logger = logging.getLogger("aipc")
    logger.setLevel(level)  # Validate before replacing a working configuration.
    logger.handlers.clear()
    logger.addHandler(handler)
    logger.propagate = False
    return logger
