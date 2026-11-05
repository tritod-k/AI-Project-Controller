"""Contract verification for provider plugin packages."""

from collections.abc import Callable
from datetime import UTC, datetime
from inspect import iscoroutinefunction

from aipc.agents.base import AgentAdapter
from aipc.agents.models import AgentCapabilities, RawAgentResult
from aipc.domain.models import AgentResult
from aipc.mcp.models import ToolDescriptor
from aipc.normalization.base import AgentResultNormalizer
from aipc.normalization.context import NormalizationContext
from aipc.observability.metrics import TelemetryExtractor
from aipc.plugins.errors import PluginContractError
from aipc.plugins.models import (
    PluginVerificationReport,
    ProviderPluginManifest,
    ProviderPluginPackage,
)


def verify_provider_plugin(
    package: ProviderPluginPackage,
    *,
    probe_raw_result: RawAgentResult | None = None,
    clock: Callable[[], datetime] | None = None,
) -> PluginVerificationReport:
    """Verify that a provider plugin package conforms to all required AIPC contracts.

    Validates:
    1. ProviderPluginManifest validity and internal identity alignment.
    2. AgentAdapter protocol implementation, async lifecycle methods, agent_id, and capabilities.
    3. AgentResultNormalizer protocol implementation and normalize() method.
    4. TelemetryExtractor protocol implementation if present.
    5. ToolDescriptor and callable handler contracts for MCP tools if present.
    6. Probe normalization producing a canonical AgentResult matching manifest identity.

    Raises:
        PluginContractError: If any contract verification check fails.
    """
    if not isinstance(package, ProviderPluginPackage):
        raise PluginContractError(f"expected ProviderPluginPackage, got {type(package).__name__}")

    # 1. Manifest verification
    try:
        manifest = ProviderPluginManifest.model_validate(package.manifest)
    except Exception as exc:
        raise PluginContractError(f"invalid plugin manifest: {exc}") from exc

    # 2. Adapter protocol verification
    adapter = package.adapter
    if not isinstance(adapter, AgentAdapter):
        raise PluginContractError(
            f"adapter for plugin '{manifest.plugin_id}' does not implement AgentAdapter"
        )
    for method in ("start", "wait", "cancel", "respond_to_approval"):
        func = getattr(adapter, method, None)
        if not callable(func) or not iscoroutinefunction(func):
            raise PluginContractError(
                f"adapter method '{method}' on plugin '{manifest.plugin_id}' "
                "must be an async coroutine"
            )
    if adapter.agent_id != manifest.agent_id:
        raise PluginContractError(
            f"adapter.agent_id '{adapter.agent_id}' does not match "
            f"manifest.agent_id '{manifest.agent_id}'"
        )
    try:
        caps = AgentCapabilities.model_validate(adapter.capabilities)
        if caps != manifest.capabilities:
            raise PluginContractError(
                f"adapter.capabilities do not match manifest.capabilities "
                f"on plugin '{manifest.plugin_id}'"
            )
    except Exception as exc:
        raise PluginContractError(f"invalid adapter capabilities: {exc}") from exc

    # 3. Normalizer protocol verification
    normalizer = package.normalizer
    if not isinstance(normalizer, AgentResultNormalizer):
        raise PluginContractError(
            f"normalizer for plugin '{manifest.plugin_id}' does not implement AgentResultNormalizer"
        )
    if not callable(getattr(normalizer, "normalize", None)):
        raise PluginContractError(
            f"normalizer for plugin '{manifest.plugin_id}' missing callable normalize() method"
        )

    # 4. Telemetry extractor verification (optional)
    telemetry_valid = True
    if package.telemetry_extractor is not None:
        extractor = package.telemetry_extractor
        if not isinstance(extractor, TelemetryExtractor) or not callable(
            getattr(extractor, "extract", None)
        ):
            raise PluginContractError(
                f"telemetry_extractor for plugin '{manifest.plugin_id}' "
                "does not implement TelemetryExtractor"
            )

    # 5. MCP tools verification (optional)
    mcp_tools_valid = True
    if package.mcp_tools:
        if not isinstance(package.mcp_tools, tuple | list):
            raise PluginContractError(
                f"mcp_tools for plugin '{manifest.plugin_id}' must be a sequence of "
                "(ToolDescriptor, handler) pairs"
            )
        for idx, item in enumerate(package.mcp_tools):
            if not isinstance(item, tuple | list) or len(item) != 2:
                raise PluginContractError(
                    f"mcp_tools[{idx}] for plugin '{manifest.plugin_id}' must be a "
                    "2-tuple (ToolDescriptor, handler)"
                )
            desc, handler = item
            try:
                ToolDescriptor.model_validate(desc)
            except Exception as exc:
                raise PluginContractError(
                    f"invalid tool descriptor in mcp_tools[{idx}] on plugin "
                    f"'{manifest.plugin_id}': {exc}"
                ) from exc
            if not callable(handler):
                raise PluginContractError(
                    f"tool handler in mcp_tools[{idx}] on plugin '{manifest.plugin_id}' "
                    "is not callable"
                )

    # 6. Probe normalization verification (optional probe input)
    probe_passed = False
    now = (clock or (lambda: datetime.now(UTC)))()
    if probe_raw_result is not None:
        if not isinstance(probe_raw_result, RawAgentResult):
            raise PluginContractError(
                f"expected RawAgentResult for probe, got {type(probe_raw_result).__name__}"
            )
        probe = probe_raw_result
        try:
            from aipc.agents.models import AgentRunHandle

            AgentRunHandle.model_validate(probe.run)
        except Exception as exc:
            raise PluginContractError(f"invalid probe raw agent result run handle: {exc}") from exc

        if probe.run.agent_id != manifest.agent_id:
            raise PluginContractError(
                f"probe raw result agent_id '{probe.run.agent_id}' does not match "
                f"manifest.agent_id '{manifest.agent_id}'"
            )

        context = NormalizationContext(
            task_id=probe.run.task_id,
            agent_id=manifest.agent_id,
            provider=manifest.provider,
            started_at=now,
            completed_at=now,
            raw_output_ref="plugin-probe.json",
        )

        try:
            raw_normalized = normalizer.normalize(probe, context)
            canonical_result = AgentResult.model_validate(raw_normalized)
        except Exception as exc:
            raise PluginContractError(
                f"probe normalization failed for plugin '{manifest.plugin_id}': {exc}"
            ) from exc

        if (canonical_result.agent_id, canonical_result.provider) != (
            manifest.agent_id,
            manifest.provider,
        ):
            raise PluginContractError(
                f"probe normalized result identity "
                f"({canonical_result.agent_id}, {canonical_result.provider}) "
                f"does not match manifest ({manifest.agent_id}, {manifest.provider})"
            )
        probe_passed = True
    else:
        probe_passed = False

    return PluginVerificationReport(
        plugin_id=manifest.plugin_id,
        provider=manifest.provider,
        agent_id=manifest.agent_id,
        adapter_contract_valid=True,
        normalizer_contract_valid=True,
        telemetry_contract_valid=telemetry_valid,
        mcp_tools_valid=mcp_tools_valid,
        probe_normalization_passed=probe_passed,
        verified_at=now,
    )


__all__ = ["verify_provider_plugin"]
