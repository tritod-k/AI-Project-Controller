"""Deterministic secret redaction and sanitization utilities."""

import re

# Pinned patterns for sensitive credential strings
_BEARER_PATTERN = re.compile(r"Bearer\s+[A-Za-z0-9_.\-]+", re.IGNORECASE)
_SK_PATTERN = re.compile(r"sk-[A-Za-z0-9_\-]{8,}")
_AIZA_PATTERN = re.compile(r"AIza[0-9A-Za-z_\-]{35}")
_KV_PATTERN = re.compile(
    r"""(?i)(api[_-]?key|token|password|secret)\s*([:=])\s*['"]?([^\s'"]+)['"]?"""
)
_AUTH_HEADER_PATTERN = re.compile(
    r"""(?i)(authorization)\s*([:=])\s*['"]?(?!Bearer\s)([^\s'"]+)['"]?"""
)


def redact_secrets(text: str) -> str:
    """Scrub known API keys, tokens, and authorization credentials from diagnostic text."""
    if not text:
        return text
    # 1. Redact Bearer tokens first so Authorization: Bearer <token> is cleanly handled
    redacted = _BEARER_PATTERN.sub("Bearer [REDACTED]", text)
    # 2. Redact sk-... keys
    redacted = _SK_PATTERN.sub("[REDACTED]", redacted)
    # 3. Redact AIza... keys
    redacted = _AIZA_PATTERN.sub("[REDACTED]", redacted)
    # 4. Redact key-value pairs
    redacted = _KV_PATTERN.sub(r"\1\2[REDACTED]", redacted)
    # 5. Redact non-Bearer authorization headers
    redacted = _AUTH_HEADER_PATTERN.sub(r"\1\2[REDACTED]", redacted)
    return redacted
