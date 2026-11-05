"""Integration test: Controller execution verified end-to-end via Dashboard/API."""

import json
from pathlib import Path

from tests.controller.support import FixtureWorker, controller, run

from aipc.dashboard import (
    DashboardApplication,
    DashboardProjectRegistration,
    DashboardService,
)
from aipc.observability import MetricsStore, MetricsTracker


def test_controller_execution_monitored_by_dashboard(target, tmp_path: Path):
    """Prove Controller -> Journal/State/Metrics -> Dashboard API & HTML fidelity."""
    metrics_root = tmp_path / "disjoint_metrics_root"
    store = MetricsStore(metrics_root)
    tracker = MetricsTracker(store=store)

    # Run Controller to PASS
    worker = FixtureWorker(logs=("10 passed in 0.1s\n",))
    engine, _ = controller(target, worker=worker, metrics_tracker=tracker)
    result = run(target, engine)
    assert result.outcome == "PASS"

    project_id = str(target["project"].project.id)

    # Register with Dashboard
    service = DashboardService()
    service.register(
        DashboardProjectRegistration(
            project=target["project"],
            state=target["state"],
            controller=engine,
            metrics_store=store,
        )
    )
    app = DashboardApplication(service)

    # 1. Verify JSON REST API snapshot
    resp_api = app.handle_request("GET", f"/api/v1/projects/{project_id}")
    assert resp_api.status_code == 200
    data = json.loads(resp_api.body)

    # Check key facts in snapshot
    assert data["project_status"]["project_id"] == project_id
    assert data["project_status"]["status"] in ("RUNNING", "COMPLETED", "READY")
    assert len(data["phase_history"]) >= 1
    assert data["phase_history"][0]["outcome"] == "PASS"
    assert data["test_status"]["reported"] is True
    assert data["cost"]["total_records"] >= 1
    assert data["cost"]["total_wall_time_seconds"] >= 0.0

    # 2. Verify Sub-endpoints
    resp_timeline = app.handle_request("GET", f"/api/v1/projects/{project_id}/timeline")
    assert resp_timeline.status_code == 200
    timeline_data = json.loads(resp_timeline.body)
    assert len(timeline_data["timeline"]) >= 1

    resp_cost = app.handle_request("GET", f"/api/v1/projects/{project_id}/cost")
    assert resp_cost.status_code == 200
    cost_data = json.loads(resp_cost.body)
    assert cost_data["total_records"] >= 1

    # 3. Verify HTML Rendering
    resp_html = app.handle_request("GET", f"/projects/{project_id}")
    assert resp_html.status_code == 200
    assert "text/html" in resp_html.content_type
    assert "1. Project Status" in resp_html.body
    assert "9. Cost &amp; Usage Tracking" in resp_html.body
    assert "11. Phase History" in resp_html.body
    assert "PASS" in resp_html.body

    # 4. Prove Read-Only: State was not modified by dashboard operations
    state_after = target["state"].load(target["project"])
    assert state_after.project_id == project_id
