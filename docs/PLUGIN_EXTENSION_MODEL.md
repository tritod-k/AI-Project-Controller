# Plugin and Super-App Extension Model

## 1. Overview
Phase 22 implements the provider-neutral **Plugin and Super-App Extension Subsystem** (`src/aipc/plugins/`) according to `MASTER_PLAN.md` (lines 2960–2986). It enables adding 3rd-party, external, and future AI providers into AIPC through declarative manifests, strict contract verification, and composition bridges without editing core orchestration or domain models.

---

## 2. Core Architectural Invariants

### 2.1 Predictable Provider Plugin Package
A provider plugin requires primarily:
1. `adapter`: Implementation of the `AgentAdapter` protocol (`start`, `wait`, `cancel`, `respond_to_approval`, `capabilities`, `agent_id`).
2. `normalizer`: Implementation of the `AgentResultNormalizer` protocol (`normalize(raw_result, context) -> AgentResult`).
3. `capabilities`: `AgentCapabilities` declared on adapter and manifest.
4. `qualification`: `ProviderCapabilityQualification` specifying runtime availability and capability statuses.
5. `manifest`: `ProviderPluginManifest` with configuration schema and identity alignment constraints.
6. Optional `telemetry_extractor`: Implementation of the `TelemetryExtractor` protocol (`extract(result) -> ProviderTelemetry`).
7. Optional `mcp_tools`: Tuple of `(ToolDescriptor, ToolHandler)` pairs.

### 2.2 Forbidden Core File Edits (`MASTER_PLAN.md` lines 2976–2984)
Registering and executing a provider plugin **never** requires editing:
- `src/aipc/project/phase_manager.py`
- `src/aipc/review/phase_gate.py`
- `src/aipc/project/state_manager.py`
- `src/aipc/domain/models.py` (`AgentResult` schema)

### 2.3 Provider-Neutrality & Dependency Isolation
- Zero imports of `aipc.adapters.*` in `src/aipc/plugins/`.
- Zero new runtime dependencies: uses only Python standard library + existing Pydantic v2 `CanonicalRecord`.

---

## 3. Architecture & Components

```text
+--------------------------------------------------------------------------+
|                       ProviderPluginPackage                              |
|  - manifest: ProviderPluginManifest                                      |
|  - adapter: AgentAdapter                                                 |
|  - normalizer: AgentResultNormalizer                                     |
|  - telemetry_extractor: TelemetryExtractor (optional)                   |
|  - mcp_tools: tuple[tuple[ToolDescriptor, ToolHandler], ...] (optional) |
+--------------------------------------------------------------------------+
                                    |
                                    v
                     +-----------------------------+
                     |    verify_provider_plugin   |
                     |  - protocol conformance     |
                     |  - async lifecycle checks   |
                     |  - probe normalization      |
                     +-----------------------------+
                                    |
                                    v
                     +-----------------------------+
                     |       PluginRegistry        |
                     +-----------------------------+
                                    |
        +---------------------------+---------------------------+
        |                           |                           |
        v                           v                           v
+----------------+          +----------------+          +----------------+
| AgentRegistry  |          |  AgentRouter   |          | MetricsTracker |
| & Bindings     |          | Qualifications |          | & MCP Tools    |
+----------------+          +----------------+          +----------------+
```

### 3.1 Contract Verification (`src/aipc/plugins/verifier.py`)
`verify_provider_plugin` enforces:
1. Manifest validity and cross-field identity alignment (`capabilities.agent_id`, `qualification.agent_id`, `qualification.provider`).
2. `AgentAdapter` protocol conformance and async coroutine methods (`start`, `wait`, `cancel`, `respond_to_approval`).
3. `AgentResultNormalizer` protocol conformance and `normalize()` callable.
4. Optional `TelemetryExtractor` protocol conformance and `extract()` callable.
5. Optional MCP tool `ToolDescriptor` validation and callable handler verification.
6. Probe normalization verification: executes normalizer with a probe `RawAgentResult` and validates that the canonical `AgentResult` matches manifest identity.

### 3.2 Super-App Composition Bridges (`src/aipc/plugins/registry.py`)
`PluginRegistry` provides direct bridges into AIPC subsystems:
- `bind_agent_registry(registry)`: Registers each plugin's adapter into `AgentRegistry`.
- `build_worker_bindings()`: Produces `{agent_id: WorkerBinding(provider, normalizer)}` for controller dispatch.
- `build_agent_qualifications()`: Produces `AgentQualification` records for direct `AgentRouter` qualification consumption.
- `build_telemetry_extractors()`: Produces `{agent_id: TelemetryExtractor}` for `MetricsTracker` cost and token extraction.
- `bind_mcp_tools(tool_registry)`: Registers all declared plugin tools into `SharedToolRegistry` for `MCPToolServer`.
