# Phase 08 — Recovery Manager evidence

## Scope and baseline

Authorized Phase 08 implementation only. Verified clean master starting HEAD and
`aipc-phase-07-pass`: `5df11213c7f25884046fcc3a7bff62b78c01fcad`.
Previous `aipc-phase-06-pass`: `8a553c3aba55222a1e0dfcfd47fc0ad0c508f074`.
Attempt branch: `codex/phase-08-attempt-01`. Baseline: 855 tests (80 Version Manager).
All recovery mutations occur in pytest temporary fixture repositories. No live AIPC
recovery/version metadata, provider calls, PASS tag, master integration or Phase 09 work.

## Architecture and authority

Read-only inspect -> typed classification -> reasoned decision -> explicit durable plan
-> repeated precondition validation -> guarded execute -> durable typed result.
ProjectState owns phase/run/task/attempt; VersionManager/Git own repository/checkpoint/
worktree facts; private recovery records own provenance and operation history; optional
evidence corroborates; CURRENT_STATE.md is only a human resume summary.

No canonical contract, project state-machine or public schema changes. Existing state
transitions implement resume/wait without forcing illegal transitions. Provider retry,
reconnect and replacement-session strategies are returned as data; no dispatch occurs.

## Acceptance evidence

| Requirement | Fixture evidence and behavior |
|---|---|
| Restart/process/worker/network/timeout continuity | `test_intact_attempt_resumes_without_replacing_files`: same attempt, files and HEAD; operational RUNNING transition, replay returns same result |
| Partial work | `test_owned_partial_work_requires_exact_heartbeat_observation`: explicit owned snapshot resumes; later unknown edits require human |
| Quota/outage/rate | `test_provider_wait_preserves_attempt_and_phase`: WAITING_PROVIDER, same attempt/HEAD, no code-failure verdict |
| Session lost | Intact-attempt and worker-owner cases: NEW_SESSION context only after stopped-worker proof; active/unknown owner blocks |
| Human edits | Parameterized tracked/staged/untracked/ignored/hidden edits survive; branch movement, conflicts and uncertain external effects require human |
| Corruption/restore | Missing clean registered checkout restores from validated checkpoint into isolated worktree; linked prior STABLE also supported; unknown existing checkout is preserved |
| Restore destination | Outside-root and existing/dirty target rejected; repeated completed restore returns existing result |
| Publication | Valid RUNNING intent + exact immutable ref + absent metadata + dead owner reconstructs metadata; missing/moved/mismatched refs or unproven orphan remain human-required |
| Matching unfinished intent | Metadata/ref match can finish RUNNING publication without ref movement; dead-owner proof required |
| Lock ownership | ALIVE blocks; UNKNOWN/legacy preserved; DEAD exact inspected lock renamed to retained audit directory; age alone irrelevant |
| Preconditions | Identity/HEAD/state/lock changes between plan/execute refuse mutation; late workspace edits/common and managed Git locks also refuse state resume |
| Persistence | Fresh manager and fresh process rebuild all managers and return the same decision; corrupt/duplicate records refused; incomplete terminal markers invalid |
| Interrupted recovery | RUNNING alive-owner blocks; dead/unknown owner requires explicit partial-result inspection; no blind replay |
| Atomic journal | Injected write failure preserves previous durable record/project state |
| Read-only inspection | Workspace files/index, project state and journal remain byte-identical |

## Red/green ledger

Tests were written before implementation/fixes. Intermediate suites are diagnostic,
not substitutes for the final complete run.

- Initial absent RecoveryManager: 9 model failures and 26 missing-API errors (red).
- Initial implementation: 35 passed in 442.29s.
- Workspace binding, prior-STABLE restore, primary branch movement, matching intent and
  worker-owner regressions reproduced failures; corrected eight-case subset: 8 passed
  in 175.14s.
- Late edit after RUNNING journal publication: failed before guard, then 1 passed in 22.71s.
- Incomplete journal completion marker failed before validation fix; 10 model tests passed.
- Missing-worktree worker liveness and late common Git-lock/provider-timeout regressions
  reproduced failures; corrected seven-case subset: 7 passed in 196.64s.
- Managed worktree index-lock regressions: 2 failed in 64.35s before observation/refusal
  fix; 2 passed in 68.04s after fix. Lock contents survive, project remains INTERRUPTED.
- Earlier full runs were stopped when safety findings required source changes; they are
  not reported as final full-suite passes. Earlier subset diagnostics are retained below
  if they exposed a failure subsequently covered by final verification.
- Earlier 64-case run: 63 passed / 1 failed in 1661.45s. It started before the
  session_strategy contract was added; its parent wrote the older in-memory record while
  its fresh subprocess loaded the newer required field. This mixed-version diagnostic is
  not a final pass. Consistent-version full/subset runs cover reload again; the strengthened
  complete fresh-process reconstruction also passed separately.

## Internal review

A bounded read-only safety review found mismatched project workspace acceptance,
unfinished matching publication intents and worker liveness precedence. Follow-up
identified restore-before-liveness and late Git-lock precondition gaps. Each was
reproduced and fixed. Parent review added managed index-lock coverage. Internal review
has no Phase Gate authority and does not self-approve the phase.

## Validation

Final verified results. Windows / Python 3.13.0, project-local .venv. All commands below exited 0.

- Version Manager dedicated subset: 80 passed in 463.51s, exit 0.
- Adapter/normalization/prompt/evidence combined subset: 264 passed in 31.84s, exit 0
  (7 / 71 / 79 / 107 respectively).
- Final full pytest: 927 passed in 1740.52s (855 baseline + 72 Phase 08), exit 0.
- Final dedicated Recovery Manager: 72 passed in 1455.77s, exit 0.
- Ruff lint: passed; format: 86 files already formatted (after two test wrapping fixes).
- pip check: no broken requirements found.
- Canonical schema drift: 4 checked; project/state drift: 2 checked.
- Fresh-process complete manager reconstruction/decision comparison: 1 passed in 40.49s, exit 0.
- CLI help: exit 0; help-only runtime unchanged.
- git diff --check and staged diff --check: passed. Full source/docs/test diff inspected;
  canonical/project/provider/schema modules and historical evidence unchanged.

## Limits / future work

Trusted single-writer recovery and quiescent external filesystem/Git writers are required.
Recovery journal has no cross-process writer mutex. Git refs, checkpoint metadata,
ProjectState and journal updates are separate publications. An ambiguous interrupted
RUNNING recovery intentionally escalates to human; it is not automatically replayed.
Unproven historical orphan refs/legacy locks and interrupted worktree operations are
preserved. Caller ownership/liveness and stable approval are not authenticated. PID age
is never death proof; remote/unknown ownership blocks. Submodules, sparse/hidden-index
workflows, detached primary checkout and relocation remain unsupported. Observations
are bounded; stronger concurrency/streaming/durability/race protections are later work.
Python 3.11/non-Windows execution not verified; no type checker configured.

## Phase 09 boundary and handoff

No Reviewer, Phase Gate, acceptance scoring, PASS/REVISE, provider dispatch/fallback,
Approval Router, scheduler or orchestration loop. Runtime reconnect/dispatch remains
future integration. No real-repository recovery or self-promotion.

Attempt commit: verified implementation commit created after this report; exact hash recorded
in the following documentation handoff and final report to avoid self-referential commit hashes.
Documentation/handoff HEAD: recorded in final report after the handoff commit.
Current state must end AWAITING_REVIEW / PENDING; only external reviewer may approve.
