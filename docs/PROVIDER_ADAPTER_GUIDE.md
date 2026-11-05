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

Phase 12 now implements the first real provider under `aipc.adapters.codex` with
optional official Python SDK, exact-workspace/session binding and CodexNormalizer.
All raw syntax remains local; core schemas/components are unchanged. The Codex
approval capability is false and native escalation is explicitly denied; no
provider-native reviewer supplies AIPC authority. See [Codex lifecycle and safety](CODEX_ADAPTER.md).

# Dedicated Phase 13 review transport

`LLMReviewerTransport` is a separate narrow async review protocol; it is not an
AgentAdapter execution interface. Optional `OpenAIResponsesReviewerTransport` uses
public official SDK methods for fresh, strict, tool-free Responses calls. It never
reuses Codex thread/turn/session objects or worker transports. Provider syntax is
projected to a private raw result and normalized before canonical ReviewResult.
See [reviewer setup and boundaries](INDEPENDENT_LLM_REVIEWER.md).

## Phase 14 Claude provider

The optional `aipc.adapters.claude` package implements the existing execution and
normalization contracts without changing core interfaces or public schemas.
Its synthetic same-session callback bridge is offline verified. Official SDK tools
remain denied and approval_events=false until live interception compatibility is
established on a supported sandbox host. See [Claude adapter](CLAUDE_ADAPTER.md).

## Phase 15 Gemini provider

`aipc.adapters.gemini` adds bounded ACP streams, exact owned lifecycle journals
and GeminiNormalizer without core/schema changes. Production launch/tools/approvals
remain blocked pending effective configuration/sandbox and live DENY qualification;
offline protocol proof cannot enable native authority. See [Gemini adapter](GEMINI_ADAPTER.md).

## Phase 22 Plugin / Super-App Extension Model

Phase 22 formalizes the predictable provider extension model under `aipc.plugins`.
A new provider requires only:
- `adapter`: Implements `AgentAdapter` protocol.
- `normalizer`: Implements `AgentResultNormalizer` protocol.
- `capabilities`: Declared `AgentCapabilities`.
- `qualification`: `ProviderCapabilityQualification`.
- `manifest`: `ProviderPluginManifest` with configuration schema.
- Optional `telemetry_extractor`: Implements `TelemetryExtractor`.
- Optional `mcp_tools`: Tuple of `(ToolDescriptor, ToolHandler)` pairs.

Registering a plugin via `PluginRegistry.register` verifies all contracts and probe normalization without requiring edits to `phase_manager.py`, `phase_gate.py`, `state_manager.py`, or the canonical `AgentResult` schema. See [Plugin Extension Model](PLUGIN_EXTENSION_MODEL.md).
