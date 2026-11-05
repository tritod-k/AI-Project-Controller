# Phase 19 Specification — Dashboard and Read-Only API

## 1. Objectives & Boundaries

Phase 19 introduces a read-only monitoring and audit dashboard with HTTP REST and HTML interfaces (`src/aipc/dashboard/`).

### Architectural Invariants
1. **Zero New Runtime Dependencies (`AGENTS.md` Rules 14 & 23)**:
   - Uses only Python standard library (`http.server`, `urllib.parse`, `html`, `json`, `re`) and existing canonical domain models (`CanonicalRecord`).
   - No external web frameworks (no FastAPI, Starlette, uvicorn, Flask, etc.) in `pyproject.toml`.
2. **Read-Only Controller Boundary (`MASTER_PLAN.md` lines 2901–2903)**:
   - *"The dashboard must call Controller APIs. It must not contain orchestration business logic."*
   - Dashboard service and endpoints are strictly read-only observation layers over `StateManager`, `ControllerJournal`, `MetricsStore`, and `ProjectDefinition`.
   - Never mutates state or journals.
   - Non-`GET` HTTP methods (`POST`, `PUT`, `DELETE`, `PATCH`) fail closed with `405 Method Not Allowed`.
3. **Secret Redaction & XSS Defense (`AGENTS.md` Rule 13)**:
   - Deterministic secret scrubbing via `redact_secrets` replaces API keys, tokens, and authorization headers with `[REDACTED]`.
   - All HTML output escapes dynamic content via `html.escape(..., quote=True)`.

---

## 2. 11 Required Dashboard Sections (`MASTER_PLAN.md` lines 2887–2900)

1. **Project Status**: `project_id`, `project_name`, `run_id`, `status`, `updated_at`.
2. **Current Phase**: `phase_id`, `phase_name`, `attempt`, `active_task_id`, `stage`, `last_stable_checkpoint`.
3. **Worker**: `configured_worker_id`, `active_worker_id`, `provider`, `routed`, `worker_run_id`, `agent_status`.
4. **Reviewer**: `configured_reviewer_id`, `review_authority`, `latest_verdict`, `consensus_mode`, `next_phase_allowed`.
5. **Approvals**: `waiting_approval`, `pending_approval_id`, `pending_risk_level`, `total_events`, and history of `ApprovalEventView` records.
6. **Test Status**: `reported`, `total`, `passed`, `failed`, `skipped`, `commands_observed`, `failed_commands`.
7. **Event Timeline**: Chronological `TimelineEventView` records (`phase_id`, `attempt`, `stage`, `timestamp`, `task_id`, `worker_run_id`).
8. **Retry Count**: `provider_retry`, `phase_revision`, `review_retry`.
9. **Cost & Usage**: `total_records`, `total_estimated_cost_usd`, `total_wall_time_seconds`, and per-agent statistics from `MetricsStore`.
10. **Logs**: Scrubbed, structured `DashboardLogEntry` records (`timestamp`, `level`, `event`, `phase_id`, `attempt`, `agent_id`, `message`).
11. **Phase History**: Per-phase progress and results (`phase_id`, `phase_name`, `status`, `attempts`, `outcome`, `review_verdict`, `stable_checkpoint_id`).

---

## 3. REST API Routes
- `GET /api/v1/health` -> `200` JSON `{"status": "ok", "schema_version": "1.0"}`
- `GET /api/v1/projects` -> `200` JSON list of projects
- `GET /api/v1/projects/{project_id}` -> `200` JSON `ProjectDashboardSnapshot`
- `GET /api/v1/projects/{project_id}/timeline` -> `200` JSON event timeline
- `GET /api/v1/projects/{project_id}/approvals` -> `200` JSON approval events
- `GET /api/v1/projects/{project_id}/cost` -> `200` JSON cost and usage summary
- `GET /api/v1/projects/{project_id}/phases` -> `200` JSON phase history
- `GET /api/v1/projects/{project_id}/logs` -> `200` JSON structured logs
- `GET /` -> `200` HTML projects index
- `GET /projects/{project_id}` -> `200` HTML project dashboard
- `POST|PUT|DELETE|PATCH *` -> `405` JSON `{"error": "Method not allowed; dashboard is strictly read-only"}`
