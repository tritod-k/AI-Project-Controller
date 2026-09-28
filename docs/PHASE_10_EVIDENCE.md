# Phase 10 — Controller Orchestration Loop evidence

## Scope and Git authority

Phase 10 attempt 01 only, on `codex/phase-10-attempt-01`, starting from
`aipc-phase-09-pass` / master `8cca14af4024cd5f070237956101ebf621bef73c`.
Prior Phase 08 tag target: `f95ee453d4dc13971276f5b971b9350b00c0555b`.
Documentation alignment: `59a5121458cd1f1dcc4f72425fd925a53531756a`.
Implementation and handoff hashes will be recorded after verified commits.
External Phase 10 review remains PENDING; this report is not PASS authorization.

The real AIPC checkout was used only for source/documentation/test development,
read-only inspection and normal Git attempt commits. Controller, VersionManager
and RecoveryManager runtime mutations executed only against disposable fixture
repositories. Master and previous PASS tags remain unchanged. No Phase 11 branch,
real-provider execution, destructive reset/clean or stable Phase 10 tag is created.

## Component composition

`State -> Prompt -> Agent -> Normalize -> Evidence -> Review -> Gate -> Version/State`.
Constructor-injected existing components retain their contracts. Exact registry
lookup does not select/fallback providers. Controller examines raw envelope
identity/status; only the injected normalizer interprets raw payload fields.
Canonical inputs/results and projected review context are revalidated. CLI remains
help-only. Six existing JSON Schemas and Phase 01 canonical models are unchanged.

State/Recovery use the primary definition; Prompt/Review use its validated
attempt-workspace projection, satisfying their existing workspace contracts.
Version's optional prior ATTEMPT source is a narrow prerequisite for preserving
repair work, shared with Recovery lineage checks; default PRE_PHASE behavior is
unchanged. Worker/caller remains responsible for committing a clean workspace.

## Acceptance evidence

| Requirement | Disposable test evidence |
| --- | --- |
| Full PASS and ordered publication | `test_lifecycle.py`: one worker; Gate exact authorization; actual STABLE; persisted completion; audit stages; logical next-phase permission without dispatch; idempotent replay |
| Explicit caller multi-phase example | `test_lifecycle.py`: PASS, REVISE/repair/PASS, final PASS; caller invokes each phase and fast-forwards fixture primary; completed project |
| Known-good bounded repair | `test_outcomes.py`: distinct attempt/task/worktree/evidence; explicit previous-task/attempt repair prompt; previous attempt retained; policy and project retry budgets |
| Safe non-PASS outcomes | FAILURE, TIMEOUT, MALFORMED_OUTPUT, BLOCKED, ERROR, UNVERIFIED, evidence/reviewer infrastructure errors; no unauthorized STABLE or worker revision |
| Approval boundary | `test_boundaries.py`: WAITING_APPROVAL handle; no Controller approval response; explicit fixture caller approval and RUNNING transition resumes same handle |
| Provider/session boundary | Quota/outage/start interruption -> operational waiting; lost process-local session -> human inspection; no replacement dispatch or implementation retry |
| Cancellation | `test_outcomes.py`: explicit adapter cancellation retains workspace; delayed failed acknowledgement cannot become CANCELLED; active driver waits for cancellation completion |
| Restart | `test_restart.py`: fresh Controller/Version/Recovery instances reconcile nine known local windows; five ambiguous windows stop or resume only proven safe continuation |
| Partial publications | `test_publication.py`: ref published/metadata failed -> Recovery inspection; STABLE with incomplete state is not completion; later exact state-intent resume completes once |
| Changed authority | Evidence bytes, primary/attempt HEAD, checkpoint ref, run/phase/task/agent/attempt/timestamp changes fail closed; terminal observed worker HEAD cannot be re-attributed after human edits |
| Intent preconditions | `test_restart.py`: revoked running authority after START/CHECKPOINT/EVIDENCE/REVIEW/STABLE intent prevents consequential continuation |
| Durable private contracts | `test_contracts.py`: typed stages, required facts, correlated identities, fake PASS rejection, immutable facts/events, corrupt journal and held/stale lease rejection |
| Repair lineage prerequisite | `test_repair_lineage.py`: exact immediately previous same-phase ATTEMPT under same PRE_PHASE; Version and Recovery agree; invalid lineage rejected |

## Fresh branch review and regression fixes

One fresh independent whole-branch review found two Important issues and no
substantiated Critical or Minor issues. It was read-only on AIPC; reproductions
used disposable fixture repositories. Internal review does not replace external
ChatGPT Phase Gate review.

1. Cancellation intent was mistaken for successful acknowledgement. The delayed
   failed-cancel regression was observed RED (false CANCELLED), then GREEN after
   giving cancellation sole terminal-result ownership and making the active
   driver await the shared operation while retaining its lease. Failure retains
   CANCEL_INTENT and WAITING_HUMAN for explicit inspection.
2. Intent callbacks could revoke authority after the last check. START_INTENT and
   STABLE_INTENT reproductions showed worker dispatch / STABLE publication under
   stale state. Expanded tests also exposed CHECKPOINT/EVIDENCE continuation.
   Revalidate lifecycle/workspace after intents, and exact reviewed state,
   evidence/checkpoint and Gate authorization before stable creation. Review and
   repair boundaries are checked as well; durable intents remain intact.

Other observed RED/GREEN development checks covered immutable task facts, required
stage facts/fake PASS, terminal HEAD attribution, exact reviewed timestamp,
reviewer exception classification and repair lineage. A lost-session fixture
initially raised StopIteration; its unknown-handle path now delegates to the
adapter's typed UnknownRunError, without changing production behavior.

## Validation

Windows, project-local Python 3.13.0. Baseline: 1017 tests on exact Phase 09 stable.
Fresh final-source suite: **1097 passed in 5223.55s (1:27:03)**, including the
1017 existing tests and all 80 new Controller tests. No source changed afterward.
Controller groups: contracts 15, repair lineage 3, lifecycle 2, outcomes 16,
boundaries 9, restart/preconditions 33, publication 2.
Explicit prior-component regression: **498 passed in 1878.24s (0:31:18)**.
Standalone review-fix checks: cancellation **2 passed in 92.55s**;
intent-authority **5 passed in 311.72s** after observed RED.
Ruff check and format (113 files), pip check, canonical schemas (4), project/state
schemas (2) and final diff checks passed. No new dependency or schema was added.
Commands use `.venv/Scripts/python.exe`; no global dependency installation.

```text
python -m pytest -v --tb=short
python -m ruff check .
python -m ruff format --check .
python -m pip check
python -m aipc.domain.schemas --check
python -m aipc.project.schemas --check
git diff --check
```

Explicit regression selection includes adapter contracts, normalization unit/mock
integration, Prompt Engine unit/contracts, Evidence Collector unit/contracts/
integration, Version models/contracts/integration, Recovery models/integration
and the complete Phase 09 Review suite. Controller test files above supply private
unit/contract, lifecycle and failure/restart integration coverage.

Exact explicit regression command:

```text
python -m pytest tests/contract/test_agent_adapters.py tests/unit/test_normalization.py tests/integration/test_mock_normalization.py tests/unit/test_prompt_engine.py tests/contract/test_prompts.py tests/unit/test_evidence.py tests/contract/test_evidence_contracts.py tests/integration/test_evidence_collection.py tests/unit/test_version_models.py tests/contract/test_version_contracts.py tests/integration/test_version_manager.py tests/unit/test_recovery_models.py tests/integration/test_recovery_manager.py tests/review -q --tb=short
```

Local command transcripts are retained under ignored `.git/phase10-validation/`.
They are observations from this development session, not authenticated execution
receipts or provider output. Portable evidence is the committed tests and actual
reported commands/results; local temporary fixture paths are not release inputs.

## Rulings made during inline execution

- Explicit user authorization to proceed takes precedence over an additional
  design approval pause. Cost if wrong: design must be revised after review.
- Use the requested normal attempt branch during manual bootstrap; runtime
  worktrees exist only in fixtures. Cost if wrong: an additional source checkout
  may be needed; no runtime mutation of AIPC occurs.
- Use one fresh whole-branch reviewer required by executing-plans, with no
  per-task agents. Cost if wrong: a defect may escape that one review; regressions
  and external acceptance remain required.
- Preserve canonical schemas and use a versioned private Controller journal.
  Cost if wrong: later private persistence migration may be needed.
- Use validated attempt projection for Prompt/Review while State/Recovery retain
  primary workspace. Cost if wrong: their existing context contract rejects it.
- Add optional immediately prior ATTEMPT source plus matching Recovery checks.
  Cost if wrong: repair lineage is rejected rather than discarding prior work.
- Explicit one-phase stop overrides the older blueprint automatic-loop example;
  fixture caller invokes and integrates each later phase explicitly. Cost if
  wrong: caller needs another invocation instead of automatic advancement.
- Replay only exact proven local windows; ambiguous external effects delegate to
  Recovery/human. Cost if wrong: extra human intervention; no blind redispatch.
- Windows uses equivalent read-only plan/ledger/review records instead of skill
  POSIX helper scripts. Cost if wrong: less helper-generated audit metadata;
  explicit evidence and actual Git hashes remain available.

Deferred minors: none were substantiated by the independent review.

## Limits preserved for later phases

Ref/metadata/state/journal publication is not one transaction. Stale/unknown locks
and ambiguous effects require inspection; exactly-once provider execution and
session reconstruction are not claimed. Cancellation acknowledgement depends on
the adapter contract. Quiescent external filesystem/Git access and single-writer
operation are required; hostile filesystem race hardening is deferred.

Reviewer authority and evidence origin are not cryptographically authenticated.
Logs/structured claims are not execution receipts. Arbitrary semantic criteria
remain UNVERIFIED without objective rules. Raw retention is caller responsibility.
Submodules, sparse/hidden-index modes, stronger durability, retention/redaction and
provider supervision remain outside scope. Python 3.11/non-Windows execution and
type checking (not configured) are unverified.

Phase 11 Approval Router, Phase 12 real Codex adapter and Phase 13 independent LLM
reviewer remain unimplemented. Existing canonical/provider boundaries are intact.

## Recommended next action

Submit Phase 10 results to the external ChatGPT reviewer and wait for PASS / REVISE / BLOCKED decision.
