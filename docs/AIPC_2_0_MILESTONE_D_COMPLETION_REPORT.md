# AIPC 2.0 Milestone D (`D01`–`D03`) — Completion Report

**Status:** `AWAITING_REVIEW` (`PhaseGate: PENDING` until exact-SHA CI on `frozenSHA_d_r14` + AST-Closed 2-Wave Codex Review Cycle #14 `PASS` + 7 preserved byte-identical `PASS 1.0` locks + Commander K.TRITOD merge approval)
**Architecture Baseline:** Ratified Workspace Architecture `v1.2 — ACCEPT_V1_2`
**Branch:** `gemini/aipc-2.0-milestone-d` (`tested_source_sha_d_r14`: `05011ccaabf1c3578353dc110e6654d86fd1c2d0`; prior `frozenSHA_d_r13`: `57befe85ac538176c79ded83eb7f1e3d35e465fa`; prior `frozenSHA_d_r12`: `a93de7d0a0fc4da11c47e96dfd1bacaabf910ea8`; prior `frozenSHA_d_r11`: `62ea3031f653a63e1b4356e9251bbbd28a2b4acc`; prior `frozenSHA_d_r10`: `179d38d1a30ff092e97651b51e31cc6a63b0febb`; prior `frozenSHA_d_r9`: `5c19b73733848b3d95a6e4c821fce24e661058b5`; prior `frozenSHA_d_r8`: `3517b898514ea57e83fdc7e594713b3ef5e4cce0`; prior `frozenSHA_d_r7`: `e6ad749f32213d1feb6e0d8661fdc48841f830ba`; prior `frozenSHA_d_r4`: `c1624e357a1bc3c8ecb89adfed59f16aed4386f8`; `1,644 passed, 1 skipped, 0 failed` Windows; `93 passed, 0 skipped, 0 failed` Linux Docker)
**Owner / Human Commander:** K.TRITOD
**Operating Posture:** STRICT `subscription_only`, `EXPERIMENTAL / SUPERVISED`, `Level A + Level B` isolation with explicit Level C host disclosure; unattended production remains **NO-GO** pending separate release gate.

---

## 1. Delivered Scope (`D01`–`D03` & `FINAL-01`–`FINAL-05`)

### `bootstrap-d` — External Milestone D Authorization Gate
- Unlocked `milestone-d` (`d01`–`d03`) strictly behind external Controller-owned `MilestoneAuthorizationTrustContext` & persisted `MilestoneAuthorizationEvidence` (`src/aipc/controller/milestone_authorization.py`, `planner_engine.py`, `task_queue.py`, `trusted_acceptance_harness.py`).
- Preserved 100% of pre-D (`A04`, `C.5`, `C.6`, `C.7`) regression contracts; `milestone-e`+ remains strictly blocked.

### `D01` — Intake, CLI/MCP Control & Consistency
- **`FINAL-01` (`ARCH-AUTH-01..03`):** Added external-key HMAC-SHA256 signing & constant-time verification (`load_operator_hmac_key`, `sign_approval_record_hmac`, `verify_approval_record_hmac`) in `milestone_authorization.py`, rejecting keys inside Workspace/Candidate/Runtime roots, symlinks, missing/tampered signatures, and expired grants; added `AIPC_OPERATOR_HMAC_*` / `AIPC_HMAC_*` stripping in `env_isolation.py` (enforced by `tools/sanitized_exec.py`).
- **`FINAL-02` (`ARCH-REC-01..02`):** Added shared `(operation_id, attempt_id)` reservation/finalization/release correlation across `repair_budget.py` (`RESERVED` -> `CONSUMED | RELEASED`) and `task_queue.py`, plus `reconcile_queue_and_budget_consistency()` releasing orphan reservations without budget leak and escalating ambiguous mid-spawn crashes fail-closed to `RECONCILIATION_REQUIRED`.
- **v1.2 Onboarding, Intake, Worktrees & Status:** Extended `src/aipc/project/scaffolding.py` with v1.2 runtime subdirectories (`plans/{proposals,approved}`, `trusted`, `audit/approvals`, `logs`), immutable `snapshot_approved_plan()`, short `wt-<hash>` (`<= 12` chars) worktree naming/cleanup, and deterministic non-authoritative `render_current_status_md()`.
- **`FINAL-04` (`ARCH-GIT-01..02`):** Added `src/aipc/project/workspace_git_checkpoint.py` (`verify_workspace_git_quiescence` + CLI) acquiring exclusive `controller.lock`, validating `.gitattributes` (`eol=lf`), `.gitignore`, zero `checkpoint_manifest.json`, zero active/unreconciled tasks, valid LF `.sha256` sidecars, zero secrets, and zero tracked/staged forbidden paths.

### `D02` — Strictly Read-Only Multi-Project Dashboard & Monitor
- Created `src/aipc/dashboard/multi_project.py` and extended `src/aipc/dashboard/server.py` (`GET /multi-project` HTML, `GET /api/multi-project` JSON, HTTP `405` on all mutations).
- Implemented `FINAL-03` 5-state raw evidence indicators (`NOT_CHECKED` default fast path, `PRESENT_VERIFIED`, `PRUNED`, `MISSING`, `HASH_MISMATCH`), genuinely observed per-model eligibility (`UNOBSERVED` default, never hardcoded `QUALIFIED`), self-contained XSS-escaped HTML (zero external CDNs), and proven zero mutation of canonical JSON stores (`ARCH-EVD-01`).

### `D03` — Two-Project E2E, Fault Injection & Closeout Gate
- Created `src/aipc/project/isolation_and_closeout.py` implementing:
  - `FINAL-05` (`ARCH-ISO-01..04`): `build_minimal_docker_verify_argv` enforcing `--network none --read-only` + minimal `:ro` mounts (rejecting `state/`, `approvals/`, ledgers, keys, and sibling projects) alongside explicit Level C Windows host OS / shared-profile disclosure for `agy.exe` and `codex.cmd`.
  - Deterministic release-closeout `.zip` + LF `.sha256` export & tamper-checked/zip-slip-safe restore (`ARCH-EVD-02..03`).
  - Read-only `verify_sealed_archive_untouched` verifier (`ARCH-EVD-04`).
- Added `tests/e2e/test_d03_multiproject_e2e_and_closeout.py` qualifying simultaneous 2-project E2E (`project-alpha`, `project-beta`), Queue/Ledger crash fault injection, `FINAL-05` isolation, deterministic closeout zip/restore, sealed archive untouched verification, and clean Windows Git checkout restore (`ARCH-GIT-03..04`).

---

## 2. Explicit Boundaries Maintained
- Zero duplicate task stores, external databases, third journals, PKI, background daemons, or `checkpoint_manifest.json` files.
- `H:/AIPC_Workspace/runtime_c/` verified 100% untouched (`107,441` files).
- `H:/AIPC_Workspace/stable_c` remains clean and pinned at `6671f87c8068f0ff8c937ce1ecab1d2b91ddcbf6`.
- Unattended production remains **NO-GO**; PR merge requires explicit Human Commander K.TRITOD approval.
