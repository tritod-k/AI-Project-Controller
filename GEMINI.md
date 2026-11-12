# AI Project Controller (AIPC) — Gemini CLI Persistent Instructions

@.agents/policies/OUTER_SUPERVISOR_RULES.md

## Critical Outer Supervisor Summary
- **No Inline Python (`-c`):** Never run `python -c`, `python.exe -c`, or `py -c`. Always execute trusted modules via `python -m tools.sanitized_exec`.
- **AIPC-First Delegation & <120-Line Cap:** Delegate all substantive implementation and unit test suites to qualified AIPC-managed Flash subscription workers (`AntigravitySubscriptionWorkerAutomation`). Keep cumulative Outer Supervisor Python authoring strictly `< 120` lines per task.
- **No Fabricated Qualification:** Always call `qualify_antigravity_subscription_runtime()` / `qualify_codex_subscription_runtime()` with `subscription_only` enforcement; never construct fake `QUALIFIED` reports.
- **Strict Boundaries:** Never touch `H:/000ProjectCode` outside `H:/000ProjectCode/AI_Project_Controller`. Keep Stable (`H:/AIPC_Workspace/stable_c`), Candidate (`H:/000ProjectCode/AI_Project_Controller`), and Runtime (`H:/AIPC_Workspace/runtime_c`) strictly isolated.
- **Durable Queue Recovery:** Inspect and reconcile `PersistentTaskQueue` state idempotently on resume; never blindly overwrite queue state or self-approve/merge without independent Codex review and Human Commander (`K.TRITOD`) authorization.
