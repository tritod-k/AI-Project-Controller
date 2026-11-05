"""Comprehensive tests for AIPC Phase 22 Plugin / Super-App Extension Model."""

import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from aipc.agents.models import (
    AgentCapabilities,
    AgentRunHandle,
    ApprovalDecision,
    RawAgentResult,
    RunStatus,
)
from aipc.agents.registry import AgentRegistry
from aipc.agents.router import AgentRouter
from aipc.agents.routing_models import (
    CapabilityStatus,
    RoutingPolicy,
    RoutingRequest,
    TaskRequirements,
)
from aipc.controller.routing_models import WorkerBinding
from aipc.domain.enums import AgentStatus, RiskLevel
from aipc.domain.models import AgentResult, EvidenceReferences, TaskRequest
from aipc.mcp.models import (
    MCPToolCallRequest,
    ToolDescriptor,
)
from aipc.mcp.protocol import MCPToolServer
from aipc.mcp.registry import SharedToolRegistry
from aipc.normalization.context import NormalizationContext
from aipc.observability.audit import MetricsStore
from aipc.observability.metrics import (
    MetricsTracker,
    ProviderTelemetry,
)
from aipc.plugins.errors import (
    PluginContractError,
    PluginRegistrationError,
)
from aipc.plugins.models import (
    ProviderCapabilityQualification,
    ProviderPluginManifest,
    ProviderPluginPackage,
)
from aipc.plugins.registry import PluginRegistry
from aipc.plugins.verifier import verify_provider_plugin

# -----------------------------------------------------------------------------
# Synthetic 3rd-Party Provider Implementation
# -----------------------------------------------------------------------------


class SyntheticProviderAdapter:
    """Synthetic async adapter satisfying AgentAdapter protocol."""

    def __init__(self, agent_id: str = "synthetic-agent") -> None:
        self._agent_id = agent_id

    @property
    def agent_id(self) -> str:
        return self._agent_id

    @property
    def capabilities(self) -> AgentCapabilities:
        return AgentCapabilities(
            filesystem=True,
            shell=True,
        )

    async def start(self, task: TaskRequest) -> AgentRunHandle:
        return AgentRunHandle(
            task_id=task.task_id,
            agent_id=self._agent_id,
            run_id="run-synth-1",
        )

    async def wait(self, run: AgentRunHandle) -> RawAgentResult:
        return RawAgentResult(
            run=run,
            status=RunStatus.COMPLETED,
            payload="synthetic execution complete",
        )

    async def cancel(self, run: AgentRunHandle) -> None:
        pass

    async def respond_to_approval(self, run: AgentRunHandle, decision: ApprovalDecision) -> None:
        pass


class SyntheticProviderNormalizer:
    """Synthetic normalizer satisfying AgentResultNormalizer protocol."""

    def normalize(self, raw_result: RawAgentResult, context: NormalizationContext) -> AgentResult:
        return AgentResult(
            schema_version="1.0",
            task_id=context.task_id,
            agent_id=context.agent_id,
            provider=context.provider,
            status=AgentStatus.COMPLETED,
            summary="synthetic execution succeeded",
            started_at=context.started_at,
            completed_at=context.completed_at,
            files_changed=[],
            commands=[],
            tests=None,
            artifacts=[],
            warnings=[],
            errors=[],
            approval_requests=[],
            worker_claimed_complete=True,
            evidence=EvidenceReferences(
                git_diff_path=None,
                test_log_path=None,
                terminal_log_path=None,
                screenshots=[],
            ),
            raw_output_ref=context.raw_output_ref,
            metadata={"telemetry": {"input_tokens": 150, "output_tokens": 50}},
        )


class SyntheticTelemetryExtractor:
    """Synthetic extractor satisfying TelemetryExtractor protocol."""

    def extract(self, result: AgentResult) -> ProviderTelemetry:
        meta = result.metadata or {}
        telem = meta.get("telemetry", {})
        return ProviderTelemetry(
            input_tokens=telem.get("input_tokens"),
            output_tokens=telem.get("output_tokens"),
        )


def _make_synthetic_package(
    *,
    plugin_id: str = "synthetic-plugin",
    agent_id: str = "synthetic-agent",
    provider: str = "synthetic-provider",
    has_telemetry: bool = True,
    has_mcp_tool: bool = True,
) -> ProviderPluginPackage:
    adapter = SyntheticProviderAdapter(agent_id=agent_id)
    normalizer = SyntheticProviderNormalizer()
    extractor = SyntheticTelemetryExtractor() if has_telemetry else None

    qualification = ProviderCapabilityQualification(
        agent_id=agent_id,
        provider=provider,
        observation_id="synth-obs-1",
        runtime=CapabilityStatus.AVAILABLE,
        capabilities={
            "filesystem": CapabilityStatus.AVAILABLE,
            "shell": CapabilityStatus.AVAILABLE,
        },
    )

    manifest = ProviderPluginManifest(
        plugin_id=plugin_id,
        provider=provider,
        agent_id=agent_id,
        version="1.0.0",
        description="Synthetic Provider Extension",
        capabilities=adapter.capabilities,
        qualification=qualification,
        configuration_schema={
            "type": "object",
            "properties": {"api_key": {"type": "string"}},
            "required": ["api_key"],
        },
    )

    tools = ()
    if has_mcp_tool:
        desc = ToolDescriptor(
            tool_id="synthetic_tool",
            category="custom",
            description="Synthetic plugin analysis tool",
            capability="filesystem",
            risk_level=RiskLevel.LOW,
            default_enabled=True,
            input_schema={"type": "object", "properties": {"query": {"type": "string"}}},
        )

        def handler(task: TaskRequest, args: dict[str, Any]) -> str:
            return f"query processed: {args.get('query')}"

        tools = ((desc, handler),)

    return ProviderPluginPackage(
        manifest=manifest,
        adapter=adapter,
        normalizer=normalizer,
        telemetry_extractor=extractor,
        mcp_tools=tools,
    )


def _make_probe_result(agent_id: str = "synthetic-agent") -> RawAgentResult:
    return RawAgentResult(
        run=AgentRunHandle(
            task_id="probe-task-1",
            agent_id=agent_id,
            run_id="probe-run-1",
        ),
        status=RunStatus.COMPLETED,
        payload="probe output",
    )


def _make_task_request(task_id: str = "task-1") -> TaskRequest:
    return TaskRequest(
        schema_version="1.0",
        task_id=task_id,
        project_id="proj-1",
        phase_id="phase-1",
        attempt=1,
        task_type="coding",
        prompt="Synthetic task",
        workspace="H:/dummy/workspace",
        allowed_tools=["filesystem", "synthetic_tool"],
        forbidden_actions=[],
        timeout_seconds=300,
    )


# -----------------------------------------------------------------------------
# Architectural Invariant Tests
# -----------------------------------------------------------------------------


def test_provider_neutrality_no_adapter_imports() -> None:
    """MASTER_PLAN.md line 2974: src/aipc/plugins/ must not import aipc.adapters.*."""
    plugins_dir = Path("src/aipc/plugins")
    assert plugins_dir.exists(), "src/aipc/plugins directory must exist"

    for py_file in plugins_dir.glob("*.py"):
        content = py_file.read_text(encoding="utf-8")
        assert "aipc.adapters" not in content, (
            f"Provider neutrality violation: {py_file} contains 'aipc.adapters' import"
        )


def test_forbidden_core_files_not_modified() -> None:
    """MASTER_PLAN.md lines 2976-2984: Core files must NOT be edited for plugins."""
    forbidden_files = [
        "src/aipc/project/phase_manager.py",
        "src/aipc/review/phase_gate.py",
        "src/aipc/project/state_manager.py",
    ]
    for path in forbidden_files:
        res = subprocess.run(
            ["git", "diff", "aipc-phase-21-pass", "--", path],
            capture_output=True,
            text=True,
            check=True,
        )
        assert res.stdout.strip() == "", (
            f"Forbidden core file modified against aipc-phase-21-pass: {path}\n{res.stdout}"
        )

    # Core AgentResult schema in src/aipc/domain/models.py must also be untouched
    res = subprocess.run(
        ["git", "diff", "aipc-phase-21-pass", "--", "src/aipc/domain/models.py"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert res.stdout.strip() == "", (
        f"Core domain models modified against aipc-phase-21-pass:\n{res.stdout}"
    )


# -----------------------------------------------------------------------------
# Manifest & Model Validation Tests
# -----------------------------------------------------------------------------


def test_manifest_identity_alignment_validation() -> None:
    """ProviderPluginManifest must enforce strict cross-field identity alignment."""
    caps = AgentCapabilities(filesystem=True)
    qual = ProviderCapabilityQualification(
        agent_id="agent-a",
        provider="provider-x",
    )

    # Valid manifest
    manifest = ProviderPluginManifest(
        plugin_id="plugin-1",
        provider="provider-x",
        agent_id="agent-a",
        description="Plugin A",
        capabilities=caps,
        qualification=qual,
    )
    assert manifest.plugin_id == "plugin-1"

    # qualification.agent_id mismatch
    qual_agent_mismatch = ProviderCapabilityQualification(
        agent_id="agent-b",
        provider="provider-x",
    )
    with pytest.raises(ValidationError, match="qualification.agent_id"):
        ProviderPluginManifest(
            plugin_id="plugin-1",
            provider="provider-x",
            agent_id="agent-a",
            description="Plugin A",
            capabilities=caps,
            qualification=qual_agent_mismatch,
        )

    # qualification.provider mismatch
    qual_provider_mismatch = ProviderCapabilityQualification(
        agent_id="agent-a",
        provider="provider-y",
    )
    with pytest.raises(ValidationError, match="qualification.provider"):
        ProviderPluginManifest(
            plugin_id="plugin-1",
            provider="provider-x",
            agent_id="agent-a",
            description="Plugin A",
            capabilities=caps,
            qualification=qual_provider_mismatch,
        )

    # qualification capability AVAILABLE but unenabled in capabilities
    qual_cap_mismatch = ProviderCapabilityQualification(
        agent_id="agent-a",
        provider="provider-x",
        capabilities={"git": CapabilityStatus.AVAILABLE},
    )
    with pytest.raises(ValidationError, match="qualification capability 'git' is AVAILABLE"):
        ProviderPluginManifest(
            plugin_id="plugin-1",
            provider="provider-x",
            agent_id="agent-a",
            description="Plugin A",
            capabilities=caps,  # git=False by default
            qualification=qual_cap_mismatch,
        )


def test_qualification_to_agent_qualification() -> None:
    """ProviderCapabilityQualification converts cleanly to AgentQualification."""
    qual = ProviderCapabilityQualification(
        agent_id="synth-1",
        provider="synth-provider",
        observation_id="obs-42",
        runtime=CapabilityStatus.AVAILABLE,
        capabilities={"filesystem": CapabilityStatus.AVAILABLE},
    )
    agent_qual = qual.to_agent_qualification()
    assert agent_qual.agent_id == "synth-1"
    assert agent_qual.observation_id == "obs-42"
    assert agent_qual.runtime == CapabilityStatus.AVAILABLE
    assert agent_qual.capabilities == {"filesystem": CapabilityStatus.AVAILABLE}


# -----------------------------------------------------------------------------
# Contract Verifier Tests
# -----------------------------------------------------------------------------


def test_verify_provider_plugin_success_with_probe() -> None:
    """verify_provider_plugin returns a full verification report with probe normalization."""
    pkg = _make_synthetic_package()
    probe = _make_probe_result(agent_id=pkg.manifest.agent_id)

    report = verify_provider_plugin(pkg, probe_raw_result=probe)
    assert report.plugin_id == pkg.manifest.plugin_id
    assert report.provider == pkg.manifest.provider
    assert report.agent_id == pkg.manifest.agent_id
    assert report.adapter_contract_valid is True
    assert report.normalizer_contract_valid is True
    assert report.telemetry_contract_valid is True
    assert report.mcp_tools_valid is True
    assert report.probe_normalization_passed is True

    # Without probe, probe_normalization_passed must honestly report False
    report_no_probe = verify_provider_plugin(pkg)
    assert report_no_probe.probe_normalization_passed is False


def test_verify_provider_plugin_failure_modes() -> None:
    """verify_provider_plugin fails closed on contract deviations."""
    # 1. Non-package input
    with pytest.raises(PluginContractError, match="expected ProviderPluginPackage"):
        verify_provider_plugin("not-a-package")  # type: ignore

    # 2. Adapter does not implement AgentAdapter
    base_pkg = _make_synthetic_package()

    class BadAdapter:
        agent_id = "synthetic-agent"

    bad_pkg = ProviderPluginPackage(
        manifest=base_pkg.manifest,
        adapter=BadAdapter(),  # type: ignore
        normalizer=base_pkg.normalizer,
    )
    with pytest.raises(PluginContractError, match="does not implement AgentAdapter"):
        verify_provider_plugin(bad_pkg)

    # 3. Adapter lifecycle methods not async
    class SyncAdapter(SyntheticProviderAdapter):
        def start(self, task: TaskRequest) -> Any:  # type: ignore
            return None

    bad_pkg = ProviderPluginPackage(
        manifest=base_pkg.manifest,
        adapter=SyncAdapter(),
        normalizer=base_pkg.normalizer,
    )
    with pytest.raises(PluginContractError, match="must be an async coroutine"):
        verify_provider_plugin(bad_pkg)

    # 4. Adapter agent_id mismatch
    class MismatchedAdapter(SyntheticProviderAdapter):
        def __init__(self) -> None:
            super().__init__(agent_id="other-agent")

    bad_pkg = ProviderPluginPackage(
        manifest=base_pkg.manifest,
        adapter=MismatchedAdapter(),
        normalizer=base_pkg.normalizer,
    )
    with pytest.raises(PluginContractError, match="does not match manifest.agent_id"):
        verify_provider_plugin(bad_pkg)

    # 5. Normalizer missing normalize()
    class BadNormalizer:
        pass

    bad_pkg = ProviderPluginPackage(
        manifest=base_pkg.manifest,
        adapter=base_pkg.adapter,
        normalizer=BadNormalizer(),  # type: ignore
    )
    with pytest.raises(PluginContractError, match="does not implement AgentResultNormalizer"):
        verify_provider_plugin(bad_pkg)

    # 6. Invalid telemetry extractor
    class BadExtractor:
        pass

    bad_pkg = ProviderPluginPackage(
        manifest=base_pkg.manifest,
        adapter=base_pkg.adapter,
        normalizer=base_pkg.normalizer,
        telemetry_extractor=BadExtractor(),  # type: ignore
    )
    with pytest.raises(PluginContractError, match="does not implement TelemetryExtractor"):
        verify_provider_plugin(bad_pkg)

    # 7. Invalid MCP tool handler (non-callable)
    desc = ToolDescriptor(
        tool_id="bad_tool",
        category="custom",
        description="Bad tool",
        capability="filesystem",
        risk_level=RiskLevel.LOW,
        input_schema={"type": "object"},
    )
    bad_pkg = ProviderPluginPackage(
        manifest=base_pkg.manifest,
        adapter=base_pkg.adapter,
        normalizer=base_pkg.normalizer,
        mcp_tools=((desc, "not-callable"),),  # type: ignore
    )
    with pytest.raises(PluginContractError, match="is not callable"):
        verify_provider_plugin(bad_pkg)

    # 8. Probe normalization identity mismatch
    class IdentityMismatchNormalizer:
        def normalize(self, raw, context):
            return AgentResult(
                schema_version="1.0",
                task_id=context.task_id,
                agent_id="wrong-agent",
                provider=context.provider,
                status=AgentStatus.COMPLETED,
                summary="mismatch",
                started_at=context.started_at,
                completed_at=context.completed_at,
                files_changed=[],
                commands=[],
                tests=None,
                artifacts=[],
                warnings=[],
                errors=[],
                approval_requests=[],
                worker_claimed_complete=True,
                evidence=EvidenceReferences(
                    git_diff_path=None,
                    test_log_path=None,
                    terminal_log_path=None,
                    screenshots=[],
                ),
                raw_output_ref=None,
            )

    bad_pkg = ProviderPluginPackage(
        manifest=base_pkg.manifest,
        adapter=base_pkg.adapter,
        normalizer=IdentityMismatchNormalizer(),  # type: ignore
    )
    probe = _make_probe_result(agent_id=base_pkg.manifest.agent_id)
    with pytest.raises(PluginContractError, match="does not match manifest"):
        verify_provider_plugin(bad_pkg, probe_raw_result=probe)


# -----------------------------------------------------------------------------
# Plugin Registry Lifecycle Tests
# -----------------------------------------------------------------------------


def test_plugin_registry_registration_and_queries() -> None:
    """PluginRegistry registers valid packages, enforces unique IDs, and validates configuration."""
    registry = PluginRegistry()
    pkg = _make_synthetic_package(plugin_id="synth-1", agent_id="agent-1")

    # Missing required configuration (omitted config=None)
    with pytest.raises(
        PluginContractError, match="missing required configuration parameter 'api_key'"
    ):
        registry.register(pkg)

    # Missing required configuration (empty dict)
    with pytest.raises(
        PluginContractError, match="missing required configuration parameter 'api_key'"
    ):
        registry.register(pkg, config={})

    # Wrong configuration property type
    with pytest.raises(PluginContractError, match="must be of type 'string'"):
        registry.register(pkg, config={"api_key": 123})

    # Successful registration
    report = registry.register(pkg, config={"api_key": "secret-123"})
    assert report.plugin_id == "synth-1"
    assert len(registry) == 1
    assert registry.has("synth-1") is True
    assert registry.get("synth-1") == pkg
    assert registry.get_by_agent_id("agent-1") == pkg
    assert registry.get_report("synth-1") == report

    # Duplicate plugin_id rejected
    pkg_dup_plugin = _make_synthetic_package(plugin_id="synth-1", agent_id="agent-2")
    with pytest.raises(PluginRegistrationError, match="already registered"):
        registry.register(pkg_dup_plugin, config={"api_key": "k"})

    # Duplicate agent_id rejected
    pkg_dup_agent = _make_synthetic_package(plugin_id="synth-2", agent_id="agent-1")
    with pytest.raises(PluginRegistrationError, match="already registered by plugin"):
        registry.register(pkg_dup_agent, config={"api_key": "k"})

    # Query unknown plugin or agent
    with pytest.raises(PluginRegistrationError, match="not found in registry"):
        registry.get("unknown-plugin")
    with pytest.raises(PluginRegistrationError, match="not found in registered plugins"):
        registry.get_by_agent_id("unknown-agent")

    # Manifest listing
    pkg2 = _make_synthetic_package(plugin_id="synth-0", agent_id="agent-0")
    registry.register(pkg2, config={"api_key": "k"})
    manifests = registry.list_manifests()
    assert len(manifests) == 2
    assert [m.plugin_id for m in manifests] == ["synth-0", "synth-1"]


# -----------------------------------------------------------------------------
# Super-App Composition Bridge Integration Tests
# -----------------------------------------------------------------------------


def test_super_app_bridges_wire_into_agent_registry_and_router() -> None:
    """PluginRegistry wires plugin adapter and qualifications into AgentRegistry and AgentRouter."""
    plugin_reg = PluginRegistry()
    pkg = _make_synthetic_package(plugin_id="plug-router", agent_id="synth-worker")
    plugin_reg.register(pkg, config={"api_key": "k"})

    # 1. Wire into AgentRegistry
    agent_reg = AgentRegistry()
    plugin_reg.bind_agent_registry(agent_reg)
    assert "synth-worker" in agent_reg.agent_ids()
    assert agent_reg.get("synth-worker") == pkg.adapter

    # 2. Wire qualifications into AgentRouter
    router = AgentRouter(
        agent_reg,
        qualifications=lambda: plugin_reg.build_agent_qualifications(),
    )

    task = _make_task_request(task_id="route-task-1")
    requirements = TaskRequirements(required=("filesystem", "shell"))
    routing_req = RoutingRequest.from_task(
        task=task,
        run_id="run-1",
        requirements=requirements,
    )

    policy = RoutingPolicy(priorities={"synth-worker": 100})
    decision = router.select(routing_req, policy)
    assert decision.outcome == "SELECTED"
    assert decision.selected_agent_id == "synth-worker"


def test_super_app_bridges_wire_into_worker_bindings() -> None:
    """PluginRegistry builds controller WorkerBinding instances."""
    plugin_reg = PluginRegistry()
    pkg = _make_synthetic_package(
        plugin_id="plug-bind", agent_id="synth-worker", provider="synth-prov"
    )
    plugin_reg.register(pkg, config={"api_key": "k"})

    bindings = plugin_reg.build_worker_bindings()
    assert "synth-worker" in bindings
    binding = bindings["synth-worker"]
    assert isinstance(binding, WorkerBinding)
    assert binding.provider == "synth-prov"
    assert binding.normalizer == pkg.normalizer


def test_super_app_bridges_wire_into_metrics_tracker(tmp_path: Path) -> None:
    """PluginRegistry provides telemetry extractors that MetricsTracker uses."""
    plugin_reg = PluginRegistry()
    pkg = _make_synthetic_package(
        plugin_id="plug-metrics", agent_id="synth-worker", provider="synth-prov"
    )
    plugin_reg.register(pkg, config={"api_key": "k"})

    extractors = plugin_reg.build_telemetry_extractors()
    assert "synth-worker" in extractors

    store = MetricsStore(tmp_path / "metrics")
    tracker = MetricsTracker(store=store, extractors=extractors)

    # Normalizer produces AgentResult with telemetry metadata
    now = datetime.now(UTC)
    context = NormalizationContext(
        task_id="metric-task",
        agent_id="synth-worker",
        provider="synth-prov",
        started_at=now,
        completed_at=now,
        raw_output_ref=None,
    )
    result = pkg.normalizer.normalize(_make_probe_result("synth-worker"), context)

    # Telemetry extraction succeeds via plugin extractor
    extracted = tracker._get_extractor("synth-worker", "synth-prov").extract(result)
    assert extracted.input_tokens == 150
    assert extracted.output_tokens == 50


def test_super_app_bridges_wire_into_mcp_shared_tools() -> None:
    """PluginRegistry registers custom MCP tools and executes via MCPToolServer."""
    plugin_reg = PluginRegistry()
    pkg = _make_synthetic_package(plugin_id="plug-mcp", agent_id="synth-worker")
    plugin_reg.register(pkg, config={"api_key": "k"})

    tool_reg = SharedToolRegistry()
    plugin_reg.bind_mcp_tools(tool_reg)

    assert tool_reg.has("synthetic_tool") is True
    desc, _ = tool_reg.get("synthetic_tool")
    assert desc.tool_id == "synthetic_tool"
    assert desc.category == "custom"

    # Invoke tool via MCPToolServer
    task = _make_task_request(task_id="mcp-task-1")
    server = MCPToolServer(
        registry=tool_reg,
        task=task,
        capabilities=pkg.adapter.capabilities,
    )

    req = MCPToolCallRequest(
        call_id="call-1",
        tool_id="synthetic_tool",
        task_id=task.task_id,
        agent_id=server.agent_id,
        arguments={"query": "test query"},
    )
    result = server.call_tool(req)
    assert result.is_error is False
    assert len(result.content) == 1
    assert "query processed: test query" in result.content[0].text
