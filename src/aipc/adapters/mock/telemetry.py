"""Telemetry extraction for Mock agent."""

from aipc.domain.models import AgentResult
from aipc.observability.metrics import CanonicalTelemetryExtractor, ProviderTelemetry


class MockTelemetryExtractor:
    """Extracts ProviderTelemetry from Mock AgentResult."""

    def __init__(self):
        self._canonical = CanonicalTelemetryExtractor()

    def extract(self, result: AgentResult) -> ProviderTelemetry:
        return self._canonical.extract(result)
