"""Optional Gemini CLI boundary; imports require neither Node nor Gemini CLI."""

from .adapter import GeminiAdapter
from .antigravity_transport import (
    AntigravityCliTransport,
    AntigravityNormalizer,
    antigravity_runtime_plan,
)
from .config import GeminiAdapterConfig
from .doctor import antigravity_runtime_info, find_agy_executable, runtime_info
from .normalizer import GeminiNormalizer
from .telemetry import GeminiTelemetryExtractor

__all__ = [
    "AntigravityCliTransport",
    "AntigravityNormalizer",
    "GeminiAdapter",
    "GeminiAdapterConfig",
    "GeminiNormalizer",
    "GeminiTelemetryExtractor",
    "antigravity_runtime_info",
    "antigravity_runtime_plan",
    "find_agy_executable",
    "runtime_info",
]
