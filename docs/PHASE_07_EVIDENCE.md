# Phase 07 Review Evidence

Phase: Version & Checkpoint Manager. Scope is Phase 07 only; external gate remains pending.
Base master: `59a5121458cd1f1dcc4f72425fd925a53531756a`.
Last stable implementation tag: `aipc-phase-06-pass`,
`8a553c3aba55222a1e0dfcfd47fc0ad0c508f074` (unchanged).
Attempt branch: `codex/phase-07-attempt-01`.
Implementation commit: `9cd4e1e0f69d042f19131bab2983388d5ec4495b`.
Message: `AIPC Phase 07 attempt 01 - version and checkpoint manager`.
This later handoff changes only evidence/state documentation; implementation remains identical.

## Architecture and acceptance evidence

| Requirement | Evidence |
|---|---|
| PRE_PHASE before worker edits | clean HEAD capture; fixture main file stays unchanged |
| ATTEMPT distinct from STABLE | lifecycle test lists no stable checkpoint until explicit promotion |
| Explicit external PASS | absent, invalid, mutated, wrong-attempt/commit authorization rejected |
| Stable immutable on revision | compare-and-create refs; differing checkpoint identity cannot replace an existing record |
| Safe restore | new checkpoint worktree contains known-good bytes; original workspace preserved |
| Unknown edits preserved | tracked, staged, untracked, ignored, conflicts and hidden index flags refuse mutation |
| Persistence across restart | fresh-manager and separate Python-process inspection returns exact stored commit |
| Git authoritative | malformed/missing/moved refs and metadata, wrong project/parent and orphans detected |
| Worktree isolation | correct branch, disjoint authorized path, independent edits, registration and common dir checked |
| Safe clean removal | dirty/hidden removal refused; clean removal retains committed branch |
| Path protection | traversal, absolute drive/UNC-style paths, reserved/ADS syntax, real Windows link/junction escape refused |
| Non-destructive Git | fixed vocabulary, AST safeguards and observed fixture commands exclude reset/clean/force/active checkout replacement |
| Existing contracts retained | protected Phase 00–06 source/config/schema diff is empty; all six schema checks pass |

Tests live in `tests/integration/test_version_manager.py`,
`tests/unit/test_version_models.py` and `tests/contract/test_version_contracts.py`.
Production VersionManager/GitBackend mutations were not exercised against this AIPC checkout.

## Independent safety review and corrections

The internal reviewer reproduced hidden-edit deletion with assume-unchanged index flags
and read-only index refresh. The implementation now refuses assume-unchanged/skip-worktree
mutation and disables optional locks. Late-configured Git clean filter execution was
also reproduced; filter overrides now refresh before inspection. Six regression cases
failed before fixes and then passed. A dangling symbolic ref collision was reproduced
separately, then fixed with unresolved-ref preservation and no-dereference ref creation.
This internal review does not constitute external PASS authorization.

## Executed validation

Project-local Python 3.13.0 / Windows. Commands use `.venv/Scripts/python.exe`.

- `-m aipc --help`: exit 0, CLI remains help-only.
- `-m ruff check .`: passed, exit 0.
- `-m ruff format --check .`: 73 files already formatted, exit 0.
- `-m pip check`: no broken requirements, exit 0.
- `-m aipc.domain.schemas --check`: 4 canonical schemas checked, exit 0.
- `-m aipc.project.schemas --check`: 2 project/state schemas checked, exit 0.
- `-m pytest tests/contract/test_agent_adapters.py -q`: 7 passed.
- `-m pytest tests/unit/test_normalization.py tests/contract/test_normalizers.py tests/integration/test_mock_normalization.py -q`: 71 passed.
- `-m pytest tests/unit/test_prompt_engine.py tests/contract/test_prompts.py -q`: 79 passed.
- `-m pytest tests/unit/test_evidence.py tests/contract/test_evidence_contracts.py tests/integration/test_evidence_collection.py -q`: 107 passed.
- `-m pytest tests/unit/test_version_models.py tests/contract/test_version_contracts.py tests/integration/test_version_manager.py -q`: 79 passed before the final additional ref regression.
- Dedicated dangling symbolic-ref regression: 1 passed on final code.
- Full `-m pytest`: 855 passed in 510.22 seconds, exit 0 (775 baseline + 80 new).
- Initial 39 absent-module errors; subsequent 47 integration tests passed before final safety additions.
- `git diff --check` and staged whitespace checks passed.

## Preserved boundaries and limitations

No canonical model, existing schema, adapter, normalizer, project state, prompt or evidence
implementation changes. No dependency changes. Historical Phase 00–06 evidence and the
aligned MASTER_PLAN/AGENTS roadmap are preserved. No real manager checkpoint refs or
Phase 07 PASS tag exists. Primary master remains at the aligned base.

Caller-created committed work is required; all uncommitted/ignored edits are unknown.
Approval records express explicit caller context, not cryptographic origin authentication.
Metadata uses existing atomic JSON storage and a per-project mutation lock; Git/JSON/worktree
publication is not a cross-resource transaction. Partial operations and stale locks remain
for explicit human inspection. No hostile filesystem race protection, arbitrary external
Git writer coordination, migration, submodules or sparse/hidden-index workflow support.
Git command outputs are captured in memory, not streaming-limited or execution evidence.
Python 3.11/non-Windows behavior is not verified; no type checker is configured.

Phase 08 owns failure classification, heartbeat, automatic resume/rollback, quota handling,
provider fallback and recovery orchestration. Phase 07 supplies caller-driven primitives only.

## Recommended next action

Submit Phase 07 results to the external ChatGPT reviewer and wait for PASS / REVISE / BLOCKED decision.

Additional final lifecycle check: stable repeat is idempotent; authorized attempt 02 cannot replace attempt 01 stable ref. Passed separately after strengthening assertions, with production code unchanged.
