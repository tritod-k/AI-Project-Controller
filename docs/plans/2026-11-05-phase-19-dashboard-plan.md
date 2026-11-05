# Phase 19 Implementation Plan — Dashboard and Read-Only API

## Execution Steps

1. **Errors**:
   - `src/aipc/dashboard/errors.py`: `DashboardError`, `ProjectNotFoundError`.
   - `src/aipc/domain/errors.py`: add `DashboardError`, `ProjectNotFoundError`.
2. **View Models**:
   - `src/aipc/dashboard/models.py`:
     - `ProjectStatusView`, `CurrentPhaseView`, `WorkerStatusView`, `ReviewerStatusView`.
     - `ApprovalEventView`, `ApprovalSummaryView`.
     - `TestStatusView`, `TimelineEventView`, `CostSummaryView`, `DashboardLogEntry`, `PhaseHistoryEntry`.
     - `ProjectDashboardSnapshot` (root document covering all 11 sections).
3. **Secret Redaction**:
   - `src/aipc/dashboard/security.py`: `redact_secrets(text: str) -> str`.
4. **Service**:
   - `src/aipc/dashboard/service.py`:
     - `DashboardProjectRegistration`.
     - `DashboardService`: `register()`, `list_projects()`, `get_snapshot(project_id)`.
5. **HTML Renderer**:
   - `src/aipc/dashboard/html.py`:
     - `render_projects_index_html(projects) -> str`.
     - `render_dashboard_html(snapshot) -> str`.
6. **Server & Request Handler**:
   - `src/aipc/dashboard/server.py`:
     - `DashboardResponse`.
     - `DashboardApplication`: path routing, method validation, 405 enforcement, 404/400 handling.
     - `DashboardHttpServer`: context-managed stdlib `http.server.ThreadingHTTPServer` binding ephemeral port.
7. **Package Exports**:
   - `src/aipc/dashboard/__init__.py`.
8. **Test Suites**:
   - `tests/dashboard/conftest.py`.
   - `tests/dashboard/test_dashboard.py`: comprehensive unit, API contract, HTML, XSS escaping, secret redaction, 405 method enforcement, live HTTP tests.
   - `tests/dashboard/test_controller_dashboard.py`: Controller end-to-end integration test with `target` fixture.
9. **Documentation**:
   - `docs/DASHBOARD_API.md`, `docs/PHASE_19_EVIDENCE.md`, `docs/PHASE_19_COMPLETION_REPORT.md`, `CURRENT_STATE.md`, `README.md`, `MASTER_PLAN.md`, `docs/ARCHITECTURE.md`.
10. **Quality Harness & Handoff**:
    - `run_fast.py`, static analysis, git status check, completion report to parent Controller.
