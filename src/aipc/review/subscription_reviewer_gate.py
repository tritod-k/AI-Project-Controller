"""Workstream B02 — Independent Subscription Reviewer Gate.

Integrates qualified subscription-authenticated reviewer transports (preferred:
`openai_codex` in `read_only` sandbox; policy-gated fallback: `google_antigravity`
in `--mode plan --sandbox`) with `RoleBasedModelRegistry`, `DeterministicReviewer`,
`ConsensusEngine`, and `PhaseGate`.

Enforces:
- 100% Subscription-Only runtime rules (`assert_subscription_only_mode`).
- Read-only candidate workspace isolation (pre/post SHA-256 snapshot verification).
- Fresh reviewer session and context separation (no shared worker/planner session
  or inherited chat history).
- Exact 40-character candidate commit SHA binding and collected git/test evidence.
- Cross-provider review by default (`prefer_cross_provider`), explicit
  `reduced_independence` labeling on isolated same-provider fallback, and
  `HUMAN_REVIEW_REQUIRED` escalation on high-risk same-provider fallback.
- Fail-closed behavior on stale SHA, missing evidence, mutable session, self-review,
  unauthorized same-provider review, workspace mutation, timeout, quota exhaustion,
  or malformed output.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Literal, Self
from uuid import uuid4

from pydantic import Field, StrictBool, StrictInt, model_validator

from aipc.agents.role_registry import (
    ModelTier,
    ReviewerIndependencePreference,
    RoleBasedModelRegistry,
    RoleModelBinding,
    RoleSelectionRequest,
)
from aipc.domain.enums import ReviewVerdict
from aipc.domain.models import CanonicalRecord, CriterionResult, NonBlank, ReviewResult
from aipc.hardening.env_isolation import (
    assert_subscription_only_mode,
    build_sanitized_child_env,
)
from aipc.hardening.secrets import assert_no_secrets
from aipc.review.consensus import (
    ConsensusDecision,
    ConsensusEngine,
    ConsensusMode,
    ConsensusPolicy,
    MemberReviewRecord,
    ReviewerSlot,
)
from aipc.review.context import checked_context, context_fingerprint, digest, review_provenance
from aipc.review.errors import PhaseGateError, ReviewContextError
from aipc.review.gate import PhaseGate
from aipc.review.llm.models import LLMReviewerConfig
from aipc.review.llm.subscription_transports import (
    LIVE_REVIEW_UNVERIFIED,
    AntigravitySubscriptionReviewerTransport,
    CodexSubscriptionReviewerTransport,
    IndependencePolicy,
    SubscriptionIndependentReviewer,
    SubscriptionReviewerConfig,
    snapshot_candidate_workspace,
)
from aipc.review.llm.transport import LLMReviewerTransport
from aipc.review.models import BoundReview, Digest, PhaseGateDecision, ReviewContext
from aipc.review.reviewer import DeterministicReviewer, bind_review
from aipc.storage.json_store import write_json_atomic
from aipc.version.models import Commit

ReviewerGateOutcome = Literal[
    "PASS",
    "REVISE",
    "BLOCKED",
    "WAITING_PROVIDER",
    "HUMAN_REVIEW_REQUIRED",
]

ReviewerRejectionCode = Literal[
    "MISSING_OR_INVALID_EVIDENCE",
    "STALE_COMMIT_SHA",
    "STALE_REVIEW_RECEIPT_SHA",
    "REVIEWER_UNAVAILABLE",
    "SELF_REVIEW_FORBIDDEN",
    "SHARED_SESSION_REJECTED",
    "INHERITED_CONTEXT_REJECTED",
    "MUTABLE_REVIEW_SESSION_REJECTED",
    "UNAUTHORIZED_SAME_PROVIDER_REVIEW",
    "HIGH_RISK_CROSS_PROVIDER_REQUIRED",
    "REVIEWER_MUTATED_CANDIDATE_WORKSPACE",
    "REVIEWER_QUOTA_OR_UNAVAILABLE",
    "REVIEWER_TIMEOUT",
    "REVIEWER_MALFORMED_OUTPUT",
    "PHASE_GATE_REJECTED",
]


def _hash_snapshot(snapshot: dict[str, str]) -> Digest:
    encoded = json.dumps(snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _verify_git_worktree_head(worktree: Path, expected_sha: str) -> str | None:
    """If worktree is inside a git repo, verify it is a worktree root at expected_sha and clean."""
    resolved_wt = worktree.resolve()
    git_marker = resolved_wt / ".git"
    env = build_sanitized_child_env()
    top_res = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=str(resolved_wt),
        env=env,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        check=False,
    )
    if top_res.returncode != 0:
        if git_marker.exists():
            return "Candidate git worktree is corrupt or unreadable."
        return None

    toplevel = Path((top_res.stdout or "").strip()).resolve()
    if toplevel != resolved_wt and not git_marker.exists():
        return (
            f"Candidate worktree '{resolved_wt}' is a subdirectory of git repository "
            f"'{toplevel}', not an isolated git worktree root."
        )

    head_res = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(resolved_wt),
        env=env,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        check=False,
    )
    actual_head = (head_res.stdout or "").strip().lower()
    if head_res.returncode != 0 or actual_head != expected_sha.lower():
        return (
            f"Candidate git worktree HEAD '{actual_head}' does not match "
            f"expected commit SHA '{expected_sha}'."
        )
    status_res = subprocess.run(
        ["git", "status", "--porcelain", "-uall"],
        cwd=str(resolved_wt),
        env=env,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        check=False,
    )
    if status_res.returncode != 0 or (status_res.stdout or "").strip():
        return "Candidate git worktree has uncommitted dirty modifications prior to review."
    return None


def sanitize_text(value: str) -> str:
    cleaned = " ".join(value.strip().split())
    return cleaned or "unspecified"


def _normalize_family(provider_or_family: str) -> str:
    lowered = provider_or_family.strip().lower()
    if lowered in ("openai", "openai_codex", "codex", "codex-subscription"):
        return "openai"
    if lowered in (
        "google",
        "google_antigravity",
        "gemini",
        "gemini-antigravity",
        "antigravity",
    ):
        return "google"
    return lowered


class SubscriptionReviewerGateConfig(CanonicalRecord):
    """Configuration for the Milestone B02 Independent Subscription Reviewer Gate."""

    operating_profile: Literal["subscription_only"] = "subscription_only"
    candidate_worktree: NonBlank
    evidence_output_dir: NonBlank
    preferred_reviewer_tiers: tuple[ModelTier, ...] = ("review_strict", "reasoning_high")
    independence_policy: ReviewerIndependencePreference = "prefer_cross_provider"
    high_risk_change: StrictBool = False
    require_cross_provider_for_high_risk: StrictBool = True
    require_deterministic_objective_check: StrictBool = True
    reviewer_authority: NonBlank = "milestone-b-independent-subscription-reviewer"
    timeout_seconds: StrictInt = Field(default=60, ge=1, le=300)
    max_prompt_bytes: StrictInt = Field(default=65536, ge=1024, le=524288)
    max_output_bytes: StrictInt = Field(default=65536, ge=1024, le=524288)
    allow_api_key_fallback: StrictBool = False

    @model_validator(mode="after")
    def validate_config(self) -> Self:
        if self.allow_api_key_fallback:
            raise ValueError("Paid API key fallback is forbidden in subscription_only profile")
        assert_subscription_only_mode(
            "codex_subscription",
            component="SubscriptionReviewerGateConfig",
            allow_api_key_fallback=self.allow_api_key_fallback,
        )
        assert_no_secrets(self.model_dump(mode="json"))
        return self


class SessionIsolationAuditReceipt(CanonicalRecord):
    """Audited proof of reviewer session isolation, context separation, and independence."""

    audit_id: NonBlank
    task_id: NonBlank
    candidate_commit_sha: Commit
    worker_agent_id: NonBlank
    worker_provider_family: NonBlank
    worker_session_id: str | None = None
    planner_provider_family: str | None = None
    planner_session_id: str | None = None
    reviewer_binding_id: NonBlank
    reviewer_provider_id: NonBlank
    reviewer_provider_family: NonBlank
    reviewer_model_id: NonBlank
    reviewer_session_id: NonBlank
    fresh_isolated_session: StrictBool
    inherited_chat_history: StrictBool = False
    read_only_sandbox_enforced: StrictBool
    workspace_snapshot_before_sha256: Digest
    workspace_snapshot_after_sha256: Digest
    workspace_mutated: StrictBool
    independence_level: Literal["cross_provider", "reduced_independence", "rejected"]
    reduced_independence: StrictBool
    high_risk_escalation_required: StrictBool = False
    rejection_code: ReviewerRejectionCode | None = None
    rejection_reason: str | None = None

    @model_validator(mode="after")
    def validate_receipt(self) -> Self:
        assert_no_secrets(self.model_dump(mode="json"))
        return self


class SubscriptionReviewerGateDecision(CanonicalRecord):
    """Canonical decision and audit package produced by the B02 Reviewer Gate."""

    gate_outcome: ReviewerGateOutcome
    task_id: NonBlank
    candidate_commit_sha: Commit
    reviewed_commit_sha: Commit | None = None
    review_result: ReviewResult
    bound_review: BoundReview | None = None
    phase_gate_decision: PhaseGateDecision | None = None
    consensus_decision: ConsensusDecision | None = None
    session_audit: SessionIsolationAuditReceipt
    receipt_path: NonBlank
    reasons: tuple[NonBlank, ...] = ()

    @model_validator(mode="after")
    def validate_decision(self) -> Self:
        if self.gate_outcome == "PASS":
            if self.bound_review is None or self.phase_gate_decision is None:
                raise ValueError("PASS gate_outcome requires bound_review and phase_gate_decision")
            if self.phase_gate_decision.verdict != "PASS":
                raise ValueError("PASS gate_outcome requires PhaseGateDecision.verdict == 'PASS'")
            if (
                self.session_audit.workspace_mutated
                or not self.session_audit.fresh_isolated_session
            ):
                raise ValueError("PASS gate_outcome forbids workspace mutation or shared session")
            if self.session_audit.high_risk_escalation_required:
                raise ValueError(
                    "High-risk same-provider review requires HUMAN_REVIEW_REQUIRED escalation"
                )
        assert_no_secrets(self.model_dump(mode="json"))
        return self


TransportFactoryCallable = Callable[
    [RoleModelBinding, SubscriptionReviewerConfig],
    LLMReviewerTransport,
]


def default_subscription_reviewer_transport_factory(
    binding: RoleModelBinding,
    reviewer_config: SubscriptionReviewerConfig,
) -> LLMReviewerTransport:
    """Build the default read-only subscription reviewer transport for a qualified binding."""
    family = _normalize_family(binding.provider_family)
    if family == "openai":
        return CodexSubscriptionReviewerTransport(reviewer_config)
    if family == "google":
        return AntigravitySubscriptionReviewerTransport(reviewer_config)
    raise ValueError(
        f"Unsupported subscription reviewer provider family: '{binding.provider_family}'"
    )


class IndependentSubscriptionReviewerGate:
    """Milestone B02 Independent Subscription Reviewer Gate with PhaseGate & Consensus."""

    def __init__(
        self,
        *,
        config: SubscriptionReviewerGateConfig,
        role_registry: RoleBasedModelRegistry,
        phase_gate: PhaseGate | None = None,
        deterministic_reviewer: DeterministicReviewer | None = None,
        transport_factory: TransportFactoryCallable | None = None,
    ) -> None:
        self.config = SubscriptionReviewerGateConfig.model_validate(config)
        self.role_registry = role_registry
        self.phase_gate = phase_gate or PhaseGate()
        self.deterministic_reviewer = deterministic_reviewer or DeterministicReviewer()
        self.transport_factory = (
            transport_factory or default_subscription_reviewer_transport_factory
        )

    def evaluate(
        self,
        context: ReviewContext,
        *,
        expected_candidate_sha: str | None = None,
        worker_agent_id: str | None = None,
        worker_provider_family: str | None = None,
        worker_session_id: str | None = None,
        planner_provider_family: str | None = None,
        planner_session_id: str | None = None,
        proposed_reviewer_session_id: str | None = None,
        inherited_chat_history: bool = False,
        read_only_sandbox: bool = True,
        attempted_binding_ids: tuple[str, ...] = (),
        explicit_binding_id: str | None = None,
    ) -> SubscriptionReviewerGateDecision:
        """Execute independent subscription review, consensus check, and PhaseGate evaluation."""
        c_root = Path(self.config.candidate_worktree).resolve()
        before_snap = snapshot_candidate_workspace(c_root)
        before_hash = _hash_snapshot(before_snap)

        task_id = context.task.task_id
        candidate_sha = context.checkpoint.git_commit
        worker_id = worker_agent_id or context.agent_result.agent_id
        effective_worker_family = _normalize_family(
            worker_provider_family
            or str(context.agent_result.metadata.get("provider_family") or "")
            or str(context.agent_result.provider or "google")
        )
        effective_worker_session = worker_session_id or (
            str(context.agent_result.metadata.get("session_id"))
            if context.agent_result.metadata.get("session_id")
            else None
        )
        effective_planner_family = (
            _normalize_family(planner_provider_family)
            if planner_provider_family
            else (
                _normalize_family(str(context.task.metadata.get("planner_provider_family")))
                if context.task.metadata.get("planner_provider_family")
                else None
            )
        )
        effective_planner_session = planner_session_id or (
            str(context.task.metadata.get("planner_session_id"))
            if context.task.metadata.get("planner_session_id")
            else None
        )

        # 1. Validate ReviewContext, collected evidence, and exact candidate commit SHA binding
        try:
            checked_ctx, evidence_bytes = checked_context(context)
        except (ValueError, ReviewContextError) as exc:
            return self._fail_closed(
                context=context,
                candidate_sha=candidate_sha,
                worker_id=worker_id,
                worker_family=effective_worker_family,
                worker_session=effective_worker_session,
                planner_family=effective_planner_family,
                planner_session=effective_planner_session,
                binding=None,
                reviewer_session_id=proposed_reviewer_session_id or f"rev-sess-{task_id}-rejected",
                before_hash=before_hash,
                after_hash=before_hash,
                read_only_sandbox=read_only_sandbox,
                inherited_chat_history=inherited_chat_history,
                gate_outcome="BLOCKED",
                rejection_code="MISSING_OR_INVALID_EVIDENCE",
                reason=f"Review context or evidence invalid: {sanitize_text(str(exc))}",
            )

        if (
            checked_ctx.evidence.git.status != "collected"
            or checked_ctx.evidence.tests.parse_status != "collected"
        ):
            return self._fail_closed(
                context=checked_ctx,
                candidate_sha=candidate_sha,
                worker_id=worker_id,
                worker_family=effective_worker_family,
                worker_session=effective_worker_session,
                planner_family=effective_planner_family,
                planner_session=effective_planner_session,
                binding=None,
                reviewer_session_id=proposed_reviewer_session_id or f"rev-sess-{task_id}-rejected",
                before_hash=before_hash,
                after_hash=before_hash,
                read_only_sandbox=read_only_sandbox,
                inherited_chat_history=inherited_chat_history,
                gate_outcome="BLOCKED",
                rejection_code="MISSING_OR_INVALID_EVIDENCE",
                reason=(
                    "Independent review requires collected git and test evidence "
                    f"(git={checked_ctx.evidence.git.status}, "
                    f"tests={checked_ctx.evidence.tests.parse_status})."
                ),
            )

        git_head_error = _verify_git_worktree_head(c_root, candidate_sha)
        if (
            checked_ctx.evidence.git.head != candidate_sha
            or (expected_candidate_sha is not None and expected_candidate_sha != candidate_sha)
            or git_head_error is not None
        ):
            mismatch_detail = git_head_error or (
                f"checkpoint SHA '{candidate_sha}', git evidence head "
                f"'{checked_ctx.evidence.git.head}', expected '{expected_candidate_sha}'"
            )
            return self._fail_closed(
                context=checked_ctx,
                candidate_sha=candidate_sha,
                worker_id=worker_id,
                worker_family=effective_worker_family,
                worker_session=effective_worker_session,
                planner_family=effective_planner_family,
                planner_session=effective_planner_session,
                binding=None,
                reviewer_session_id=proposed_reviewer_session_id or f"rev-sess-{task_id}-rejected",
                before_hash=before_hash,
                after_hash=before_hash,
                read_only_sandbox=read_only_sandbox,
                inherited_chat_history=inherited_chat_history,
                gate_outcome="BLOCKED",
                rejection_code="STALE_COMMIT_SHA",
                reason=f"Stale or mismatched candidate commit SHA rejected: {mismatch_detail}",
            )

        # 2. Resolve qualified subscription Reviewer binding via RoleBasedModelRegistry
        role_decision = self.role_registry.resolve(
            RoleSelectionRequest(
                role="reviewer",
                preferred_tiers=self.config.preferred_reviewer_tiers,
                worker_provider_family=effective_worker_family,
                reviewer_independence_policy=self.config.independence_policy,
                attempted_binding_ids=attempted_binding_ids,
                explicit_binding_id=explicit_binding_id,
            )
        )
        if role_decision.outcome != "SELECTED" or role_decision.selected_binding is None:
            same_provider_rejected = any(
                r.code == "SAME_PROVIDER_REJECTED" for r in role_decision.rejected_bindings
            )
            rej_code: ReviewerRejectionCode = (
                "UNAUTHORIZED_SAME_PROVIDER_REVIEW"
                if same_provider_rejected
                else "REVIEWER_UNAVAILABLE"
            )
            outcome: ReviewerGateOutcome = (
                "BLOCKED" if same_provider_rejected else "WAITING_PROVIDER"
            )
            detail = (
                "; ".join(f"{r.binding_id}:{r.code}" for r in role_decision.rejected_bindings)
                or "No eligible subscription reviewer binding registered"
            )
            return self._fail_closed(
                context=checked_ctx,
                candidate_sha=candidate_sha,
                worker_id=worker_id,
                worker_family=effective_worker_family,
                worker_session=effective_worker_session,
                planner_family=effective_planner_family,
                planner_session=effective_planner_session,
                binding=None,
                reviewer_session_id=proposed_reviewer_session_id or f"rev-sess-{task_id}-unavail",
                before_hash=before_hash,
                after_hash=before_hash,
                read_only_sandbox=read_only_sandbox,
                inherited_chat_history=inherited_chat_history,
                gate_outcome=outcome,
                rejection_code=rej_code,
                reason=f"No qualified subscription reviewer available ({detail}).",
            )

        binding = role_decision.selected_binding
        reviewer_family = _normalize_family(binding.provider_family)
        att_num = checked_ctx.task.attempt
        fresh_token = uuid4().hex[:8]
        default_sess = (
            f"rev-sess-{binding.provider_id}-{task_id}-att-{att_num:02d}-"
            f"{candidate_sha[:8]}-{fresh_token}"
        )
        reviewer_session_id = proposed_reviewer_session_id or default_sess

        # 3. Enforce Session Isolation, Self-Review Prohibition, and Read-Only Sandbox
        if binding.binding_id == worker_id or binding.provider_id == worker_id:
            return self._fail_closed(
                context=checked_ctx,
                candidate_sha=candidate_sha,
                worker_id=worker_id,
                worker_family=effective_worker_family,
                worker_session=effective_worker_session,
                planner_family=effective_planner_family,
                planner_session=effective_planner_session,
                binding=binding,
                reviewer_session_id=reviewer_session_id,
                before_hash=before_hash,
                after_hash=before_hash,
                read_only_sandbox=read_only_sandbox,
                inherited_chat_history=inherited_chat_history,
                gate_outcome="BLOCKED",
                rejection_code="SELF_REVIEW_FORBIDDEN",
                reason=f"Worker identity '{worker_id}' cannot act as its own reviewer.",
            )

        if (effective_worker_session and reviewer_session_id == effective_worker_session) or (
            effective_planner_session and reviewer_session_id == effective_planner_session
        ):
            return self._fail_closed(
                context=checked_ctx,
                candidate_sha=candidate_sha,
                worker_id=worker_id,
                worker_family=effective_worker_family,
                worker_session=effective_worker_session,
                planner_family=effective_planner_family,
                planner_session=effective_planner_session,
                binding=binding,
                reviewer_session_id=reviewer_session_id,
                before_hash=before_hash,
                after_hash=before_hash,
                read_only_sandbox=read_only_sandbox,
                inherited_chat_history=inherited_chat_history,
                gate_outcome="BLOCKED",
                rejection_code="SHARED_SESSION_REJECTED",
                reason=(
                    f"Reviewer session '{reviewer_session_id}' collides with worker or "
                    "planner session; fresh isolated reviewer session is mandatory."
                ),
            )

        if inherited_chat_history:
            return self._fail_closed(
                context=checked_ctx,
                candidate_sha=candidate_sha,
                worker_id=worker_id,
                worker_family=effective_worker_family,
                worker_session=effective_worker_session,
                planner_family=effective_planner_family,
                planner_session=effective_planner_session,
                binding=binding,
                reviewer_session_id=reviewer_session_id,
                before_hash=before_hash,
                after_hash=before_hash,
                read_only_sandbox=read_only_sandbox,
                inherited_chat_history=True,
                gate_outcome="BLOCKED",
                rejection_code="INHERITED_CONTEXT_REJECTED",
                reason="Reviewer session must not inherit worker or planner chat history.",
            )

        if not read_only_sandbox:
            return self._fail_closed(
                context=checked_ctx,
                candidate_sha=candidate_sha,
                worker_id=worker_id,
                worker_family=effective_worker_family,
                worker_session=effective_worker_session,
                planner_family=effective_planner_family,
                planner_session=effective_planner_session,
                binding=binding,
                reviewer_session_id=reviewer_session_id,
                before_hash=before_hash,
                after_hash=before_hash,
                read_only_sandbox=False,
                inherited_chat_history=inherited_chat_history,
                gate_outcome="BLOCKED",
                rejection_code="MUTABLE_REVIEW_SESSION_REJECTED",
                reason="Mutable reviewer session rejected; read-only sandbox is mandatory.",
            )

        is_same_as_worker = reviewer_family == effective_worker_family
        is_reduced_independence = is_same_as_worker
        if is_reduced_independence and self.config.independence_policy == "cross_provider_required":
            return self._fail_closed(
                context=checked_ctx,
                candidate_sha=candidate_sha,
                worker_id=worker_id,
                worker_family=effective_worker_family,
                worker_session=effective_worker_session,
                planner_family=effective_planner_family,
                planner_session=effective_planner_session,
                binding=binding,
                reviewer_session_id=reviewer_session_id,
                before_hash=before_hash,
                after_hash=before_hash,
                read_only_sandbox=read_only_sandbox,
                inherited_chat_history=inherited_chat_history,
                gate_outcome="BLOCKED",
                rejection_code="UNAUTHORIZED_SAME_PROVIDER_REVIEW",
                reason=(
                    f"Reviewer family '{reviewer_family}' matches worker family under "
                    "cross_provider_required policy."
                ),
            )

        # 4. Execute SubscriptionIndependentReviewer in read-only sandbox
        llm_provider = "openai" if reviewer_family == "openai" else "gemini"
        auth_mode_enum = (
            "codex_subscription" if reviewer_family == "openai" else "antigravity_subscription"
        )
        transport_indep_policy: IndependencePolicy = (
            "cross_provider_required"
            if self.config.independence_policy == "cross_provider_required"
            else "allow_isolated_same_provider_fallback"
        )
        worker_sessions = tuple(
            s for s in (effective_worker_session, effective_planner_session) if s is not None
        )
        llm_cfg = LLMReviewerConfig(
            reviewer_id=binding.binding_id,
            provider=llm_provider,
            model=binding.model_id,
            timeout_seconds=float(self.config.timeout_seconds),
            max_request_bytes=self.config.max_prompt_bytes,
        )
        sub_cfg = SubscriptionReviewerConfig(
            llm_config=llm_cfg,
            auth_mode=auth_mode_enum,
            independence_policy=transport_indep_policy,
            expected_candidate_sha=candidate_sha,
            candidate_workspace=str(c_root),
            worker_session_ids=worker_sessions,
            allow_api_key_fallback=False,
            high_risk_change=self.config.high_risk_change,
        )
        out_dir = Path(self.config.evidence_output_dir).resolve()
        ev_root = Path(checked_ctx.evidence_root).resolve()
        if out_dir == ev_root or out_dir.is_relative_to(ev_root) or ev_root.is_relative_to(out_dir):
            audit_base = ev_root.parent / f"{ev_root.name}_b02_llm_audit"
        else:
            audit_base = out_dir / "llm_reviews"
        audit_root = audit_base / reviewer_session_id
        transport = self.transport_factory(binding, sub_cfg)
        sub_reviewer = SubscriptionIndependentReviewer(
            sub_cfg,
            transport,
            audit_root=audit_root,
        )
        semantic_review = sub_reviewer.review(checked_ctx)

        after_snap = snapshot_candidate_workspace(c_root)
        after_hash = _hash_snapshot(after_snap)
        workspace_mutated = before_hash != after_hash

        if (
            workspace_mutated
            or semantic_review.metadata.get("error_code") == "REVIEWER_MUTATED_CANDIDATE_WORKSPACE"
        ):
            return self._fail_closed(
                context=checked_ctx,
                candidate_sha=candidate_sha,
                worker_id=worker_id,
                worker_family=effective_worker_family,
                worker_session=effective_worker_session,
                planner_family=effective_planner_family,
                planner_session=effective_planner_session,
                binding=binding,
                reviewer_session_id=reviewer_session_id,
                before_hash=before_hash,
                after_hash=after_hash,
                read_only_sandbox=True,
                inherited_chat_history=False,
                gate_outcome="BLOCKED",
                rejection_code="REVIEWER_MUTATED_CANDIDATE_WORKSPACE",
                reason="Reviewer mutated files in the candidate worktree; review rejected.",
                workspace_mutated=True,
            )

        receipt_sha = semantic_review.metadata.get("candidate_commit_sha")
        if receipt_sha is not None and str(receipt_sha) != candidate_sha:
            return self._fail_closed(
                context=checked_ctx,
                candidate_sha=candidate_sha,
                worker_id=worker_id,
                worker_family=effective_worker_family,
                worker_session=effective_worker_session,
                planner_family=effective_planner_family,
                planner_session=effective_planner_session,
                binding=binding,
                reviewer_session_id=reviewer_session_id,
                before_hash=before_hash,
                after_hash=after_hash,
                read_only_sandbox=True,
                inherited_chat_history=False,
                gate_outcome="BLOCKED",
                rejection_code="STALE_REVIEW_RECEIPT_SHA",
                reason=(
                    f"Reviewer receipt SHA '{receipt_sha}' does not match "
                    f"candidate commit SHA '{candidate_sha}'."
                ),
            )

        # 5. Check fail-closed reviewer infrastructure / quota / timeout / malformed output errors
        error_code_str = str(semantic_review.metadata.get("error_code") or "")
        live_status = str(semantic_review.metadata.get("live_review_status") or "VERIFIED")
        if semantic_review.verdict == "ERROR" or live_status == LIVE_REVIEW_UNVERIFIED:
            if error_code_str in ("timeout", "REVIEWER_TIMEOUT"):
                mapped_code: ReviewerRejectionCode = "REVIEWER_TIMEOUT"
                mapped_outcome: ReviewerGateOutcome = "WAITING_PROVIDER"
            elif error_code_str in (
                "rate_limit",
                "auth",
                "sdk_missing",
                "service",
                "connection",
                "REVIEWER_QUOTA_OR_UNAVAILABLE",
            ):
                mapped_code = "REVIEWER_QUOTA_OR_UNAVAILABLE"
                mapped_outcome = "WAITING_PROVIDER"
            else:
                mapped_code = "REVIEWER_MALFORMED_OUTPUT"
                mapped_outcome = "BLOCKED"
            return self._fail_closed(
                context=checked_ctx,
                candidate_sha=candidate_sha,
                worker_id=worker_id,
                worker_family=effective_worker_family,
                worker_session=effective_worker_session,
                planner_family=effective_planner_family,
                planner_session=effective_planner_session,
                binding=binding,
                reviewer_session_id=reviewer_session_id,
                before_hash=before_hash,
                after_hash=after_hash,
                read_only_sandbox=True,
                inherited_chat_history=False,
                gate_outcome=mapped_outcome,
                rejection_code=mapped_code,
                reason="; ".join(semantic_review.risks)
                or f"Subscription reviewer failed closed ({error_code_str or 'ERROR'}).",
                existing_review=semantic_review,
            )

        # 6. Combine semantic subscription review with DeterministicReviewer via ConsensusEngine
        consensus_decision: ConsensusDecision | None = None
        if self.config.require_deterministic_objective_check:
            det_review = self._build_objective_companion_review(checked_ctx, semantic_review)
            policy = ConsensusPolicy(
                policy_id="b02-subscription-plus-deterministic",
                mode=ConsensusMode.PRIMARY_WITH_VETO,
                slots=(
                    ReviewerSlot(reviewer_id=binding.binding_id, role="primary"),
                    ReviewerSlot(reviewer_id="deterministic-objective-reviewer", role="secondary"),
                ),
            )
            member_records = (
                MemberReviewRecord(
                    reviewer_id=binding.binding_id,
                    role="primary",
                    review=semantic_review,
                    review_digest=digest(semantic_review),
                ),
                MemberReviewRecord(
                    reviewer_id="deterministic-objective-reviewer",
                    role="secondary",
                    review=det_review,
                    review_digest=digest(det_review),
                ),
            )
            aggregated_review, consensus_decision = ConsensusEngine.aggregate(
                policy,
                checked_ctx,
                evidence_bytes,
                member_records,
            )
        else:
            aggregated_review = semantic_review

        indep_label: Literal["cross_provider", "reduced_independence"] = (
            "reduced_independence" if is_reduced_independence else "cross_provider"
        )
        high_risk_escalation = bool(
            is_reduced_independence
            and self.config.high_risk_change
            and self.config.require_cross_provider_for_high_risk
        )

        merged_meta = dict(semantic_review.metadata)
        merged_meta.update(aggregated_review.metadata)
        merged_meta.update(
            {
                "candidate_commit_sha": candidate_sha,
                "reviewer_binding_id": binding.binding_id,
                "reviewer_provider_id": binding.provider_id,
                "reviewer_provider_family": reviewer_family,
                "reviewer_model_requested": binding.model_id,
                "reviewer_session_id": reviewer_session_id,
                "independence_level": indep_label,
                "reduced_independence": is_reduced_independence,
                "high_risk_escalation_required": high_risk_escalation,
                "live_review_status": "VERIFIED",
            }
        )
        risks_list = list(aggregated_review.risks)
        if is_reduced_independence:
            note = (
                "REDUCED_INDEPENDENCE: same-provider reviewer executed in a fresh, "
                "read-only isolated session."
            )
            if note not in risks_list:
                risks_list.append(note)
        if high_risk_escalation:
            esc_note = (
                "HIGH_RISK_CROSS_PROVIDER_REQUIRED: high-risk change reviewed under "
                "reduced_independence fallback requires explicit human review."
            )
            if esc_note not in risks_list:
                risks_list.append(esc_note)

        final_review = ReviewResult.model_validate(
            aggregated_review.model_copy(
                update={
                    "verdict": ReviewVerdict(aggregated_review.verdict),
                    "metadata": merged_meta,
                    "risks": risks_list,
                }
            )
        )

        session_audit = SessionIsolationAuditReceipt(
            audit_id=f"aud-{task_id}-att-{checked_ctx.task.attempt:02d}-{candidate_sha[:8]}",
            task_id=task_id,
            candidate_commit_sha=candidate_sha,
            worker_agent_id=worker_id,
            worker_provider_family=effective_worker_family,
            worker_session_id=effective_worker_session,
            planner_provider_family=effective_planner_family,
            planner_session_id=effective_planner_session,
            reviewer_binding_id=binding.binding_id,
            reviewer_provider_id=binding.provider_id,
            reviewer_provider_family=reviewer_family,
            reviewer_model_id=binding.model_id,
            reviewer_session_id=reviewer_session_id,
            fresh_isolated_session=True,
            inherited_chat_history=False,
            read_only_sandbox_enforced=True,
            workspace_snapshot_before_sha256=before_hash,
            workspace_snapshot_after_sha256=after_hash,
            workspace_mutated=False,
            independence_level=indep_label,
            reduced_independence=is_reduced_independence,
            high_risk_escalation_required=high_risk_escalation,
            rejection_code=("HIGH_RISK_CROSS_PROVIDER_REQUIRED" if high_risk_escalation else None),
            rejection_reason=(
                "High-risk change requires cross-provider or explicit human review."
                if high_risk_escalation
                else None
            ),
        )

        # If high-risk change used same-provider fallback, escalate to HUMAN_REVIEW_REQUIRED
        if high_risk_escalation:
            return self._persist_and_return(
                gate_outcome="HUMAN_REVIEW_REQUIRED",
                task_id=task_id,
                attempt=checked_ctx.task.attempt,
                candidate_sha=candidate_sha,
                reviewed_sha=candidate_sha,
                review_result=final_review,
                bound_review=None,
                phase_gate_decision=None,
                consensus_decision=consensus_decision,
                session_audit=session_audit,
                reasons=(
                    "High-risk change reviewed via reduced_independence same-provider fallback; "
                    "escalated to HUMAN_REVIEW_REQUIRED.",
                ),
            )

        # 7. Bind review to exact provenance and evaluate canonical PhaseGate
        gate_ctx = (
            checked_ctx
            if checked_ctx.state.last_review is None
            else checked_ctx.model_copy(
                update={"state": checked_ctx.state.model_copy(update={"last_review": None})}
            )
        )
        if final_review.review_id.startswith("reference-"):
            final_review = final_review.model_copy(
                update={"review_id": "reference-" + context_fingerprint(gate_ctx)}
            )
        try:
            bound = bind_review(
                gate_ctx,
                final_review,
                authority=self.config.reviewer_authority,
                reviewed=review_provenance(gate_ctx),
            )
            gate_dec = self.phase_gate.evaluate(bound, gate_ctx)
        except (ReviewContextError, PhaseGateError) as exc:
            failed_audit = session_audit.model_copy(
                update={
                    "rejection_code": "PHASE_GATE_REJECTED",
                    "rejection_reason": sanitize_text(str(exc)),
                }
            )
            return self._persist_and_return(
                gate_outcome="BLOCKED",
                task_id=task_id,
                attempt=checked_ctx.task.attempt,
                candidate_sha=candidate_sha,
                reviewed_sha=candidate_sha,
                review_result=final_review,
                bound_review=None,
                phase_gate_decision=None,
                consensus_decision=consensus_decision,
                session_audit=failed_audit,
                reasons=(f"PhaseGate rejected review submission: {sanitize_text(str(exc))}",),
            )

        mapped_gate_outcome: ReviewerGateOutcome = (
            "PASS"
            if gate_dec.verdict == "PASS"
            else "REVISE"
            if gate_dec.verdict == "REVISE"
            else "BLOCKED"
        )
        return self._persist_and_return(
            gate_outcome=mapped_gate_outcome,
            task_id=task_id,
            attempt=checked_ctx.task.attempt,
            candidate_sha=candidate_sha,
            reviewed_sha=candidate_sha,
            review_result=final_review,
            bound_review=bound,
            phase_gate_decision=gate_dec,
            consensus_decision=consensus_decision,
            session_audit=session_audit,
            reasons=gate_dec.reasons or (f"PhaseGate evaluated verdict: {gate_dec.verdict}.",),
        )

    def _build_objective_companion_review(
        self,
        context: ReviewContext,
        semantic_review: ReviewResult,
    ) -> ReviewResult:
        """Build objective companion review enforcing context.rules and global checks."""
        det_review = self.deterministic_reviewer.review(context)
        if det_review.verdict in ("BLOCKED", "ERROR"):
            return det_review

        phase = next(p for p in context.project.phases if p.id == context.task.phase_id)
        required_ids = {
            c.id for c in phase.acceptance_criteria if c.required or context.require_optional
        }
        rule_ids = {r.criterion_id for r in context.rules}
        sem_by_id = {c.criterion_id: c for c in semantic_review.criteria}
        merged_criteria: list[CriterionResult] = []
        for det_c in det_review.criteria:
            if det_c.criterion_id in rule_ids:
                merged_criteria.append(det_c)
            elif det_c.criterion_id in sem_by_id:
                merged_criteria.append(sem_by_id[det_c.criterion_id])
            else:
                merged_criteria.append(det_c)

        failed_ids = [c.criterion_id for c in merged_criteria if c.status == "FAIL"]
        required_non_pass = any(
            c.criterion_id in required_ids and c.status != "PASS" for c in merged_criteria
        )
        global_fixes = [
            f
            for f in det_review.required_fixes
            if not any(f.startswith(f"{c.id}:") for c in phase.acceptance_criteria)
        ]
        if failed_ids or required_non_pass or global_fixes:
            fixes = [
                f
                for f in det_review.required_fixes
                if not any(cid not in rule_ids and f.startswith(f"{cid}:") for cid in sem_by_id)
            ]
            for gf in global_fixes:
                if gf not in fixes:
                    fixes.append(gf)
            if not fixes:
                fixes = list(semantic_review.required_fixes) or [
                    "Resolve failing objective or semantic criteria and re-collect evidence."
                ]
            return det_review.model_copy(
                update={
                    "verdict": ReviewVerdict.REVISE,
                    "criteria": merged_criteria,
                    "failed_criteria": failed_ids,
                    "required_fixes": fixes,
                    "next_phase_allowed": False,
                }
            )
        return det_review.model_copy(
            update={
                "verdict": ReviewVerdict.PASS,
                "criteria": merged_criteria,
                "failed_criteria": [],
                "required_fixes": [],
                "next_phase_allowed": True,
            }
        )

    def _fail_closed(
        self,
        *,
        context: ReviewContext,
        candidate_sha: str,
        worker_id: str,
        worker_family: str,
        worker_session: str | None,
        planner_family: str | None,
        planner_session: str | None,
        binding: RoleModelBinding | None,
        reviewer_session_id: str,
        before_hash: str,
        after_hash: str,
        read_only_sandbox: bool,
        inherited_chat_history: bool,
        gate_outcome: ReviewerGateOutcome,
        rejection_code: ReviewerRejectionCode,
        reason: str,
        workspace_mutated: bool = False,
        existing_review: ReviewResult | None = None,
    ) -> SubscriptionReviewerGateDecision:
        clean_reason = sanitize_text(reason)
        phase = next(
            (p for p in context.project.phases if p.id == context.task.phase_id),
            context.project.phases[0],
        )
        if existing_review is not None:
            meta = dict(existing_review.metadata)
            meta.setdefault("error_code", rejection_code)
            meta.setdefault("live_review_status", LIVE_REVIEW_UNVERIFIED)
            review_res = existing_review.model_copy(update={"metadata": meta})
        else:
            review_res = ReviewResult(
                schema_version="1.0",
                review_id=f"b02-failclosed-{context.task.task_id}-{candidate_sha[:8]}",
                task_id=context.task.task_id,
                verdict="ERROR" if gate_outcome == "WAITING_PROVIDER" else "BLOCKED",
                confidence=0.0,
                criteria=[
                    CriterionResult(
                        criterion_id=c.id,
                        status="UNVERIFIED",
                        evidence=[],
                        notes=clean_reason,
                    )
                    for c in phase.acceptance_criteria
                ],
                failed_criteria=[],
                required_fixes=[],
                risks=[clean_reason],
                next_phase_allowed=False,
                metadata={
                    "error_code": rejection_code,
                    "live_review_status": LIVE_REVIEW_UNVERIFIED,
                    "candidate_commit_sha": candidate_sha,
                },
            )

        audit = SessionIsolationAuditReceipt(
            audit_id=f"aud-{context.task.task_id}-att-{context.task.attempt:02d}-{candidate_sha[:8]}",
            task_id=context.task.task_id,
            candidate_commit_sha=candidate_sha,
            worker_agent_id=worker_id,
            worker_provider_family=worker_family,
            worker_session_id=worker_session,
            planner_provider_family=planner_family,
            planner_session_id=planner_session,
            reviewer_binding_id=binding.binding_id if binding is not None else "none",
            reviewer_provider_id=binding.provider_id if binding is not None else "none",
            reviewer_provider_family=(
                _normalize_family(binding.provider_family) if binding is not None else "none"
            ),
            reviewer_model_id=binding.model_id if binding is not None else "none",
            reviewer_session_id=reviewer_session_id,
            fresh_isolated_session=not inherited_chat_history
            and reviewer_session_id not in (worker_session, planner_session),
            inherited_chat_history=inherited_chat_history,
            read_only_sandbox_enforced=read_only_sandbox,
            workspace_snapshot_before_sha256=before_hash,
            workspace_snapshot_after_sha256=after_hash,
            workspace_mutated=workspace_mutated,
            independence_level="rejected",
            reduced_independence=False,
            high_risk_escalation_required=False,
            rejection_code=rejection_code,
            rejection_reason=clean_reason,
        )
        return self._persist_and_return(
            gate_outcome=gate_outcome,
            task_id=context.task.task_id,
            attempt=context.task.attempt,
            candidate_sha=candidate_sha,
            reviewed_sha=None,
            review_result=review_res,
            bound_review=None,
            phase_gate_decision=None,
            consensus_decision=None,
            session_audit=audit,
            reasons=(clean_reason,),
        )

    def _persist_and_return(
        self,
        *,
        gate_outcome: ReviewerGateOutcome,
        task_id: str,
        attempt: int,
        candidate_sha: str,
        reviewed_sha: str | None,
        review_result: ReviewResult,
        bound_review: BoundReview | None,
        phase_gate_decision: PhaseGateDecision | None,
        consensus_decision: ConsensusDecision | None,
        session_audit: SessionIsolationAuditReceipt,
        reasons: tuple[str, ...],
    ) -> SubscriptionReviewerGateDecision:
        out_dir = Path(self.config.evidence_output_dir).resolve()
        out_dir.mkdir(parents=True, exist_ok=True)
        receipt_file = out_dir / f"b02_reviewer_gate_{task_id}_att_{attempt:02d}.json"
        rel_or_abs = str(receipt_file.as_posix())
        decision = SubscriptionReviewerGateDecision(
            gate_outcome=gate_outcome,
            task_id=task_id,
            candidate_commit_sha=candidate_sha,
            reviewed_commit_sha=reviewed_sha,
            review_result=review_result,
            bound_review=bound_review,
            phase_gate_decision=phase_gate_decision,
            consensus_decision=consensus_decision,
            session_audit=session_audit,
            receipt_path=rel_or_abs,
            reasons=tuple(sanitize_text(r) for r in reasons if r),
        )
        write_json_atomic(receipt_file, decision.model_dump(mode="json"))
        return decision


__all__ = [
    "IndependentSubscriptionReviewerGate",
    "ReviewerGateOutcome",
    "ReviewerRejectionCode",
    "SessionIsolationAuditReceipt",
    "SubscriptionReviewerGateConfig",
    "SubscriptionReviewerGateDecision",
    "TransportFactoryCallable",
    "default_subscription_reviewer_transport_factory",
]
