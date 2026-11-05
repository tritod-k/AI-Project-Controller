# Phase 16 Router Implementation Plan

> For agentic workers: REQUIRED SUB-SKILL superpowers:executing-plans. Execute inline under the supplied approved blueprint; steps use checkbox syntax.

Goal: deterministic capability routing and bounded safe fallback with Controller-owned execution.
Architecture: pure Registry-backed Router; private typed decisions; append-only Controller routing audit; Recovery precedes no-effect fallback.
Tech Stack: existing Python >=3.11, Pydantic, atomic JSON, pytest; no new dependencies.
Spec: docs/plans/2026-10-05-phase-16-router-spec.md.

## Global Constraints
Preserve all Phase 00-15 tags/contracts/provider restrictions. Only Phase 16.
Do not run provider calls, duplicate giant suites, consensus or cost routing.

## Review Focus
- Malicious/corrupt persisted routing history must not authorize a duplicate dispatch.
- Mutable capability/qualification aliases must not alter an already published decision.
- State/approval/cancellation changes during start failure must forbid fallback.
- Repair attempts with a different selected agent must retain previous review provenance.
- Restart around failed-start publication must retain exact no-effect/Recovery identity.

## Task 1: deterministic eligibility and policy
Files: src/aipc/agents/routing_models.py, router.py, routing_errors.py; tests/routing/test_router.py.
Produces: AgentRouter.select(request, policy) -> RoutingDecision; revalidate(decision, policy); fallback(request, policy, attempted, failure, recovery_safe).
- [ ] Write independent fixtures for actual-like blocked providers, priority/ties, explicit rejection, unknown status, missing capability, mutable snapshots and bounded fallback.
- [ ] Run pytest tests/routing/test_router.py. Expected RED: missing Router behavior.
- [ ] Implement typed conservative private models and pure Registry-backed selection.
- [ ] Run targeted tests. Expected GREEN with no execution or provider imports.

## Task 2: durable Controller integration
Files: src/aipc/controller/routing_flow.py, routing_models.py; modify engine.py, models.py, journal.py, approval_flow.py; tests/routing/test_controller.py.
Consumes Task 1; produces optional worker_router/policy/requirements/bindings integration.
- [ ] Write Controller selection/fallback/restart/crash/staleness/DENY/REVISE/cancel tests against real disposable state/version/recovery/evidence/review components.
- [ ] Run pytest tests/routing/test_controller.py. Expected RED: integration missing.
- [ ] Implement append-only audit and dispatch revalidation; fallback only from proven terminal no-effect start failure after current Recovery inspection; preserve legacy execution.
- [ ] Run routing + Controller + Recovery/Approval/Registry subsets. Expected GREEN, no duplicate dispatch.

## Task 3: fresh review and fix pass
- [ ] Update docs/state and inspect whole diff; dispatch exactly one fresh read-only whole-branch reviewer on supplied request/spec/plan/ledger.
- [ ] For substantive findings, reproduce RED before fixes; run covering targeted GREEN tests. Record rulings and deferred minors.

## Task 4: frozen regression and handoff
- [ ] Freeze all production/test/config/schema/tool/dependency inputs and dependency versions.
- [ ] Run exactly one final full pytest and one explicit relevant component suite. Expected zero failures/errors; record exact counts/exit codes.
- [ ] Run CLI, Ruff check/format, pip check, six schema checks, package checks and diff checks. Expected PASS.
- [ ] Update state/report; inspect diff and commit AIPC Phase 16 attempt 01 - agent router and fallback. Expected clean attempt branch and unchanged stable tags/master.
- [ ] Report actual SHA, AWAITING_REVIEW / PENDING, provider BLOCKED qualifications and stop.
