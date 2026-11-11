# AIPC 2.0 — ACTIVATION & MILESTONE A (SUBSCRIPTION-ONLY MULTI-PROVIDER FOUNDATIONS)

## ROLE & HUMAN COMMANDER AUTHORIZATION
You are the Lead Architect and Main Supervisor for AI Project Controller (AIPC).
Please respond in clear, concise English.
The Human Commander has reviewed and merged Phase 24 (PR #4) into `master` and now EXPLICITLY AUTHORIZES:
1. Executing the **Section 12 Phase 24 Handoff / Activation Protocol** in `docs/roadmaps/AIPC_2_0_UNIVERSAL_SUBSCRIPTION_ORCHESTRATION_ROADMAP.md`.
2. Implementing **AIPC 2.0 — Milestone A (Workstreams A01, A02, A03, and A04)** on a new feature branch (`gemini/aipc-2.0-milestone-a`), using the Phase 24 self-development architecture, sanitized child environments (`tools.sanitized_exec`), and subscription-authenticated workers/reviewers.

## STRICT OPERATING & CREDENTIAL BOUNDARIES
- **Outer Supervisor Session Only:** This external Gemini CLI session uses Vertex AI credits solely for orchestration and architectural supervision.
- **Inner AIPC 100% Subscription-Only:** All AIPC runtime code, adapters, workers, and reviewers MUST use subscription-authenticated developer runtimes only (`agy.exe` Antigravity subscription and `codex` ChatGPT subscription). Separately billed API-key paths (`GEMINI_API_KEY`, `GOOGLE_API_KEY`, `OPENAI_API_KEY`, Vertex billing, Anthropic billing) remain strictly prohibited inside AIPC.
- **Mandatory Child Environment Isolation:** Every child process (`pytest`, `python -m aipc`, `agy.exe`, `codex`, `docker`, `gh`) MUST be launched via `tools.sanitized_exec` or sanitized environment construction with all Outer Supervisor credentials stripped.
- **Do NOT:** commit directly to `master`, auto-merge PRs, start Milestone B/C/D before Milestone A is accepted, or weaken any Phase 00–24 tests or safety gates.

## STEP 1 — PHASE 24 HANDOFF & GAP EVALUATION (ROADMAP SECTION 12)
1. Read `AGENTS.md`, `SKILL.md`, `MASTER_PLAN.md`, `CURRENT_STATE.md`, `docs/OPERATIONAL_CLI_GUIDE.md`, and `docs/roadmaps/AIPC_2_0_UNIVERSAL_SUBSCRIPTION_ORCHESTRATION_ROADMAP.md`.
2. Verify `origin/master` includes the merged Phase 24 commit and create branch `gemini/aipc-2.0-milestone-a` from verified `master`.
3. Re-evaluate existing AIPC modules against Workstreams `A01`–`D03` and classify each as `ALREADY_AVAILABLE`, `PARTIAL`, `BLOCKED`, or `MISSING`.
4. Update `docs/roadmaps/AIPC_2_0_UNIVERSAL_SUBSCRIPTION_ORCHESTRATION_ROADMAP.md` (bumping version to `0.2`), `MASTER_PLAN.md`, and `CURRENT_STATE.md` to record Phase 24 merge acceptance and the Human Commander's explicit authorization of **Milestone A (A01–A04)**. Commit this activation step cleanly.

## STEP 2 — IMPLEMENT MILESTONE A WORKSTREAMS SEQUENTIALLY
Delegate bounded coding and testing tasks to the verified Antigravity Flash subagents (`agy.exe --agent aipc-implementation-worker` / `aipc-explorer` / `aipc-test-auditor`) via `tools.sanitized_exec`:

- **A01 — Subscription Authentication & Runtime Qualification:**
  - Probe supported Codex and Antigravity runtimes without reading or exporting raw credentials/tokens.
  - Capture supported model IDs, account-independent qualification metadata, availability, sandbox constraints, and capability evidence without secrets.
  - Separate Desktop/UI presence from headless execution and reviewer-mode support.
  - Hard-disable separately billed API-key paths in the `subscription_only` operating profile.
  - *Exit criterion:* Reproducible eligibility reports; missing subscription/model/auth/CLI causes deterministic `BLOCKED`/`UNAVAILABLE` states.

- **A02 — Multi-Provider Operational Integration:**
  - Extend existing adapters to support real, qualified execution and role-specific modes via official subscription CLI/SDK paths.
  - Verify live end-to-end execution of both subscription-backed providers (`Antigravity` and `Codex`) with exact identity and evidence, preserving worker isolation and normalization.

- **A03 — Role-Based Provider/Model Registry:**
  - Define typed, schema-validated role configurations, capabilities, preferences, exclusions, and fallback policies for `Planner`, `Worker`, and `Reviewer` (never embedding hardcoded provider names in core orchestration logic).
  - Track model requested vs model actually used, session/run IDs, qualifications, and role provenance, with negative tests for unavailable/ineligible assignments.

- **A04 — Planner Orchestration Engine:**
  - Produce canonical plan/task graphs from human-approved goals limited to authorized milestone scope, integrated with Phase 24's `PersistentTaskQueue`.
  - Persist plan versions, criteria, dependencies, owner, prompts, and approval lineage so a plan from one provider is verifiably resumable by another from canonical state, and out-of-scope work is rejected.

## STEP 3 — INDEPENDENT REVIEW, CI & PULL REQUEST
1. Verify each workstream (`A01`–`A04`) with targeted `pytest` and `ruff` checks, making coherent commits per workstream.
2. Run live independent review on the candidate changes using Phase 24's `CodexSubscriptionReviewerTransport` (`SubscriptionIndependentReviewer` in `read_only` mode via `tools.sanitized_exec`), applying bounded repairs if `REVISE` findings occur.
3. Run the full regression suite and schema checks, push `gemini/aipc-2.0-milestone-a` to `origin`, verify GitHub Actions CI passes on Python 3.12 & 3.13, open the Pull Request (do NOT merge), and provide a comprehensive Final Report.
