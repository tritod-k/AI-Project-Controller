# Phase 15 Gemini adapter design

User authority: Phase 15 implementation blueprint, optimized verification mode.
Baseline: aipc-phase-14-pass / a895b03fae2b322fa550469008fee7ada07f5ebb.

Provider-local GeminiAdapter implements AgentAdapter; GeminiNormalizer alone emits
canonical results. A bounded stdio ACP peer handles initialize, new/load session,
prompt, updates and cancel without adding provider branches to core. A trusted
stream connector is injectable for offline fixtures. No raw Google agent loop.

Production CLI execution is fail-closed until effective settings, sandbox and
permission behavior can be independently qualified. CLI and auth are absent on
this host. Production capabilities are false, permission replies always cancelled,
filesystem/terminal RPCs denied. No approval bridge is advertised: v0.61.0's
permission payload lacks exact tool arguments. This restriction cannot be lifted
by a config boolean or an injected native policy. The ACP peer is a protocol
implementation, not proof of real-runtime safety.

Private atomic journals persist start/prompt/cancel intents, exact task/config,
workspace/repository identities, session and turn ownership, bounded redacted
terminal projections. Ambiguous active fresh-process journals never redispatch.
Explicit successful-turn continuation loads the exact session; repair attempts
start fresh. Cancel notification is not terminal confirmation. Missing test/command
receipts stay absent. Operational failures never become an implementation verdict.

No source changes outside the Gemini package; shared contract factory gains Gemini.
Core, canonical schemas and previous providers remain unchanged. Live execution
and broader concurrency/DLP/cryptographic origin guarantees remain unverified.
