# Phase 11 — Approval Router

`aipc.approvals.ApprovalRouter` consumes revalidated canonical `ApprovalRequest`,
private `ApprovalContext`, structured `ActionFacts` and a separate `ApprovalPolicy`.
It classifies, routes and atomically audits permission decisions. It does not execute
commands, respond through adapters, change project state, review work, publish Git
checkpoints or authorize phases. Existing canonical contracts and all six public
schemas remain unchanged; no provider API or new dependency is required.

## Decisions and risk

| Condition | Routing |
| --- | --- |
| Matching DENY rule | DENY |
| Matching REQUIRE_HUMAN rule | REQUIRE_HUMAN |
| Explicit scoped allow plus all safety constraints | AUTO_ALLOW |
| No match, including LOW | REQUIRE_HUMAN by default |
| Unknown, HIGH/CRITICAL, external/network or restricted resource | Never AUTO_ALLOW |

Effective risk is max(canonical risk, structured minimum). Workspace reads start
LOW; writes/executable operations MEDIUM; Git/network writes HIGH; deletion,
financial, destructive or irreversible facts CRITICAL. External scope/network
category establish an external floor even when facts claim local. Unknown action
or capability establishes at least MEDIUM. Static reasons explain risk. Command
strings, descriptions and provider prose are not parsed as authority or proof of
safety. Trusted callers remain responsible for accurate structured facts.

Rules select exact action, capability, scope, contained portable path prefix and
risk ceiling. Matching deny rules dominate human rules; human rules dominate allow.
Specificity and rule ID break ties deterministically. Duplicate IDs, invalid
categories/decisions, unsafe paths and unscoped automatic permission are rejected.
Default AUTO_ALLOW is rejected. No permission is learned from previous decisions.

## Examples

```python
from aipc.approvals import ApprovalPolicy, ApprovalRule

conservative = ApprovalPolicy()
read_notes = ApprovalPolicy(rules=(ApprovalRule(
    rule_id="read-notes", decision="AUTO_ALLOW", action_type="filesystem_read",
    capability="filesystem", scope="workspace", resource_prefix="notes",
    max_risk="LOW",
),))
deny_git_writes = ApprovalPolicy(rules=(ApprovalRule(
    rule_id="protect-history", decision="DENY", action_type="git_write",
    capability="git", max_risk="CRITICAL",
),))
```

Automatic permission additionally requires matching known access, permitted task
tools, no forbidden action, risk at most MEDIUM and an actual contained local
resource. `.git`/`.env*` resources require human routing. Component containment and
symlink/junction guards prevent traversal and naive prefix matches. Human approval
cannot add missing execution authority or adapter capabilities.

## Exact identity and audit

Context binds project/run/phase/attempt, canonical task, worker agent/run, approval
ID, complete request digest, primary/attempt HEAD, optional project-state digest
and action/resource facts. Controller supplies the live state digest. Fingerprints
include exact command, metadata and tool permissions. Any changed binding makes
the prior decision stale rather than transferring permission.

`route(request, context, policy=None)` persists routing before returning it.
`resolve(request, context, human)` records an external `HumanApprovalDecision`
binding routing digest, approval ID, APPROVE/DENY, actor reference and aware time.
`inspect(decision)` returns typed audit; `invalidate(decision, reason)` closes
cancelled/stale/expired/lost-session authority. Identical replay is idempotent;
conflicting decisions and rewritten routing/history fail closed.
Routing a closed record raises a typed error; only `inspect` returns historical
routing. A former AUTO_ALLOW is never returned as renewed closed authority.

Private version 1.0 JSON audits use hashed project/run/approval keys in an authorized
caller root. Reads are bounded at 1 MiB. Corrupt, duplicate-key and unsafe-path
records are rejected and preserved. Atomic replacement uses existing storage;
immutable routing facts retain append-only resolution/invalidation events.
Persistence failure cannot yield unrecorded permission. Canonical `expires_at`
uses an injected aware clock; no implicit lifetime is introduced. Expiry closes
authority even when historical resolution still records APPROVE.

Audits and pending summaries retain known labels, identifiers, risks, static reasons,
actor references and SHA-256 fingerprints. They omit request description, prompt,
command and arbitrary metadata/resource values. Caller IDs/rule IDs/actor references
must be nonsecret identifiers. Hashes bind data; they do not authenticate origin or
provide a full secret detector. Existing evidence/journal retention remains separate.

## Human and Controller boundaries

`ConsoleHumanApproval.decide(decision, actor_reference=...)` is an optional injected
input/presentation boundary outside Router. Explicit APPROVE/DENY creates a typed
decision; invalid input, EOF and input timeout leave permission pending. It shows
resource/command fingerprints, so a trusted caller must separately present the
exact canonical request for informed review. It neither authenticates a human nor
wires runtime execution into the help-only CLI.

Adapters may implement the separate optional
`PendingApprovalSource.pending_approval(handle) -> ApprovalRequest | None` protocol.
Provider translation belongs there. Required AgentAdapter methods, raw envelopes
and normalization remain unchanged. Mock exposes a stable canonical request for
its process-local waiting run independently of raw syntax; terminal, denied or
cancelled runs expose no pending request.

Inject `approval_router`, `approval_policy` and optionally a trusted structured
`approval_facts(request, task)` resolver into Controller. Without Router, Phase 10
behavior remains intact. Human routing returns `ControllerResult.pending_approval`
and the exact `pending_run`. Submit an external typed decision with
`await controller.submit_approval(project, phase_id, human, policy=controller_policy)`.
ControllerPolicy still owns attempts, tools and phase stop; it is not ApprovalPolicy.

Automatic, policy denial and human resolutions use the same exact response path.
Controller revalidates request, live state, clean primary/managed Git, HEAD, policy,
resources, capabilities and task tools. It persists APPROVAL_RESPONSE_INTENT,
checks again after callbacks, responds on the same handle, then persists
APPROVAL_RESPONDED. No second worker task is dispatched. Approval cannot bypass
normal evidence/review/Gate or independently promote a phase.

Acknowledged APPROVE clears WAITING_APPROVAL via the existing RUNNING transition
and polls the same run. Acknowledged DENY clears the wait and pauses WAITING_HUMAN
with operational APPROVAL_DENIED, without normalization/review/repair/PASS. This
legal transition records acknowledged lifecycle control, not execution of the
denied action. Cancellation invalidates pending audit after adapter acknowledgement;
lost ownership invalidates authority and never dispatches a replacement.

## Restart and Phase 12 boundary

Fresh Router/Controller instances retain pending routing and typed resolutions.
A decision before response intent can continue only after exact live checks.
Intent without acknowledgement is ambiguous: WAITING_HUMAN, no resend. A durable
acknowledgement can continue the same owned run. Worker changes after acknowledgement
are legitimate; pre-response HEAD is not falsely treated as the terminal worker
commit. Version/Recovery retain safety and recovery ownership; no rollback algorithm
or destructive Git operation is added.

There is no exactly-once external response guarantee. Audit, Controller journal,
project state and provider acknowledgement are separate publications. Single-writer
and quiescent Git/filesystem assumptions remain; stale leases/corrupt records need
inspection. Actor authority is recorded, not cryptographically authenticated.
Phase 12 owns real-provider translation, pending-action association, session
reconstruction and provider idempotency. LLM approval review, Dashboard/API,
authentication and stronger concurrency remain later work.
