"""Integration tests: MetricsTracker integrated into Controller lifecycle."""

from pathlib import Path

import pytest
from tests.controller.support import FixtureWorker, controller, run

from aipc.controller.errors import ControllerError
from aipc.domain.enums import ReviewVerdict
from aipc.observability import MetricsStore, MetricsTracker


def test_controller_metrics_disjoint_root_validation(target, tmp_path: Path):
    # Metrics root inside evidence destination
    overlapping_store = MetricsStore(Path(target["destination"]) / "metrics")
    tracker = MetricsTracker(store=overlapping_store)

    with pytest.raises(ControllerError, match="metrics store root must be disjoint"):
        controller(target, metrics_tracker=tracker)

    # Metrics root inside repository root
    repo_store = MetricsStore(Path(target["versions"].repository) / "metrics")
    tracker_repo = MetricsTracker(store=repo_store)

    with pytest.raises(ControllerError, match="metrics store root must be disjoint"):
        controller(target, metrics_tracker=tracker_repo)


def test_controller_tracks_attempt_metrics_and_finalization(target, tmp_path: Path):
    metrics_root = tmp_path / "disjoint_metrics_root"
    store = MetricsStore(metrics_root)
    tracker = MetricsTracker(store=store)

    # Attempt 1 produces 9 passed (rule requires 10) -> REVISE
    # Attempt 2 produces 10 passed -> PASS
    worker = FixtureWorker(logs=("9 passed in 0.1s\n", "10 passed in 0.1s\n"))
    engine, _ = controller(target, worker=worker, metrics_tracker=tracker)

    result = run(target, engine, auto_repair=True, max_attempts=2)

    assert result.outcome == "PASS"
    assert result.review_verdict == "PASS"
    assert result.stable_checkpoint is not None

    # Load recorded execution metrics for fixture project
    records = store.load_records("fixture")
    assert len(records) == 2

    # Attempt 1: Revised
    rec1 = records[0]
    assert rec1.attempt == 1
    assert rec1.revision_count == 0
    assert rec1.gate_verdict == ReviewVerdict.REVISE
    assert rec1.succeeded is False

    # Attempt 2: Passed
    rec2 = records[1]
    assert rec2.attempt == 2
    assert rec2.revision_count == 1
    assert rec2.gate_verdict == ReviewVerdict.PASS
    assert rec2.succeeded is True

    # Check project metrics summary
    summary = tracker.summarize("fixture")
    assert summary.total_records == 2
    assert len(summary.agents) == 1

    agent_stat = summary.agents[0]
    assert agent_stat.agent_id == "worker"
    assert agent_stat.total_runs == 2
    assert agent_stat.successful_runs == 1
    assert agent_stat.revised_runs == 1
    assert agent_stat.total_revisions == 1
    assert agent_stat.success_rate == 0.5
    assert agent_stat.sufficient_historical_data is False
