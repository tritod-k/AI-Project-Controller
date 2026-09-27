# Phase 04 normalization design

Owner's attached Phase 04 request and MASTER_PLAN.md Phase 04 are authoritative.
Base: f6e58dfafde178a0044ce9e859bff2a9e7055288 (actual tag target verified).
The owner authorizes continuous implementation after the scope summary.

Add a provider-neutral AgentResultNormalizer Protocol and NormalizationContext with
task_id, agent_id, provider, explicit nullable started_at/completed_at/raw_output_ref.
Identity comes from validated context and must match the raw handle; no guessed IDs,
timestamps, counts, files or commands. Shared validate_agent_result(candidate) wraps
canonical validation in a typed error and returns only an AgentResult. No routing.

MockNormalizer lives in adapters/mock/normalizer.py. Require a RawAgentResult,
validated handle/context, dictionary payload, nonblank mock_status/mock_run and matching
run identity. Explicitly map done, task_failed, permission_denied, cancelled, timed_out.
Check envelope lifecycle consistency. SUCCESS requires nested message; task_failed
requires mock_error; approval terminals require matching APPROVE/DENY. Unknown extra
fields are ignored, never copied into metadata. Known fields cannot be coerced or guessed.
Permission-needed/waiting approval is explicitly non-terminal and rejected; approval
normalization/router policy are outside this slice. Terminal approval outcomes normalize.

The Phase 03 timeout raises AgentTimeoutError rather than returning raw. Preserve it:
normalize_timeout(run, error, context) requires the observed typed error and maps an
explicit mock timeout descriptor through the same validation boundary. No retry engine.
Synthetic timeout raw fixtures also exercise the explicit timed_out mapping.

Raw diagnostics are caller-retained and referenced with raw_output_ref. No storage
framework, log output, Evidence Collector or raw payload embedding. Canonical unknown
tests/times remain null; no observed changes/evidence use empty lists/null references.
Only mock completion claims true; other terminal statuses false.

Tests: real mock success/failure/cancel/malformed/approval/timeout paths; strict raw
shape/status/ID/context/timestamp failures; canonical validation and defensive output;
unknown extras; actual referenced diagnostic fixture. Five JSON test-only formats map
through separate fixture normalizers into semantically equivalent canonical results.
AST dependency checks plus runtime canonical consumption demonstrate no raw dependency
in core project modules. No production fake providers or normalizer registry.
No canonical schema/model changes, new dependencies or Phase 05 functionality.
