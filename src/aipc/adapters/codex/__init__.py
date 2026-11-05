"""First real provider; importing this package never imports or launches the SDK."""

from .adapter import CodexAdapter
from .config import CodexAdapterConfig
from .errors import CodexAdapterError
from .events import CodexRawResult
from .normalizer import CodexNormalizer
from .telemetry import CodexTelemetryExtractor

__all__ = [
    "CodexAdapter",
    "CodexAdapterConfig",
    "CodexAdapterError",
    "CodexNormalizer",
    "CodexRawResult",
    "CodexTelemetryExtractor",
]
