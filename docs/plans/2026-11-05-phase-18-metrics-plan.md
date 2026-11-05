# Phase 18 Implementation Plan: Cost, Usage, and Performance Tracking

## Work Breakdown

1. **Phase 18 Pre-Setup & Error Hierarchy**:
   - Create `src/aipc/observability/errors.py` with `TelemetryError`.
   - Re-export `TelemetryError` in `src/aipc/domain/errors.py` and `src/aipc/observability/__init__.py`.

2. **Core Observability Models & Metrics Engine**:
   - Implement `src/aipc/observability/metrics.py`:
     - `NonNegativeFloat` validator
     - `ProviderTelemetry`
     - `TelemetryExtractor` Protocol & `CanonicalTelemetryExtractor`
     - `ModelPricing` & `CostPolicy`
     - `ExecutionMetricsRecord`
     - `AgentStatistics` & `ProjectMetricsSummary`
     - `MetricsTracker`

3. **Storage & Audit Persistence**:
   - Implement `src/aipc/observability/audit.py`:
     - `MetricsLedger`
     - `MetricsStore` (atomic single-writer JSON persistence, replay idempotency, in-place gate finalization, summary aggregation)

4. **Provider Adapter Telemetry Extractors**:
   - `src/aipc/adapters/codex/telemetry.py` (`CodexTelemetryExtractor`)
   - `src/aipc/adapters/claude/telemetry.py` (`ClaudeTelemetryExtractor`)
   - `src/aipc/adapters/gemini/telemetry.py` (`GeminiTelemetryExtractor`)
   - `src/aipc/adapters/mock/telemetry.py` (`MockTelemetryExtractor`)
   - Export extractors in their respective adapter packages.

5. **Controller Integration**:
   - Update `src/aipc/controller/engine.py`:
     - Accept optional `metrics_tracker: MetricsTracker | None = None`
     - Disjoint root validation for `metrics_tracker.store.root`
     - Record attempt at `NORMALIZED` (`gate_verdict=None`)
     - Finalize attempt at `GATED` (`gate_verdict=decision.verdict`)
     - Include in `_configuration()` fingerprint when present

6. **Targeted Verification & Testing**:
   - `tests/observability/test_metrics.py`: Unit and contract tests for extraction, pricing, ledger persistence, replay idempotency, statistics, historical readiness threshold, and provider-neutrality assertions.
   - `tests/observability/test_controller_metrics.py`: End-to-end Controller lifecycle integration test with metrics recording across revision and pass stages.

7. **Documentation & Handoff**:
   - `docs/COST_AND_USAGE_TRACKING.md`
   - `docs/ARCHITECTURE.md` and `MASTER_PLAN.md` updates
   - `docs/PHASE_18_EVIDENCE.md` and `docs/PHASE_18_COMPLETION_REPORT.md`
   - `CURRENT_STATE.md` updated to `Phase 18 — Cost, Usage, and Performance Tracking`, `Status: AWAITING_REVIEW`, `Phase Gate: PENDING`.
