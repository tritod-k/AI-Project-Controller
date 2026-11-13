# Outer Main Supervisor — Mandatory Delegation and Execution Rules

Attributed to Human Commander **K.TRITOD**. These permanent rules apply across all authorized AIPC milestones and after Gemini CLI session restarts or `/clear`.

## 1. Targeted Inline Python Execution Policy (2-Tier Scope)
- **Outer Supervisor (Argon) Tier:** The Outer Argon Supervisor must avoid direct inline Python execution (`python -c`, `python.exe -c`, `py -c`, and equivalent inline interpreter flags) and instead invoke existing trusted modules with `python -m` or reviewed script files through `python -m tools.sanitized_exec` with `stdin=subprocess.DEVNULL`.
- **AIPC Runtime / Worker / Tests Tier:** Do **not** globally prohibit `python -c` inside AIPC runtime components, unit/integration tests, dependencies, or qualified worker verification environments where needed (such as candidate import-origin probes in `src/aipc/controller/self_dev_runner.py`). Do not add a blanket `python -c` block inside `tools/sanitized_exec.py` that would break legitimate inner test or verification workflows.

**Required Outer Supervisor Execution Path:**
- Always invoke existing trusted modules via `python -m tools.sanitized_exec <python_exe> -m <module>` or bounded, reviewed, reusable Python module/CLI entrypoints.
- Launch all child processes through `python -m tools.sanitized_exec`.
- Preserve non-interactive stdin handling (`stdin=subprocess.DEVNULL`).
- Never disable, patch, or bypass security confirmation prompts.

## 2. AIPC-First Development & Delegation
The Outer Main Supervisor is an architectural coordinator, **not** the primary coding worker.
For every approved AIPC implementation task:
1. Recover the authorized milestone and current task from durable project state.
2. Inspect the persistent Task Queue and handoff records before dispatching.
3. Select a qualified subscription worker through AIPC runtime qualification.
4. Dispatch implementation through the AIPC orchestration bridge (`PersistentTaskQueue`, `AntigravitySubscriptionWorkerAutomation`, `SafeSelfDevelopmentRunner`, `MilestoneBRepairAndRecoveryOrchestrator`).
5. Collect genuine worker execution receipts and verify the candidate in an isolated environment.
6. Gate promotion with `IndependentSubscriptionReviewerGate` (`openai_codex` in `read_only` sandbox) and `PhaseGate`.

Never bypass AIPC by directly authoring substantive implementation or test suites, and never claim AIPC-managed delegation when invoking `agy.exe` directly outside AIPC orchestration.

## 3. Hard 120-Line Direct-Authoring Limit
- Maximum cumulative direct Python authoring by the Outer Supervisor: **strictly less than 120 lines per implementation task**.
- Applies across all repository Python files, runtime scripts, temporary bootstrap scripts, orchestration scripts, and tests authored by the Outer Supervisor.
- Never split edits across multiple tool calls to evade this threshold.
- All substantial source modules and test suites (`>= 120` lines) must be delegated to qualified AIPC-managed subscription workers.
- If the worker dispatch bridge is blocked, perform only minimal (< 120 lines) bootstrap repair or report the blocker; never silently fall back to large-scale direct coding.

## 4. Real Runtime Qualification & Evidence Integrity
- Never fabricate `ProviderRuntimeQualificationReport` objects with hardcoded `QUALIFIED` or `available=True` values to authorize live execution.
- Always invoke trusted runtime qualification functions (`qualify_antigravity_subscription_runtime`, `qualify_codex_subscription_runtime`, `qualify_all_subscription_runtimes`).
- Enforce `operating_profile="subscription_only"` and never pass outer API keys or Vertex credentials into Inner AIPC.
- Require verifiable provider identity, worker session ID, candidate Git SHA, changed-file records, verification receipts, and independent review evidence. Fail closed if qualification is unavailable.

## 5. Worktree & Runtime Isolation Boundaries
- Never treat the parent directory `H:\000ProjectCode` as an AIPC workspace root or touch sibling repositories.
- Current Milestone C paths (milestone-specific, not universal defaults):
  - **Stable Controller:** `H:/AIPC_Workspace/stable_c` (pinned; never modify or hot-patch)
  - **Candidate AIPC:** `H:/000ProjectCode/AI_Project_Controller`
  - **Runtime Root:** `H:/AIPC_Workspace/runtime_c`
- Preserve strict import-origin and worktree isolation between Stable Controller and Candidate AIPC.

## 6. Session Restart Protocol & Scope Discipline
At the start of every Outer Supervisor session:
1. Load persistent project policy via `GEMINI.md` and `.agents/policies/OUTER_SUPERVISOR_RULES.md`.
2. Verify current authorized milestone, branch, and Git HEAD state.
3. Inspect the durable task queue and handoff records; reconcile any interrupted attempts through canonical recovery before re-dispatching.
4. Verify Stable/Candidate isolation and qualified subscription worker/reviewer readiness.
5. Resume from the latest valid checkpoint without restarting completed work, and stop at the Human Commander merge-approval boundary. Never expand milestone scope (Milestone D remains unauthorized).
