"""Deterministic deep secret scanner for payloads, dictionaries, and strings."""

import re
from typing import Any

from aipc.hardening.errors import SecretExposureError

_BEARER_PATTERN = re.compile(r"Bearer\s+[A-Za-z0-9_.\-]+", re.IGNORECASE)
_SK_PATTERN = re.compile(r"(?<![A-Za-z0-9])sk-[A-Za-z0-9_\-]{8,}")
_AIZA_PATTERN = re.compile(r"AIza[0-9A-Za-z_\-]{35}")
_KV_PATTERN = re.compile(
    r"""(?i)(api[_-]?key|token|password|secret)\s*([:=])\s*['"]?([^\s'"]+)['"]?"""
)
_AUTH_HEADER_PATTERN = re.compile(
    r"""(?i)(authorization)\s*([:=])\s*['"]?(?!Bearer\s)([^\s'"]+)['"]?"""
)


def _scan_str(text: str) -> list[str]:
    findings: list[str] = []
    if _BEARER_PATTERN.search(text):
        findings.append("detected unredacted Bearer token")
    if _SK_PATTERN.search(text):
        findings.append("detected unredacted sk-... API key")
    if _AIZA_PATTERN.search(text):
        findings.append("detected unredacted AIza... Google API key")
    if _KV_PATTERN.search(text):
        findings.append("detected unredacted sensitive credential key-value pair")
    if _AUTH_HEADER_PATTERN.search(text):
        findings.append("detected unredacted Authorization header")
    return findings


def scan_for_secrets(payload: Any) -> tuple[str, ...]:
    """Recursively scan data structures for unredacted sensitive credentials."""
    findings: list[str] = []

    def _traverse(obj: Any) -> None:
        if isinstance(obj, str):
            findings.extend(_scan_str(obj))
        elif isinstance(obj, dict):
            for k, v in obj.items():
                if isinstance(k, str):
                    findings.extend(_scan_str(k))
                _traverse(v)
        elif isinstance(obj, (list, tuple, set)):
            for item in obj:
                _traverse(item)

    _traverse(payload)
    return tuple(findings)


def assert_no_secrets(payload: Any) -> None:
    """Raise SecretExposureError if any unredacted credential or secret is detected."""
    findings = scan_for_secrets(payload)
    if findings:
        raise SecretExposureError(f"unredacted secret detected: {'; '.join(findings)}")
