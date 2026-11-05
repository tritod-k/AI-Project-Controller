# Phase 18 Specification: Cost, Usage, and Performance Tracking

## 1. Objectives & Boundaries

Phase 18 implements provider-neutral metrics, usage, and cost tracking for AI Project Controller (AIPC):
1. **Provider-Neutrality Boundary (`AGENTS.md` Rules 5, 6, 7)**:
   - Core observability (`src/aipc/observability/*`) must never import from `aipc.adapters.*`.
   - Core logic must never contain provider-specific branching (`if provider == ...`).
   - Normalization boundaries are preserved: raw provider responses are never passed to metrics tracking. Telemetry extraction operates strictly on canonical `AgentResult` records.
2. **Never Fabricate Missing Telemetry**:
   - Fields that are not reported by the provider (`input_tokens`, `output_tokens`, `cached_tokens`, `wall_time_seconds`, `agent_turns`, `tool_calls`, `estimated_cost_usd`) must remain `None`. Never invent `0` or `0.0` for unreported metrics.
3. **No Automatic Routing from Performance Metrics**:
   - `MASTER_PLAN.md` line 2871: Performance metrics must NOT be used for automatic routing. `AgentRouter` remains strictly static/deterministic.
   - `AgentStatistics.sufficient_historical_data` serves as a read-only readiness indicator (`total_runs >= min_historical_runs`, default `10`).

---

## 2. Data Models (`src/aipc/observability/metrics.py`)

### 2.1 Types
- `NonNegativeFloat`: Strict non-negative float (`ge=0.0`, no NaN/inf), accepts both float and int types.

### 2.2 `ProviderTelemetry`
Canonical model capturing extracted usage/cost metadata:
```python
class ProviderTelemetry(CanonicalRecord):
    model: NonBlank | None = None
    input_tokens: NonNegativeInt | None = None
    output_tokens: NonNegativeInt | None = None
    cached_tokens: NonNegativeInt | None = None
    wall_time_seconds: NonNegativeFloat | None = None
    agent_turns: NonNegativeInt | None = None
    tool_calls: NonNegativeInt | None = None
    estimated_cost_usd: NonNegativeFloat | None = None
```

### 2.3 `ModelPricing` & `CostPolicy`
- `ModelPricing`: Defines per-million token pricing for a `(provider, model)` pair.
  - `provider: NonBlank`
  - `model: NonBlank | None = None` (None acts as provider-level default)
  - `input_cost_per_million_usd: NonNegativeFloat`
  - `output_cost_per_million_usd: NonNegativeFloat`
  - `cached_input_cost_per_million_usd: NonNegativeFloat | None = None`
- `CostPolicy`:
  - `rates: tuple[ModelPricing, ...]`
  - `prefer_provider_reported_cost: StrictBool = True`
  - Validation: rejects duplicate `(provider, model)` entries.
  - Calculation:
    1. If `prefer_provider_reported_cost` and `telemetry.estimated_cost_usd is not None`, return reported cost.
    2. Lookup rate by `(provider, model)`, fallback to `(provider, None)`.
    3. If matching rate and input/output tokens exist, compute:
       `((input * rate.input + cached * rate.cached + output * rate.output) / 1_000_000.0)`
    4. Otherwise return `telemetry.estimated_cost_usd` or `None`.

### 2.4 `ExecutionMetricsRecord`
Persisted execution metric entry for a single attempt:
- `record_id: NonBlank`
- `project_id: NonBlank`, `run_id: NonBlank`, `phase_id: NonBlank`, `attempt: PositiveInt`, `task_id: NonBlank`, `agent_id: NonBlank`, `provider: NonBlank`, `model: NonBlank | None`
- `recorded_at: Timestamp`
- Telemetry dimensions (`input_tokens`, `output_tokens`, `cached_tokens`, `wall_time_seconds`, `agent_turns`, `tool_calls`, `estimated_cost_usd`)
- Execution outcome (`revision_count`, `agent_status`, `gate_verdict`, `succeeded`, `result_digest`)
- Invariants:
  - `revision_count == attempt - 1`
  - `succeeded == (agent_status == AgentStatus.COMPLETED and (gate_verdict is None or gate_verdict == ReviewVerdict.PASS))`

### 2.5 `AgentStatistics` & `ProjectMetricsSummary`
Aggregates per-agent performance over historical records:
- `total_runs`, `successful_runs`, `failed_runs`, `revised_runs`, `cancelled_runs`, `timed_out_runs`
- `success_rate`, `total_revisions`
- `total_input_tokens`, `total_output_tokens`, `total_cached_tokens`
- `total_wall_time_seconds`, `average_wall_time_seconds`
- `total_agent_turns`, `total_tool_calls`
- `total_estimated_cost_usd`, `runs_with_token_data`, `runs_with_cost_data`
- `sufficient_historical_data: StrictBool` (`total_runs >= min_historical_runs`)

---

## 3. Storage & Persistence (`src/aipc/observability/audit.py`)

- `MetricsStore`:
  - File-based atomic JSON storage at `root / f"{project_id}.metrics.json"` using `write_json_atomic`.
  - Replay idempotency: identical `record_id` with identical content is a no-op.
  - Finalization: an existing record with `gate_verdict is None` can be updated in-place when gated (`gate_verdict is not None`), updating `succeeded` and `recorded_at`.
  - Conflicting replays raise `TelemetryError`.

---

## 4. Provider Adapter Extractors (`src/aipc/adapters/<provider>/telemetry.py`)

- `CodexTelemetryExtractor`: Parses `result.metadata["codex"]["usage"]` (`total` preferred over `last`), `agent_turns` from `turn_id`, `tool_calls` from commands and files.
- `ClaudeTelemetryExtractor`: Parses `result.metadata["claude"]`, extracts tokens from `usage`, `total_cost_usd`, `duration_ms`.
- `GeminiTelemetryExtractor`: Parses `result.metadata["gemini"]`, extracts tokens from `usage`, `duration_ms`.
- `MockTelemetryExtractor`: Delegates to `CanonicalTelemetryExtractor`.
- Provider extractors reside strictly in adapter packages and are injected into `MetricsTracker`.

---

## 5. Controller Integration (`src/aipc/controller/engine.py`)

- Optional `metrics_tracker: MetricsTracker | None = None`.
- Disjoint root validation: `metrics_tracker.store.root` must not overlap repository, worktree, or evidence roots.
- Records attempt at `NORMALIZED` stage (`gate_verdict=None`).
- Records attempt at `GATED` stage (`gate_verdict=decision.verdict`), finalizing the record.
