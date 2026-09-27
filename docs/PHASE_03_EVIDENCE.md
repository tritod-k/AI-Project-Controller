# Phase 03 attempt 01 — external review evidence

Phase 03 — Agent Adapter Interface + Mock Agent. Status AWAITING_REVIEW;
Phase Gate PENDING. No Phase 04 implementation or Phase 03 PASS tag.

## Exact Git references

- Branch: `codex/phase-03-attempt-01`.
- Implementation commit: `0e89683765999c5a32b9f1160bd2f08839c75e7a`.
- Message: `AIPC Phase 03 attempt 01 - agent adapter and mock agent`.
- Stable master / `aipc-phase-02-pass`: `fa189a31e8483831f92f9fa86907ff022211cfe8`.

This evidence and the implementation hash in CURRENT_STATE.md are added by a
subsequent documentation-only handoff commit. Its exact HEAD appears in the final
report. No code changes follow the implementation reference above.

## Validation results

Environment: Windows, Python 3.13.0, pytest 9.1.1, Ruff 0.16.9,
Pydantic 2.13.5, PyYAML 6.0.3; existing project-local dependencies only.

| Check | Result |
| --- | --- |
| `python -m aipc --help` | Exit 0; CLI remains help-only |
| `pytest` | 518 passed, 0 failed, 0 skipped; 9.03 seconds; exit 0 |
| `pytest tests/contract/test_agent_adapters.py -q` | 7 passed; 0.39 seconds; exit 0 |
| `python -m ruff check .` | All checks passed; exit 0 |
| `python -m ruff format --check .` | 40 files formatted; exit 0 |
| `python -m pip check` | No broken requirements; exit 0 |
| `python -m aipc.domain.schemas --check` | Four canonical schemas consistent; exit 0 |
| `python -m aipc.project.schemas --check` | Two project/state schemas consistent; exit 0 |
| `git diff --check` and staged check | Exit 0 |
| Secret-pattern scan | 80 tracked/staged files scanned, no matches; no tracked .env |
| Wheel build and isolated non-editable install | Passed |
| Installed-package scenario/registry smoke, CLI and pip check | Passed |

All 448 Phase 00–02 cases remain intact. New cases: 39 contract-model/registry,
24 mock behavior and 7 shared adapter contract tests (70 total). Tests use standard
asyncio.run; no new dependencies or timing-based sleeps. No type checker configured.
Python 3.11 and non-Windows execution remain unverified.

Wheel artifact (ignored): `dist/phase03/ai_project_controller-0.0.1-py3-none-any.whl`.
SHA256: `1c43b89aa16f83e588f4a2007496f13c8480df4fac9ece7a0262b3eba7469f8b`.
Installed package imported from `.venv/phase03-wheel/Lib/site-packages/aipc`, not
the editable source. Smoke checks executed registry lookup, all five scenarios,
both approval decisions and cancellation through that installed package.

## Acceptance criteria

| Blueprint criterion | Evidence |
| --- | --- |
| Controller can execute a task without knowing provider | PASS: shared contract test registers a worker, looks it up by ID, calls Protocol start/wait and verifies identity/lifecycle without provider payload fields. This proves the controller-facing seam; no controller loop is implemented |
| Mock adapter returns provider-specific fake raw data | PASS: mock unit tests check mock_status/mock_run/message, task failure, raw pending permission and unchanged malformed text |
| Raw data is not consumed directly by phase logic | PASS: RawAgentResult is distinct from canonical AgentResult; canonical validation rejects mock payloads. Existing project/state/storage implementation is unchanged and imports no mock or raw envelope |
| Contract tests exist | PASS: reusable adapter_factory table and seven generic lifecycle/registry/approval tests; provider payload assertions confined to mock unit tests |

## Scenario evidence

- SUCCESS: start returns owned handle; wait returns raw COMPLETED; repeat wait stable.
- FAILURE: raw FAILED / task_failed, distinct from an adapter timeout exception.
- TIMEOUT: immediate typed AgentTimeoutError; subsequent waits and cancel preserve timeout.
- APPROVAL_REQUEST: raw WAITING_APPROVAL; APPROVE completes, DENY fails; invalid and
  duplicate responses do not replace valid state.
- MALFORMED_OUTPUT: literal malformed text remains representable and unnormalized.
- CANCEL: start/cancel/wait yields CANCELLED for all scenarios; pending approval also
  cancels. Repeated cancel is harmless; completed/failed/timed-out runs stay terminal.

Unknown/foreign/altered handles fail for wait/cancel/approval. Repeated task IDs create
independent runs. Mutated canonical requests are revalidated before start; returned
handle/raw snapshots cannot mutate owned run state. Registry rejects duplicate and
unknown IDs, sync lifecycle methods and invalid/mutated capabilities or identities.

## Review, scope and decisions

One fresh read-only reviewer inspected the staged whole branch against owner request,
design and plan; independently passed all 70 new cases in 3.81 seconds and staged
diff checks. No Critical, Important or Minor findings. This is supplemental code
review, not an external Phase Gate PASS. State/evidence/commits were completed by the
coordinator after source review.

Explicit decisions: mock wait returns pending approval rather than blocking; scenarios
are deterministic while opaque UUID namespaces prevent instance/run collisions.
Raw payload validation belongs to Phase 04. Real-provider behavior is excluded by
scope. Mock persistence and cross-thread execution are excluded by its documented
process-local/single-loop contract; later recovery/provider work needs its own rules.
No deferred minors or outstanding source fixes.

The four canonical models, six schemas, project/storage code, CLI, dependency
declaration and repository instructions/blueprint remain unchanged. Only required
agent errors extend the existing hierarchy. No real provider, normalization layer,
router, prompt/evidence/review/gate engine, approval policy, supervisor, event bus,
Version Manager or Recovery Manager is implemented.

## Limits and next action

Mock runs are retained in memory for repeated lifecycle operations and do not survive
restart. Capability/metadata dictionaries retain established mutable-collection
semantics; callers must revalidate at boundaries. No filesystem or tool authority is
granted by capability flags or TaskRequest. Phase 02 single-writer limitations remain
accepted; stronger locking/durability and later phase-number discrepancies remain
future considerations. No later phase is authorized by this worker.

Submit Phase 03 results to the external ChatGPT reviewer and wait for
PASS / REVISE / BLOCKED decision. Stop before Phase 04.
