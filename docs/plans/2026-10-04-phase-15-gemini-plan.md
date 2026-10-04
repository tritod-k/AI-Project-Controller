# Phase 15 execution plan

1. RED tests for strict raw/config/status boundaries. Implement typed config,
   operational errors, raw projection, normalizer, guarded readiness and journal.
2. RED lifecycle tests. Implement exact workspace/task ownership, idempotent start,
   terminal replay, explicit load/continuation, cancel confirmation and denial.
3. RED ACP wire tests. Implement bounded JSON-RPC, session updates, permission and
   proxy rejection, stop reasons and malformed/unknown identity rejection. Run
   shared adapter and canonical/evidence integration tests using offline fixtures.
4. Write provider/evidence/state docs. One fresh read-only whole-branch review;
   reproduce substantive findings RED then fix. Freeze input hashes. Run one full
   regression and one explicit component selection, fast checks, wheel/base import.
   Record immutable receipts. One attempt commit; no merge/PASS tag/Phase 16.

Self-review: production launch gating deliberately excludes unproven coding/tools;
fake permission denial is not live proof. Fresh terminal replay and active ambiguity
are distinct. Connector injection is trusted test/application code, not config.
