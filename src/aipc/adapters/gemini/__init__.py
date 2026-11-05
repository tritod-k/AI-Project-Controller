"""Optional Gemini CLI boundary; imports require neither Node nor Gemini CLI."""

from .adapter import GeminiAdapter
from .config import GeminiAdapterConfig
from .normalizer import GeminiNormalizer
from .telemetry import GeminiTelemetryExtractor

__all__ = ["GeminiAdapter", "GeminiAdapterConfig", "GeminiNormalizer", "GeminiTelemetryExtractor"]
