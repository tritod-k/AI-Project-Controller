"""Unit and contract tests for metrics, cost policy, and telemetry tracking."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from aipc.adapters.claude.telemetry import ClaudeTelemetryExtractor
from aipc.adapters.codex.telemetry import CodexTelemetryExtractor
from aipc.adapters.gemini.telemetry import GeminiTelemetryExtractor
from aipc.adapters.mock.telemetry import MockTelemetryExtractor
from aipc.agents.router import AgentRouter
from aipc.domain.enums import AgentStatus, ReviewVerdict
from aipc.domain.models import AgentResult, TaskRequest
from aipc.observability import (
    CanonicalTelemetryExtractor,
    CostPolicy,
    ExecutionMetricsRecord,
    MetricsStore,
    MetricsTracker,
    ModelPricing,
    ProjectMetricsSummary,
    ProviderTelemetry,
    TelemetryError,
)


def _base_agent_result(**overrides) -> AgentResult:
    now = datetime(2026, 11, 5, 12, 0, 0, tzinfo=UTC)
    data = {
        "schema_version": "1.0",
        "task_id": "task-001",
        "agent_id": "worker-1",
        "provider": "test-provider",
        "status": AgentStatus.COMPLETED,
        "started_at": now - timedelta(seconds=10),
        "completed_at": now,
        "summary": "Completed successfully.",
        "files_changed": [{"path": "file.py", "change_type": "modified"}],
        "commands": [{"command": "pytest", "exit_code": 0}],
        "tests": None,
        "artifacts": [],
        "warnings": [],
        "errors": [],
        "approval_requests": [],
        "worker_claimed_complete": True,
        "evidence": {
            "git_diff_path": None,
            "test_log_path": None,
            "terminal_log_path": None,
            "screenshots": [],
        },
        "raw_output_ref": None,
        "metadata": {},
    }
    data.update(overrides)
    return AgentResult.model_validate(data)


def _base_task_request(**overrides) -> TaskRequest:
    data = {
        "schema_version": "1.0",
        "task_id": "task-001",
        "project_id": "test-project",
        "phase_id": "18",
        "attempt": 1,
        "task_type": "code",
        "prompt": "Execute phase",
        "workspace": "C:/workspace",
        "allowed_tools": ["run_command"],
        "forbidden_actions": [],
        "timeout_seconds": 300,
        "metadata": {},
    }
    data.update(overrides)
    return TaskRequest.model_validate(data)


# 1. Provider-Neutrality: Zero Adapter Imports in Core Observability
def test_provider_neutrality_no_adapter_imports():
    observability_dir = Path("src/aipc/observability")
    for file_path in observability_dir.glob("*.py"):
        content = file_path.read_text(encoding="utf-8")
        assert "aipc.adapters" not in content, (
            f"Provider adapter import forbidden in core observability: {file_path}"
        )


# 2. Canonical Telemetry Extractor
def test_canonical_telemetry_extractor():
    extractor = CanonicalTelemetryExtractor()

    # Valid telemetry metadata
    res = _base_agent_result(
        metadata={
            "telemetry": {
                "model": "canonical-model",
                "input_tokens": 100,
                "output_tokens": 50,
                "cached_tokens": 20,
                "wall_time_seconds": 4.5,
                "agent_turns": 2,
                "tool_calls": 3,
                "estimated_cost_usd": 0.0012,
            }
        }
    )
    tel = extractor.extract(res)
    assert tel.model == "canonical-model"
    assert tel.input_tokens == 100
    assert tel.output_tokens == 50
    assert tel.cached_tokens == 20
    assert tel.wall_time_seconds == 4.5
    assert tel.agent_turns == 2
    assert tel.tool_calls == 3
    assert tel.estimated_cost_usd == 0.0012

    # Missing metadata returns empty ProviderTelemetry with None
    res_empty = _base_agent_result(metadata={})
    tel_empty = extractor.extract(res_empty)
    assert tel_empty.input_tokens is None
    assert tel_empty.output_tokens is None
    assert tel_empty.estimated_cost_usd is None

    # Malformed metadata raises TelemetryError
    res_malformed = _base_agent_result(metadata={"telemetry": "not-a-dict"})
    with pytest.raises(TelemetryError, match="must be a dictionary"):
        extractor.extract(res_malformed)

    res_invalid_tokens = _base_agent_result(metadata={"telemetry": {"input_tokens": -5}})
    with pytest.raises(TelemetryError, match="malformed telemetry metadata"):
        extractor.extract(res_invalid_tokens)


# 3. Codex Telemetry Extractor
def test_codex_telemetry_extractor():
    extractor = CodexTelemetryExtractor()

    # Normal case with usage.total
    res = _base_agent_result(
        provider="codex",
        metadata={
            "codex": {
                "turn_id": "turn-123",
                "usage": {
                    "total": {
                        "inputTokens": 500,
                        "outputTokens": 250,
                        "cachedInputTokens": 100,
                    }
                },
            }
        },
    )
    tel = extractor.extract(res)
    assert tel.input_tokens == 500
    assert tel.output_tokens == 250
    assert tel.cached_tokens == 100
    assert tel.agent_turns == 1
    # 1 command + 1 file = 2
    assert tel.tool_calls == 2

    # Fallback to usage.last if total absent
    res_last = _base_agent_result(
        provider="codex",
        metadata={
            "codex": {
                "turn_id": "turn-456",
                "usage": {
                    "last": {
                        "input_tokens": 300,
                        "output_tokens": 150,
                    }
                },
            }
        },
    )
    tel_last = extractor.extract(res_last)
    assert tel_last.input_tokens == 300
    assert tel_last.output_tokens == 150
    assert tel_last.cached_tokens is None

    # Missing usage leaves tokens None
    res_no_usage = _base_agent_result(provider="codex", metadata={"codex": {}})
    tel_no_usage = extractor.extract(res_no_usage)
    assert tel_no_usage.input_tokens is None
    assert tel_no_usage.output_tokens is None

    # Malformed/negative token value raises TelemetryError
    res_bad = _base_agent_result(
        provider="codex",
        metadata={"codex": {"usage": {"total": {"inputTokens": -1}}}},
    )
    with pytest.raises(TelemetryError, match="invalid usage token value"):
        extractor.extract(res_bad)


# 4. Claude Telemetry Extractor
def test_claude_telemetry_extractor():
    extractor = ClaudeTelemetryExtractor()

    res = _base_agent_result(
        provider="claude",
        metadata={
            "claude": {
                "model": "claude-3-5-sonnet-20241022",
                "session_id": "sess-999",
                "total_cost_usd": 0.045,
                "duration_ms": 3200,
                "usage": {
                    "input_tokens": 1200,
                    "output_tokens": 400,
                    "cache_read_input_tokens": 300,
                    "cache_creation_input_tokens": 100,
                },
            }
        },
    )
    tel = extractor.extract(res)
    assert tel.model == "claude-3-5-sonnet-20241022"
    assert tel.input_tokens == 1200
    assert tel.output_tokens == 400
    assert tel.cached_tokens == 400  # 300 + 100
    assert tel.estimated_cost_usd == 0.045
    assert tel.wall_time_seconds == 3.2
    assert tel.agent_turns == 1

    # Missing claude metadata returns empty
    assert extractor.extract(_base_agent_result()).input_tokens is None

    # Negative total_cost_usd raises TelemetryError
    res_bad_cost = _base_agent_result(metadata={"claude": {"total_cost_usd": -0.5}})
    with pytest.raises(TelemetryError, match="invalid claude total_cost_usd"):
        extractor.extract(res_bad_cost)


# 5. Gemini Telemetry Extractor
def test_gemini_telemetry_extractor():
    extractor = GeminiTelemetryExtractor()

    res = _base_agent_result(
        provider="gemini",
        metadata={
            "gemini": {
                "model": "gemini-1.5-pro",
                "turn_id": "gemini-turn-1",
                "duration_ms": 2500,
                "usage": {
                    "input_tokens": 800,
                    "output_tokens": 200,
                    "cached_tokens": 50,
                },
            }
        },
    )
    tel = extractor.extract(res)
    assert tel.model == "gemini-1.5-pro"
    assert tel.input_tokens == 800
    assert tel.output_tokens == 200
    assert tel.cached_tokens == 50
    assert tel.wall_time_seconds == 2.5
    assert tel.agent_turns == 1

    # Missing duration_ms leaves wall_time_seconds None
    res_no_dur = _base_agent_result(metadata={"gemini": {"model": "gemini-flash"}})
    tel_no_dur = extractor.extract(res_no_dur)
    assert tel_no_dur.wall_time_seconds is None


# 6. Mock Telemetry Extractor
def test_mock_telemetry_extractor():
    extractor = MockTelemetryExtractor()
    res = _base_agent_result(metadata={"telemetry": {"input_tokens": 42, "output_tokens": 10}})
    tel = extractor.extract(res)
    assert tel.input_tokens == 42
    assert tel.output_tokens == 10


# 7. Invariant: Never Fabricate Missing Telemetry
def test_never_fabricate_missing_telemetry():
    tel = ProviderTelemetry()
    assert tel.input_tokens is None
    assert tel.output_tokens is None
    assert tel.cached_tokens is None
    assert tel.wall_time_seconds is None
    assert tel.agent_turns is None
    assert tel.tool_calls is None
    assert tel.estimated_cost_usd is None

    # String float values are strictly rejected
    with pytest.raises(ValidationError, match="value must be a strict int or float"):
        ProviderTelemetry(wall_time_seconds="1.5")  # type: ignore[arg-type]

    with pytest.raises(ValidationError, match="value must be a strict int or float"):
        ProviderTelemetry(estimated_cost_usd="0.045")  # type: ignore[arg-type]

    # Boolean is rejected (not float/int)
    with pytest.raises(ValidationError, match="value must be a strict int or float"):
        ProviderTelemetry(wall_time_seconds=True)  # type: ignore[arg-type]


# 8. Cost Policy Estimation & Duplicate Validation
def test_cost_policy_calculations():
    # Duplicate (provider, model) rates raise ValueError
    with pytest.raises(ValidationError, match="duplicate rate entry"):
        CostPolicy(
            rates=(
                ModelPricing(
                    provider="p1",
                    model="m1",
                    input_cost_per_million_usd=3.0,
                    output_cost_per_million_usd=15.0,
                ),
                ModelPricing(
                    provider="p1",
                    model="m1",
                    input_cost_per_million_usd=2.5,
                    output_cost_per_million_usd=10.0,
                ),
            )
        )

    policy = CostPolicy(
        rates=(
            ModelPricing(
                provider="anthropic",
                model="claude-3-5-sonnet",
                input_cost_per_million_usd=3.0,
                output_cost_per_million_usd=15.0,
                cached_input_cost_per_million_usd=0.30,
            ),
            ModelPricing(
                provider="anthropic",
                model=None,  # Fallback for anthropic
                input_cost_per_million_usd=5.0,
                output_cost_per_million_usd=20.0,
            ),
        ),
        prefer_provider_reported_cost=True,
    )

    # 1. Prefer provider reported cost when present
    tel_reported = ProviderTelemetry(
        model="claude-3-5-sonnet",
        input_tokens=1_000_000,
        output_tokens=1_000_000,
        estimated_cost_usd=0.0123,
    )
    assert policy.estimate_cost("anthropic", tel_reported) == 0.0123

    # 2. Compute cost with cached token rate when provider cost not reported
    policy_no_prefer = policy.model_copy(update={"prefer_provider_reported_cost": False})
    tel_cached = ProviderTelemetry(
        model="claude-3-5-sonnet",
        input_tokens=1_000_000,  # $3.00
        cached_tokens=1_000_000,  # $0.30
        output_tokens=1_000_000,  # $15.00
        estimated_cost_usd=0.0123,
    )
    # Expected: (1M * 3.0 + 1M * 0.30 + 1M * 15.0) / 1M = 18.30 USD
    cost = policy_no_prefer.estimate_cost("anthropic", tel_cached)
    assert cost == 18.30

    # 3. Fallback to provider-level pricing when model not explicitly matched
    tel_unknown_model = ProviderTelemetry(
        model="unknown-claude-model",
        input_tokens=1_000_000,  # $5.00
        output_tokens=1_000_000,  # $20.00
    )
    assert policy.estimate_cost("anthropic", tel_unknown_model) == 25.0

    # 4. Return None when no matching rate and no reported cost
    tel_other_provider = ProviderTelemetry(
        model="other-model",
        input_tokens=100,
        output_tokens=50,
    )
    assert policy.estimate_cost("other-provider", tel_other_provider) is None


# 9. Execution Metrics Record Invariants
def test_execution_metrics_record_invariants():
    now = datetime(2026, 11, 5, 12, 0, 0, tzinfo=UTC)
    base_kwargs = {
        "record_id": "rec-1",
        "project_id": "proj-1",
        "run_id": "run-1",
        "phase_id": "18",
        "attempt": 2,
        "task_id": "task-1",
        "agent_id": "agent-1",
        "provider": "codex",
        "recorded_at": now,
        "revision_count": 1,  # attempt (2) - 1
        "agent_status": AgentStatus.COMPLETED,
        "gate_verdict": ReviewVerdict.PASS,
        "succeeded": True,
        "result_digest": "a" * 64,
    }

    # Valid
    rec = ExecutionMetricsRecord(**base_kwargs)
    assert rec.revision_count == 1
    assert rec.succeeded is True

    # revision_count != attempt - 1 raises ValueError
    bad_rev = dict(base_kwargs, revision_count=0)
    with pytest.raises(ValidationError, match="revision_count.*must equal attempt - 1"):
        ExecutionMetricsRecord(**bad_rev)

    # succeeded must match agent_status and gate_verdict
    bad_succ = dict(base_kwargs, succeeded=False)
    with pytest.raises(ValidationError, match="succeeded.*does not match"):
        ExecutionMetricsRecord(**bad_succ)


# 10. Metrics Store Persistence & Replay Idempotency
def test_metrics_store_persistence_and_finalization(tmp_path: Path):
    store = MetricsStore(tmp_path / "metrics_root")
    now = datetime(2026, 11, 5, 12, 0, 0, tzinfo=UTC)

    # 1. Reject invalid project IDs
    with pytest.raises(TelemetryError, match="invalid project_id"):
        store.load_records("../traversal")

    rec_normal = ExecutionMetricsRecord(
        record_id="rec-001",
        project_id="proj-alpha",
        run_id="run-1",
        phase_id="18",
        attempt=1,
        task_id="task-1",
        agent_id="worker-codex",
        provider="codex",
        recorded_at=now,
        input_tokens=500,
        output_tokens=100,
        revision_count=0,
        agent_status=AgentStatus.COMPLETED,
        gate_verdict=None,  # Ungated initially at NORMALIZED stage
        succeeded=True,
        result_digest="b" * 64,
    )

    # Record initially
    saved = store.record(rec_normal)
    assert saved.record_id == "rec-001"
    assert len(store.load_records("proj-alpha")) == 1

    # Idempotent replay of identical record is no-op
    replayed = store.record(rec_normal)
    assert replayed == rec_normal
    assert len(store.load_records("proj-alpha")) == 1

    # In-place finalization: update gate_verdict to PASS
    rec_finalized = rec_normal.model_copy(
        update={
            "gate_verdict": ReviewVerdict.PASS,
            "recorded_at": now + timedelta(seconds=5),
        }
    )
    finalized = store.record(rec_finalized)
    assert finalized.gate_verdict == ReviewVerdict.PASS
    assert len(store.load_records("proj-alpha")) == 1

    # Conflicting replay raises TelemetryError
    rec_conflict = rec_normal.model_copy(
        update={
            "input_tokens": 9999,  # Changed tokens
            "gate_verdict": ReviewVerdict.PASS,
        }
    )
    with pytest.raises(TelemetryError, match="conflicting metrics record"):
        store.record(rec_conflict)

    # Restart reload from new store instance
    fresh_store = MetricsStore(tmp_path / "metrics_root")
    records = fresh_store.load_records("proj-alpha")
    assert len(records) == 1
    assert records[0].record_id == "rec-001"
    assert records[0].gate_verdict == ReviewVerdict.PASS


# 11. Agent Statistics and Project Summary Aggregation
def test_agent_statistics_and_summary_aggregation(tmp_path: Path):
    store = MetricsStore(tmp_path)
    now = datetime(2026, 11, 5, 12, 0, 0, tzinfo=UTC)

    # Create 12 runs for worker-1 (>= 10, sufficient_historical_data=True)
    # 10 PASS, 1 REVISE, 1 FAILED
    for i in range(1, 13):
        if i <= 10:
            status, gate, succ = AgentStatus.COMPLETED, ReviewVerdict.PASS, True
        elif i == 11:
            status, gate, succ = AgentStatus.COMPLETED, ReviewVerdict.REVISE, False
        else:
            status, gate, succ = AgentStatus.FAILED, ReviewVerdict.REVISE, False

        rec = ExecutionMetricsRecord(
            record_id=f"run-{i:02d}",
            project_id="proj-stats",
            run_id="run-1",
            phase_id="18",
            attempt=1,
            task_id=f"task-{i}",
            agent_id="worker-1",
            provider="codex",
            recorded_at=now + timedelta(minutes=i),
            input_tokens=1000,
            output_tokens=200,
            wall_time_seconds=10.0,
            estimated_cost_usd=0.01,
            revision_count=0,
            agent_status=status,
            gate_verdict=gate,
            succeeded=succ,
            result_digest="c" * 64,
        )
        store.record(rec)

    # Create 3 runs for worker-2 (< 10, sufficient_historical_data=False)
    for j in range(1, 4):
        rec2 = ExecutionMetricsRecord(
            record_id=f"w2-run-{j}",
            project_id="proj-stats",
            run_id="run-1",
            phase_id="18",
            attempt=j,
            task_id=f"w2-task-{j}",
            agent_id="worker-2",
            provider="claude",
            recorded_at=now + timedelta(hours=1, minutes=j),
            input_tokens=2000,
            output_tokens=500,
            wall_time_seconds=20.0,
            estimated_cost_usd=0.05,
            revision_count=j - 1,
            agent_status=AgentStatus.COMPLETED,
            gate_verdict=ReviewVerdict.PASS,
            succeeded=True,
            result_digest="d" * 64,
        )
        store.record(rec2)

    summary = store.summarize("proj-stats", min_historical_runs=10)
    assert isinstance(summary, ProjectMetricsSummary)
    assert summary.total_records == 15
    assert len(summary.agents) == 2

    # Verify worker-1 stats
    w1 = next(a for a in summary.agents if a.agent_id == "worker-1")
    assert w1.total_runs == 12
    assert w1.successful_runs == 10
    assert w1.failed_runs == 1
    assert w1.revised_runs == 2
    assert w1.success_rate == round(10 / 12, 4)
    assert w1.total_input_tokens == 12_000
    assert w1.total_output_tokens == 2_400
    assert w1.total_wall_time_seconds == 120.0
    assert w1.average_wall_time_seconds == 10.0
    assert w1.total_estimated_cost_usd == 0.12
    assert w1.sufficient_historical_data is True

    # Verify worker-2 stats
    w2 = next(a for a in summary.agents if a.agent_id == "worker-2")
    assert w2.total_runs == 3
    assert w2.successful_runs == 3
    assert w2.success_rate == 1.0
    assert w2.total_revisions == 0 + 1 + 2  # 3
    assert w2.sufficient_historical_data is False  # 3 < 10

    # Total project metrics
    assert summary.total_estimated_cost_usd == round(0.12 + 0.15, 6)
    assert summary.total_wall_time_seconds == 120.0 + 60.0


# 12. Metrics Tracker Coordination & Error Handling
def test_metrics_tracker_coordination(tmp_path: Path):
    store = MetricsStore(tmp_path)
    tracker = MetricsTracker(
        store=store,
        cost_policy=CostPolicy(),
        extractors={"codex": CodexTelemetryExtractor()},
    )

    task = _base_task_request(task_id="t-1", attempt=1)
    result = _base_agent_result(task_id="t-1", provider="codex")

    # Happy path
    rec = tracker.record_attempt(task, result, gate_verdict=ReviewVerdict.PASS)
    assert rec.task_id == "t-1"
    assert rec.succeeded is True
    assert len(store.load_records("test-project")) == 1

    # Replay with advanced clock (+5s) returns existing record idempotently
    time_advanced = rec.recorded_at + timedelta(seconds=5)
    tracker_advanced = MetricsTracker(
        store=store,
        cost_policy=CostPolicy(),
        extractors={"codex": CodexTelemetryExtractor()},
        clock=lambda: time_advanced,
    )
    rec_replayed = tracker_advanced.record_attempt(task, result, gate_verdict=ReviewVerdict.PASS)
    assert rec_replayed.record_id == rec.record_id
    assert rec_replayed.recorded_at == rec.recorded_at
    assert len(store.load_records("test-project")) == 1

    # Identity mismatch raises TelemetryError
    bad_task = _base_task_request(task_id="different-task", attempt=1)
    with pytest.raises(TelemetryError, match="task_id mismatch"):
        tracker.record_attempt(bad_task, result)

    # Invalid input raises TelemetryError
    with pytest.raises(TelemetryError):
        tracker.record_attempt("invalid", result)


# 13. Safety Invariant: Router Remains Static / Decoupled from Metrics
def test_router_remains_static_no_metrics_coupling():
    # Router module and class must not reference metrics or observability
    import aipc.agents.router as router_mod

    assert not hasattr(router_mod, "MetricsTracker")
    assert not hasattr(router_mod, "MetricsStore")
    assert not hasattr(AgentRouter, "metrics_tracker")
    assert not hasattr(AgentRouter, "metrics_store")
