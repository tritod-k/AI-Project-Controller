# Future Adapter Contract Boundary

Phase 01 defines canonical data contracts only. No adapter or normalization
implementation exists yet; those layers must be added in their authorized phases.

Future adapters must emit provider-neutral payloads validated through the models
documented in [CANONICAL_MODELS.md](CANONICAL_MODELS.md). The intended flow remains:

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

Provider-specific capability negotiation, lifecycle, events, retry, routing, approval
handling and shared adapter contract suites remain later-phase implementation work.
