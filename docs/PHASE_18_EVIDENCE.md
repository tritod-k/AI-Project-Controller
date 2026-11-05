# Phase 18 Evidence — Cost, Usage, and Performance Tracking

Stable starting point: `aipc-phase-17-pass` / `74bb4974c826cdb00b44259a4af71509d3d56dcf`.
Attempt branch: `codex/phase-18-attempt-01`. No stable promotion, merge, or Phase 19 work.

Implementation and completion report belong to the containing attempt commit; the actual commit SHA will be recorded upon commit creation. A containing commit cannot embed its own hash.

---

## 1. Scope & Implementation Summary

Phase 18 implements provider-neutral Cost, Usage, and Performance Tracking:
- **`src/aipc/observability/` (Provider-Neutral Core)**:
  - `src/aipc/observability/metrics.py`:
    - `ProviderTelemetry`: Canonical telemetry record capturing `input_tokens`, `output_tokens`, `cached_tokens`, `wall_time_seconds`, `agent_turns`, `tool_calls`, and `estimated_cost_usd`.
    - `TelemetryExtractor` (Protocol) & `CanonicalTelemetryExtractor`: Structural interface and canonical projection for reading telemetry from `AgentResult.metadata["telemetry"]`.
    - `ModelPricing` & `CostPolicy`: Model-based pricing calculation (`input_per_million`, `output_per_million`, `cached_per_million`) with safe fallback to `estimated_cost_usd`.
    - `ExecutionMetricsRecord`: Immutable per-attempt audit record with strict invariants (`revision_count == attempt - 1`, `succeeded == (agent_status == COMPLETED and gate_verdict in (None, PASS))`).
    - `AgentStatistics`: Read-only aggregated historical metrics exposing `sufficient_historical_data` (default `total_runs >= 10`).
    - `ProjectMetricsSummary`: Aggregated project-level cost, token, time, and success statistics.
    - `MetricsTracker`: In-memory coordinator for tracking attempts, finalizing gate verdicts, and reporting agent/project summaries.
  - `src/aipc/observability/audit.py`:
    - `MetricsLedger`: Single-file persistent document schema (`{project_id}.metrics.json`).
    - `MetricsStore`: Atomic persistence using `write_json_atomic` and `load_json`, enforcing exact-match replay idempotency and in-place gate verdict finalization (`NORMALIZED` -> `GATED`).
  - `src/aipc/observability/errors.py`:
    - `TelemetryError`: Typed domain error for telemetry validation failures, corrupt records, conflicting replays, and disjoint root violations.
  - `src/aipc/observability/__init__.py`:
    - Clean public exports of all models, extractors, policies, stores, and error types.
- **Provider-Specific Telemetry Extractors (`src/aipc/adapters/<provider>/telemetry.py`)**:
  - `CodexTelemetryExtractor`: Extracts usage from `usage.prompt_tokens`, `completion_tokens`, `cached_tokens`, `wall_time_seconds`, `turn_count`, `tool_call_count`.
  - `ClaudeTelemetryExtractor`: Extracts usage from `usage.input_tokens`, `output_tokens`, `cache_read_input_tokens`, `wall_time_seconds`, `turns`, `tool_calls`.
  - `GeminiTelemetryExtractor`: Extracts usage from `usageMetadata.promptTokenCount`, `candidatesTokenCount`, `cachedContentTokenCount`, `duration_seconds`, `turn_count`, `tool_calls`.
  - `MockTelemetryExtractor`: Extracts simulated telemetry from mock execution metadata.
- **Strict Invariant: Never Fabricate Missing Telemetry**:
  - Unreported provider telemetry values remain strictly `None`. They are never defaulted to `0` or `0.0`.
- **Strict Invariant: AgentRouter Remains Static / Deterministic**:
  - `AgentRouter` is strictly uncoupled from runtime metrics. Performance statistics remain read-only audit facts.
- **Controller Lifecycle Integration (`src/aipc/controller/engine.py`)**:
  - Added optional `metrics_tracker: MetricsTracker | None = None` to `Controller.__init__`.
  - Validates disjoint metrics store root against repo, worktree, and evidence roots.
  - Injects telemetry hooks at `CANCELLED`, `NORMALIZED` (`gate_verdict=None`), and `GATED` (`gate_verdict=decision.verdict`).

---

## 2. Test Execution & Targeted Verification Receipts

Targeted test execution verified Phase 18 functional boundaries without requiring repeated 90-minute full suite execution.

### 2.1 Targeted Phase 18 Observability Unit Suite (13/13 PASS)
Executed via `.venv\Scripts\python.exe -m pytest tests/observability/test_metrics.py -v`:
- `test_provider_neutrality_no_adapter_imports`: Verifies `aipc.observability` has zero imports of `aipc.adapters`.
- `test_canonical_telemetry_extractor`: Verifies `CanonicalTelemetryExtractor` extracts telemetry from canonical `AgentResult.metadata`.
- `test_codex_telemetry_extractor`: Verifies `CodexTelemetryExtractor` projects Codex-native usage dictionaries.
- `test_claude_telemetry_extractor`: Verifies `ClaudeTelemetryExtractor` projects Claude-native usage dictionaries.
- `test_gemini_telemetry_extractor`: Verifies `GeminiTelemetryExtractor` projects Gemini-native usage dictionaries.
- `test_mock_telemetry_extractor`: Verifies `MockTelemetryExtractor` extracts mock telemetry.
- `test_never_fabricate_missing_telemetry`: Verifies omitted fields remain strictly `None` and are never fabricated as 0.
- `test_cost_policy_calculations`: Verifies model pricing calculation, token scaling, cached token pricing, and explicit cost override.
- `test_execution_metrics_record_invariants`: Verifies invariant enforcement (`revision_count == attempt - 1`, success matching).
- `test_metrics_store_persistence_and_finalization`: Verifies atomic disk persistence, replay idempotency, conflicting replay rejection, and in-place gate finalization.
- `test_agent_statistics_and_summary_aggregation`: Verifies agent stats calculation, `sufficient_historical_data` threshold, and project summary aggregation.
- `test_metrics_tracker_coordination`: Verifies tracker end-to-end attempt recording and summary extraction.
- `test_router_remains_static_no_metrics_coupling`: Verifies `AgentRouter` has no metrics dependency or dynamic performance rerouting.
- **Total Unit Suite**: **13 passed in 1.31s**.

### 2.2 Controller Integration Suite (2/2 PASS)
Executed via `.venv\Scripts\python.exe -m pytest tests/observability/test_controller_metrics.py -v`:
- `test_controller_records_metrics_for_attempt_and_gate_pass`: Verifies `Controller.run_phase` with `MetricsTracker` records normalized attempt, updates record in-place to `gate_verdict=PASS`, and persists to disk.
- `test_controller_disjoint_metrics_root_validation`: Verifies `Controller.__init__` rejects overlapping metrics store roots.
- **Total Integration Suite**: **2 passed in 174.57s**.

### 2.3 Contract & Controller Compatibility Suites (113/113 PASS)
Executed via `.venv\Scripts\python.exe -m pytest tests/contract tests/controller/test_contracts.py -q`:
- `tests/contract`: 98 passed in 17.71s.
- `tests/controller/test_contracts.py`: 15 passed in 0.41s.
- **Total Contract Suite**: **113 passed in 18.12s**.

### 2.4 Fast Quality Harness (10/10 PASS)
Executed via `.venv\Scripts\python.exe .superpowers/sdd/2026-10-05-phase-16/run_fast.py`:
- `ruff check`: 0 errors (PASS)
- `ruff format --check`: 0 unformatted files (PASS)
- `pip check`: 0 dependency conflicts (PASS)
- `domain-schemas`: PASS
- `project-schemas`: PASS
- `cli-help`: PASS
- `diff-check`: PASS
- `wheel build`: PASS
- `base-install`: PASS
- `base-import`: PASS
- **Fast Harness Exit Code**: **0 (100% PASS)**.

---

## 3. Preserved Provider Qualifications

All existing provider boundaries and limitations from Phases 12, 13, 14, 15, 16, and 17 remain strictly preserved:
- **Codex**: Validated coding worker; existing documented limitations unchanged.
- **Claude**: Adapter implementation PASS; production coding/tool capability NOT READY / BLOCKED.
- **Gemini**: Adapter implementation PASS; offline contracts PASS; production coding/runtime NOT READY / BLOCKED.
- **Independent LLM Reviewer**: Implementation PASS; real credentialed quality UNVERIFIED.
- **Agent Router**: Deterministic static routing; no metrics coupling.
- **Multi-Reviewer Consensus**: All 4 modes verified; fail-closed error/blocker precedence preserved.

---

## 4. Evidence Reuse Policy & Rationale

Per `AGENTS.md` Section 31.2, untouched modules from earlier approved phases (Phases 00–17) retain their verified regression receipts. Rerunning the full 90-minute Git worktree test suite for untouched modules is unnecessary because:
1. Untouched subsystem code has zero modifications.
2. The Fast Quality Harness validated formatting, linting, packaging, and schema validity for all modules.
3. Fresh targeted unit, contract, and Controller integration tests verified 100% green execution across the new and modified observability components.
