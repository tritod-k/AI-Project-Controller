AIPC — TARGETED INLINE PYTHON POLICY, GEMINI 4 HYDROGEN WORKER & MILESTONE C RESUME

Human Commander K.TRITOD authorizes a bounded policy clarification, conditional Gemini 4 Hydrogen subscription worker integration, and continuation of the previously authorized Milestone C (C01–C03) workflow.

Target Candidate branch: gemini/aipc-2.0-milestone-cLast reported Candidate commit: 8528dc0

Respond in concise English.

Preserve all existing work, task queues, handoffs, evidence, and authorization boundaries. Do not restart completed tasks, modify Stable Controller source, or begin Milestone D. Note: If reading this prompt from a local operator file (docs/RESUME_MILESTONE_C_HYDROGEN.md), do NOT stage or commit this operator prompt file into Git.

PART 1 — TARGETED INLINE PYTHON EXECUTION POLICY (2-TIER SCOPE)

Keep the existing 120-line-per-implementation-task Outer Supervisor authoring limit unchanged.

Verify and refine .agents/policies/OUTER_SUPERVISOR_RULES.md (and keep GEMINI.md / AGENTS.md consistent) so that:

Outer Supervisor (Argon) Tier: The Outer Argon Supervisor must avoid direct inline Python execution (python -c, python.exe -c, py -c, and equivalent forms) and instead prefer existing trusted modules invoked with python -m or reviewed script files through python -m tools.sanitized_exec.

AIPC Runtime / Worker / Tests Tier: Do NOT globally prohibit python -c inside AIPC runtime components, unit/integration tests, dependencies, or qualified worker verification environments where needed (e.g., import-origin probes). Do not add a blanket python -c block inside tools/sanitized_exec.py that would break legitimate inner test or verification workflows.

Preserve all existing environment sanitization, security approvals, and test behavior. Make minimal documentation/policy adjustments on the Candidate branch only if needed, and commit cleanly.

Do not bypass interactive security confirmations or weaken existing authorization checks. If mandatory human approval is required, preserve state and report the blocker.

Verify that the persistent policy remains loadable after Gemini CLI session resets. Do not attempt to execute interactive /memory commands through Shell.

PART 2 — GEMINI 4 HYDROGEN SUBSCRIPTION WORKER INTEGRATION

2.1 Objective

Evaluate and, only if genuinely supported, integrate Gemini 4 Hydrogen as the preferred AIPC implementation worker instead of Gemini 3.8 Flash.

Target operating model:

Outer Supervisor: Vertex Gemini 4 Argon — planning, architecture, coordination, and verification oversight only.

Primary Implementation Worker: Gemini 4 Hydrogen — Medium reasoning, if available and qualified.

Fallback Implementation Worker: Gemini 3.8 Flash (gemini-3.8-flash-medium) — only when qualified.

Independent Reviewer: Codex CLI (openai_codex) using subscription authentication and read_only sandbox.

Orchestrator: AIPC Stable Controller.

All Inner AIPC execution must remain strictly subscription_only.

2.2 Read-Only Runtime Compatibility Assessment

Before modifying the model-selection implementation:

Inspect the currently installed Antigravity CLI executable and version.

Discover the supported model-listing and model-selection commands from the installed CLI.

Retrieve the actual available model identifiers.

Verify whether Gemini 4 Hydrogen is available to the currently authenticated Antigravity subscription account.

Discover the exact Hydrogen model identifier from the runtime. Do not invent or guess model names.

Determine whether the runtime supports explicit reasoning-effort configuration, including Medium.

Inspect the existing AIPC subscription qualification and model-selection implementation.

Verify that the CLI execution mode, filesystem restrictions, environment sanitation, and subscription authentication remain compatible.

Do not silently install or upgrade external CLI binaries.

If an update is required, report the requirement and continue with the existing qualified Flash worker unless an update is separately authorized.

2.3 Strict Subscription-Only Boundary

Allowed:

Google Antigravity CLI subscription authentication.

Qualified Hydrogen or Flash subscription workers.

Qualified Codex CLI subscription reviewers.

Strictly prohibited inside Inner AIPC:

Vertex AI credentials.

GEMINI_API_KEY, GOOGLE_API_KEY, or other paid API-key authentication.

Paid API fallback.

Inheritance of Outer Supervisor credentials.

Fabricated provider qualification results.

Unverified model availability or capabilities.

A manually constructed QUALIFIED report without trusted runtime evidence must never authorize worker execution.

2.4 Minimal Hydrogen Integration

Only if Hydrogen is confirmed available and qualified:

Reuse the existing AntigravitySubscriptionWorkerAutomation and subscription adapter.

Do not introduce a duplicate provider adapter.

Add the exact discovered Hydrogen model identifier to the supported model-selection and qualification path.

Prefer Hydrogen Medium for new authorized implementation tasks when the runtime supports this configuration.

Preserve Gemini 3.8 Flash as an eligible fallback.

Keep model selection configurable and compatible with existing task contracts.

Record actual requested and executed model identities in Worker Automation receipts.

Detect unexplained requested-versus-actual model mismatches.

Preserve existing canonical contracts, provider restrictions, filesystem scope checks, and safety gates.

Do not give a worker Shell, Git, network, or additional filesystem permissions merely to enable Hydrogen.

Stable Controller Compatibility: The Stable Controller must remain pinned and must not import unapproved Candidate code. If Hydrogen support added in Candidate cannot safely be used by the pinned Stable Controller, report that integration limitation and continue using the already qualified Flash path rather than hot-patching Stable.

2.5 Tests and Qualification Evidence

Add focused tests for:

Hydrogen model discovery and availability.

Qualified Hydrogen selection.

Flash fallback when Hydrogen is unavailable.

Unqualified model rejection.

Subscription-only enforcement.

Requested-versus-actual model mismatch.

Worker receipt accuracy and provenance.

No credential leakage.

Existing Flash worker regression compatibility.

Distinguish clearly between:

Mock/unit-test PASS.

Live Antigravity subscription qualification PASS.

Actual AIPC-managed worker execution PASS.

Do not claim live Hydrogen qualification based only on mocks, static configuration, or direct Outer Supervisor agy.exe invocation.

2.6 Unavailable Hydrogen Behavior

If Hydrogen is not available, not qualified, or incompatible with the pinned Stable Controller:

Do not force Hydrogen execution.

Do not fabricate a successful qualification.

Retain the existing qualified gemini-3.8-flash-medium worker.

Record the reason and evidence as HYDROGEN_SUBSCRIPTION_UNAVAILABLE or another precise blocker status.

Continue Milestone C using the qualified Flash worker.

Hydrogen availability must not become an unnecessary blocker for C01–C03.

PART 3 — RESUME MILESTONE C VIA AIPC-MANAGED SUBSCRIPTION WORKERS

Once Part 1 is verified and Part 2 is either qualified or safely deferred, resume Milestone C from the latest valid checkpoint (previously reported commits aa49268 / 8528dc0).

3.1 Checkpoint and Queue Recovery

Before dispatching additional tasks:

Verify the current Candidate branch and HEAD SHA.

Inspect CURRENT_STATE.md, the Milestone C roadmap, and relevant execution handoffs.

Inspect the existing PersistentTaskQueue, including previous C01 attempts and attempt counters.

Check whether any task is RUNNING, FAILED, PENDING, ELIGIBLE, or completed.

Reconcile incomplete attempts using existing supported recovery mechanisms.

Preserve prior worker receipts and verification evidence.

Do not blindly reinitialize, overwrite, or duplicate task queues.

Current known paths:

Stable Controller: H:\AIPC_Workspace\stable_c

Candidate: H:\000ProjectCode\AI_Project_Controller

Runtime: H:\AIPC_Workspace\runtime_c

Persistent Queue: H:\AIPC_Workspace\runtime_c\queue\milestone_c_queue.json

Treat these as current environment paths, not permanent universal defaults.

3.2 Complete the AIPC Worker Dispatch Bridge

Complete the minimal AIPC-managed worker dispatch bridge connecting:

HumanGoalSpec → PlannerOrchestrationEngine → PersistentTaskQueue → AntigravitySubscriptionWorkerAutomation → SafeSelfDevelopmentRunner → IndependentSubscriptionReviewerGate → MilestoneBRepairAndRecoveryOrchestrator → PhaseGate

Verify actual interfaces and canonical contracts rather than assuming unsupported constructor fields or method names.

Avoid unnecessary architectural rewrites.

The bridge must:

Use a qualified subscription worker.

Dispatch an authorized bounded task through AIPC.

Execute against the correct Candidate worktree.

Preserve sanitized environment and filesystem restrictions.

Capture real worker session identity and actual model.

Record changed files, receipts, verification results, and exact Git SHA.

Preserve task recovery, retry limits, independent review, and PhaseGate behavior.

Do not treat direct Outer Supervisor invocation of agy.exe as equivalent to AIPC-managed delegation.

3.3 Execute C01–C03

Execute the previously authorized workstreams sequentially:

C01 — Universal Project Adapters, Profiles & Templates + Stable/Candidate Isolation

C02 — Multi-Language Isolated Verification & E2E Pipeline

C03 — Trusted Acceptance Test Harness & Multi-Provider Self-Development Dogfooding

Use the following implementation worker selection policy:

First: qualified Gemini 4 Hydrogen, if genuinely supported and compatible.

Otherwise: qualified Gemini 3.8 Flash (gemini-3.8-flash-medium).

All substantive implementation and unit-test writing must be dispatched through AIPC-managed subscription workers.

The Outer Argon Supervisor must respect the existing less-than-120-lines cumulative direct Python authoring limit per implementation task.

Do not split one implementation task artificially to bypass the limit.

Preserve all original Milestone C Roadmap v0.5 acceptance criteria, trusted test isolation, and evidence requirements.

3.4 Independent Review and Final Git Handoff

After C01–C03 verification and documentation updates (CURRENT_STATE.md and roadmap):

Run targeted and regression verification.

Verify trusted acceptance tests and isolation boundaries.

Confirm actual AIPC-managed subscription worker execution evidence.

Create the final Candidate commit.

Freeze the exact HEAD SHA.

Run live independent Codex review (openai_codex in read_only sandbox) against that exact SHA.

If the Candidate changes after review, repeat the required verification and review against the new SHA.

Push gemini/aipc-2.0-milestone-c.

Verify GitHub Actions for Python 3.12 and 3.13.

Prepare the Milestone C PR, or Draft PR when mandatory qualification remains blocked.

Do not auto-merge, deploy, or begin Milestone D.

PART 4 — FINAL REPORT

Provide a concise operational report covering:

Persistent policy verification and changed files.

Inline Python execution policy and Runtime Guard decision.

Candidate branch and current HEAD.

Antigravity CLI version.

Actual Hydrogen model ID, if discovered.

Hydrogen subscription qualification status.

Selected Primary and Fallback Worker models.

Whether Medium reasoning is supported.

Actual AIPC-managed worker execution evidence.

C01/C02/C03 Task Queue statuses.

Test and regression results.

Stable/Candidate isolation status.

Independent Codex review and exact reviewed SHA.

CI and PR status.

Remaining blockers requiring Human Commander action.

Use precise evidence-backed statuses:

POLICY_VERIFIED / POLICY_BLOCKED

HYDROGEN_QUALIFIED / HYDROGEN_SUBSCRIPTION_UNAVAILABLE

AIPC_WORKER_DISPATCH_VERIFIED / DISPATCH_BLOCKED

MILESTONE_C_PASS / MILESTONE_C_BLOCKED

Never claim PASS without actual supporting evidence.

Attribution: K.TRITOD.

START

Recover the current Milestone C checkpoint and durable queue.

Verify and refine the targeted two-tier inline Python policy.

Conduct the read-only Hydrogen compatibility assessment.

If qualified, implement the minimal Hydrogen worker integration without modifying pinned Stable Controller.

If unavailable, retain the qualified Flash worker.

Resume C01–C03 through actual AIPC-managed subscription worker dispatch.

Complete independent verification and PR handoff.

Stop at the Human Commander merge-approval boundary.