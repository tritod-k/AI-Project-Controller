"""Known credential redaction, not an execution or universal DLP boundary."""

import re

_SECRETS = (
    re.compile(
        r"""(?i)(["'](?:[\w-]+[_-])?(?:api[_-]?key|access[_-]?token|password|passwd|secret|authorization|token)["']\s*:\s*)(?:"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*')"""
    ),
    re.compile(
        r"(?i)((?:api[_-]?key|authorization|access[_-]?token|password|secret)\s*[=:]\s*)(?:\"[^\"\r\n]*\"|'[^'\r\n]*'|[^\r\n,;]+)"
    ),
    re.compile(r"(?i)\bBearer\s+[^\s\"']+"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}"),
)


def redact(text: str) -> str:
    text = _SECRETS[0].sub(lambda match: match.group(1) + '"[REDACTED]"', text)
    for pattern in _SECRETS[1:]:
        text = pattern.sub("[REDACTED]", text)
    return text


def bounded(text: str, limit: int) -> str:
    return text.encode("utf-8")[:limit].decode("utf-8", errors="ignore")


def clean_json(value):
    """Redact string values before JSON serialization; preserve structural escaping."""
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, list):
        return [clean_json(i) for i in value]
    if isinstance(value, tuple):
        return [clean_json(i) for i in value]
    if isinstance(value, dict):
        sensitive = {
            "api_key",
            "access_token",
            "password",
            "passwd",
            "secret",
            "authorization",
            "token",
        }

        def secret_key(key):
            normalized = key.casefold().replace("-", "_")
            return normalized in sensitive or any(
                normalized.endswith("_" + name) for name in sensitive
            )

        return {
            key: "[REDACTED]" if secret_key(key) else clean_json(item)
            for key, item in value.items()
        }
    return value
