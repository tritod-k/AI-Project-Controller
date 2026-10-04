# Independent LLM Reviewer — Phase 13

The reviewer supplies bounded semantic judgment through the existing synchronous
`Reviewer.review(ReviewContext) -> ReviewResult` interface. `DeterministicReviewer`
remains supported. The Controller and PhaseGate have no provider-specific changes.

```text
checked ReviewContext + verified retained EvidenceBundle bytes
  -> IndependentLLMReviewer -> dedicated LLMReviewerTransport
  -> stateless Responses API -> private structured proposal
  -> local validation / objective overrides -> canonical ReviewResult
  -> bind_review with exact reviewed provenance -> existing PhaseGate
```

The model is an independent evidence-based reviewer, not a controller authority.
A model PASS does not publish Git refs, modify project state or authorize itself.
Only the existing Gate can return a logical stable authorization for the exact
attempt, and the authorized caller applies that decision. Manual external ChatGPT
review remains authoritative for this real Phase 13 implementation.

## Configuration and installation

Install `pip install -e '.[dev,reviewer]'` for the optional official
`openai==3.24.0` SDK. Base installation and core reviewer imports need no provider
SDK. Importing `aipc.adapters.openai.reviewer` is safe without it; instantiating its
transport raises typed `sdk_missing` when unavailable. The Phase 12 Codex SDK extra
is separate. This phase uses only public Responses SDK methods.

```python
from pathlib import Path

from aipc.adapters.openai.reviewer import OpenAIResponsesReviewerTransport
from aipc.review.llm import IndependentLLMReviewer, LLMReviewerConfig

config = LLMReviewerConfig(
    reviewer_id="independent-reviewer",
    provider="openai",
    model="<explicitly-authorized-available-model>",
    worker_model=None,  # Explicit provenance, unavailable when caller has no model ID.
    timeout_seconds=60,
    max_output_tokens=4096,
    reasoning_effort=None,
    schema_retries=1,
    store=False,
)
reviewer = IndependentLLMReviewer(
    config,
    OpenAIResponsesReviewerTransport(config),
    audit_root=Path("/explicitly-authorized-external-review-audit"),
)
# review = reviewer.review(checked_context)
# async_review = await reviewer.review_async(checked_context)
```

`OPENAI_API_KEY` is read at runtime through the normal SDK authentication path.
There is no key field in reviewer configuration, no Codex credential-file reuse
and no automatic provider/model selection or fallback. The endpoint is explicitly
the official OpenAI API. Provider aliases may resolve to a different actual model
name; both requested and returned identities are audited.

The synchronous compatibility method runs a dedicated event loop in a new thread
and blocks the existing Controller caller until review returns. It does not reuse
the worker loop or session. `review_async` is available to native async callers.
Timeout bounds each call, not all preprocessing or multiple schema attempts.
Timeout/cancellation does not establish that remote processing or billing stopped.

## Independence and authority

The transport accepts only `LLMReviewRequest` and returns `LLMReviewRawResult`.
It has no AgentAdapter start/wait/cancel interface. Worker transport/session,
thread/turn handles, conversation and previous-response configuration are rejected
as extra fields. Reviewer and worker agent IDs must differ. Different models or
vendors are optional; independent session, role and context are mandatory.

Each Responses call has a fresh client, fixed `instructions`, one user JSON input,
strict `text.format` JSON schema, `tools=[]`, `tool_choice="none"`, no parallel
tools, `background=False`, `include=[]` and `truncation="disabled"`. Storage is false
by default. SDK retries are disabled. No worker thread, turn, hidden reasoning or
previous response enters the reviewer. Reasoning response items are discarded;
only one completed assistant structured text output is projected.

Evidence authority is:

1. Revalidated canonical identities, project/state/attempt and exact checkpoint.
2. Integrity-checked Evidence Collector observations and retained file bytes.
3. Canonical structured worker claims, kept distinct from parsed observations.
4. Worker narrative, which cannot override any of the above.

Structural identity, hashes, byte limits and parsed test observations are checked
before dispatch. The phase must explicitly be running and awaiting review. Evidence
and the original caller-owned context are revalidated before retry/publication and
after the response, then again by the existing Gate. An optimistic
proposal cannot override failed/missing tests, failed commands, worker/evidence
errors, incomplete completion, unresolved approvals or dirty/missing Git evidence.
Configured objective criterion evaluators remain authoritative.

## Untrusted content, budgets and citations

Prompt version is `independent-review-v1`. Serialized input is deterministic for
the same checked context/configuration. Source, README, comments, patch, logs and
narrative appear only as JSON user data. They never become control instructions.
Instructions explicitly reject embedded requests to approve, execute or change
authority. This framing is not a proof of resistance to semantic prompt injection;
tool restrictions, canonical validation and deterministic Gate checks remain the
execution/authority boundary. Semantic judgments can still be mistaken.

Default selection excludes raw provider diagnostics and full task/result/command/
index snapshots. It sends a safe canonical projection, observed summaries and
sorted selected text artifacts. The manifest lists included, truncated, excluded,
unsupported and unavailable items, original/content hashes and exact UTF-8 sizes.
Default content limits are 32 KiB per item and 128 KiB total; input plus fixed
instructions is bounded to 256 KiB. JSON escaping counts toward that total.
Schema and SDK envelope overhead are additional. Binary content is explicit;
UTF-8 truncation never splits a character. Configuration limits are bounded.

Known credential assignments, bearer values and common key strings are redacted
before serialization/persistence, including quoted JSON fields and sensitive dictionary
keys. This is not universal secret detection. Callers
must authorize evidence roots and apply any required retention/redaction policy.
Hashes establish integrity and correlation, not origin authentication.

Every proposal must assess each exact criterion once. Unknown/duplicate/missing
criterion IDs, unknown/unavailable citations, duplicate JSON keys, non-finite
values, extra fields and contradictory verdict/fix declarations are rejected.
Required failures or uncertainty become canonical REVISE with criterion-specific
fixes. Optional non-PASS criteria follow the existing `require_optional` policy.
Only FAIL IDs enter `failed_criteria`; required UNVERIFIED still prevents PASS.

Semantic PASS needs nonempty citations to complete, unredacted selected text.
Callers can bind exact required evidence per criterion through
`SemanticEvidenceRequirement`; omitted/truncated required content becomes
UNVERIFIED. A valid citation proves retained input availability, not that the
model's interpretation or source coverage is correct. Objective rules may use
verified full local evidence without depending on model-selected truncation.
Authoritative criterion statements and phase objectives are sent in full within the
overall budget; they are never silently truncated. Requirements needing credential
redaction fail configuration before dispatch so approval cannot assess weakened text.

## Verdicts and failures

Validated PASS/REVISE/BLOCKED proposals are normalized to the unchanged canonical
`ReviewResult`. External/safety blockers produce BLOCKED without worker repair
instructions. Canonical REVISE feeds the existing PromptEngine repair path without
provider translation. Canonical confidence is a diagnostic 1.0 for locally accepted
PASS and 0.0 otherwise; it is not calibrated probability or Gate authority.

Authentication, rate limit/quota, timeout, connection/service failure, refusal,
truncation, wrong raw identity and exhausted malformed proposals produce ERROR,
never implementation REVISE. Only malformed structured proposals permit bounded
schema retry (default one, maximum two) of the same deterministic request; no
provider error retry, fallback or consensus is implemented. Preflight configuration/
context errors raise safe typed exceptions before a paid call.

## Audit, restart and supported limits

The explicit audit root must be disjoint from both workspace and evidence roots.
Only audit files are written. No source/Git operations, arbitrary shell execution,
worker repair, repository mutation tools or web search are exposed.

Per-request audit records persist configuration, prompt version, actual input and
instruction digests, project/run/phase/attempt/task/worker identities, evidence
selection and hashes, exact commit, call count, response identity/token usage,
sanitized structured raw reference/digest and canonical result/digest. Full input,
API headers/keys, SDK objects and hidden reasoning are not retained. Malformed JSON
text is withheld rather than persisting unparsed secret-bearing diagnostics. There
is no full evidence INFO logging.

An exclusive request lease guards the single-writer journal. Owned empty leases are
released after checking a unique ownership token and directory identity; existing/stale
or altered locks fail closed and are not blindly removed. Ownership is checked at audit
publication boundaries; this is not a claim of complete hostile filesystem race safety.
Intent is atomic and durable before dispatch. Raw projection is atomically published
before received state; received state is published before canonical state. An exact
completed record replays without a paid call. A verified received response can be
normalized on restart without dispatch. Terminal provider ERROR is also replayed;
an explicit new review identity/configuration is needed after operator resolution.

An interrupted call intent without a verified received record is ambiguous and
requires inspection. It never automatically resends. A raw file whose hash was not
published in the journal is not trusted for automatic replay or promotion. Corrupt
records, mismatched manifests/digests and unsafe paths are preserved and rejected.

This does not claim exactly-once paid execution, cryptographic reviewer authority,
multi-file transactions, directory-fsync power-loss guarantees, distributed
concurrency or complete filesystem race protection. Unknown human edits and locks
remain for owner inspection. Stronger recovery/hardening is deferred.

## Live smoke

`tools/llm_reviewer_live_smoke.py` requires `AIPC_LLM_REVIEWER_LIVE=1`, normal
authentication and an explicit `AIPC_LLM_REVIEWER_MODEL`. Ordinary pytest never calls
a live provider. It uses at most four small calls, output limit 2048 and no schema
retry. It retains disposable external fixture/audit receipts for inspection.

PASS, REVISE, semantic and injection cases use labeled synthetic evidence snapshots.
Their Git/test observations are crafted fixture data, not execution receipts or
evidence about the real AIPC checkout. The optional live Codex worker pipeline is
separately skipped without authorization/budget. Missing authentication is
`LIVE_REVIEW_BLOCKED_AUTH_REQUIRED`; skips are never reported as passing.

Phase 14 Claude Adapter, provider fallback, routing and reviewer consensus are absent.
