# Phase 22 Specification: Plugin / Super-App Extension Model

## 1. Objective
Establish a predictable, provider-neutral Plugin and Super-App Extension Subsystem (`src/aipc/plugins/`) according to `MASTER_PLAN.md` lines 2960–2986 and `AGENTS.md`. The extension model enables 3rd-party and future AI providers to plug into AIPC seamlessly without modifying any core project management, state, review, or canonical domain schemas.

## 2. Core Architectural & Safety Invariants
1. **Predictable Provider Plugin Package**:
   A provider plugin requires only:
   - `adapter`: Implements `AgentAdapter` protocol (`start`, `wait`, `cancel`, `respond_to_approval`, `capabilities`, `agent_id`).
   - `normalizer`: Implements `AgentResultNormalizer` protocol (`normalize(raw_result, context) -> AgentResult`).
   - `provider capability declaration`: `AgentCapabilities` and `ProviderCapabilityQualification`.
   - `configuration`: `ProviderPluginManifest` specifying schema version, plugin identity, configuration JSON schema.
   - Optional `telemetry_extractor`: Implements `TelemetryExtractor` (`extract(result) -> ProviderTelemetry`).
   - Optional `mcp_tools`: Tuple of `(ToolDescriptor, ToolHandler)` pairs registered into `SharedToolRegistry`.
2. **Forbidden Core File Edits (`MASTER_PLAN.md` lines 2976–2984)**:
   Registering or operating plugins must NOT modify:
   - `src/aipc/project/phase_manager.py`
   - `src/aipc/review/phase_gate.py`
   - `src/aipc/project/state_manager.py`
   - `src/aipc/domain/models.py` (`AgentResult` schema)
3. **Zero New Runtime Dependencies (`AGENTS.md` Rules 14 & 23)**:
   Uses standard library + existing Pydantic v2 `CanonicalRecord`. Zero additions to `pyproject.toml`.
4. **Strict Provider Neutrality (`AGENTS.md` Rule 5)**:
   `src/aipc/plugins/` contains zero imports of `aipc.adapters.*`.
5. **Fail-Closed Contract Verification**:
   Plugins are verified before registration. Invalid capability mappings, missing lifecycle methods, mismatched agent identity, missing required configuration parameters, and failed probe normalizations fail closed with `PluginContractError` or `PluginRegistrationError`.

## 3. Data Models (`src/aipc/plugins/models.py`)
- `ProviderCapabilityQualification`:
  - `agent_id: NonBlank`
  - `provider: NonBlank`
  - `observation_id: NonBlank = "plugin-qualification"`
  - `runtime: CapabilityStatus = CapabilityStatus.AVAILABLE`
  - `capabilities: dict[str, CapabilityStatus] = Field(default_factory=dict)`
  - `to_agent_qualification() -> AgentQualification`
- `ProviderPluginManifest`:
  - `schema_version: Literal["1.0"] = "1.0"`
  - `plugin_id: NonBlank`
  - `provider: NonBlank`
  - `agent_id: NonBlank`
  - `version: NonBlank = "1.0.0"`
  - `description: NonBlank`
  - `capabilities: AgentCapabilities`
  - `qualification: ProviderCapabilityQualification`
  - `configuration_schema: dict[str, JsonValue]`
  - Enforces:
    - `capabilities.agent_id == agent_id`
    - `qualification.agent_id == agent_id`
    - `qualification.provider == provider`
- `PluginVerificationReport`:
  - `plugin_id: NonBlank`
  - `provider: NonBlank`
  - `agent_id: NonBlank`
  - `adapter_contract_valid: StrictBool`
  - `normalizer_contract_valid: StrictBool`
  - `telemetry_contract_valid: StrictBool`
  - `mcp_tools_valid: StrictBool`
  - `probe_normalization_passed: StrictBool`
  - `verified_at: Timestamp`
- `ProviderPluginPackage` (frozen dataclass):
  - `manifest: ProviderPluginManifest`
  - `adapter: Any` (`AgentAdapter`)
  - `normalizer: Any` (`AgentResultNormalizer`)
  - `telemetry_extractor: TelemetryExtractor | None = None`
  - `mcp_tools: tuple[tuple[ToolDescriptor, Any], ...] = ()`

## 4. Verification & Registry (`src/aipc/plugins/verifier.py`, `registry.py`)
- `verify_provider_plugin(package, probe_raw_result=None, clock=None) -> PluginVerificationReport`
- `PluginRegistry`:
  - `register(package, probe_raw_result=None, config=None) -> PluginVerificationReport`
  - `get(plugin_id) -> ProviderPluginPackage`
  - `get_by_agent_id(agent_id) -> ProviderPluginPackage`
  - `list_manifests() -> tuple[ProviderPluginManifest, ...]`
  - Super-app composition bridges:
    - `bind_agent_registry(registry: AgentRegistry) -> None`
    - `build_worker_bindings() -> dict[str, WorkerBinding]`
    - `build_qualifications() -> tuple[ProviderCapabilityQualification, ...]`
    - `build_agent_qualifications() -> tuple[AgentQualification, ...]`
    - `build_telemetry_extractors() -> dict[str, TelemetryExtractor]`
    - `bind_mcp_tools(tool_registry: SharedToolRegistry) -> None`
