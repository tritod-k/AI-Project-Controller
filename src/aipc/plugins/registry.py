"""Plugin registry and super-app composition bridges."""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from aipc.agents.models import RawAgentResult
from aipc.agents.registry import AgentRegistry
from aipc.agents.routing_models import AgentQualification
from aipc.controller.routing_models import WorkerBinding
from aipc.mcp.registry import SharedToolRegistry
from aipc.observability.metrics import TelemetryExtractor
from aipc.plugins.errors import PluginContractError, PluginRegistrationError
from aipc.plugins.models import (
    PluginVerificationReport,
    ProviderCapabilityQualification,
    ProviderPluginManifest,
    ProviderPluginPackage,
)
from aipc.plugins.verifier import verify_provider_plugin


class PluginRegistry:
    """Central registry for provider plugin packages and super-app wiring."""

    def __init__(self, *, clock: Callable[[], datetime] | None = None) -> None:
        self._plugins: dict[str, ProviderPluginPackage] = {}
        self._by_agent_id: dict[str, str] = {}
        self._reports: dict[str, PluginVerificationReport] = {}
        self._clock = clock or (lambda: datetime.now(UTC))

    def register(
        self,
        package: ProviderPluginPackage,
        *,
        probe_raw_result: RawAgentResult | None = None,
        config: dict[str, Any] | None = None,
    ) -> PluginVerificationReport:
        """Register and contract-verify a provider plugin package.

        Enforces:
        - Unique plugin_id across all registered plugins.
        - Unique agent_id across all registered plugins.
        - Valid configuration matching manifest.configuration_schema required properties.
        - Complete contract verification of adapter, normalizer, telemetry, and MCP tools.
        - Successful probe normalization matching manifest identity.

        Raises:
            PluginRegistrationError: If plugin_id or agent_id is already registered.
            PluginContractError: If configuration is missing required keys or verification fails.
        """
        if not isinstance(package, ProviderPluginPackage):
            raise PluginRegistrationError(
                f"expected ProviderPluginPackage, got {type(package).__name__}"
            )

        manifest = package.manifest
        if manifest.plugin_id in self._plugins:
            raise PluginRegistrationError(f"plugin '{manifest.plugin_id}' is already registered")
        if manifest.agent_id in self._by_agent_id:
            existing = self._by_agent_id[manifest.agent_id]
            raise PluginRegistrationError(
                f"agent '{manifest.agent_id}' is already registered by plugin '{existing}'"
            )

        # Validate configuration against manifest schema
        if config is not None and not isinstance(config, dict):
            raise PluginContractError("config must be a dictionary")

        effective_config = config if config is not None else {}
        required_keys = manifest.configuration_schema.get("required", [])
        if isinstance(required_keys, list):
            for key in required_keys:
                if key not in effective_config:
                    raise PluginContractError(
                        f"missing required configuration parameter '{key}' "
                        f"for plugin '{manifest.plugin_id}'"
                    )

        properties = manifest.configuration_schema.get("properties", {})
        if isinstance(properties, dict):
            for key, val in effective_config.items():
                if key in properties and isinstance(properties[key], dict):
                    expected_type = properties[key].get("type")
                    type_ok = True
                    if expected_type == "string":
                        type_ok = isinstance(val, str)
                    elif expected_type == "integer":
                        type_ok = isinstance(val, int) and not isinstance(val, bool)
                    elif expected_type == "number":
                        type_ok = isinstance(val, int | float) and not isinstance(val, bool)
                    elif expected_type == "boolean":
                        type_ok = isinstance(val, bool)
                    elif expected_type == "object":
                        type_ok = isinstance(val, dict)
                    elif expected_type == "array":
                        type_ok = isinstance(val, list | tuple)

                    if not type_ok:
                        raise PluginContractError(
                            f"configuration parameter '{key}' for plugin "
                            f"'{manifest.plugin_id}' must be of type "
                            f"'{expected_type}', got '{type(val).__name__}'"
                        )

        # Execute contract verification
        report = verify_provider_plugin(
            package, probe_raw_result=probe_raw_result, clock=self._clock
        )

        # Store verified package
        self._plugins[manifest.plugin_id] = package
        self._by_agent_id[manifest.agent_id] = manifest.plugin_id
        self._reports[manifest.plugin_id] = report
        return report

    def get(self, plugin_id: str) -> ProviderPluginPackage:
        """Retrieve a registered plugin package by plugin_id."""
        if plugin_id not in self._plugins:
            raise PluginRegistrationError(f"plugin '{plugin_id}' not found in registry")
        return self._plugins[plugin_id]

    def get_by_agent_id(self, agent_id: str) -> ProviderPluginPackage:
        """Retrieve a registered plugin package by agent_id."""
        if agent_id not in self._by_agent_id:
            raise PluginRegistrationError(f"agent '{agent_id}' not found in registered plugins")
        return self._plugins[self._by_agent_id[agent_id]]

    def get_report(self, plugin_id: str) -> PluginVerificationReport:
        """Retrieve verification report for a registered plugin."""
        if plugin_id not in self._reports:
            raise PluginRegistrationError(f"verification report for plugin '{plugin_id}' not found")
        return self._reports[plugin_id]

    def list_manifests(self) -> tuple[ProviderPluginManifest, ...]:
        """Return all registered plugin manifests sorted deterministically by plugin_id."""
        return tuple(self._plugins[pid].manifest for pid in sorted(self._plugins.keys()))

    def list_plugin_ids(self) -> tuple[str, ...]:
        """Return all registered plugin IDs sorted deterministically."""
        return tuple(sorted(self._plugins.keys()))

    def has(self, plugin_id: str) -> bool:
        """Check whether a plugin_id is registered."""
        return plugin_id in self._plugins

    def __len__(self) -> int:
        return len(self._plugins)

    # -------------------------------------------------------------------------
    # Super-App Composition Bridges
    # -------------------------------------------------------------------------

    def bind_agent_registry(self, registry: AgentRegistry) -> None:
        """Register all plugin adapters into an AIPC AgentRegistry."""
        for pkg in self._plugins.values():
            registry.register(pkg.adapter)

    def build_worker_bindings(self) -> dict[str, WorkerBinding]:
        """Produce controller WorkerBinding instances for all registered plugins."""
        return {
            pkg.manifest.agent_id: WorkerBinding(
                provider=pkg.manifest.provider,
                normalizer=pkg.normalizer,
            )
            for pkg in self._plugins.values()
        }

    def build_qualifications(self) -> tuple[ProviderCapabilityQualification, ...]:
        """Produce ProviderCapabilityQualification records for all registered plugins."""
        return tuple(pkg.manifest.qualification for pkg in self._plugins.values())

    def build_agent_qualifications(self) -> tuple[AgentQualification, ...]:
        """Produce AgentQualification records suitable for direct AgentRouter consumption."""
        return tuple(
            pkg.manifest.qualification.to_agent_qualification() for pkg in self._plugins.values()
        )

    def build_telemetry_extractors(self) -> dict[str, TelemetryExtractor]:
        """Produce agent_id -> TelemetryExtractor mapping for plugins that declare telemetry."""
        return {
            pkg.manifest.agent_id: pkg.telemetry_extractor
            for pkg in self._plugins.values()
            if pkg.telemetry_extractor is not None
        }

    def bind_mcp_tools(self, tool_registry: SharedToolRegistry) -> None:
        """Register all declared plugin MCP tools into an AIPC SharedToolRegistry."""
        for pkg in self._plugins.values():
            for desc, handler in pkg.mcp_tools:
                tool_registry.register(desc, handler)


__all__ = ["PluginRegistry"]
