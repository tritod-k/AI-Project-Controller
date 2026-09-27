# Phase 09 — Reviewer + Phase Gate evidence

## Scope and stable start

Authorized Phase 09 only, from clean `master` HEAD and `aipc-phase-08-pass`:
`f95ee453d4dc13971276f5b971b9350b00c0555b`. Prior Phase 07 PASS target:
`5df11213c7f25884046fcc3a7bff62b78c01fcad`. Attempt branch:
`codex/phase-09-attempt-01`. Phase 08 baseline: 927 tests, including 72 Recovery
and 80 Version tests. No prior PASS tag moved, no master merge or Phase 10 work.
Phase 09 implementation attempt: `9ecd6bde8c1c249120fa499daabd7c960acd5a6f`.
Commit message: `AIPC Phase 09 attempt 01 - reviewer and phase gate`.
The subsequent handoff commit changes only state and evidence documentation;
the validated implementation remains unchanged.

## Architecture and authority

`ProjectDefinition`/`PhaseDefinition` + canonical `TaskRequest`/`AgentResult` +
Phase 06 `EvidenceBundle` -> provider-neutral `Reviewer.review(ReviewContext)` ->
canonical Phase 01 `ReviewResult` -> independent `PhaseGate.evaluate(...)` ->
private logical `PhaseGateDecision`. Exact Phase 07 ATTEMPT metadata, structured
reviewed provenance and `ProjectState` form the binding context. No core adapter,
normalizer, canonical, state-machine, Evidence Collector, Version Manager or Recovery
Manager implementation was changed. All six existing public schemas remain intact.

Observed retained data outranks structured worker claims, which outrank worker
narrative. Checks consume safe indexed bytes and reparse the retained pytest summary;
commands and transcripts remain claims, not execution receipts. An explicit rule
maps each objective criterion to a test outcome, indexed artifact or Git change
corroborated by the retained patch. Semantic criteria without machine proof remain
UNVERIFIED. Required FAIL/UNVERIFIED prevents PASS; optional non-PASS is recorded
without automatically blocking. Missing reproducible evidence produces REVISE.

The separate gate enforces review completeness, verdict consistency, collected
citations for every PASS criterion, exact project/run/phase/attempt/task/agent/
evidence/checkpoint provenance, clean retained Git status and mandatory test
observations. PASS returns only logical mark-pass, stable and next-phase actions
plus the existing exact-attempt `StableCheckpointAuthorization`. VersionManager
remains the actual Git publication authority. REVISE returns current-phase repair;
BLOCKED requires external/human resolution; ERROR requires reviewer infrastructure
resolution. No gate invocation mutates Git or ProjectState. Phase 05 repair prompts
consume the unchanged REVISE `ReviewResult` with its fixes and prior-attempt context.

## Acceptance evidence

| Requirement | Tests and observed behavior |
|---|---|
| Canonical and identity boundary | Mutable nested canonical data revalidated; mismatched project/phase/attempt/task/run/commit rejected; canonical snapshots and rehashed forgeries checked |
| Evidence integrity/read limits | Manifest, index, task/result/command snapshots, every artifact hash/size and reparsed test log checked read-only; 16 MiB artifact/64 MiB total caps cannot be disabled |
| Required PASS/FAIL/UNVERIFIED | Objective test, artifact and changed-file rules yield individual `CriterionResult`; missing/failed required data yields REVISE with criterion-specific fixes |
| Optional criteria | Optional FAIL and UNVERIFIED recorded without blocking default PASS; explicit require-optional policy changes that behavior; false optional PASS refused |
| Evidence conflict and narrative | Structured test claims disagreeing with parsed failures yield FAIL/REVISE; optimistic summary never overrides observations; declared changed file without corroborating patch is UNVERIFIED |
| Verdict semantics | PASS, repairable REVISE, operational/provenance BLOCKED and reviewer infrastructure ERROR tested; FAIL and BLOCKED remain distinct |
| Stale/exact review | Changed bytes, task, result, state, checkpoint and mutated review rejected; attempt-01 reference cannot rebind to attempt-02, including directly built envelopes |
| Manual structured review | Bare PASS rejected; external review requires independent reviewed provenance; semantic PASS requires collected citation; no prose parsing or force-PASS |
| Gate outcomes | PASS logical stable/next-phase, REVISE repair-only, BLOCKED human, ERROR infrastructure; contradiction/criterion incompleteness/wrong phase or attempt rejected |
| Prompt integration | REVISE `ReviewResult` feeds existing repair prompt; fixes, failed IDs, attempt context and stop rule retained |
| Version integration | PASS authorization accepted for exact attempt/commit in isolated fixture Git repo; reuse across attempts/phases and non-PASS promotion denied |
| State integration | Fixture PASS/REVISE/BLOCKED decisions align with existing explicit complete/retry/human-wait transitions; gate itself does not apply them |
| Provider/phase boundaries | No provider API/LLM, Controller loop, worker execution, arbitrary command execution, auto retry, Approval Router or real-repository runtime checkpoint/recovery mutation |

## Red/green review corrections

Tests were written before behavior changes and their initial failures observed.
Initial missing package test failed before implementation. The independent read-only
whole-branch reviewer found an attempt-rebinding bug, unsupported Git change PASS,
empty/manual criterion evidence, false optional PASS and incomplete ERROR handling.
Each was reproduced by a failing regression then corrected. A direct-envelope
rebinding variant and cross-file patch-header variant were also reproduced and
closed. A one-gigabyte caller-specified evidence-read budget was reproduced and
rejected with a capped private contract. This internal review did not approve the
real Phase 09 Phase Gate; external review remains pending.

## Validation and limits

The dedicated `pytest tests/review -q --tb=short` run passed 90/90 in 77.50s
on Windows / Python 3.13.0. The explicit Recovery, Version, Evidence Collector,
Prompt Engine, Normalization and Adapter contract/unit/integration subset passed
455/455 in 1517.99s, exit 0. Final full `python -m pytest -q --tb=short`:
1017/1017 passed in 1450.48s (24m10s), exit 0, including all 927 baseline tests
and 90 Phase 09 tests. CLI help, Ruff
lint/format, pip dependency check, four canonical and two project/state schema
drift checks have exited 0. A previous full run was intentionally interrupted
after a late read-budget regression was added; it is not final evidence.
An additional disposable fixture check explicitly configured a missing optional
artifact: criterion UNVERIFIED, required tests PASS, overall review and gate PASS.
It exited 0 and made no Git mutation.

The legacy command used `python -m pytest <the following files> -q --tb=short`:

| Subset | Files under tests/ | Passing cases |
|---|---|---:|
| Recovery | unit/test_recovery_models.py, integration/test_recovery_manager.py | 72 |
| Version | unit/test_version_models.py, contract/test_version_contracts.py, integration/test_version_manager.py | 80 |
| Evidence | unit/test_evidence.py, contract/test_evidence_contracts.py, integration/test_evidence_collection.py | 107 |
| Prompt | unit/test_prompt_engine.py, contract/test_prompts.py | 79 |
| Normalization | unit/test_normalization.py, contract/test_normalizers.py, integration/test_mock_normalization.py | 71 |
| Adapter | unit/test_agent_contracts.py, contract/test_agent_adapters.py | 46 |

The implementation remains offline. All Version Manager mutations in integration
tests target disposable pytest fixture repositories; no operation targets the
real AIPC checkpoint/ref store. Private logical authority and retained SHA-256
hashes do not authenticate callers or evidence origin. The caller supplies the
phase review-stage marker and checkpoint metadata; Phase 10 must wait for actual
stable publication before applying advancement. Unknown Git quoted/rename path
forms remain UNVERIFIED in the deterministic file-change evaluator. Semantic
architecture/code-quality judgment awaits Phase 13. Review audit persistence,
live Git/evidence race protection and cross-process durability remain later work.
