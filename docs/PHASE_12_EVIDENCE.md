# Phase 12 — First Real Provider: Codex Adapter evidence

Implementation attempt, not Phase Gate PASS. External/manual review remains authoritative.

## Stable starting point and scope

- Clean master, `aipc-phase-11-pass`: `8a937057f43e4b0443a0a87668407ef79e7aaf4b`.
- Previous `aipc-phase-10-pass`: `0c2375ff936fb6cd2b5e09fc8694cae49ed4c4b6`.
- Branch: `codex/phase-12-attempt-01`, created from the verified stable tag.
- No existing runtime checkpoint/attempt metadata required recovery in the real checkout.
- No master integration, stable promotion, PASS tag, destructive Git or Phase 13 work.
- Core source, canonical models and all six schemas retain their original Git content.

## Runtime and dependency inspection

Windows, Python 3.13.0. Existing desktop Codex CLI 0.159.2 reported an authenticated
ChatGPT session. Initially no Python SDK was installed. The official public
`openai-codex` 0.160.0 wheel/API was inspected, then installed only in the project
environment through the pinned optional dependency. Its bundled runtime is 0.160.0.
No account IDs, tokens, login files or complete host configuration were printed/copied.
Live tests reuse the existing session, never initiate login.
Readiness-only compatibility probes passed with SDK 0.160.0 against both runtime
0.159.2 and 0.160.0 without starting a thread/turn. Real worker execution was
tested with 0.160.0 only; 0.159.2 worker execution is not claimed as verified.

The public SDK provides AsyncCodex/AsyncThread/AsyncTurnHandle start/resume/read/turn,
stream and interrupt methods. No Agents API, Responses coding loop or Node sidecar.
No model selection/routing/fallback is implemented; unspecified model remains unknown.

## Provider and canonical architecture

```text
existing Controller -> AgentAdapter -> CodexAdapter -> CodexTransport -> real Codex
CodexRawResult -> CodexNormalizer -> canonical validation -> AgentResult
```

Provider-specific syntax, SDK imports and safety guards live under adapters/codex.
Existing Controller/provider-neutral subsystems contain no new provider branches.
The same shared contract suite exercises Mock and fake Codex. Ordinary pytest needs
neither provider SDK nor authentication/network. A fresh wheel installation imports
the adapter/normalizer and runs CLI help with the optional SDK absent.

## Safety and independent review

- Native escalation implicitly approved: **NO**.
- Codex native auto-review supplies AIPC Approval Router authority: **NO**.
- `approval_events`: **FALSE**; no safe native bridge.
- Exact thread/turn calls use deny_all/never; effective thread reviewer is user.
- Before initialization, a pin-specific private guard replaces SDK default acceptance
  with decline/empty permissions. Missing guard layout/unknown requests fail closed.
- Private read-only config/read verifies effective host policy; merged host MCP
  servers are disabled before a thread. Unsafe/dotted keys and policy drift fail closed.
- Default workspace-write; read-only supported; full access rejected. Exact resolver
  binding, disjoint protected roots, link checks and full tool-bundle authorization
  precede dispatch. Worker network/search/MCP/extra writable roots are disabled.
- Turn sandbox override is deliberately omitted: actual installed-SDK wire probe
  confirms sandboxPolicy=null, approvalPolicy=never and inherited validated policy.

Fresh independent architectural review identified three issues: SDK turn enum would
lose temporary-directory exclusions, cancelled readiness could leak a client, and
JSON-quoted credential text could survive known-field redaction. All three received
failing regression reproductions, fixes and passing checks. Re-review found no unresolved
important issues; 42 focused tests passed. This review does not grant Phase Gate PASS.

Additional red/green tests protect malformed/duplicate snapshots, stream contradictions,
private prompt/reasoning exclusion, parallel continuation dispatch, cwd traversal and
unsupported rename paths. Known-field redaction is not complete semantic DLP.

## Sessions, cancellation and operational recovery

Atomic bounded journals bind task/config digest, run/agent/project/phase/attempt,
workspace, exact thread/turn and SDK/runtime identity. Dispatch/interrupt intents
precede side effects. Uncertain acknowledgements stop without duplicate dispatch.
Fresh active resume verifies the original task and exact saved provider turn through
public resume/read; lost/mismatched sessions never trigger guessed replacement runs.
Explicit completed-turn follow-up stays in the same thread/attempt. Repairs use new
workspaces/clients/threads. Cancellation requires observed interrupted terminal status;
timeout preserves live identity and uncertain interrupt is never replayed.
Auth/quota/rate/overload/transport failures remain operational conditions through existing
Controller/Recovery seams, not task defects or fabricated REVISE results.

## Live provider results

Final smoke after the independent-review fixes uses four worker turns in disposable
external repositories only. Receipt: `.superpowers/sdd/2026-10-03-phase-12/live-final-receipt.json`.

| Check | Result | Evidence |
|---|---|---|
| LIVE AUTH/RUNTIME | PASS | Existing session, SDK/runtime 0.160.0 |
| LIVE READ | PASS | README BLUE, clean fixture, canonical COMPLETED |
| LIVE WRITE | PASS | Only sample.txt changed to after; actual Git patch observed |
| LIVE RESUME | PASS | Fresh adapter/client, same thread, different follow-up turn |
| LIVE CONTROLLER | PASS | Existing canonical/evidence chain; REVISE_REQUIRED, no semantic PASS |
| LIVE APPROVAL | SKIPPED_WITH_REASON | No safe bridge; capability false and escalations denied |

Read thread: `01a101c2-a07f-7881-8d98-ae8ec435d6fb`.
Read turn: `01a101c2-a303-7941-b2d3-bf0963ef5d10`.
Resume turn: `01a101c2-f464-7052-857a-2ec7c6036fcd`.
Write thread: `01a101c3-3e19-74a0-9ba8-b6c01ed3a6ae`.
Write turn: `01a101c3-40a8-74e1-91be-2cf23fa55e9c`.

Write evidence status is PARTIAL because no test log/raw artifact was supplied;
the observable patch is collected. Missing evidence was not invented. The Controller
fixture deliberately has no objective acceptance rule and cannot grant semantic PASS.
Live cancellation/session loss are covered offline, not claimed as live-tested.

Earlier narrow smoke receipts are preserved. Initial Controller helper verification
used nonexistent fixture accessors; corrected accessor/context.evidence assertions
passed. After sandbox correction the final complete smoke passed. Across development,
nine small worker turns were used, including necessary correction/revalidation runs;
no exhaustive live suite or real-repository worker execution occurred.

## Regression and packaging validation

Baseline: 1201 tests. Final full regression: **1322 passed in 8009.77s (2:13:29)**,
exit 0. Explicit component selection: **811 passed in 8020.69s (2:13:40)**, exit 0.
Both XML results have zero failures, errors and skips. All 164 frozen source/test/
schema/pyproject SHA-256 inputs remain unchanged after both runs.

| Component | Passed |
|---|---:|
| Codex adapter/normalizer/safety | 114 |
| Shared adapter contracts | 14 |
| Controller | 80 |
| Approval Router | 104 |
| Reviewer / Phase Gate | 90 |
| Recovery Manager | 72 |
| Version Manager | 80 |
| Evidence Collector | 107 |
| Prompt Engine | 79 |
| Existing Normalization | 71 |

Exact arguments, start/end timestamps and exit codes are retained in
full-verified.exit.json and components-verified.exit.json. verified-summary.json
checks XML totals and unchanged frozen inputs; no interrupted run is counted as PASS.

Final Codex/shared contract selection: 128 passed in 94.03s, including two safe
lifecycle metadata logging tests. Logging and re-review passed without changing core logging.
Eight additional red/green regressions reject contradictory declined/failed command
exits, alternate rename aliases, stale follow-up turns, duplicate SDK identities and
unowned active turns. Independent re-review passed 28 focused tests with no unresolved
important findings. Earlier full/component runs were stopped before completion for
logging and these guards; their receipts are preserved, not counted as successful verification.

Passed ancillary checks: CLI help, Ruff check, Ruff format (158 files), pip check,
four canonical schemas, two project/state schemas and git diff --check. Wheel build
and isolated installation passed; core-only wheel imports with no optional SDK passed.
Final wheel SHA-256: `4c09a44bd9d7c61b470c00d927179a5c6e157a1ce4cb52749f07592bb73a9a69`.
Receipts/logs/XML and the installed-SDK offline wire probe remain in the ignored
`.superpowers/sdd/2026-10-03-phase-12` evidence directory, not automatically staged.

## Accepted implementation limits and review handoff

Private SDK guards require pin-specific upgrade review. Single-writer/quiescent access
and trusted caller workspace binding remain required. Provider/journal publications
are not transactional; exact-once execution/interrupt is not promised. Runtime sandbox
and host policy are trusted; arbitrary prompt/forbidden-action prose is not a security
boundary. Stronger concurrency, adversarial filesystem race prevention, command policy,
DLP/origin authentication and retention are later hardening concerns. Python 3.11 and
non-Windows execution are unverified. No approval bridge, routing/fallback or independent
semantic LLM Reviewer is implemented.

All required regression receipts completed. CURRENT_STATE.md is AWAITING_REVIEW,
Attempt 01, Phase Gate PENDING; Phase 00–11 PASS and documentation alignment remain
preserved. Verified implementation attempt:
`5daa6f0db4de52e05945f0117735918142aabba0`.
Commit message: `AIPC Phase 12 attempt 01 - codex adapter`.
This subsequent documentation-only handoff records the implementation hash without
changing verified source/tests; the final handoff HEAD is in the Phase Completion Report.
Submit to external ChatGPT review and stop before Phase 13 or stable promotion.
