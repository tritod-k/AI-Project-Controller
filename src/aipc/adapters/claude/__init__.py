"""Optional provider integration; importing it never imports the external SDK."""

from .adapter import ClaudeAdapter
from .config import ClaudeAdapterConfig
from .events import ClaudeRawResult
from .normalizer import ClaudeNormalizer
from .telemetry import ClaudeTelemetryExtractor

__all__ = [
    "ClaudeAdapter",
    "ClaudeAdapterConfig",
    "ClaudeNormalizer",
    "ClaudeRawResult",
    "ClaudeTelemetryExtractor",
]
