"""Canonical models and packages for plugin and super-app extensions."""

from dataclasses import dataclass
from typing import Any, Literal, Self

from pydantic import Field, JsonValue, StrictBool, model_validator

from aipc.agents.base import AgentAdapter
from aipc.agents.models import AgentCapabilities
from aipc.agents.routing_models import AgentQualification, CapabilityStatus
from aipc.domain.models import CanonicalRecord, NonBlank, Timestamp
from aipc.mcp.models import ToolDescriptor
from aipc.normalization.base import AgentResultNormalizer
from aipc.observability.metrics import TelemetryExtractor


class ProviderCapabilityQualification(CanonicalRecord):
    """Declared runtime and capability qualification for a provider plugin."""

    agent_id: NonBlank
    provider: NonBlank
    observation_id: NonBlank = "plugin-qualification"
    runtime: CapabilityStatus = CapabilityStatus.AVAILABLE
    capabilities: dict[str, CapabilityStatus] = Field(default_factory=dict)

    def to_agent_qualification(self) -> AgentQualification:
        """Convert to AgentQualification for consumption by AgentRouter."""
        return AgentQualification(
            agent_id=self.agent_id,
            observation_id=self.observation_id,
            runtime=self.runtime,
            capabilities=self.capabilities,
        )


class ProviderPluginManifest(CanonicalRecord):
    """Declarative specification of a provider plugin and its configuration schema."""

    schema_version: Literal["1.0"] = "1.0"
    plugin_id: NonBlank
    provider: NonBlank
    agent_id: NonBlank
    version: NonBlank = "1.0.0"
    description: NonBlank
    capabilities: AgentCapabilities
    qualification: ProviderCapabilityQualification
    configuration_schema: dict[str, JsonValue] = Field(
        default_factory=lambda: {"type": "object", "properties": {}, "required": []}
    )

    @model_validator(mode="after")
    def validate_identity_alignment(self) -> Self:
        if hasattr(self.capabilities, "agent_id") and self.capabilities.agent_id != self.agent_id:
            raise ValueError(
                f"capabilities.agent_id '{self.capabilities.agent_id}' does not match "
                f"manifest.agent_id '{self.agent_id}'"
            )
        if self.qualification.agent_id != self.agent_id:
            raise ValueError(
                f"qualification.agent_id '{self.qualification.agent_id}' does not match "
                f"manifest.agent_id '{self.agent_id}'"
            )
        if self.qualification.provider != self.provider:
            raise ValueError(
                f"qualification.provider '{self.qualification.provider}' does not match "
                f"manifest.provider '{self.provider}'"
            )
        for cap_name, cap_status in self.qualification.capabilities.items():
            if cap_status == CapabilityStatus.AVAILABLE:
                declared = getattr(self.capabilities, cap_name, None)
                if declared is None:
                    declared = self.capabilities.extensions.get(cap_name, False)
                if not declared:
                    raise ValueError(
                        f"qualification capability '{cap_name}' is AVAILABLE, but "
                        f"manifest.capabilities does not enable '{cap_name}'"
                    )
        return self


class PluginVerificationReport(CanonicalRecord):
    """Receipt proving contract verification of a provider plugin package."""

    plugin_id: NonBlank
    provider: NonBlank
    agent_id: NonBlank
    adapter_contract_valid: StrictBool
    normalizer_contract_valid: StrictBool
    telemetry_contract_valid: StrictBool
    mcp_tools_valid: StrictBool
    probe_normalization_passed: StrictBool
    verified_at: Timestamp


@dataclass(frozen=True)
class ProviderPluginPackage:
    """Executable plugin bundle containing manifest, adapter, and normalizer."""

    manifest: ProviderPluginManifest
    adapter: AgentAdapter
    normalizer: AgentResultNormalizer
    telemetry_extractor: TelemetryExtractor | None = None
    mcp_tools: tuple[tuple[ToolDescriptor, Any], ...] = ()


__all__ = [
    "PluginVerificationReport",
    "ProviderCapabilityQualification",
    "ProviderPluginManifest",
    "ProviderPluginPackage",
]
