"""Acceptance and negative tests for Milestone C.5 RG02 evidence-based finding triage."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml
from tests.review.conftest import context

from aipc.controller import (
    ApprovedTaskContract,
    BoundedRepairCoordinator,
    ControllerError,
    MilestoneAuthorizationEvidence,
    PersistentTaskQueue,
    QueuedTaskStatus,
    persist_milestone_authorization_evidence,
)
from aipc.domain.enums import ReviewVerdict
from aipc.domain.models import CriterionResult, ReviewResult
from aipc.hardening.env_isolation import build_sanitized_child_env
from aipc.hardening.errors import SecretExposureError
from aipc.project.models import ProjectDefinition
from aipc.review import CriterionRule, bind_review
from aipc.review.context import digest, review_provenance
from aipc.review.governance_policy import PolicyTrustContext, load_review_governance_policy
from aipc.review.governance_triage import ReviewTriageDecision, UntrustedFindingDescriptor

pytest_plugins = ("tests.review.conftest",)

_C5_BASELINE_SHA = "4b0d19e3fd3ee30d06f9731ae90564581204f2be"
_CANDIDATE_SHA = "a" * 40


def _valid_policy_payload() -> dict[str, Any]:
    return {
        "policy_version": "1.0",
        "policy_id": "aipc-c5-review-governance",
        "operating_profile": "subscription_only",
        "owner": "K.TRITOD",
        "source_authority": "operator_owned_external_policy",
        "review": {
            "preserve_raw_review": True,
            "preserve_canonical_verdict": True,
            "require_exact_candidate_sha": True,
            "default_independence": "prefer_cross_provider",
            "require_objective_evidence_for_blockers": True,
            "unsupported_or_unverifiable_critical_finding": "NEEDS_TRIAGE_HUMAN",
            "advisory": {
                "auto_dispatch": False,
                "action": "AUDIT_BACKLOG_ONLY",
            },
            "release_blocker": {
                "allow_release": False,
                "auto_dispatch_only_if_authorized_and_within_budget": True,
            },
            "needs_triage": {
                "allow_release_if_credible_unresolved": False,
                "action": "HUMAN_REVIEW_REQUIRED",
            },
        },
        "repair_budget": {
            "default_auto_code_repairs_per_task": 2,
            "total_auto_code_repairs_per_milestone": 8,
            "budget_exhausted_action": "HALT_ON_RETRY_EXHAUSTION",
            "preserve_unresolved_blockers": True,
            "counter_scope": "approved_milestone_and_root_task_lineage",
            "persist_across_restarts": True,
            "persist_across_provider_fallback": True,
            "reject_counter_reset_by_task_splitting": True,
            "extension": {
                "human_commander_approval_required": True,
                "requires_explicit_task_and_scope": True,
                "requires_max_additional_attempts": True,
                "auditable_and_expiring": True,
            },
        },
        "change_scope": {
            "cannot_expand_milestone_from_review": True,
            "prohibit_speculative_security_hardening": True,
            "no_second_phase_gate": True,
            "no_auto_merge": True,
            "no_auto_deploy": True,
            "no_milestone_d": True,
        },
    }


def _create_c5_trust_env(
    tmp_path: Path,
    *,
    project_id: str = "example",
) -> tuple[PolicyTrustContext, Path, Path]:
    ext_root = tmp_path / "external_runtime"
    cand_root = tmp_path / "candidate_repo"
    ext_root.mkdir(parents=True, exist_ok=True)
    cand_root.mkdir(parents=True, exist_ok=True)

    policy_path = ext_root / "policy" / "c5_review_policy.yaml"
    policy_path.parent.mkdir(parents=True, exist_ok=True)
    policy_bytes = yaml.safe_dump(_valid_policy_payload(), sort_keys=False).encode("utf-8")
    policy_path.write_bytes(policy_bytes)
    policy_sha = hashlib.sha256(policy_bytes).hexdigest()

    approval_path = ext_root / "approvals" / "c5_approval.json"
    evidence = MilestoneAuthorizationEvidence(
        approval_id="c5-approval-001",
        project_id=project_id,
        milestone_id="milestone-c5",
        decision="APPROVED",
        authorized_by="K.TRITOD",
        baseline_commit_sha=_C5_BASELINE_SHA,
        authorized_workstreams=("rg01", "rg02", "rg03", "rg04"),
    )
    persist_milestone_authorization_evidence(approval_path, evidence)

    trust_ctx = PolicyTrustContext(
        policy_path=policy_path,
        expected_sha256=policy_sha,
        trusted_external_root=ext_root,
        excluded_roots=(cand_root,),
        approval_path=approval_path,
        expected_approval_id="c5-approval-001",
        expected_owner="K.TRITOD",
        expected_project_id=project_id,
        expected_milestone_id="milestone-c5",
    )
    return trust_ctx, policy_path, approval_path


def _c5_contract(approval_path: Path, *, task_id: str = "task-rg02-triage") -> ApprovedTaskContract:
    return ApprovedTaskContract(
        task_id=task_id,
        phase_id="rg02",
        milestone_id="milestone-c5",
        title="RG02 evidence-based finding triage",
        objective="Triage review findings deterministically at repair dispatch",
        depends_on=(),
        max_attempts=2,
        in_scope=("src/aipc/review/governance_triage.py",),
        out_of_scope=("src/aipc/domain/models.py",),
        validation_commands=("pytest tests/unit/test_c5_rg02_triage.py -q",),
        require_independent_review=True,
        approval_id="c5-approval-001",
        approval_evidence_path=str(approval_path),
    )


def _legacy_contract(task_id: str = "task-p24-legacy") -> ApprovedTaskContract:
    return ApprovedTaskContract(
        task_id=task_id,
        phase_id="09",
        milestone_id="phase-24",
        title="Legacy Phase 24 repair",
        objective="Verify pre-C5 repair compatibility",
        depends_on=(),
        max_attempts=2,
        in_scope=("src/aipc/controller/repair_coordinator.py",),
        out_of_scope=("src/aipc/domain/models.py",),
        validation_commands=("pytest tests/controller/test_repair_and_rereview.py -q",),
        require_independent_review=True,
    )


def _make_review(
    ctx: Any,
    *,
    review_id: str = "rev-rg02-01",
    required_status: str = "FAIL",
    required_evidence: list[str] | None = None,
    optional_status: str = "PASS",
    optional_evidence: list[str] | None = None,
    required_fixes: list[str] | None = None,
    risks: list[str] | None = None,
    confidence: float = 0.9,
    metadata: dict[str, Any] | None = None,
) -> ReviewResult:
    req_ev = ["test_log"] if required_evidence is None else required_evidence
    opt_ev = ["git_diff"] if optional_evidence is None else optional_evidence
    criteria = [
        CriterionResult(
            criterion_id="AC-tests",
            status=required_status,  # type: ignore[arg-type]
            evidence=req_ev,
            notes="Required test criterion assessment.",
        ),
        CriterionResult(
            criterion_id="AC-optional",
            status=optional_status,  # type: ignore[arg-type]
            evidence=opt_ev,
            notes="Optional criterion assessment.",
        ),
    ]
    failed = [c.criterion_id for c in criteria if c.status == "FAIL"]
    return ReviewResult(
        schema_version="1.0",
        review_id=review_id,
        task_id=ctx.task.task_id,
        verdict=ReviewVerdict.REVISE,
        confidence=confidence,
        criteria=criteria,
        failed_criteria=failed,
        required_fixes=required_fixes or ["Address review finding."],
        risks=risks or ["Pending resolution."],
        next_phase_allowed=False,
        metadata={"candidate_commit_sha": ctx.checkpoint.git_commit, **(metadata or {})},
    )


def _setup_running_c5_task(
    review_case: dict[str, Any],
    tmp_path: Path,
) -> tuple[PersistentTaskQueue, BoundedRepairCoordinator, Any, PolicyTrustContext, Path]:
    trust_ctx, policy_path, approval_path = _create_c5_trust_env(tmp_path)
    queue = PersistentTaskQueue(tmp_path / "queue.json")
    contract = _c5_contract(approval_path)
    queue.initialize(
        project_id="example",
        milestone_id="milestone-c5",
        contracts=(contract,),
        approval_id="c5-approval-001",
        approval_evidence_path=approval_path,
    )
    queue.begin_dispatch(contract.task_id, worker_id="agy-hydrogen", provider="gemini")
    coordinator = BoundedRepairCoordinator(queue=queue, policy_trust_context=trust_ctx)
    ctx = context(review_case)
    return queue, coordinator, ctx, trust_ctx, policy_path


def test_advisory_only_findings_never_auto_dispatch_and_preserve_revise_verdict(
    review_case: dict[str, Any],
    tmp_path: Path,
) -> None:
    queue, coordinator, ctx, _trust_ctx, _policy_path = _setup_running_c5_task(
        review_case, tmp_path
    )
    review = _make_review(
        ctx,
        required_status="PASS",
        optional_status="FAIL",
        optional_evidence=["git_diff"],
        required_fixes=["Optional style cleanup only."],
    )
    bound = bind_review(
        ctx,
        review,
        authority="phase-24-independent-reviewer",
        reviewed=review_provenance(ctx),
    )

    eval_res = coordinator.evaluate_attempt_and_coordinate(
        task_id="task-rg02-triage",
        project=ctx.project,
        phase=ctx.project.phases[0],
        state=ctx.state,
        context=ctx,
        review=review,
        bound_review=bound,
    )

    assert eval_res.action == "HOLD_FOR_HUMAN_TRIAGE"
    assert eval_res.queue_status == QueuedTaskStatus.WAITING_HUMAN
    assert eval_res.repair_plan is None
    assert eval_res.gate_decision is not None
    assert eval_res.gate_decision.verdict == ReviewVerdict.REVISE
    assert eval_res.triage_decision is not None
    assert eval_res.triage_decision.outcome == "AUDIT_BACKLOG_ONLY"
    assert eval_res.triage_decision.authorize_repair is False
    assert len(eval_res.triage_decision.advisory_backlog) == 1
    advisory = eval_res.triage_decision.advisory_backlog[0]
    assert advisory.disposition == "NON_BLOCKING_ADVISORY"
    assert advisory.review_id == review.review_id
    assert advisory.criterion_id == "AC-optional"
    assert advisory.reviewed_sha == _CANDIDATE_SHA
    assert advisory.collected_evidence_refs == ("git_diff",)

    # Attempt ledger preserves raw REVISE verdicts and queue blocks auto-dispatch
    snap = queue.load()
    rec = snap.tasks[0]
    assert rec.status == QueuedTaskStatus.WAITING_HUMAN
    assert rec.attempts[-1].review_verdict == "REVISE"
    assert rec.attempts[-1].gate_verdict == "REVISE"
    assert queue.next_eligible_task() is None


def test_evidence_backed_blocker_reaches_schedule_bounded_repair_in_coordinator(
    review_case: dict[str, Any],
    tmp_path: Path,
) -> None:
    queue, coordinator, ctx, _trust_ctx, _policy_path = _setup_running_c5_task(
        review_case, tmp_path
    )
    review = _make_review(
        ctx,
        required_status="FAIL",
        required_evidence=["test_log"],
        required_fixes=["Fix failing assertion in src/aipc/review/governance_triage.py."],
    )
    descriptors = (
        UntrustedFindingDescriptor(
            finding_id="f-blocker-01",
            review_id=review.review_id,
            criterion_id="AC-tests",
            reviewed_sha=_CANDIDATE_SHA,
            evidence_refs=("test_log",),
            original_text="Required test criterion failed on collected test_log.",
            source_ref="review.criteria[AC-tests]",
            claimed_severity="ADVISORY",
            target_paths=("src/aipc/review/governance_triage.py",),
        ),
    )

    eval_res = coordinator.evaluate_attempt_and_coordinate(
        task_id="task-rg02-triage",
        project=ctx.project,
        phase=ctx.project.phases[0],
        state=ctx.state,
        context=ctx,
        review=review,
        finding_descriptors=descriptors,
    )

    assert eval_res.action == "SCHEDULE_BOUNDED_REPAIR"
    assert eval_res.queue_status == QueuedTaskStatus.REPAIR_REQUIRED
    assert eval_res.repair_plan is not None
    assert eval_res.repair_plan.next_attempt == 2
    assert eval_res.gate_decision is not None
    assert eval_res.gate_decision.verdict == ReviewVerdict.REVISE
    assert eval_res.triage_decision is not None
    assert eval_res.triage_decision.outcome == "AUTHORIZE_BOUNDED_REPAIR"
    assert eval_res.triage_decision.authorize_repair is True
    assert len(eval_res.triage_decision.release_blockers) == 1
    blocker = eval_res.triage_decision.release_blockers[0]
    assert blocker.disposition == "RELEASE_BLOCKER"
    assert blocker.collected_evidence_refs == ("test_log",)
    assert blocker.reviewed_sha == _CANDIDATE_SHA
    assert queue.next_eligible_task() is not None


def test_credible_uncertain_mandatory_finding_holds_for_human_triage(
    review_case: dict[str, Any],
    tmp_path: Path,
) -> None:
    _queue, coordinator, ctx, _trust_ctx, _policy_path = _setup_running_c5_task(
        review_case, tmp_path
    )
    review = _make_review(ctx, required_status="FAIL", required_evidence=["test_log"])
    descriptors = (
        UntrustedFindingDescriptor(
            finding_id="f-uncertain-01",
            review_id=review.review_id,
            criterion_id="AC-tests",
            reviewed_sha=_CANDIDATE_SHA,
            evidence_refs=("test_log",),
            original_text="Possible race condition, uncertain without additional trace.",
            source_ref="review.criteria[AC-tests]",
            uncertain=True,
            confidence=0.4,
            confidence_limits=("uncertain_reproduction",),
        ),
    )

    eval_res = coordinator.evaluate_attempt_and_coordinate(
        task_id="task-rg02-triage",
        project=ctx.project,
        phase=ctx.project.phases[0],
        state=ctx.state,
        context=ctx,
        review=review,
        finding_descriptors=descriptors,
    )

    assert eval_res.action == "HOLD_FOR_HUMAN_TRIAGE"
    assert eval_res.queue_status == QueuedTaskStatus.WAITING_HUMAN
    assert eval_res.repair_plan is None
    assert eval_res.gate_decision is not None
    assert eval_res.gate_decision.verdict == ReviewVerdict.REVISE
    assert eval_res.triage_decision is not None
    assert eval_res.triage_decision.outcome == "HOLD_FOR_HUMAN_TRIAGE"
    assert eval_res.triage_decision.needs_triage_findings[0].disposition == "NEEDS_TRIAGE"


@pytest.mark.parametrize(
    "descriptor_kwargs",
    [
        {"reviewed_sha": "b" * 40},
        {"evidence_refs": ("nonexistent_artifact",)},
        {"review_id": "rev-other-99"},
        {"target_paths": ("src/aipc/domain/models.py",)},
        {"claimed_scope_expansion": True, "original_text": "Expand scope to Milestone D."},
        {"requested_policy_change": True},
        {"requested_acceptance_change": True},
        {"claimed_authorize_repair": True, "advisory_only": True},
    ],
)
def test_untrusted_descriptor_anomalies_rejected_into_human_triage(
    review_case: dict[str, Any],
    tmp_path: Path,
    descriptor_kwargs: dict[str, Any],
) -> None:
    _queue, coordinator, ctx, _trust_ctx, _policy_path = _setup_running_c5_task(
        review_case, tmp_path
    )
    review = _make_review(ctx, required_status="FAIL", required_evidence=[])
    base_kwargs: dict[str, Any] = {
        "finding_id": "f-neg-01",
        "review_id": review.review_id,
        "criterion_id": "AC-tests",
        "reviewed_sha": _CANDIDATE_SHA,
        "evidence_refs": ("test_log",),
        "original_text": "Mandatory finding check.",
        "source_ref": "review.criteria[AC-tests]",
        "target_paths": ("src/aipc/review/governance_triage.py",),
    }
    base_kwargs.update(descriptor_kwargs)
    descriptors = (UntrustedFindingDescriptor(**base_kwargs),)

    eval_res = coordinator.evaluate_attempt_and_coordinate(
        task_id="task-rg02-triage",
        project=ctx.project,
        phase=ctx.project.phases[0],
        state=ctx.state,
        context=ctx,
        review=review,
        finding_descriptors=descriptors,
    )

    assert eval_res.action == "HOLD_FOR_HUMAN_TRIAGE"
    assert eval_res.queue_status == QueuedTaskStatus.WAITING_HUMAN
    assert eval_res.repair_plan is None
    assert eval_res.triage_decision is not None
    assert eval_res.triage_decision.authorize_repair is False
    assert eval_res.triage_decision.needs_triage_findings[0].disposition == "NEEDS_TRIAGE"


def test_raw_canonical_revise_without_collected_proof_holds_for_triage(
    review_case: dict[str, Any],
    tmp_path: Path,
) -> None:
    _queue, coordinator, ctx, _trust_ctx, _policy_path = _setup_running_c5_task(
        review_case, tmp_path
    )
    review = _make_review(
        ctx,
        required_status="FAIL",
        required_evidence=[],
        required_fixes=["Fix unverified failure."],
    )

    eval_res = coordinator.evaluate_attempt_and_coordinate(
        task_id="task-rg02-triage",
        project=ctx.project,
        phase=ctx.project.phases[0],
        state=ctx.state,
        context=ctx,
        review=review,
    )

    assert eval_res.action == "HOLD_FOR_HUMAN_TRIAGE"
    assert eval_res.queue_status == QueuedTaskStatus.WAITING_HUMAN
    assert eval_res.repair_plan is None
    assert eval_res.triage_decision is not None
    assert eval_res.triage_decision.needs_triage_findings[0].disposition == "NEEDS_TRIAGE"


def test_c5_fails_closed_when_policy_missing_or_tampered_after_construction(
    review_case: dict[str, Any],
    tmp_path: Path,
) -> None:
    _trust_ctx, _policy_path, approval_path = _create_c5_trust_env(tmp_path / "missing_pol")
    queue_missing = PersistentTaskQueue(tmp_path / "missing_pol" / "queue.json")
    contract = _c5_contract(approval_path)
    queue_missing.initialize(
        project_id="example",
        milestone_id="milestone-c5",
        contracts=(contract,),
        approval_id="c5-approval-001",
        approval_evidence_path=approval_path,
    )
    queue_missing.begin_dispatch(contract.task_id, worker_id="agy-hydrogen", provider="gemini")
    coord_missing = BoundedRepairCoordinator(queue=queue_missing, policy_trust_context=None)
    ctx = context(review_case)
    review = _make_review(ctx)

    with pytest.raises(ControllerError, match="PolicyTrustContext"):
        coord_missing.evaluate_attempt_and_coordinate(
            task_id=contract.task_id,
            project=ctx.project,
            phase=ctx.project.phases[0],
            state=ctx.state,
            context=ctx,
            review=review,
        )

    # Policy modified after coordinator construction must fail closed at consequential decision
    _q, coord_tamper, ctx_t, _tctx, policy_path = _setup_running_c5_task(
        review_case, tmp_path / "tampered"
    )
    review_t = _make_review(ctx_t)
    tampered = _valid_policy_payload()
    tampered["repair_budget"]["default_auto_code_repairs_per_task"] = 5
    policy_path.write_bytes(yaml.safe_dump(tampered, sort_keys=False).encode("utf-8"))

    with pytest.raises(ControllerError, match="Governance policy check failed"):
        coord_tamper.evaluate_attempt_and_coordinate(
            task_id="task-rg02-triage",
            project=ctx_t.project,
            phase=ctx_t.project.phases[0],
            state=ctx_t.state,
            context=ctx_t,
            review=review_t,
        )


def test_direct_build_repair_plan_cannot_bypass_c5_triage(
    review_case: dict[str, Any],
    tmp_path: Path,
) -> None:
    queue, coordinator, ctx, _trust_ctx, _policy_path = _setup_running_c5_task(
        review_case, tmp_path
    )
    task_rec = queue.load().tasks[0]
    review = _make_review(ctx)

    with pytest.raises(ControllerError, match="ReviewTriageDecision"):
        coordinator._build_repair_plan(
            task_rec=task_rec,
            project=ctx.project,
            phase=ctx.project.phases[0],
            state=ctx.state,
            review=review,
            previous_task_id=ctx.task.task_id,
            candidate_sha=_CANDIDATE_SHA,
        )


def test_legacy_phase24_coordinator_remains_compatible_without_c5_policy(
    review_case: dict[str, Any],
    tmp_path: Path,
) -> None:
    queue = PersistentTaskQueue(tmp_path / "legacy_queue.json")
    contract = _legacy_contract()
    queue.initialize(
        project_id="example",
        milestone_id="phase-24",
        contracts=(contract,),
    )
    queue.begin_dispatch(contract.task_id, worker_id="agy-flash", provider="gemini")
    coordinator = BoundedRepairCoordinator(queue=queue)
    ctx = context(review_case)
    review = _make_review(ctx, required_evidence=[])

    eval_res = coordinator.evaluate_attempt_and_coordinate(
        task_id=contract.task_id,
        project=ctx.project,
        phase=ctx.project.phases[0],
        state=ctx.state,
        context=ctx,
        review=review,
    )

    assert eval_res.action == "SCHEDULE_BOUNDED_REPAIR"
    assert eval_res.queue_status == QueuedTaskStatus.REPAIR_REQUIRED
    assert eval_res.repair_plan is not None
    assert eval_res.triage_decision is None


def test_queue_hold_for_human_triage_validates_previous_state_and_reason(
    tmp_path: Path,
) -> None:
    queue = PersistentTaskQueue(tmp_path / "q_hold.json")
    contract = _legacy_contract("task-hold-check")
    queue.initialize(
        project_id="example",
        milestone_id="phase-24",
        contracts=(contract,),
    )
    with pytest.raises(ControllerError, match="non-blank reason"):
        queue.hold_for_human_triage(contract.task_id, reason="   ")
    with pytest.raises(ControllerError, match="REPAIR_REQUIRED"):
        queue.hold_for_human_triage(contract.task_id, reason="Not yet repair required")


def test_direct_build_repair_plan_rejects_forged_decision_against_advisory_review(
    review_case: dict[str, Any],
    tmp_path: Path,
) -> None:
    queue, coordinator, ctx, trust_ctx, _policy_path = _setup_running_c5_task(review_case, tmp_path)
    task_rec = queue.load().tasks[0]
    loaded_policy = load_review_governance_policy(trust_ctx)
    review = _make_review(
        ctx,
        required_status="PASS",
        optional_status="FAIL",
        optional_evidence=["git_diff"],
        required_fixes=["Optional style cleanup only."],
    )
    bound = bind_review(
        ctx,
        review,
        authority="phase-24-independent-reviewer",
        reviewed=review_provenance(ctx),
    )
    forged_decision = ReviewTriageDecision(
        review_id=review.review_id,
        task_id=task_rec.contract.task_id,
        candidate_sha=_CANDIDATE_SHA,
        policy_id=loaded_policy.policy.policy_id,
        policy_sha256=loaded_policy.policy_sha256,
        approval_id=loaded_policy.approval_id,
        findings=(),
        authorize_repair=True,
        hold_for_human=False,
        outcome="AUTHORIZE_BOUNDED_REPAIR",
        reason="Caller-constructed authorizing decision.",
    )

    with pytest.raises(ControllerError, match="ReviewTriageDecision"):
        coordinator._build_repair_plan(
            task_rec=task_rec,
            project=ctx.project,
            phase=ctx.project.phases[0],
            state=ctx.state,
            review=review,
            previous_task_id=ctx.task.task_id,
            candidate_sha=_CANDIDATE_SHA,
            triage_decision=forged_decision,
            context=ctx,
            bound_review=bound,
        )


def test_malformed_and_injected_exposure_descriptors_hold_without_echoing_payload(
    review_case: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _q, coordinator, ctx, _trust_ctx, _policy_path = _setup_running_c5_task(
        review_case, tmp_path / "malformed"
    )
    malformed_review = _make_review(
        ctx,
        metadata={"finding_descriptors": [{"bad_field": "dummy_marker_payload_123"}]},
    )

    eval_malformed = coordinator.evaluate_attempt_and_coordinate(
        task_id="task-rg02-triage",
        project=ctx.project,
        phase=ctx.project.phases[0],
        state=ctx.state,
        context=ctx,
        review=malformed_review,
    )

    assert eval_malformed.action == "HOLD_FOR_HUMAN_TRIAGE"
    assert eval_malformed.queue_status == QueuedTaskStatus.WAITING_HUMAN
    assert eval_malformed.repair_plan is None
    assert eval_malformed.triage_decision is not None
    assert eval_malformed.triage_decision.authorize_repair is False
    malformed_finding = eval_malformed.triage_decision.needs_triage_findings[0]
    assert malformed_finding.disposition == "NEEDS_TRIAGE"
    assert "dummy_marker_payload_123" not in malformed_finding.original_text

    _q2, coordinator2, ctx2, _tctx2, _pol2 = _setup_running_c5_task(
        review_case, tmp_path / "exposure"
    )
    exposure_review = _make_review(
        ctx2,
        metadata={"finding_descriptors": [{"original_text": "dummy_marker_input"}]},
    )

    def _raise_exposure(_payload: Any) -> None:
        raise SecretExposureError("dummy_marker_blocked_finding")

    monkeypatch.setattr("aipc.hardening.secrets.assert_no_secrets", _raise_exposure)
    eval_exposure = coordinator2.evaluate_attempt_and_coordinate(
        task_id="task-rg02-triage",
        project=ctx2.project,
        phase=ctx2.project.phases[0],
        state=ctx2.state,
        context=ctx2,
        review=exposure_review,
    )

    assert eval_exposure.action == "HOLD_FOR_HUMAN_TRIAGE"
    assert eval_exposure.queue_status == QueuedTaskStatus.WAITING_HUMAN
    assert eval_exposure.repair_plan is None
    assert eval_exposure.triage_decision is not None
    exposure_finding = eval_exposure.triage_decision.needs_triage_findings[0]
    assert exposure_finding.disposition == "NEEDS_TRIAGE"
    assert "dummy_marker_blocked_finding" not in exposure_finding.original_text
    assert "dummy_marker_input" not in exposure_finding.original_text


@pytest.mark.parametrize(
    "import_sequence",
    [
        (
            "from aipc.review.governance_policy import "
            "PolicyTrustContext, load_review_governance_policy",
            "from aipc.controller import BoundedRepairCoordinator",
            "from aipc.review.governance_triage import evaluate_review_triage",
        ),
        (
            "from aipc.controller import BoundedRepairCoordinator",
            "from aipc.review.governance_policy import "
            "PolicyTrustContext, load_review_governance_policy",
            "from aipc.review.governance_triage import evaluate_review_triage",
        ),
        (
            "from aipc.review.governance_triage import evaluate_review_triage",
            "from aipc.review.governance_policy import "
            "PolicyTrustContext, load_review_governance_policy",
            "from aipc.controller import BoundedRepairCoordinator",
        ),
    ],
    ids=["governance_policy_first", "controller_first", "governance_triage_first"],
)
def test_fresh_process_import_orders_avoid_circular_import_and_bind_candidate_src(
    import_sequence: tuple[str, ...],
) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    src_root = (repo_root / "src").resolve()
    expected_aipc_root = (src_root / "aipc").resolve()

    child_script = "\n".join(
        [
            *import_sequence,
            "import json",
            "import aipc",
            "import aipc.controller.repair_coordinator as rc",
            "import aipc.review.governance_policy as gp",
            "import aipc.review.governance_triage as gt",
            "print(json.dumps({",
            "    'aipc': aipc.__file__,",
            "    'repair_coordinator': rc.__file__,",
            "    'governance_policy': gp.__file__,",
            "    'governance_triage': gt.__file__,",
            "}))",
        ]
    )
    child_env = build_sanitized_child_env(
        extra_env={
            "PYTHONPATH": str(src_root),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
    )
    proc = subprocess.run(
        [sys.executable, "-c", child_script],
        cwd=str(repo_root),
        env=child_env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, f"Fresh import failed: {proc.stderr}"
    origins = json.loads(proc.stdout.strip())
    for mod_name, mod_path in origins.items():
        resolved = Path(mod_path).resolve()
        assert resolved.is_relative_to(expected_aipc_root), (
            f"Module {mod_name} resolved outside candidate src/aipc: {resolved}"
        )


def test_unrelated_collected_artifact_citation_holds_for_triage_without_autorepair(
    review_case: dict[str, Any],
    tmp_path: Path,
) -> None:
    _queue, coordinator, ctx, _trust_ctx, _policy_path = _setup_running_c5_task(
        review_case, tmp_path
    )
    # git_diff is collected globally in ctx.evidence.items, but AC-tests is linked to test_log.
    review = _make_review(
        ctx,
        required_status="FAIL",
        required_evidence=["git_diff"],
        required_fixes=["Fix failing test assertion."],
    )
    eval_res = coordinator.evaluate_attempt_and_coordinate(
        task_id="task-rg02-triage",
        project=ctx.project,
        phase=ctx.project.phases[0],
        state=ctx.state,
        context=ctx,
        review=review,
    )

    assert eval_res.action == "HOLD_FOR_HUMAN_TRIAGE"
    assert eval_res.queue_status == QueuedTaskStatus.WAITING_HUMAN
    assert eval_res.repair_plan is None
    assert eval_res.triage_decision is not None
    assert eval_res.triage_decision.authorize_repair is False
    assert eval_res.triage_decision.needs_triage_findings[0].disposition == "NEEDS_TRIAGE"
    assert eval_res.triage_decision.needs_triage_findings[0].collected_evidence_refs == ()


def test_positively_linked_artifact_and_file_changed_rules_authorize_bounded_repair(
    review_case: dict[str, Any],
    tmp_path: Path,
) -> None:
    _queue, coordinator, _base_ctx, _trust_ctx, _policy_path = _setup_running_c5_task(
        review_case, tmp_path
    )
    case = dict(review_case)
    proj_data = case["project"].model_dump(mode="json")
    proj_data["phases"][0]["acceptance_criteria"].extend(
        [
            {"id": "AC-artifact", "statement": "Design artifact exists", "required": True},
            {"id": "AC-changed", "statement": "Source file changed", "required": True},
        ]
    )
    case["project"] = ProjectDefinition.model_validate(proj_data)
    base_ctx = context(case, artifacts=(("design", b"spec bytes"),))
    linked_ctx = base_ctx.model_copy(
        update={
            "rules": (
                *base_ctx.rules,
                CriterionRule(criterion_id="AC-artifact", kind="artifact", target="design"),
                CriterionRule(
                    criterion_id="AC-changed", kind="file_changed", target="src/example.py"
                ),
            )
        }
    )
    review = ReviewResult(
        schema_version="1.0",
        review_id="rev-rg02-linked-01",
        task_id=linked_ctx.task.task_id,
        verdict=ReviewVerdict.REVISE,
        confidence=0.92,
        criteria=[
            CriterionResult(
                criterion_id="AC-tests",
                status="PASS",
                evidence=["test_log"],
                notes="Tests passed.",
            ),
            CriterionResult(
                criterion_id="AC-optional",
                status="PASS",
                evidence=["git_diff"],
                notes="Optional passed.",
            ),
            CriterionResult(
                criterion_id="AC-artifact",
                status="FAIL",
                evidence=["design"],
                notes="Design spec incomplete.",
            ),
            CriterionResult(
                criterion_id="AC-changed",
                status="FAIL",
                evidence=["git_diff"],
                notes="Missing guard in src/example.py.",
            ),
        ],
        failed_criteria=["AC-artifact", "AC-changed"],
        required_fixes=["Complete design spec.", "Add guard in src/example.py."],
        risks=["Pending fixes."],
        next_phase_allowed=False,
        metadata={"candidate_commit_sha": _CANDIDATE_SHA},
    )

    eval_res = coordinator.evaluate_attempt_and_coordinate(
        task_id="task-rg02-triage",
        project=linked_ctx.project,
        phase=linked_ctx.project.phases[0],
        state=linked_ctx.state,
        context=linked_ctx,
        review=review,
    )

    assert eval_res.action == "SCHEDULE_BOUNDED_REPAIR"
    assert eval_res.triage_decision is not None
    assert len(eval_res.triage_decision.release_blockers) == 2
    assert eval_res.triage_decision.release_blockers[0].collected_evidence_refs == ("design",)
    assert eval_res.triage_decision.release_blockers[1].collected_evidence_refs == ("git_diff",)


def test_mixed_review_excludes_advisories_from_repair_plan_and_prompt_preserving_raw_review(
    review_case: dict[str, Any],
    tmp_path: Path,
) -> None:
    _queue, coordinator, ctx, _trust_ctx, _policy_path = _setup_running_c5_task(
        review_case, tmp_path
    )
    advisory_marker = "DO_NOT_IMPLEMENT_ADVISORY_CLEANUP"
    review = ReviewResult(
        schema_version="1.0",
        review_id="rev-rg02-mixed-01",
        task_id=ctx.task.task_id,
        verdict=ReviewVerdict.REVISE,
        confidence=0.95,
        criteria=[
            CriterionResult(
                criterion_id="AC-tests",
                status="FAIL",
                evidence=["test_log"],
                notes=f"Required test failed. Duplicated advisory: {advisory_marker}",
            ),
            CriterionResult(
                criterion_id="AC-optional",
                status="FAIL",
                evidence=["git_diff"],
                notes=f"Optional style item: {advisory_marker}",
            ),
        ],
        failed_criteria=["AC-tests", "AC-optional"],
        required_fixes=[
            "Fix failing assertion in src/aipc/review/governance_triage.py.",
            advisory_marker,
        ],
        risks=[f"Risk note duplicating {advisory_marker}"],
        next_phase_allowed=False,
        metadata={"candidate_commit_sha": _CANDIDATE_SHA},
    )
    bound = bind_review(
        ctx,
        review,
        authority="phase-24-independent-reviewer",
        reviewed=review_provenance(ctx),
    )
    raw_snapshot = review.model_copy(deep=True)
    bound_digest_before = bound.review_digest

    descriptors = (
        UntrustedFindingDescriptor(
            finding_id="f-blocker-01",
            review_id=review.review_id,
            criterion_id="AC-tests",
            reviewed_sha=_CANDIDATE_SHA,
            evidence_refs=("test_log",),
            original_text="Fix failing assertion in src/aipc/review/governance_triage.py.",
            source_ref="review.criteria[AC-tests]",
            target_paths=("src/aipc/review/governance_triage.py",),
        ),
        UntrustedFindingDescriptor(
            finding_id="f-advisory-01",
            review_id=review.review_id,
            criterion_id="AC-optional",
            reviewed_sha=_CANDIDATE_SHA,
            evidence_refs=("git_diff",),
            original_text=advisory_marker,
            source_ref="review.criteria[AC-optional]",
            advisory_only=True,
        ),
    )

    eval_res = coordinator.evaluate_attempt_and_coordinate(
        task_id="task-rg02-triage",
        project=ctx.project,
        phase=ctx.project.phases[0],
        state=ctx.state,
        context=ctx,
        review=review,
        bound_review=bound,
        finding_descriptors=descriptors,
    )

    assert eval_res.action == "SCHEDULE_BOUNDED_REPAIR"
    assert eval_res.repair_plan is not None
    assert eval_res.triage_decision is not None
    assert len(eval_res.triage_decision.release_blockers) == 1
    assert len(eval_res.triage_decision.advisory_backlog) == 1
    assert eval_res.repair_plan.failed_criteria == ("AC-tests",)
    assert eval_res.repair_plan.required_fixes == (
        "[f-blocker-01] Fix failing assertion in src/aipc/review/governance_triage.py.",
    )
    assert "Fix failing assertion in src/aipc/review/governance_triage.py." in (
        eval_res.repair_plan.repair_prompt
    )
    assert advisory_marker not in eval_res.repair_plan.repair_prompt
    assert all(advisory_marker not in fix for fix in eval_res.repair_plan.required_fixes)
    assert review == raw_snapshot
    assert bound.review_digest == bound_digest_before
    assert digest(review) == bound_digest_before


def test_mixed_review_with_triage_finding_holds_and_excludes_autorepair(
    review_case: dict[str, Any],
    tmp_path: Path,
) -> None:
    _queue, coordinator, ctx, _trust_ctx, _policy_path = _setup_running_c5_task(
        review_case, tmp_path
    )
    review = _make_review(
        ctx,
        required_status="FAIL",
        required_evidence=["test_log"],
        optional_status="FAIL",
        optional_evidence=["git_diff"],
        required_fixes=["Fix blocker.", "Triage item.", "Advisory item."],
    )
    descriptors = (
        UntrustedFindingDescriptor(
            finding_id="f-blocker-01",
            review_id=review.review_id,
            criterion_id="AC-tests",
            reviewed_sha=_CANDIDATE_SHA,
            evidence_refs=("test_log",),
            original_text="Fix blocker.",
            source_ref="review.criteria[AC-tests]",
            target_paths=("src/aipc/review/governance_triage.py",),
        ),
        UntrustedFindingDescriptor(
            finding_id="f-triage-01",
            review_id=review.review_id,
            criterion_id="AC-tests",
            reviewed_sha=_CANDIDATE_SHA,
            evidence_refs=("git_diff",),
            original_text="Triage item backed only by unrelated git_diff.",
            source_ref="review.criteria[AC-tests]",
        ),
        UntrustedFindingDescriptor(
            finding_id="f-advisory-01",
            review_id=review.review_id,
            criterion_id="AC-optional",
            reviewed_sha=_CANDIDATE_SHA,
            evidence_refs=("git_diff",),
            original_text="Advisory item.",
            source_ref="review.criteria[AC-optional]",
            advisory_only=True,
        ),
    )

    eval_res = coordinator.evaluate_attempt_and_coordinate(
        task_id="task-rg02-triage",
        project=ctx.project,
        phase=ctx.project.phases[0],
        state=ctx.state,
        context=ctx,
        review=review,
        finding_descriptors=descriptors,
    )

    assert eval_res.action == "HOLD_FOR_HUMAN_TRIAGE"
    assert eval_res.queue_status == QueuedTaskStatus.WAITING_HUMAN
    assert eval_res.repair_plan is None
    assert eval_res.triage_decision is not None
    assert eval_res.triage_decision.authorize_repair is False
    assert len(eval_res.triage_decision.needs_triage_findings) == 1
    assert eval_res.triage_decision.needs_triage_findings[0].finding_id == "f-triage-01"
