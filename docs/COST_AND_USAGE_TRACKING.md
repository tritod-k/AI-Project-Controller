# Cost, Usage, and Performance Tracking

## Overview

AI Project Controller (AIPC) provides provider-neutral telemetry extraction, cost estimation, and per-agent performance tracking across all coding worker and reviewer runs.

## Key Principles

1. **Provider-Neutrality Boundary**:
   Core observability modules (`aipc.observability.metrics`, `aipc.observability.audit`) operate solely on canonical models (`AgentResult`, `TaskRequest`, `ExecutionMetricsRecord`). Provider-specific metadata structures (Codex, Claude, Gemini) are parsed only within adapter packages (`aipc.adapters.<provider>.telemetry`).
2. **Never Fabricate Missing Telemetry**:
   If a provider or model does not report token counts, tool calls, duration, or costs, the metric remains `None`. Values are never guessed or defaulted to `0.0`.
3. **No Automatic Routing from Performance Metrics**:
   Per `MASTER_PLAN.md` line 2871, historical performance metrics are purely diagnostic and are not used for automated routing decisions until explicitly authorized with sufficient statistical confidence (`sufficient_historical_data: True`, requiring at least 10 historical runs).

## Components

### `ProviderTelemetry`
Canonical telemetry payload containing:
- `model`: Model name / identifier
- `input_tokens`, `output_tokens`, `cached_tokens`: Token usage numbers
- `wall_time_seconds`: Elapsed wall clock time
- `agent_turns`: Number of interaction turns
- `tool_calls`: Number of command / file mutations performed
- `estimated_cost_usd`: Estimated monetary cost in USD

### `CostPolicy` & `ModelPricing`
Allows users to configure per-million token rates for models (`input_cost_per_million_usd`, `output_cost_per_million_usd`, `cached_input_cost_per_million_usd`). When `prefer_provider_reported_cost` is enabled and the provider supplies an authoritative cost (e.g., Claude), the provider's reported cost is preserved.

### `MetricsStore` & `MetricsTracker`
Provides atomic single-writer JSON persistence for per-project metrics ledgers (`{project_id}.metrics.json`). Ensures idempotent replay safety and allows in-place finalization when an attempt progresses from `NORMALIZED` to `GATED`.

### `AgentStatistics` & `ProjectMetricsSummary`
Aggregates run counts, success rates, revisions, token volumes, execution time, and cumulative cost across all agents associated with a project.
