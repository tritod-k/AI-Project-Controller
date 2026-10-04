# Phase 15 evidence — Gemini Adapter

Stable starting point: aipc-phase-14-pass / a895b03fae2b322fa550469008fee7ada07f5ebb.
Attempt branch: codex/phase-15-attempt-01. No stable promotion, merge or Phase 16 work.

Implementation and completion report are the containing attempt commit; the actual
full commit SHA is supplied in the final report and retained handoff.json after
publication. A containing commit cannot embed its own hash.

Development RED receipts: absent boundary module, absent lifecycle adapter and
absent ACP peer; later malformed RPC-error waiter timed out, and wire usage/model
facts were absent. Targeted implementations/fixes made these cases GREEN. An initial
test incorrectly treated a new project as the same run; corrected to match the
contract's project-scoped identity. NormalizationContext fixtures were corrected
to supply required nullable timestamps/reference. No production contract changed.

Targeted run: 98 passed (before 11 additional observation/framing cases), JUnit:
`.superpowers/sdd/2026-10-04-phase-15/targeted.xml`. Observation cases: 11 passed.
One fresh read-only whole-branch reviewer found four Important issues: asynchronous
load history contamination, response-future dispatch race, valid idle diagnostics
rejected, and incomplete protocol shape checks. Eight RED regressions failed before
fixes; 48 related wire/safety/observation/review cases then passed. Native ACP resume
is withheld and loaded-session prompt dispatch is refused until replay is unambiguous.
No Critical or Minor issue was reported. Final frozen regression receipts follow.
Development targeted receipts are not final regression receipts.

## Runtime evidence

Python 3.13.0; Node v24.18.0; npm 11.18.0. Actual Gemini CLI absent; no known global
package, auth environment or cached OAuth file presence. Actual model unavailable.
Pinned official source inspected: Gemini CLI v0.61.0 / ACP SDK dependency0.16.1;
this is static compatibility research, not the tested installed runtime version.

LIVE AUTH/RUNTIME: LIVE_RUNTIME_BLOCKED. LIVE READ, DENY, WRITE, RESUME, CANCEL and
CONTROLLER: UNVERIFIED / NOT_RUN. No paid calls, credentials read, interactive auth,
runtime installation or real provider mutation. Smoke is presence-only and blocked.

Production all capabilities false, including approval_events/filesystem/shell/git.
OFFLINE ACP streams demonstrate protocol start/load requests/prompt/deny/proxy denial;
native safe resume is unadvertised, and cancel is an exact notification with separate
deterministic fixture terminal confirmation.
they do not prove native tools request permission or sandbox enforcement. Official
launch is therefore unconditionally gated pending separately reviewed compatibility.
Requested conservative runtime_plan is never represented as effective policy proof.

Current Controller integration uses disposable VersionManager fixture repositories
and existing Evidence/Reviewer/Gate interfaces. Missing tests yield REVISE_REQUIRED,
not fabricated PASS. Core/providers/canonical/schema bytes remain unchanged except
the shared contract factory's additional Gemini selection.

## Accepted limitations for review

Real CLI execution/permissions/sandbox/resume/cancel remain unverified; production
launch qualification is not implemented on this unsupported host. No AIPC permission
bridge is advertised. A trusted stream connector is injection authority and cannot
be supplied via production config. History after load has protocol/session limits;
there is no provider turn ID or reliable native replay barrier. Native load cannot
dispatch a follow-up prompt. Single writer, nontransactional provider/journal writes,
no exactly-once or cryptographic origin/authority guarantees; known-field redaction
is not full DLP. Caller controls raw retention. No source/Git recovery or fallback.

## Final immutable implementation gates

- Full regression: 1661 passed; 5280.89s; exit 0.
- Explicit relevant component selection: 1150 passed;
  5135.81s; exit 0.
- Gemini package: 90 cases pass in both suites;
  shared AgentAdapter contracts include seven Gemini cases.
- Both JUnit receipts have zero failures, errors and skips. Ordinary tests are OFFLINE.
- 240 source/test/schema/config/tool/manifest inputs unchanged before,
  between and after the final suites. Runtime/dependency versions and runner hash retained.
- Ruff check/format, CLI help, pip check, all six schema checks, diff check,
  wheel build/target installation and base imports with optional-provider imports
  blocked: all exit 0. Base smoke preserves typed runtime unavailability.
- Previous tags and master remain unchanged; no Phase 15 stable promotion.

Exact receipts: `.superpowers/sdd/2026-10-04-phase-15/` contains full/components
logs, stderr, JUnit XML, command/exit/elapsed JSON, frozen-inputs.json, runtime.json,
input-verification.json, fast-checks.json, final-summary.json, technical-review.md
and review RED/GREEN receipts. They are local ignored evidence, not vendored runtime
or committed historical reports. Only docs/state are finalized after source freeze;
do not rerun giant suites solely to write the handoff.
