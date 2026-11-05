# AI Project Controller — Dashboard & REST API (Phase 19)

## 1. Overview & Architecture

The AIPC Dashboard & REST API provides real-time, provider-neutral observation into orchestration execution state, phase transitions, agent runs, approvals, consensus decisions, cost/usage metrics, and structured logs.

Per `MASTER_PLAN.md` (Phase 19) and `AGENTS.md` (Rules 5, 6, 7, 13, 14, 15, 21, 23), the dashboard operates under strict architectural constraints:

1. **Zero New Runtime Dependencies**:
   Implemented entirely using the Python standard library (`http.server`, `urllib.parse`, `html`, `json`, `re`, `logging`) alongside existing canonical Pydantic v2 schemas (`CanonicalRecord`). No FastAPI, Starlette, Flask, or Uvicorn runtime dependencies are required.
2. **Read-Only Observation Boundary**:
   The dashboard is strictly an observer over `StateManager`, `ControllerJournal`, `MetricsStore`, and `ProjectDefinition`. It contains **zero** orchestration logic, cannot trigger attempts, approve actions, or alter state. All non-`GET` HTTP methods (`POST`, `PUT`, `DELETE`, `PATCH`) are rejected with `405 Method Not Allowed`.
3. **Secret Redaction**:
   All dynamic textual data (logs, error messages, task inputs/outputs) pass through `redact_secrets()`, which scrubs OpenAI API keys (`sk-...`), Google AI Studio keys (`AIza...`), Bearer authorization tokens, and key-value credential patterns (`api_key=...`, `token=...`, `password=...`) into `[REDACTED]`.
4. **XSS Protection**:
   All HTML template parameters are sanitized through `html.escape(str(val), quote=True)` before injection into semantic HTML5 markup.
5. **Provider-Neutrality**:
   The dashboard module does not import or branch on provider adapters (`aipc.adapters.*`). It operates strictly on canonical view models.

---

## 2. HTTP Endpoints

### 2.1 HTML Routes

| Route | Method | Description | Content-Type |
|---|---|---|---|
| `/` | `GET` | Projects Index listing all registered projects, current status, run IDs, and links. | `text/html; charset=utf-8` |
| `/projects/{project_id}` | `GET` | Semantic HTML5 dashboard rendering all 11 required sections for the given project. | `text/html; charset=utf-8` |

### 2.2 REST API Routes

| Endpoint | Method | Query Parameters | Description |
|---|---|---|---|
| `/api/v1/projects` | `GET` | None | Returns JSON array of registered project summaries (`list[ProjectStatusView]`). |
| `/api/v1/projects/{project_id}` | `GET` | `limit` (int, default: 100) | Returns complete canonical snapshot (`ProjectDashboardSnapshot`) covering all 11 sections. |
| `/api/v1/projects/{project_id}/status` | `GET` | None | Section 1: `ProjectStatusView`. |
| `/api/v1/projects/{project_id}/phase` | `GET` | None | Section 2: `CurrentPhaseView`. |
| `/api/v1/projects/{project_id}/worker` | `GET` | None | Section 3: `WorkerStatusView`. |
| `/api/v1/projects/{project_id}/reviewer` | `GET` | None | Section 4: `ReviewerStatusView`. |
| `/api/v1/projects/{project_id}/approvals` | `GET` | None | Section 5: `ApprovalSummaryView`. |
| `/api/v1/projects/{project_id}/tests` | `GET` | None | Section 6: `TestStatusView`. |
| `/api/v1/projects/{project_id}/timeline` | `GET` | `limit` (int, default: 100) | Section 7: `list[TimelineEventView]`. |
| `/api/v1/projects/{project_id}/retries` | `GET` | None | Section 8: `RetryCountView`. |
| `/api/v1/projects/{project_id}/cost` | `GET` | None | Section 9: `CostSummaryView`. |
| `/api/v1/projects/{project_id}/phases` | `GET` | None | Section 11: `list[PhaseHistoryEntry]`. |
| `/api/v1/projects/{project_id}/logs` | `GET` | `limit` (int, default: 100) | Section 10: `list[DashboardLogEntry]`. |

---

## 3. The 11 Required Dashboard Sections

Each project snapshot aggregates 11 structured sections:

1. **Project Status (`project_status`)**:
   - `project_id`, `project_name`, `run_id`, `status` (`READY`, `RUNNING`, `WAITING_APPROVAL`, `WAITING_HUMAN`, `COMPLETED`, `FAILED`, `ABORTED`), `updated_at`, `created_at`.
2. **Current Phase (`current_phase`)**:
   - `phase_id`, `phase_name`, `attempt`, `stage` (`PLANNING`, `EXECUTION`, `EVALUATION`), `active_task_id`, `last_stable_checkpoint`.
3. **Worker Status (`worker`)**:
   - `configured_worker_id`, `active_worker_id`, `provider`, `routed` (boolean), `agent_status`.
4. **Reviewer Status (`reviewer`)**:
   - `configured_reviewer_id`, `review_authority`, `latest_verdict` (`PASS`, `FAIL`, `BLOCKED`, `NEEDS_REVISION`), `consensus_mode` (`single`, `primary_with_veto`, `unanimous_strict`, `majority_rule`), `next_phase_allowed`.
5. **Approvals (`approvals`)**:
   - `waiting_approval` (boolean), `pending_approval_id`, `pending_risk_level`, `events` (`list[ApprovalEventView]` with ID, risk, route, action type, resolution, and timestamp).
6. **Test Status (`test_status`)**:
   - `reported` (boolean), `passed`, `failed`, `skipped`, `total`, `commands_observed`, `failed_commands`.
7. **Event Timeline (`event_timeline`)**:
   - Ordered list of `TimelineEventView` entries: `timestamp`, `phase_id`, `attempt`, `stage`, `task_id`, `worker_run_id`.
8. **Retry Count (`retry_count`)**:
   - `phase_revision` (attempt revisions), `provider_retry` (transient fallback retries), `review_retry` (reviewer revision loops).
9. **Cost & Usage Tracking (`cost`)**:
   - `total_estimated_cost_usd`, `total_wall_time_seconds`, `total_records`, `agents` (`list[AgentStatistics]` broken down by agent ID, provider, runs, success rate, cost, wall time).
10. **Structured Logs (`logs`)**:
    - Secret-redacted log entries: `timestamp`, `level` (`INFO`, `WARNING`, `ERROR`), `phase_id`, `attempt`, `event`, `message`.
11. **Phase History (`phase_history`)**:
    - Complete record across all project phases: `phase_id`, `phase_name`, `status`, `attempts`, `outcome`, `review_verdict`, `stable_checkpoint_id`.

---

## 4. Error Responses

All error responses return standard HTTP error status codes and JSON payloads:

```json
{
  "error": "Error description message"
}
```

- **`400 Bad Request`**: Invalid query parameter format (e.g. non-integer `?limit=abc`).
- **`404 Not Found`**: Project ID not registered or endpoint path unrecognized.
- **`405 Method Not Allowed`**: Request method is not `GET`. Includes header `Allow: GET`.

---

## 5. Usage Example

```python
from aipc.dashboard import DashboardService, DashboardHttpServer
from aipc.project.state_manager import StateManager
from aipc.controller.journal import ControllerJournal
from aipc.observability.metrics import MetricsStore

# Initialize service
service = DashboardService()
service.register(
    project_id="my-project",
    project_name="My Orchestrated Project",
    state_manager=state_manager,
    journal=controller_journal,
    metrics_store=metrics_store,
)

# Start lightweight HTTP server on ephemeral or configured port
server = DashboardHttpServer(service, host="127.0.0.1", port=0)
server.start()

print(f"Dashboard available at: {server.base_url}")
# ... interact via browser or HTTP client ...

server.stop()
```
