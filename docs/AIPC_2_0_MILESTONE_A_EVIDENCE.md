# AIPC 2.0 Milestone A Evidence — Subscription Runtime & Planner Foundation (`A01`–`A04`)

- **Baseline `master` Commit (Phase 24 Merge)**: `26f455fd40d7728d99dcf5494c1cad9353e45988`
- **Feature Branch**: `gemini/aipc-2.0-milestone-a`
- **Implementation Candidate Commit**: `c9008772d42f02b6696510fda98587a7cea30a97`
- **Date**: 2026-11-11
- **Operating Profile**: `subscription_only` (100% Subscription-Only inside AIPC; all child processes launched via `tools.sanitized_exec`)

---

## 1. Phase 24 Handoff & Gap Evaluation Receipt (`146763e`)

Before writing any Milestone A runtime code, the Phase 24 Post-Completion Handoff Protocol (`docs/roadmaps/AIPC_2_0_UNIVERSAL_SUBSCRIPTION_ORCHESTRATION_ROADMAP.md` Section 11) was executed and committed in `146763e`:
- Verified `master` at `26f455fd40d7728d99dcf5494c1cad9353e45988` with Phase 24 artifacts (`docs/PHASE_24_EVIDENCE.md`, `docs/PHASE_24_COMPLETION_REPORT.md`, `src/aipc/controller/task_queue.py`, `src/aipc/review/llm/subscription_transports.py`, `src/aipc/controller/repair_coordinator.py`, `src/aipc/controller/self_dev_runner.py`, `src/aipc/hardening/env_isolation.py`, `tools/sanitized_exec.py`).
- Re-evaluated Workstreams `A01`–`D03` against Phase 24 deliverables and activated Milestone A (`A01`–`A04`) in `docs/roadmaps/AIPC_2_0_UNIVERSAL_SUBSCRIPTION_ORCHESTRATION_ROADMAP.md` (`v0.2`), `MASTER_PLAN.md` (Section 38), and `CURRENT_STATE.md`.

---

## 2. Workstream Deliverables & Commits

| Workstream | Commit | Implementation Module | Test Suite | Status |
| :--- | :--- | :--- | :--- | :--- |
| **A01 — Subscription Auth & Runtime Qualification** | `8df8d59` | `src/aipc/adapters/subscription_qualification.py` | `tests/unit/test_a01_subscription_qualification.py` | **VERIFIED** |
| **A02 — Multi-Provider Operational Integration** | `a4acd2d` | `src/aipc/adapters/operational_integration.py` | `tests/unit/test_a02_operational_integration.py` | **VERIFIED** |
| **A03 — Role-Based Provider/Model Registry** | `0de2e4c` | `src/aipc/agents/role_registry.py` | `tests/unit/test_a03_role_registry.py` | **VERIFIED** |
| **A04 — Planner Orchestration Engine** | `c900877` | `src/aipc/controller/planner_engine.py`, `src/aipc/controller/__init__.py` | `tests/unit/test_a04_planner_engine.py` | **VERIFIED** |

---

## 3. Empirical Subscription Runtime Qualification & Subagent Receipts

All subprocesses were executed via `.\.venv\Scripts\python.exe -m tools.sanitized_exec ...`, stripping `GEMINI_API_KEY`, `GOOGLE_API_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, and all `VERTEX_*` / `GOOGLE_APPLICATION_CREDENTIALS` variables before child spawn.

1. **Antigravity CLI (`agy.exe` `1.5.5`) Quota & Model Tier Observation**:
   - Invoking `agy.exe --agent aipc-explorer` with default `model: flash` bucket returned a structured pre-start quota error (`status: "RESOURCE_EXHAUSTED"`, `error_code: 429`, `conversation_id: "4f688e1a-2f7b-4a71-9632-c9f197105fd8"`, `error_id: "ee0db9b1-29dc-45bd-89d9-7146f4d93182-1"`).
   - Explicitly targeting `--model gemini-3.8-flash-medium` on `agy.exe` succeeded immediately (`status: "SUCCESS"`, `conversation_id: "77ea1d89-2860-4d91-807f-d4878bcd5d2b"` and `conversation_id: "24987f1e-c1bb-480f-8050-023ca8e92f35"`, `duration_seconds: 4.7764468`, `input_tokens: 9938`, `output_tokens: 121`).
   - Also confirmed that `agy.exe` custom agent frontmatter parser requires `model: flash` in `.agents/agents/*.md` while `--model gemini-3.8-flash-medium` overrides the runtime model via CLI flags.
   - Unverified/unavailable models (`"argon"`, `"gemini-argon"`) and Desktop-only surfaces (`antigravity-desktop`, `codex-desktop`) are deterministically classified as `UNAVAILABLE` / `BLOCKED` by `src/aipc/adapters/subscription_qualification.py` and rejected by `src/aipc/agents/role_registry.py`.

2. **Live Subscription-Authenticated Independent Review Receipt (`c900877`)**:
   - Executed via `.\.venv\Scripts\python.exe -m tools.sanitized_exec C:\Users\Administrator\AppData\Local\agy\bin\agy.exe --agent aipc-test-auditor --model gemini-3.8-flash-medium --mode plan --sandbox --disable-slash-commands --output-format json --print-timeout 60s`
   - **Conversation ID**: `2276e36d-5e9f-45ff-ba55-c90acb48467e`
   - **Review ID**: `REV-20261112-AIPC20-MILA-c900877`
   - **Candidate SHA**: `c900877` (`c9008772d42f02b6696510fda98587a7cea30a97`)
   - **Verdict**: `PASS` (confidence `0.98`, `required_fixes: []`)
   - **Duration / Token Usage**: `11.6037717s`, `input_tokens: 10000`, `output_tokens: 1085`, `thinking_tokens: 698`, `total_tokens: 11085`
   - **Independence Classification Note**: Live subscription review on this Windows host was executed in an isolated read-only `--mode plan --sandbox` session via Antigravity CLI (`gemini-3.8-flash-medium`); cross-provider Codex subscription review (`gpt-5.3-codex`) is verified deterministically in offline unit/contract tests (`tests/unit/test_a02_operational_integration.py`, `tests/unit/test_a03_role_registry.py`, `tests/unit/test_a04_planner_engine.py`, `tests/review/test_subscription_reviewer.py`) and remains `DEFERRED` for live host execution until an active local `~/.codex/auth.json` subscription session is provisioned on the host.

---

## 4. Sanitized Test & Linter Verification Summary

All verification commands were executed in a sanitized child environment via `tools.sanitized_exec`:

- **Ruff Linter**:
  - Command: `.\.venv\Scripts\python.exe -m tools.sanitized_exec .\.venv\Scripts\python.exe -m ruff check src/ tests/ tools/`
  - Result: **`All checks passed!` (exit code `0`)**
- **Milestone A Focused Unit & Architecture Boundary Suite**:
  - Command: `.\.venv\Scripts\python.exe -m tools.sanitized_exec .\.venv\Scripts\python.exe -m pytest -q tests/unit/test_a01_subscription_qualification.py tests/unit/test_a02_operational_integration.py tests/unit/test_a03_role_registry.py tests/unit/test_a04_planner_engine.py tests/routing/test_structure.py`
  - Result: **30 passed (0 failures)**
- **Comprehensive Regression Suite (`tests/unit/`, `tests/routing/`, `tests/review/`, `tests/hardening/`, `tests/controller/`, `tests/llm/test_integrations.py`)**:
  - Command: `.\.venv\Scripts\python.exe -m tools.sanitized_exec .\.venv\Scripts\python.exe -m pytest -q tests/unit/ tests/routing/ tests/review/ tests/hardening/ tests/controller/test_task_queue.py tests/controller/test_repair_and_rereview.py tests/controller/test_self_dev_runner.py tests/controller/test_boundaries.py tests/llm/test_integrations.py`
  - Result: **895 passed in 1407.91s (0 failures)**
