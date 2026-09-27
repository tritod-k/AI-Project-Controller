# Phase 06 attempt 01 — Evidence Collector evidence

## Approved baseline and scope

- Stable master/tag: aipc-phase-05-pass,
  `f5d61ea5e546bc0b6cd0c23aa726fb61ad13b6e6` (verified exact match, clean tree).
- Attempt branch: `codex/phase-06-attempt-01`, created from that stable tag.
- Phase 06 only: no reviewer, phase gate, controller, provider, execution or later phase.
- Gate remains PENDING; worker requests external review, not PASS promotion.
- Implementation commit: `9912eb5d14826e9cad33368711caa6a79e49e496`.
  `AIPC Phase 06 attempt 01 - evidence collector`.
  A subsequent documentation-only handoff records this exact hash; its actual HEAD
  is supplied in the final report. No symbolic command is substituted for a hash.

## Verification

Windows / Python 3.13.0, local .venv. Commands return exit 0 unless stated otherwise.

| Check | Observed result |
| --- | --- |
| `python -m aipc --help` | existing help-only CLI succeeds |
| `python -m pytest -q` | 775 passed, 0 failed/skipped, 23.63 seconds |
| Evidence unit/integration/contract subset | 107 passed, 11.60 seconds |
| Adapter contract subset | 7 passed, 0.33 seconds |
| Normalization subset | 71 passed, 0.69 seconds |
| Prompt Engine subset | 79 passed, 2.94 seconds |
| `python -m ruff check .` | all checks passed |
| `python -m ruff format --check .` | 64 files formatted |
| `python -m pip check` | no broken requirements |
| `python -m aipc.domain.schemas --check` | four canonical schemas unchanged |
| `python -m aipc.project.schemas --check` | two project/state schemas unchanged |
| Protected diff against Phase 05 | domain models/project/adapters/agents/normalization/prompts/schemas/dependencies/instructions unchanged |
| `git diff --check` | no whitespace errors |
| Secret-pattern audit | 125 tracked/staged files, no matches, no tracked .env |

The full run is 668 existing tests + 107 new tests. Test repositories and logs are
isolated temporary fixtures. No real provider/API calls or global installs occur.
No configured type checker. Python 3.11/non-Windows and wheel installation unverified.

## Architecture and acceptance evidence

- Canonical typed TaskRequest/AgentResult revalidated before writing. Mutable command
  mutation, wrong object types, wrong task IDs and wrong workspace fail before output.
- Snapshot and manifest round trips preserve task/result data without mutating inputs.
- SHA-256/byte counts match generated and binary copied artifacts; all bundle paths
  relative. Duplicate references copy once and retain separate request records.
- Observable Git clean/modified/staged/added/deleted/renamed changes, HEAD/branch,
  detached HEAD and supplied baseline committed changes covered with real Git fixtures.
  Index/source bytes and status before/after are identical. No destructive commands.
- Worker command strings remain declarations, nullable/nonzero codes preserved;
  hostile-looking fixture command never creates its marker file.
- Structured counts stay 42 while contradictory worker prose claims 100 and actual
  log parses 2 passed/1 failed. Both summaries retained, agreement false, warning.
- Missing logs/raw/artifacts, unsupported descriptors/parsers, invalid UTF-8, unavailable
  Git and injected command/file failures yield explicit unavailable/failed items.
- Traversal with both separators, absolute/drive/UNC/ADS/reserved paths, symlinks and
  actual Windows junction escapes rejected. Missing/wrong workspace and overlapping
  or linked destinations rejected. Case-insensitive .env/.git exclusions regression-tested.
- Exclusive attempt reservation preserves existing evidence bytes. Atomic manifest
  inspection verifies indexed files exist with expected hashes before publication.
  Injected manifest replace failure leaves no completed manifest; no silent retry/overwrite.
- COMPLETE versus PARTIAL indicates availability only. Actual failing tests/nonzero
  commands do not turn collection into a phase verdict. No future review.json generated.

## Test-first and independent review record

- Initial 81 tests failed because production evidence modules were absent; then passed.
- Missing mandatory manifest items regression failed, then passed after index validation.
- Harmless Git clean-filter fixture created a marker before suppression; after disabling
  configured clean/smudge/process filters it no longer executes. External diff, textconv,
  fsmonitor, optional locks, lazy fetching and pagers are disabled as appropriate.
- Independent read-only review confirmed filter risk and Windows casing bypass.
  Three uppercase restricted-source tests failed before casefold checks, then passed.
- Reviewer found Git submodule settings could hide dirtiness and forced child inspection
  could run child-only filters. Gitlinks in index/baseline now make Git evidence explicitly
  unavailable before child inspection. Both regressions failed before correction, then passed.
- Review reported no other actionable findings. This internal review does not substitute
  for external reviewer approval. Submodule support remains a documented limitation.

## Limits and rulings

- Explicit run/checkpoint sidecar, no task-name inference. Evidence root outside workspace;
  absolute source refs rejected even when inside. Callers must supply portable relative refs.
- Quiescent trusted filesystem assumption; no adversarial concurrent replacement sandbox,
  hard-link provenance guarantee, multifile atomic snapshot, retention or signing system.
- Default 16 MiB per source/Git output; Git has 15-second per-call timeout. Git byte cap
  is postcapture, so not a streaming memory limit or total-run budget.
- Unsupported pytest errors/xfail/xpass remain unavailable, not coerced to passing counts.
- Full canonical snapshots and authorized diffs/logs may contain sensitive caller data;
  exclusion of conventional secret files is not comprehensive content redaction. No logging.
- Submodules in current index or baseline are unavailable until a separately safe observer.
- Atomic JSON reuse follows Phase 02 single-writer/power-loss limitations. Failed attempt
  directories are preserved for inspection; recovery/locking remains a later responsibility.
- Plan/design were retained in the coherent attempt commit as explicitly requested by user.

## Exact phase-numbering discrepancy (unchanged)

| Function | AGENTS.md section 29 | MASTER_PLAN.md roadmap |
| --- | --- | --- |
| Version & Checkpoint Manager | not in first sequence | 07 |
| Recovery Manager | not in first sequence | 08 |
| Reviewer + Phase Gate | 07 | 09 |
| Controller Loop | 08 | 10 |
| Approval Router | 09 | 11 |
| Codex Adapter | 10 | 12 |
| Independent LLM Reviewer | 11 | 13 |

SKILL.md has **no numbered phase roadmap**; it describes workflow/layers and refers
to MASTER_PLAN/current phase. CURRENT_STATE has deferred alignment until after Phase 06.
No silent renumbering. Dedicated documentation-alignment authorization required before
any affected Phase 07 work.

## Next action

Submit Phase 06 results to the external ChatGPT reviewer and wait for PASS / REVISE /
BLOCKED decision. No Phase 06 PASS tag, no integration into master, no Phase 07 work.
