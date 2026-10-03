# Adapter Contract Boundary

Phase 01 defines canonical data contracts. Phase 03 adds the adapter Protocol,
capabilities, run/raw envelopes, exact registry and a deterministic mock. The
Phase 04 normalization layer now provides a shared Protocol/context/validator and
mock-specific mapping. See [AGENT_ADAPTERS.md](AGENT_ADAPTERS.md) and
[NORMALIZATION.md](NORMALIZATION.md).

Adapters return RawAgentResult with provider-specific content. Normalizers
must produce provider-neutral payloads validated through the models documented in
[CANONICAL_MODELS.md](CANONICAL_MODELS.md). The intended flow remains:

```text
raw provider output -> adapter/normalizer -> canonical AgentResult -> downstream consumers
```

Do not send raw provider responses directly to controller state, reviewer logic or
the Phase Gate. Keep diagnostic raw output behind a `raw_output_ref`; retain optional
extension information in root `metadata` rather than adding core fields or branching
on provider identity. Unknown evidence must be explicit null/empty values according
to the contract, not invented success or count claims.

Require schema version 1.0 and validate with `model_validate` or `model_validate_json`.
JSON Schema alone does not check cross-field totals/time ordering. Avoid unchecked
construction/copies at boundaries and revalidate mutable payloads before consuming them.

Phase 03 includes shared adapter contract tests and mock lifecycle/approval responses.
Phase 11 supplies provider-neutral approval policy and optional canonical pending
approval observation; see [APPROVAL_POLICY.md](APPROVAL_POLICY.md). Provider-specific
approval IDs/action/resource translation must stop in the adapter before downstream
routing. Real provider integration, session reconstruction and provider response
idempotency remain Phase 12+ work.
