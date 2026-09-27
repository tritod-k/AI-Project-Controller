# Phase 01 Attempt 01 Evidence

## Review target

- Phase: Phase 01 — Canonical Domain Models.
- Implementation commit: `8a44d7ea2cc0a85488850eb9ed37127ad433f861`.
- Commit message: `AIPC Phase 01 attempt 01 - canonical domain models`.
- Branch: `codex/phase-01-attempt-01`.
- Last stable checkpoint: `aipc-phase-00-pass` / master at
  `ae0e476d1586bdcfea6b0a46005d675dc618f12f`.
- Status: AWAITING_REVIEW; attempt 01; external Phase Gate: PENDING.

This evidence/state handoff follows the implementation commit and changes only
documentation. Review the implementation hash above for all source, fixtures,
tests and schemas. The final report supplies the documentation handoff HEAD hash
separately. No self-referential hash placeholder is used.

## Implemented scope

Four Pydantic 2 canonical roots, five string enums and five nested record types
provide provider-independent validation and JSON round trips. Every root requires
schema version 1.0. Unknown fields/versions/enums, malformed structures, invalid
identifiers, non-finite values and contradictory known counts/timestamps fail.
Nullable evidence remains explicit. JSON metadata preserves optional extensions.

Python models generate four Draft 2020-12 schemas. Generation is deterministic;
check mode detects missing/drifted artifacts without writing, and generation
preserves unrelated files. No provider SDK/adapter, routing, normalization,
reviewer, state machine, Version Manager or Recovery Manager was implemented.
AgentCapabilities is deferred to Phase 03.

Runtime dependency rationale: Pydantic provides validation/serialization/schema
generation from one contract definition. Development-only jsonschema independently
checks schemas; rfc3339-validator enables real date-time format checks. No global
dependency installation occurred. There was no earlier canonical contract to migrate.

## Environment and results

Windows; Python 3.13.0; pytest 9.1.1; Ruff 0.16.9; Pydantic 2.13.5;
jsonschema 4.26.0; rfc3339-validator 0.1.4. Required commands used the project-local
`.venv/Scripts` directory on PATH and exited 0.

| Command/check | Observed result |
| --- | --- |
| `python -m aipc --help` | Existing help-only CLI succeeds |
| `pytest` | 216 passed, 0 failed, 0 skipped, 4.42 seconds |
| `python -m ruff check .` | All checks passed |
| `python -m ruff format --check .` | 16 files already formatted |
| `python -m pip check` | No broken requirements found |
| `python -m aipc.domain.schemas --check` | Checked 4 canonical schemas |
| `git diff --check` / staged equivalent | No whitespace errors |
| Draft 2020-12 schema checks | Four meta-schemas valid; valid fixtures accepted and invalid structures/enums rejected |
| Secret-pattern scan | 45 tracked files, 0 private-key/token-pattern matches; real .env not tracked |
| Blueprint/bootstrap diff against stable commit | AGENTS.md, SKILL.md, MASTER_PLAN.md, CLI and logging unchanged |

The suite contains 171 model cases, 33 schema/generator cases and all 12 original
bootstrap tests. Model coverage includes all required roots, enum values, required
fields/version, strict scalar/collection types, nested records, explicit nulls,
JSON metadata, malformed JSON, revalidation and timestamp/count constraints.

## Test-driven and review evidence

Initial model tests failed because `aipc.domain` did not exist before implementation.
Generator tests likewise failed for the missing schema module. Independent date-time
checks exposed the missing optional format validator; adding a development dependency
enabled the intended checks. Numeric-string/space-separated timestamps had failing
regressions before the shared RFC3339 constraint was added.

Supplemental read-only code review identified one actionable issue: aware Python
datetime offsets containing seconds/fractions lost precision in JSON serialization.
Three rejection tests failed before the fix. The shared validator now rejects
non-minute UTC offsets; those three tests plus two round-trip instant/offset
preservation cases pass. No other actionable finding was reported. This supplemental
review does not constitute external Phase Gate approval.

## Wheel evidence

Executed with project-local environments:

```powershell
.venv/Scripts/python.exe -m pip wheel . --no-deps --wheel-dir dist/phase01
.venv/Scripts/python.exe -m venv .venv/phase01-wheel
.venv/phase01-wheel/Scripts/python.exe -m pip install dist/phase01/ai_project_controller-0.0.1-py3-none-any.whl
.venv/phase01-wheel/Scripts/python.exe -m aipc --help
.venv/phase01-wheel/Scripts/aipc.exe --help
.venv/phase01-wheel/Scripts/python.exe -m pip check
```

All exited 0. A smoke script verified that `aipc.__file__` points into the isolated
wheel environment, validated all four literal fixtures, serialized/revalidated
each root, generated four schemas and confirmed jsonschema was absent. Thus the
installed contracts/generator do not require development-only validators.
Wheel SHA256: `9a3cdb4b121cdd6f290d030dd81cc1ad4845081e83d85d2998377b2c7d772ce5`.
Wheel/build environments are ignored local verification artifacts.

## Acceptance evidence

- [PASS] Valid samples validate: all four literal fixture model/schema cases pass.
- [PASS] Invalid status/verdict fails: unsupported/case-mismatched model cases
  and independent schema negative cases pass; risk levels are also covered.
- [PASS] Missing required fields fail: identifier/structure/version cases pass.
- [PASS] Models serialize to JSON: all four exact fixture and JSON round trips pass.
- [PASS] JSON Schemas generated/maintained: four committed artifacts match fresh
  generation; meta-validation, deterministic generation and read-only drift tests pass.
- [PASS] Explicit compatible schema version: missing/incompatible versions fail
  for every root; nested approvals reuse the same versioned ApprovalRequest contract.
- [PASS] Bootstrap preserved: all 12 original tests pass and source is unchanged.

These are worker acceptance findings, not a Phase Gate PASS decision.

## Limits and next action

Python 3.11 and non-Windows execution remain unverified. No type checker is
configured. JSON Schema cannot express count sums/time comparisons and has
different integer semantics; Python validation remains the final boundary.
Collections are shallowly frozen and must be revalidated after mutation.
These limitations are documented in CANONICAL_MODELS.md.

The later-phase numbering discrepancy between AGENTS.md and MASTER_PLAN.md remains
deferred until affected phases are authorized. Original blueprint files and the
Phase 00 stable checkpoint remain unchanged. No Phase 01 PASS tag or Phase 02 work.

Submit Phase 01 results to the external ChatGPT reviewer and wait for PASS / REVISE / BLOCKED decision.
