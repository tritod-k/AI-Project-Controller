# Phase 04 Evidence — Normalization Layer

## Review handoff

Status: AWAITING_REVIEW. Phase Gate: PENDING. Attempt: 01.
Branch: `codex/phase-04-attempt-01`.
Implementation commit: `bc10f9aae15656793c3df2663f5c169c9bfaaf8b`.
Message: `AIPC Phase 04 attempt 01 - normalization layer`.
Verified stable master / aipc-phase-03-pass:
`f6e58dfafde178a0044ce9e859bff2a9e7055288`.
The previous displayed tag hash typo did not reflect a Git discrepancy.
The subsequent documentation handoff changes only this evidence and CURRENT_STATE.md;
its exact resulting HEAD is reported after commit, separately from implementation.
No Phase 04 PASS tag, stable merge, gate decision or next-phase authorization.

## Acceptance criteria

- [PASS] Shared normalizer Protocol accepts RawAgentResult/context and returns validated
  AgentResult. See normalization/base.py, context.py, validation.py and 55 unit cases.
- [PASS] Provider-specific mock mapping is isolated in adapters/mock/normalizer.py;
  explicit lifecycle/status/identity checks and typed failures. No shared provider branching.
- [PASS] SUCCESS, FAILURE, CANCEL, observed TIMEOUT and terminal approval APPROVE/DENY
  use real Phase 03 adapter integration; pending approval cannot complete.
- [PASS] Malformed output, unknown status, missing identity/context, primitive/nested
  type errors, invalid timestamps, contradictory outcomes and invalid canonical totals fail.
- [PASS] Five raw formats map through five test-only normalizers to equivalent complete
  canonical JSON except provider identity; each passes committed JSON Schema.
- [PASS] Raw/canonical type separation, canonical-only consumer seam, core AST imports
  and production key-location inspection establish the current downstream boundary.
- [PASS] Caller-stored raw JSON reference resolves; metadata remains empty and canonical
  root shape unchanged. No full raw response embedded or Evidence Collector introduced.
- [PASS] Existing 518 tests continue passing; all six schemas, canonical models, Phase 03
  adapter/agent code, project/storage and dependency declarations unchanged.
- [PASS] Required CLI/lint/format/pip/schema/subset/full tests and diff checks pass.
- [PASS] Attempt state AWAITING_REVIEW/PENDING and exact stable/implementation hashes recorded.
- [UNVERIFIED] External Phase 04 review, Python 3.11, non-Windows and wheel installation.
  These were not claimed as completed by the worker.

## Architecture and mappings

RawAgentResult -> MockNormalizer -> candidate -> AgentResult.model_validate -> AgentResult.
The shared validator also revalidates already-created mutable canonical objects.
Only provider-independent identity/timestamps/reference are provided in explicit context.
Schema version remains 1.0; unknown tests are null, commands/files/artifacts empty,
unknown evidence paths null, metadata empty. Worker completion is only a claim.

| Raw status | Envelope | Canonical |
| --- | --- | --- |
| done | COMPLETED | COMPLETED |
| task_failed | FAILED | FAILED |
| permission_denied + DENY | FAILED | FAILED |
| cancelled | CANCELLED | CANCELLED |
| timed_out | TIMED_OUT | TIMED_OUT |
| permission_needed | WAITING_APPROVAL | UnsupportedRawStatusError |

Approval done requires APPROVE or an ordinary validated message. Missing/invalid
message cannot be excused by a supplied decision. Terminal permission fields,
error fields outside task_failed, and decisions outside approval variants are rejected.
The timeout bridge requires an observed AgentTimeoutError; caller associates it with
its run. This preserves the Phase 03 exception contract and introduces no retry loop.

## Malformed input evidence

Unit cases reject missing/unknown/wrong-type status; missing/wrong-type/mismatched run;
non-dictionary raw/nested message, missing message/body, invalid handle/lifecycle;
context task/agent disagreement; missing each of six context fields; blank identities,
wrong primitives, naive/numeric/reversed timestamps, blank reference; unchecked context
copy; missing/malformed failure details; DENY/APPROVE contradictions; invalid canonical
counts and mutated canonical collections. Eight additional regressions reject conflicting
known error/permission/decision fields across terminal variants. Unknown unrelated
extras remain ignored and are never copied to canonical metadata.
Actual Phase 03 malformed literal text stays unchanged and raises MalformedRawResultError.

## Five heterogeneous formats

`tests/fixtures/normalization/style_a.json`: flat state/message/counts.
`style_b.json`: nested execution code/list of counts/output summary.
`style_c.json`: numeric status, finished flag/result and renamed metric counts.
`style_d.json`: text/tests/finish event list.
`style_e.json`: response status/body pair and renamed test fields.
All map to COMPLETED, summary done, task task, agent worker, 3 total/3 passed/0 failed/
0 skipped. Full result JSON compares equal after removing only provider identity.
Fixture mappers are test-only; no fake production provider or normalizer registry.

## Commands and observed results

All Python commands used `.venv/Scripts/python.exe` in the repository; pytest commands
used `python -m pytest`. Windows Python 3.13.0 / pytest 9.1.1 / Ruff 0.16.9 /
Pydantic 2.13.5 / PyYAML 6.0.3. No new dependencies/global installs.

| Command | Result |
| --- | --- |
| python -m aipc --help | Original help-only CLI; exit 0 |
| pytest -q | 589 passed, 0 failed/skipped, 8.81s; exit 0 |
| pytest tests/unit/test_normalization.py tests/integration/test_mock_normalization.py tests/contract/test_normalizers.py -q | 71 passed, 0.60s; exit 0 |
| pytest tests/contract/test_agent_adapters.py -q | 7 passed, 0.42s; exit 0 |
| python -m ruff check . | All checks passed; exit 0 |
| python -m ruff format --check . | 48 files already formatted; exit 0 |
| python -m pip check | No broken requirements; exit 0 |
| python -m aipc.domain.schemas --check | Checked 4 canonical schemas; exit 0 |
| python -m aipc.project.schemas --check | Checked 2 project schemas; exit 0 |
| git diff --check / git diff --cached --check | No whitespace errors; exit 0 |

589 = 518 existing + 55 normalization unit + 8 mock integration + 8 normalization contracts.
TDD evidence: 47 initial unit and 8 actual mock integration cases failed before the
production normalizer existed; all passed after implementation. Review regressions:
8 contradictory-known-field cases failed with DID NOT RAISE before the guards, then passed.

Read-only internal reviewer independently ran the initial 63 normalization tests and
70 existing Phase 03 tests, identified one P2 known-field contradiction issue, and
rechecked the fix/documentation: 71 passed in 0.62s, no remaining actionable findings.
This internal review is not the external Phase Gate decision.

## Scope and Git audit

Protected diff against stable Phase 03 returned exit 0 for domain models/enums/schemas,
all schemas, agents, approved mock adapter, project/storage, pyproject.toml and
AGENTS.md/SKILL.md/MASTER_PLAN.md. Domain errors gained only the four normalization errors.
`rg -l` for mock status/run/decision/permission/error keys in production found only
adapters/mock/adapter.py and adapters/mock/normalizer.py. No provider branching, real
SDK/dependency, router, Prompt Engine, Evidence Collector or Phase 05 work was added.
97 tracked/staged files scanned for private-key/token patterns and tracked .env: zero matches.
Staged diff inspected before commit; implementation commit left a clean working tree.
Final documentation handoff is checked separately with git status/log/tags and stable refs.

## Limits, recovery and next action

No known defects remain. Raw references are caller-owned opaque references; no existence,
retention or durability guarantee. Provider identity and timeout/run association are
caller responsibilities. Pending approval is intentionally outside terminal AgentResult.
Mock run persistence and real approval blocking remain deferred. Canonical mutable
collections must be revalidated at boundaries. Project/state single-writer limits and
later durability hardening remain accepted. AGENTS/MASTER_PLAN phase numbering must be
resolved before affected later phases are authorized. No configured live controller
state conflicts with this summary; persisted state is test-only.
No architecture deviations or approvals requested. Resume from this branch and exact
implementation commit; preserve existing stable tags and unknown edits.

Submit Phase 04 results to the external ChatGPT reviewer and wait for PASS / REVISE / BLOCKED decision.
