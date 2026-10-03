# Phase 11 — Approval Router design

The current user attachment authorizes end-to-end Phase 11 implementation without
an additional design approval gate. Its conservative defaults override the earlier
blueprint examples that treated LOW as automatic permission. Canonical models and
the six public schemas remain unchanged.

## Boundaries

`aipc.approvals` revalidates the existing ApprovalRequest, typed context, structured
action facts and policy. It classifies and records routing; it never executes the
requested action. Audit publication is its only filesystem mutation. Controller
owns adapter responses, state transitions and response intents. Review/Gate and
Version/Recovery retain their existing responsibilities.

An optional `PendingApprovalSource.pending_approval(run)` returns a canonical
ApprovalRequest or None. It is separate from the unchanged AgentAdapter Protocol.
Provider-specific translation belongs in the adapter. Mock implements this optional
boundary. Controller injects the Router and a separate ApprovalPolicy; without a
Router its Phase 10 approval behavior remains unchanged.

## Identity and risk

ApprovalContext binds project run, canonical task, exact adapter handle, approval ID,
request fingerprint, primary/attempt HEAD and structured ActionFacts. Request and
context fingerprints include command, metadata and all resource facts without
persisting those values. Canonical model instances are revalidated on every boundary.
Context is supplied by a trusted caller, not authenticated cryptographically.

Effective risk is max(canonical risk, deterministic structured minimum). Known read
operations start LOW, local writes/shell execution MEDIUM, Git/external mutations
HIGH, and destructive/irreversible/financial facts CRITICAL. Unknown actions or
capabilities never auto-allow. Facts cannot contradict a known action's capability.

## Policy

Strict private policy/rule models use fixed selectors, not a DSL. Matching order is
DENY, REQUIRE_HUMAN, explicit narrow AUTO_ALLOW, conservative default. Rule order
does not alter precedence; tied matches use specificity then rule ID. Duplicate IDs,
invalid capabilities/risk ceilings and unsafe prefixes fail validation. AUTO_ALLOW
requires a known action, exact capability, workspace scope, explicit path prefix,
permitted task tool and effective risk at most MEDIUM. HIGH/CRITICAL never auto-allow
in this phase. Approval never adds task tool authority or overrides forbidden actions.

Workspace path evaluation reuses portable path and link/junction guards. It checks
actual containment, not string prefixes. External resources do not auto-allow.

## Persistence and human decisions

Atomic bounded JSON audit records retain immutable routing facts and append-only
resolution events. Approval ID reuse with changed request/context/policy fails closed.
Identical route/decision replay is idempotent; conflicting decisions cannot overwrite
the record. Persistence failure prevents an adapter response. Audits retain hashes,
structured labels and actor references, not command/prompt/description/metadata text.

Typed HumanApprovalDecision binds the entire routing decision fingerprint and
approval identity, APPROVE/DENY, actor reference and aware timestamp. A small injected
console presenter accepts only explicit APPROVE/DENY choices outside the core Router;
EOF/invalid input retains pending state. Existing canonical expires_at is honored
against an injected clock; no new implicit expiry interval is introduced.

## Controller and crash windows

WAITING_APPROVAL can route through the injected Router. REQUIRE_HUMAN returns a safe
structured pending decision. AUTO_ALLOW/policy DENY and an explicit human resolution
go through the same exact-response path. Immediately before response, Controller
revalidates live state, primary/managed Git cleanliness and HEAD, task tool authority,
Router record, current pending request and resource facts. Changes after an intent
observer are checked again before calling the adapter.

Durable APPROVAL_RESPONSE_INTENT precedes adapter response; APPROVAL_RESPONDED follows
acknowledgement. An interrupted intent is ambiguous and stops WAITING_HUMAN without
resending. A durable acknowledgement can resume the same handle after restart. Denial
is an operational stop, not review/repair/PASS. Cancellation closes the approval audit;
lost sessions never cause replacement dispatch. Worker actions may change their own
workspace after acknowledgement, so only pre-response snapshots authorize response.

## Verification and limitations

Tests exercise pure routing/risk/identity, atomic audit failure/reload, console/expiry,
optional mock boundary, Controller same-run auto/human/deny, cancellation/session loss,
dirty/stale preconditions, and response crash windows in disposable fixture repos.
All prior tests and schemas remain intact. Actor authority is recorded, not signed;
single-writer/quiescent filesystem assumptions and separate audit/journal/state/provider
publications remain. No exactly-once external response claim, real provider, LLM reviewer,
dashboard, adaptive approval learning or next-phase execution is added.
