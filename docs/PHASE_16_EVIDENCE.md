# Phase 16 Evidence — Agent Router + Fallback

Stable starting point: `aipc-phase-15-pass` / `ca593556583287716b61a6fb81a7ec8e9807d3b8`.
Attempt branch: `codex/phase-16-attempt-01`. No stable promotion, merge, or Phase 17 work.

Implementation and completion report belong to the containing attempt commit; the actual commit SHA will be recorded upon commit creation. A containing commit cannot embed its own hash.

---

## 1. Scope & Implementation Summary

Phase 16 implements provider-neutral worker routing and bounded no-effects fallback:
- **`src/aipc/agents/router.py`**: Pure registry-backed `AgentRouter` implementing deterministic candidate qualification, capability filtering, priority ranking, explicit worker preference, and attempt history tracking.
- **`src/aipc/agents/routing_models.py` & `routing_errors.py`**: Immutable dataclass models (`RoutingRequest`, `RoutingDecision`, `TaskRequirements`, `AgentQualification`, `OperationalFailure`) and typed routing errors (`RoutingError`, `RoutingExhaustedError`, `RoutingCycleError`, `RoutingProviderError`).
- **`src/aipc/controller/routing_flow.py` & `routing_models.py`**: Controller-owned routing flow (`WorkerRoutingFlow`) and journal audit models (`WorkerRoutingEvent`, `WorkerBinding`). Controller maintains single-writer ownership of dispatch intent, boundary start, failure classification, and Recovery clearance before fallback continuation.
- **Corrupt Journal Protection**: `RunRecord` in `src/aipc/controller/models.py` enforces `routed: StrictBool = False`. A routed run strictly forbids missing/erased routing assignments for active attempts, and unrouted runs forbid phantom routing history. `ControllerJournal` validates that routed runs cannot be loaded as unrouted.

---

## 2. Test Execution & Targeted Verification Receipts

Targeted test execution verified Phase 16 functional boundaries without requiring repeated 90-minute full suite execution.

### 2.1 Targeted Phase 16 Routing Suite (59/59 PASS)
Executed via `.venv\Scripts\python.exe -m pytest tests/routing -q`:
- `tests/routing/test_router.py`: Candidate selection, priority ordering, capability satisfaction, fallback eligibility, exhausted candidates, cycle prevention (23 passed).
- `tests/routing/test_structure.py`: Model serialization, immutability, validation bounds, hash consistency (13 passed).
- `tests/routing/test_controller.py`: End-to-end Controller routing pipeline, primary worker dispatch, clean git state preservation, journal audit trail, and all 3 corrupt history rejection cases (`dispatch`, `handle`, `history`) (15 passed).
- `tests/routing/test_controller_safety.py`: Boundary failure containment, Recovery Manager clearance, effect isolation, and single-attempt fallback boundaries (8 passed).
- **Total Routing Suite**: **59 passed, 0 failed, 0 skipped**.

### 2.2 Contract & Backward Compatibility Verification (113 PASS)
- `tests/contract`: **98 passed in 14.97s** (agent adapters, domain schemas, evidence contracts, normalizers, project schemas, prompts, version contracts).
- `tests/controller/test_contracts.py`: **15 passed in 0.41s** (controller journal immutability, atomic publication, RunRecord schema validation).
- **Legacy Controller Compatibility Smoke**: `tests/controller/test_lifecycle.py::test_pass_composes_all_subsystems_without_dispatching_next` **PASSED in 77.33s** (proves legacy unrouted Controller orchestration works without regressions after adding `routed: StrictBool = False`).

### 2.3 Fast Quality Harness (`run_fast.py`) (10/10 PASS)
Executed via `.venv\Scripts\python.exe .superpowers/sdd/2026-10-05-phase-16/run_fast.py`:
- `ruff`: **0** (All checks passed)
- `format`: **0** (All files formatted)
- `pip-check`: **0** (No broken requirements)
- `domain-schemas`: **0** (Canonical schemas valid)
- `project-schemas`: **0** (Project schemas valid)
- `cli-help`: **0** (CLI interface operational)
- `diff-check`: **0** (Git diff whitespace clean)
- `wheel`: **0** (Distribution package builds cleanly)
- `base-install`: **0** (Wheel installs into clean target)
- `base-import`: **0** (Base import smoke clean)

---

## 3. Evidence Reuse Rationale

- **Untouched Phase 00–15 Modules**: The foundational architecture (Canonical Models, State Machine, Normalization, Prompts, Evidence Collector, Version Manager, Recovery Manager, Phase Gate, Controller Loop, Approval Router, Codex Adapter, Independent Reviewer, Claude Adapter, Gemini Adapter) passed full regression (1661/1661) during Phase 15 completion.
- Their source, test, schema, configuration, and manifest files remain byte-for-byte identical to the approved Phase 15 baseline.
- Per `AGENTS.md` Section 31.2, these approved receipts are reused. All new and modified Phase 16 modules (`src/aipc/agents/router.py`, `src/aipc/agents/routing_*.py`, `src/aipc/controller/routing_*.py`, `src/aipc/controller/models.py`, `src/aipc/controller/journal.py`, `src/aipc/controller/engine.py`, `tests/routing/`) are verified with fresh targeted suites and fast quality gates.

---

## 4. Provider Qualification Preservation

Phase 16 does NOT modify provider adapter boundaries or capability flags:
- **Codex Adapter**: `AVAILABLE` for coding tasks within authorized scopes.
- **Claude Adapter**: Production coding and mutating tools remain `BLOCKED` (`approval_events=False`).
- **Gemini Adapter**: Production runtime and coding capabilities remain `BLOCKED` (`approval_events=False`).
- **Router Boundary**: Router respects registered qualifications and strictly prevents routing mutating tasks to BLOCKED providers.
