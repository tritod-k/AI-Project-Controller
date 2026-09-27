# Phase 05 Evidence — Prompt Engine

## Review handoff

Status: AWAITING_REVIEW. Phase Gate: PENDING. Attempt: 01.
Branch: `codex/phase-05-attempt-01`.
Implementation commit: `2475041eeeb9d53eded6984175a99633dcd0cf1f`.
Message: `AIPC Phase 05 attempt 01 - prompt engine`.
Stable master / aipc-phase-04-pass: `fddb51f59b7358e8b3bd88ad72f6cc6f25b633ec`.
Starting branch/HEAD/tag and clean-tree checks matched the approved checkpoint.
The subsequent documentation handoff changes only CURRENT_STATE.md and this report;
its own resulting HEAD is reported separately after commit. No Phase 05 PASS tag,
stable merge or Phase 06 branch/functionality is created.

## Acceptance criteria

- [PASS] Clear typed PromptEngine API produces text, never invokes agents or commands.
  PromptContext sidecar supplies missing rendering fields without schema changes.
- [PASS] Phase prompt includes all 13 required sections. Parameterized assertions pin
  every section; 17 independent completion-report field cases pin the report contract.
- [PASS] Project/phase identity, objective, dependencies, attempt/state/checkpoint and
  required/optional criterion ID/statement are rendered from input with no invented criteria.
- [PASS] Fixed current-phase scope, preservation, validation/evidence and stop instructions
  are present in phase and repair outputs, independent of supplied next-phase flag.
- [PASS] Canonical REVISE review supplies failed IDs, fixes, notes, evidence, risks and
  verdict; repair context explicitly identifies earlier attempt and reviewed task.
- [PASS] Wrong identity/phase/context, invalid canonical mutation, unassociated task/
  attempt/review/criteria and non-REVISE repair inputs raise PromptError.
- [PASS] Repair instructs preserving satisfied criteria, inspecting existing work and
  making the smallest fixes without restarting from scratch or advancing phases.
- [PASS] Repeated rendering, declaration-order criteria/dependencies and two fresh
  subprocesses with different hash seeds produce identical UTF-8 bytes.
- [PASS] Multiline Markdown, quotes, nested fences, Unicode and special punctuation are
  retained literally; absent optionals display explicit phrases without None/undefined.
- [PASS] Full representative phase/repair golden prompts are committed and inspectable.
- [PASS] AST contracts and assignment-invariance tests show no provider/execution
  dependencies or provider-specific branches. Existing models/schemas remain unchanged.
- [PASS] Existing 589 plus 79 new cases pass; all required checks and explicit subsets pass.
- [PASS] Updated CURRENT_STATE AWAITING_REVIEW/PENDING and exact Git handoff recorded.
- [UNVERIFIED] External Phase 05 decision, Python 3.11/non-Windows and wheel installation.

## Phase prompt evidence

Inspect `tests/fixtures/prompts/phase.md` with literal inputs.json. Its headings are:
ROLE, PROJECT CONTEXT, CURRENT PHASE, OBJECTIVE, IN SCOPE, OUT OF SCOPE,
FILES TO READ FIRST, ACCEPTANCE CRITERIA, IMPLEMENTATION CONSTRAINTS, TEST REQUIREMENTS,
SAFETY / APPROVAL RULES, COMPLETION REPORT FORMAT and STOP CONDITION.

The sample renders project sample / Sample Project / ./workspace, phase 01 / Verify,
attempt 1, RUNNING, dependency 00, checkpoint sample-phase-00-pass and AC-Z [REQUIRED]
before AC-A [OPTIONAL]. Caller-supplied sample pytest -q is rendered but never executed.
Standard instruction files and relevant sample source/tests are included.
The report requires all 17 owner-requested fields, including actual Git handoff/evidence.

## Repair evidence

Inspect `tests/fixtures/prompts/repair.md`. It adds REPAIR ATTEMPT CONTEXT and PREVIOUS
REVIEW, displaying previous attempt 1, current repair attempt 2, task-original,
review-original, REVISE, confidence 0.9, AC-Z FAIL, tests.log evidence, boundary notes,
required fix, risk and next-phase declaration. Only structured ReviewResult is accepted.

Association guards require previous_task_id == review.task_id, previous_attempt less
than current_attempt, selected-phase criterion membership and agreement with any
state.last_review. No task-ID parser, retry increment or Phase Gate policy is implemented.
Both outputs require not starting the next phase, validation/evidence, CURRENT_STATE
handoff, completion report and stopping for controller/reviewer approval. Repair
preservation instructions are asserted literally in independent unit tests.

## Determinism and framing

71 unit cases and 8 contracts cover required content, optional fields, identity errors,
canonical revalidation, order, preservation, immutable inputs and text handling.
Rendering has no clock/UUID/random/filesystem lookup. Tuple/list declaration order
is intentional. Input text is fenced with more backticks than any contained run.
Six unusual-text cases cover multiline Markdown/quotes, nested long fences, Unicode,
blank lines, punctuation and code-like content without truncation.

Full golden examples pin intentional layout; independent literal assertions verify
the content rather than relying solely on snapshots. Two subprocesses per path use
PYTHONHASHSEED=1 and 999 and compare exact UTF-8 bytes to in-process output. Windows
stdout initially translated LF to CRLF in the test harness; writing to stdout.buffer
removed transport translation without changing the renderer or weakening byte equality.

## Commands and observed results

Commands ran using `.venv/Scripts/python.exe` in this checkout (`python -m pytest`
for pytest commands). Windows Python 3.13.0 / pytest 9.1.1 / Ruff 0.16.9 /
Pydantic 2.13.5. No global installs or dependency additions.

| Command | Observed result |
| --- | --- |
| python -m aipc --help | Original help-only CLI; exit 0 |
| pytest -q | 668 passed, 0 failed/skipped, 12.54s; exit 0 |
| pytest tests/unit/test_prompt_engine.py tests/contract/test_prompts.py -q | 79 passed, 2.79s; exit 0 |
| pytest tests/unit/test_normalization.py tests/integration/test_mock_normalization.py tests/contract/test_normalizers.py -q | 71 passed, 0.67s; exit 0 |
| pytest tests/contract/test_agent_adapters.py -q | 7 passed, 0.32s; exit 0 |
| python -m ruff check . | All checks passed; exit 0 |
| python -m ruff format --check . | 54 files already formatted; exit 0 |
| python -m pip check | No broken requirements; exit 0 |
| python -m aipc.domain.schemas --check | Checked 4 canonical schemas; exit 0 |
| python -m aipc.project.schemas --check | Checked 2 project/state schemas; exit 0 |
| git diff --check / git diff --cached --check | No whitespace errors; exit 0 |

668 = 589 existing + 71 prompt unit + 8 prompt contracts. Initial 52 phase tests
failed before prompt modules existed; all passed after implementation. Eighteen
repair tests failed while the repair method was absent, then passed with that path.
A repair draft was removed before the repair tests; this evidence does not claim
that no design/code draft existed beforehand. New architecture/golden tests verify
the existing feature after implementation; no production behavior was added by them.

Fresh read-only independent whole-branch reviewer ran 79 tests (exit 0), inspected
source, contracts, examples and documentation, and reported no actionable findings.
This internal review is not the external Phase Gate approval.

## Scope and Git audit

Diff against Phase 04 returned exit 0 for canonical models/enums, project/state,
agents/adapters/normalization/storage, all six schemas, pyproject.toml and repository
instructions. Domain errors gained only PromptError. Prompt modules import existing
provider-neutral contracts, Pydantic and standard-library re/collections helpers.
There is no real-provider dependency, TaskRequest execution, state write, command run,
Evidence Collector, reviewer/gate/controller, router or Phase 06 implementation.
110 tracked/staged files scanned for secret patterns/tracked .env: zero matches.
Staged scope/source/state diff inspected before the implementation commit; its working
tree was clean. Documentation handoff is checked separately; master and prior tags stay
at their approved commits. No configured live structured controller state exists.

## Known limits and decisions

No known defects. Prompt rendering is not authority/readiness validation; caller must
decide valid phase execution and correlate the explicit previous task/attempt. Fencing
protects Markdown structure, not semantic model behavior or injection resistance.
Commands/files are supplied, not discovered or executed. Canonical collections retain
Phase 01 mutation semantics and must be revalidated downstream. Raw diagnostics remain
external, unresolved approval non-terminal, mock storage process-local and project
persistence single-writer. Later locking/durability and real providers remain deferred.
AGENTS/MASTER_PLAN phase numbering must be reconciled before affected later phases.

Architecture deviations: none. No approvals requested or deferred minors.
Ruling: use explicit prior task/attempt sidecar fields instead of guessing from task-ID
syntax; future runtime callers must supply those fields. No canonical schema distortion.

Submit Phase 05 results to the external ChatGPT reviewer and wait for PASS / REVISE / BLOCKED decision.
