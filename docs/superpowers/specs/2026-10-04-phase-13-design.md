# Phase 13 — Independent LLM Reviewer design

## Authorized outcome

The human's Phase 13 implementation brief (105 sections, supplied 2026-10-04)
authorizes end-to-end implementation in `codex/phase-13-attempt-01` from
`aipc-phase-12-pass`, commit `82c7d9dec5b606881bf7c02043ae0ccc6b34d82b`.
Its architecture, safety constraints and completion/stop conditions are binding.
This document makes the implementation choices within that supplied design concrete.
It does not authorize the real repository's PASS or Phase 14.

## Architecture

`ReviewContext -> checked_context -> bounded evidence request ->
IndependentLLMReviewer -> LLMReviewerTransport -> OpenAI Responses API ->
private structured proposal -> local validation -> ReviewResult -> existing PhaseGate`.

Provider-neutral code lives in `aipc.review.llm`. The optional provider-specific
transport lives in `aipc.adapters.openai.reviewer`; it is not an AgentAdapter.
OpenAI SDK objects and raw Responses syntax never reach Controller or PhaseGate.
The existing synchronous Reviewer contract is preserved with a dedicated thread/
event-loop bridge. An async `review_async` entry point is available to async callers.
The transport makes one stateless async request with a bounded timeout and no tools.
The wrapper blocks the synchronous caller during review; it does not change the
Controller scheduling model or claim remote cancellation when local waiting ends.

## Independence and authority

Reviewer IDs differ from worker IDs. Typed configuration and requests accept no
worker handles, sessions, Codex threads/turns, previous response IDs or conversations.
The reviewer excludes arbitrary worker metadata, raw diagnostics, prompts and hidden
reasoning from its provider payload. Worker/model identity is audit information only;
matching vendors/models are allowed. A dedicated fresh client/request has no worker
conversation. SDK auto retries are disabled; there is no provider/model fallback.

Authority: validated canonical identities/state, then collected observations, then
structured worker claims, then narrative as supplemental data. Existing deterministic
objective evaluators and global required tests remain binding. Structural conflicts
raise ReviewContextError before a paid call. Operational blockers yield BLOCKED.
Objective FAIL/UNVERIFIED cannot be upgraded by model optimism. LLM output is validated
review input, never execution authority; existing PhaseGate owns logical promotion.

## Request and evidence contract

Typed config requires reviewer ID, provider and model. Reasoning effort is optional;
timeout, output tokens, provider storage, schema-retry and byte limits are bounded.
Default storage is false, no tools/web/background execution, API truncation disabled.
Prompt version is explicit. Deterministic JSON framing puts only fixed control
instructions in the system/instructions channel and untrusted data in the user input.

All evidence is integrity checked using Phase 09 before selection. Retained text items
are selected by explicit IDs or a conservative default excluding raw/task/result/index
snapshots. Each selection records ID/path/hash, available/included bytes and complete,
truncated, excluded, unavailable or unsupported status. Default per-item content budget
is 32 KiB, total content 128 KiB and serialized request 256 KiB. Bounds cover UTF-8 bytes.
Selected data is redacted for known credential fields/patterns; this is not full DLP.
Binary evidence is metadata-only. Omission/truncation is explicit and cannot support a
semantic PASS. Optional per-criterion evidence requirements bind semantic judgments to
complete selected items; default semantic judgments require complete textual citations.
Required objective evidence remains enforced regardless of semantic confidence.

The deterministic request ID binds context, source task/result/evidence digests,
exact attempt/commit, config, prompt version and the actual selected/redacted payload.
Time and UUIDs are not request inputs. No hidden reasoning or credentials are stored.

## Proposal validation and failure semantics

A private strict schema returns the exact request ID, PASS/REVISE/BLOCKED proposal,
one criterion assessment per declared ID, PASS/FAIL/UNVERIFIED, concise rationale,
evidence IDs, criterion-specific fix and an optional explicit external/safety blocker.
No stable authorization is accepted from the provider. Unknown/duplicate/missing IDs,
unknown citations, unsupported PASS citations, duplicate JSON keys, nonfinite numbers,
extra fields and inconsistent proposals fail closed. Local canonical revalidation is
mandatory. Required FAIL/UNVERIFIED produces REVISE; all non-PASS outcomes forbid
advancement. API success alone is never PASS. Objective observations override narrative.

Auth, timeout, rate limit, service/connection failure, refusal, truncation and malformed
output are reviewer infrastructure ERROR, with no worker repair or implicit fallback.
Only malformed structured proposals may use explicitly bounded schema retries against
the same immutable evidence and model (default one retry). Every call is audited.
Provider failures never consume implementation revisions. Out-of-band cancellation
preserves dispatch intent; it does not claim a remotely cancelled paid request.

## Persistence and restart

An explicit caller-owned audit root must be disjoint from workspace/evidence roots.
Safe bounded paths use hashed request IDs; no repository source/Git mutation occurs.
Private versioned records reuse atomic JSON/fsync/replace. A per-request exclusive
writer lease prevents concurrent duplicate dispatch and is never blindly retired.
Manifest and request digest are published before CALL_INTENT. Selected provider
response projection is durably hashed/referenced before canonical publication.
Completed exact reviews replay after evidence/config revalidation. RECEIVED responses
can be normalized after restart without another paid call. Intent without a trustworthy
received result is ambiguous and yields infrastructure inspection, not automatic resend.
Exactly-once API invocation and cross-resource transactions are not claimed.

Safe audit includes manifest/digests, prompt version, separate worker/reviewer/model
identities, response ID/status, structured proposal reference, canonical result and
bounded token usage. It excludes full provider output, evidence dumps, request headers,
API keys and hidden reasoning. Corrupt/tampered/stale records fail closed.

## Verification and scope

Offline fixtures cover semantic compliance/scope/algorithm/docs/bypass cases; adverse
provider output and failures; missing/conflicting evidence; injection in patches/logs/
README/narrative; independence; deterministic serialization; persistence and crash
windows; Gate/repair/Controller integration. Existing 1322 tests and all six schemas
must remain valid. Optional official SDK is pinned to the inspected current version
3.24.0 and exercised with offline public HTTP/request/response fixtures.

Live smoke is opt-in through AIPC_LLM_REVIEWER_LIVE=1 plus normal API authentication
and an explicitly configured model. Synthetic/disposable data only; never review this
implementation as acceptance authority. Missing API authentication is an acceptable
LIVE_REVIEW_BLOCKED_AUTH_REQUIRED outcome, not fabricated live PASS.

No Claude/Gemini worker, consensus, provider fallback/routing, cost accounting,
execution tools, public-schema changes or automatic next-phase behavior is added.
Final state is AWAITING_REVIEW/PENDING with an attempt commit, no PASS tag, then stop.
