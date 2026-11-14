AIPC 2.0 — MILESTONE C TARGETED REPAIR (1934593) & FINAL PR HANDOFF

HUMAN COMMANDER AUTHORIZATION

Human Commander K.TRITOD authorizes resuming AIPC 2.0 Milestone C on branch gemini/aipc-2.0-milestone-c directly from candidate commit 1934593a8d1bb80de5c9cbdafe72fddaf0d8303b (1934593) to resolve the remaining Codex Independent Review findings, achieve a verified live Codex PASS, push the branch, verify GitHub Actions CI, and open the Milestone C Pull Request.

Respond in concise English.

CRITICAL: Do NOT restart completed C01–C03 workstreams or perform broad repository re-audits. Do NOT stage or commit this local operator file (docs/RESUME_MILESTONE_C_HYDROGEN.md) into Git. Do NOT auto-merge, deploy, modify the pinned Stable Controller, or start Milestone D.

1. VERIFIED STARTING CHECKPOINT (1934593)

The following Milestone C deliverables are already implemented, tested (1154 passed), formatted (LF normalized), and committed on gemini/aipc-2.0-milestone-c (branched from master baseline 849c86684867487f6f0ad8eb54c603cba240dd34):

Permanent Outer Supervisor governance (GEMINI.md, AGENTS.md, .agents/policies/OUTER_SUPERVISOR_RULES.md) & two-tier inline Python policy.

Qualified agy 1.5.8 + gemini-4-hydrogen-medium primary subscription worker integration & CLI worker dispatch bridge (src/aipc/adapters/subscription_worker.py).

C01: src/aipc/project/universal_profiles.py + unit tests.

C02: src/aipc/controller/milestone_c_pipeline.py + unit tests.

C03: src/aipc/review/trusted_acceptance_harness.py + unit tests.

Live Codex Independent Review on 1934593: Already executed and saved in H:\AIPC_Workspace\runtime_c (*review_1934593.json). Findings R2, R3, and R8 are RESOLVED; findings R1, R4, R5, R6, R7, and R9 plus stale commit metadata in CURRENT_STATE.md remain open.

Known workspace paths:

Stable Controller (pinned, read-only): H:\AIPC_Workspace\stable_c

Candidate Worktree: H:\000ProjectCode\AI_Project_Controller

Runtime & Saved Review Artifacts: H:\AIPC_Workspace\runtime_c

Persistent Queue: H:\AIPC_Workspace\runtime_c\queue\milestone_c_queue.json

2. MANDATORY OPERATING & TOKEN EFFICIENCY RULES

Obey .agents/policies/OUTER_SUPERVISOR_RULES.md strictly:

Outer Argon Supervisor direct Python authoring cap: < 120 cumulative lines per task.

Outer Supervisor must NOT invoke python -c / python.exe -c / py -c or execute scripts from ~/.gemini/tmp/.

Launch all child processes (git, pytest, ruff, python, codex.cmd, gh) via python -m tools.sanitized_exec with non-interactive stdin.

Never use interactive gh run watch; use single-shot gh run list or gh pr checks.

AIPC-Managed Hydrogen Worker Delegation:

Delegate substantive Python source and unit-test repairs for R1, R4, R5, R6, R7, and R9 through AIPC's subscription worker automation (aipc.adapters.subscription_worker using gemini-4-hydrogen-medium, with gemini-3.8-flash-medium fallback).

Keep task prompts focused on the exact target files and exact Codex findings—do not dump unrelated files into worker prompts.

Preserve Clean LF Line Endings:

Ensure new edits preserve LF line endings (git diff --check 849c866..HEAD must report zero warnings/errors).

3. STEP-BY-STEP EXECUTION PLAN

Step 1 — Lightweight State & Review Recovery

Run git status -sb and git rev-parse HEAD via tools.sanitized_exec to confirm HEAD is 1934593a8d1bb80de5c9cbdafe72fddaf0d8303b and inspect any untracked operator files.

Read the saved Codex review artifact (*review_1934593.json) under H:\AIPC_Workspace\runtime_c and inspect only the specific line ranges in subscription_worker.py, milestone_c_pipeline.py, trusted_acceptance_harness.py, and related tests implicated by R1, R4, R5, R6, R7, and R9.

Reconcile H:\AIPC_Workspace\runtime_c\queue\milestone_c_queue.json cleanly if needed before dispatching repair tasks.

Step 2 — Bounded Repair of R1, R4, R5, R6, R7, R9

Group R1, R4, R5, R6, R7, and R9 into concise, bounded repair task contracts (e.g., worker/pipeline fixes vs. trusted acceptance harness fixes + regression unit tests covering each finding).

Dispatch the repair tasks via aipc.adapters.subscription_worker (gemini-4-hydrogen-medium) and record durable worker receipts in H:\AIPC_Workspace\runtime_c.

Run targeted unit tests (test_b01, test_c01, test_c02, test_c03), ruff check ., and ruff format --check . via tools.sanitized_exec.

Run the full regression suite via tools.sanitized_exec and verify candidate import origin resolves to H:\000ProjectCode\AI_Project_Controller\src\aipc.

Commit the verified code and test repairs cleanly on gemini/aipc-2.0-milestone-c (let this implementation repair commit be <IMPL_SHA>).

Step 3 — Documentation Synchronization & Clean Worktree Freeze

Update CURRENT_STATE.md, MASTER_PLAN.md, docs/roadmaps/AIPC_2_0_UNIVERSAL_SUBSCRIPTION_ORCHESTRATION_ROADMAP.md, docs/AIPC_2_0_MILESTONE_C_EVIDENCE.md, and docs/AIPC_2_0_MILESTONE_C_COMPLETION_REPORT.md so all commit SHAs (referencing <IMPL_SHA> as the latest implementation commit), test counts, and R1–R9 resolution summaries are 100% accurate and contain no stale eae7689 references.

Remove or move docs/RESUME_MILESTONE_C_HYDROGEN.md (and any other temporary local operator prompt file) out of the Candidate repository before final freeze so git status --porcelain is 100% clean.

Verify ruff check ., ruff format --check ., and git diff --check 849c866..HEAD pass with zero issues, then commit the documentation updates to produce the frozen <CANDIDATE_HEAD_SHA>.

Step 4 — Live Cross-Provider Codex Re-Review (Exact SHA Bound)

Execute live OpenAI Codex subscription review (codex.cmd exec --sandbox read-only --ephemeral) via tools.sanitized_exec against the exact frozen <CANDIDATE_HEAD_SHA> (comparing 849c86684867487f6f0ad8eb54c603cba240dd34..<CANDIDATE_HEAD_SHA>) and save the full JSON receipt in H:\AIPC_Workspace\runtime_c.

Confirm that HEAD remained unchanged and the worktree remained clean during review.

If Codex returns REVISE: Repeat bounded Hydrogen worker repair -> verification -> doc sync -> commit -> live Codex re-review on the new frozen SHA until Codex returns PASS.

Once Codex returns PASS: Do NOT create any further commits unless explicitly recording the final review receipt under the exact same verified PR convention used in Milestone B (and only if required; otherwise preserve the exact PASS-reviewed <CANDIDATE_HEAD_SHA> as the pushed PR HEAD and include the Codex PASS receipt details in the PR body and runtime_c evidence).

Step 5 — Push, CI Verification & Pull Request

Push gemini/aipc-2.0-milestone-c to origin via tools.sanitized_exec.

Poll GitHub Actions CI status using single-shot gh run list --branch gemini/aipc-2.0-milestone-c / gh pr checks (never gh run watch) until both Python 3.12 and Python 3.13 jobs succeed.

Create the Milestone C Pull Request targeting master with a comprehensive summary (scope C01–C03, Gemini 4 Hydrogen worker receipts, R1–R9 resolution, test counts, and exact Codex PASS SHA/confidence).

Stop at the Human Commander merge boundary. Do NOT auto-merge.

4. FINAL REPORT

Provide a concise report covering:

Resolution summary for each Codex finding (R1, R4, R5, R6, R7, R9) and CURRENT_STATE.md synchronization.

AIPC-managed Gemini 4 Hydrogen worker dispatch receipts used for the repairs.

Final ruff and regression pytest counts.

Live Codex Independent Review verdict, confidence, and exact reviewed HEAD SHA.

GitHub Actions CI status (Python 3.12 & 3.13) and Milestone C PR URL.

Final Human Commander merge approval request.

Attribution: K.TRITOD.