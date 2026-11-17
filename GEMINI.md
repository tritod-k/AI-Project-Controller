# AI Project Controller (AIPC) — Gemini CLI Persistent Instructions

@.agents/policies/OUTER_SUPERVISOR_RULES.md

## Critical Outer Supervisor Summary
- **Targeted Inline Python Policy (2-Tier Scope):** The Outer Argon Supervisor must avoid direct inline Python (`python -c`, `python.exe -c`, `py -c`) and invoke trusted modules via `python -m tools.sanitized_exec`. Do not globally prohibit `python -c` inside AIPC runtime components, tests, or worker verification probes, and do not add a blanket `python -c` block inside `tools/sanitized_exec.py`.
- **AIPC-First Delegation & <120-Line Cap:** Delegate all substantive implementation and unit test suites to qualified AIPC-managed subscription workers (`AntigravitySubscriptionWorkerAutomation`, preferring Gemini 4 Hydrogen when qualified, otherwise fallback `gemini-3.8-flash-medium`). Keep cumulative Outer Supervisor Python authoring strictly `< 120` lines per task.
- **No Fabricated Qualification:** Always call `qualify_antigravity_subscription_runtime()` / `qualify_codex_subscription_runtime()` with `subscription_only` enforcement; never construct fake `QUALIFIED` reports.
- **Strict Boundaries:** Never touch `H:/000ProjectCode` outside `H:/000ProjectCode/AI_Project_Controller`. Keep Stable (`H:/AIPC_Workspace/stable_c`), Candidate (`H:/000ProjectCode/AI_Project_Controller`), and Runtime (`H:/AIPC_Workspace/runtime_c`) strictly isolated.
- **Durable Queue Recovery:** Inspect and reconcile `PersistentTaskQueue` state idempotently on resume; never blindly overwrite queue state or self-approve/merge without independent Codex review and Human Commander (`K.TRITOD`) authorization.
