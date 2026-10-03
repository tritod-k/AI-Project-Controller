# Phase 11 — Approval Router evidence

This is an implementation-attempt record, not external Phase Gate approval.
Implementation and verification are complete; external review remains pending.

## Exact starting point and scope

- Branch: `codex/phase-11-attempt-01`.
- Starting master/HEAD/`aipc-phase-10-pass`: `0c2375ff936fb6cd2b5e09fc8694cae49ed4c4b6`.
- Previous `aipc-phase-09-pass`: `8cca14af4024cd5f070237956101ebf621bef73c`.
- Exact baseline: 1097 tests passed in approved Phase 10 finalization.
- Initial tree was clean; no real Controller/Version/Recovery metadata existed.
- No real provider, network, credentials, Phase 12/13 work, merge or PASS tag.
- Canonical contracts and all six public schemas remain unchanged.
- Historical completion/evidence reports remain unchanged.

## Test evidence collected during development

| Boundary | Observed RED | Observed GREEN |
| --- | --- | --- |
| Routing/types/policy | 33 failed, missing boundary (7.34s) | 33 passed (10.43s) |
| Persistence/console | 26 failed, missing Router/presenter (10.28s) | Combined 59 passed (1.81s) |
| Canonical Mock observation | 4 failed, missing optional protocol/method (0.74s) | New + adapter/mock regression 94 passed (2.47s) |
| Controller integration | 19 failed, missing injection (9.81s) | Initial 18 passed; remaining observer test corrected and passed (20.39s) |
| External/network risk | 2 failed, 10 passed (0.64s) | Fast suite 75 passed (2.67s) |
| Lost session/conflicting terminal decision | 2 failed, 3 passed (351.60s) | Authority suite 6 passed (345.50s) |
| Replaced pending approval ID | Incorrect PASS, 1 failed (183.46s) | Included in authority suite above |
| Closed/expired route replay | 5 failed, 19 passed (1.93s) | Fast suite 79 passed (2.44s) |

The initial Controller run's remaining failure was an injection callback that
repeated an already-applied RUNNING transition when the failure outcome was saved.
Guarding the injection to the initial intent preserved its assertion: no response
after authority changed. New test module names were made unique after full
collection exposed collisions with existing un-packaged Controller test names.
No tests were removed or existing assertions weakened.

## Coverage and architecture

New tests: canonical boundary 4, structural contracts 12, response restart 5,
authority 6, console 6, Controller flows 14, persistence 24, routing 33: total 104.
Final full execution: 1201 passed, comprising the unchanged 1097 baseline plus
104 new tests. Both final XML reports have zero failures, errors and skips.

Routing proves conservative LOW/default behavior, all risk floors, deterministic
deny/human precedence, explicit narrow allow, invalid policy rejection and exact
request/context association. Resource tests cover component containment, traversal
and a real Windows junction escape. Mutation is revalidated at canonical boundaries.

Atomic audit tests cover fresh reload, duplicate/conflicting decisions, immutable
routing, corrupt/duplicate JSON preservation, publication failure, cancellation,
explicit expiry and closed-authority rejection. Audit/presenter omit command,
description, prompt and arbitrary metadata values; fake sentinels verify omission.

Controller tests use disposable Git repositories only. Same-run automatic/human
approval, operational denial, cancellation, lost ownership, stale action/resource,
dirty/changed HEAD, tool authority and after-intent mutation cannot dispatch another
worker or bypass evidence/review/Gate. Response intent is durable before adapter
response; acknowledgement is distinct. Crash before intent can safely revalidate;
intent without receipt stops WAITING_HUMAN; durable receipt continues the same run.

## Fresh development review

One fresh read-only whole-branch reviewer inspected tracked/untracked source against
the attachment, plan/design and repository instructions. No Critical or Minor findings.
One Important finding: direct `route` callers could receive historical AUTO_ALLOW
after a closed audit. Regression tests reproduced all closed statuses and post-route
expiry before the fix. Router now raises a typed closed-authority error; historical
routing remains available through `inspect`. Final regression passed below.
The reviewer independently ran 75 bounded tests successfully (3.49s), before the
additional closed-authority tests were added. This is advice, not external PASS.

## Final regression verification

Full suite: **1201 passed in 8296.27s**, exit 0, finished
`2026-09-29T11:10:55.5680963+00:00`.
Explicit components: **690 passed in 8283.96s**, exit 0, finished
`2026-09-29T11:10:45.6517451+00:00`.
On 2026-10-03 both final XML reports and exit receipts were inspected: no failures,
errors or skips. All 160 source/input SHA-256 entries matched the pre-execution
snapshot, so the completed executions still validate the handoff source.

An interrupted earlier run produced only partial logs and no final XML/exit result;
its partial output is not counted as a passed suite. Resumed executions use separate
`full-resumed`/`components-resumed` logs, XML and exit-code receipts under
`.superpowers/sdd/2026-09-29-phase-11`. The original partial logs remain preserved.
Commands use `.venv/Scripts/python.exe`:

```text
-m pytest -vv --tb=short --junitxml=.superpowers/sdd/2026-09-29-phase-11/full-resumed.xml
-m pytest -vv --tb=short --junitxml=.superpowers/sdd/2026-09-29-phase-11/components-resumed.xml <explicit paths below>
-m aipc --help
-m ruff check .
-m ruff format --check .
-m pip check
-m aipc.domain.schemas --check
-m aipc.project.schemas --check
git diff --check
```

Explicit paths: `tests/controller`, `tests/review`, `tests/approvals`,
`tests/unit/test_recovery_models.py`, `tests/integration/test_recovery_manager.py`,
`tests/unit/test_version_models.py`, `tests/integration/test_version_manager.py`,
`tests/contract/test_version_contracts.py`, `tests/unit/test_evidence.py`,
`tests/integration/test_evidence_collection.py`, `tests/contract/test_evidence_contracts.py`,
`tests/unit/test_prompt_engine.py`, `tests/contract/test_prompts.py`,
`tests/unit/test_normalization.py`, `tests/integration/test_mock_normalization.py`,
`tests/contract/test_normalizers.py`, `tests/contract/test_agent_adapters.py`.
Explicit passed selection: 690. Normalizer contracts account for additional eight tests
beyond the previously used component selection; baseline implementation is unchanged.

| Explicit regression component | Passed |
| --- | ---: |
| Controller | 80 |
| Reviewer / Phase Gate | 90 |
| Recovery Manager | 72 |
| Version Manager | 80 |
| Evidence Collector | 107 |
| Prompt Engine | 79 |
| Normalization | 71 |
| Adapter contracts | 7 |
| Approval Router / Controller approval integration | 104 |
| Total | 690 |

CLI remains help-only. On 2026-10-03 CLI help, Ruff check/format (134 files),
dependency check, four canonical/two project schema checks and git diff --check
all exited 0. Local development transcripts are retained in the ignored Phase 11
planning directory; they are development validation evidence, not production
action execution receipts. No production runtime metadata was created.

## Limitations and rulings

Single-writer/quiescent filesystem assumptions, nontransactional audit/journal/state/
provider publications and unverified actor identity remain explicit. Hashes bind
content, not origin. Trusted callers must supply accurate structured facts and
nonsecret identifiers; this layer is not a command parser or full DLP system.
Real-provider session reconstruction/idempotency remains Phase 12; no exactly-once
provider response is claimed. Ambiguous receipts and unknown edits stop for inspection.

Execution rulings, in order:

1. Use the requested attempt branch in the shared checkout, preserving master and
   fixture isolation. Cost if wrong: workspace placement, not approved history.
2. Consolidate implementation into the final attempt commit. Cost: less granular
   history; design/tests/ledger preserve development evidence.
3. Acknowledged DENY follows legal WAITING_APPROVAL -> RUNNING -> WAITING_HUMAN,
   with no execution of the denied action. Cost: transient RUNNING observation.
4. Finish handoff/full regression before commit. Cost if wrong: stale/overstated evidence.
5. Verify the three known Controller findings through reproduced regressions;
   no duplicate reviewer pass. Cost: remaining regression risk is tested/external-reviewed.
6. Defer real-provider session/idempotency to Phase 12. Cost: no real-provider service yet.
7. Retain documented transaction/concurrency/authentication assumptions. Cost:
   deployments outside them require stronger safeguards.
8. Trust structured facts and bounded redaction instead of command semantics/full
   secret detection. Cost: mislabeled facts/secret identifiers are not authenticated/detected.
9. Retain ignored planning/test evidence for resume alongside previous preserved
   notes. Cost: local ignored disk usage, no committed runtime artifacts.
10. Resume interrupted validation with hidden local launchers and separate final
    XML/exit receipts. Cost: local background processes must be tracked; partial
    earlier logs are never substituted for completed results.

Deferred minor findings: none.
