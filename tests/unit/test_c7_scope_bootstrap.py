"""Unit and contract tests for Milestone C.7 (RCE01-RCE03) scope compatibility bootstrap."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from aipc.controller import (
    ApprovedTaskContract,
    ControllerError,
    MilestoneAuthorizationEvidence,
    PersistentTaskQueue,
    QueuedTaskStatus,
    persist_milestone_authorization_evidence,
    verify_milestone_c_authorization_scope,
)
from aipc.controller.milestone_authorization import (
    ApprovedMilestoneScope,
    MilestoneAuthorizationTrustContext,
    issue_milestone_authorization_record,
)
from aipc.controller.planner_engine import HumanGoalSpec
from aipc.controller.task_queue import (
    load_milestone_authorization_evidence,
    verify_milestone_authorization_scope,
)
from aipc.project.models import AcceptanceCriterion
from aipc.review.trusted_acceptance_harness import (
    UNAUTHORIZED_MILESTONE_D_BLOCKED,
    TrustedHarnessManifest,
    assert_milestone_c_scope_authorized,
)

C7_BASELINE_SHA = "f98846e8639b3ab846afe49454a6f79f6b236c36"
C6_BASELINE_SHA = "a66d19f2dbf3fe806317472c5599b2e0a381175e"
C5_BASELINE_SHA = "4b0d19e3fd3ee30d06f9731ae90564581204f2be"
C_BASELINE_SHA = "849c86684867487f6f0ad8eb54c603cba240dd34"
DIGEST_1 = "a" * 64

C7_IDENTIFIERS = (
    "milestone-c7",
    "milestone_c7",
    "milestone-c.7",
    "milestone_c.7",
    "c.7",
)
C7_WORKSTREAMS = ("rce01", "rce02", "rce03")


def _persist_c7_evidence(
    tmp_path: Path,
    *,
    filename: str = "milestone_c7_approval.json",
    approval_id: str = "ktritod-c7-20261118",
    project_id: str = "proj-c7",
    milestone_id: str = "milestone-c7",
    decision: str = "APPROVED",
    baseline_commit_sha: str = C7_BASELINE_SHA,
    authorized_workstreams: tuple[str, ...] = C7_WORKSTREAMS,
) -> tuple[Path, MilestoneAuthorizationEvidence]:
    ev = MilestoneAuthorizationEvidence(
        approval_id=approval_id,
        project_id=project_id,
        milestone_id=milestone_id,
        decision=decision,
        authorized_by="K.TRITOD",
        baseline_commit_sha=baseline_commit_sha,
        authorized_workstreams=authorized_workstreams,
    )
    path = persist_milestone_authorization_evidence(tmp_path / filename, ev)
    return path, ev


def _issue_c7_trust_context(
    tmp_path: Path,
    *,
    filename: str = "external_c7_grant.json",
    record_id: str = "rec-c7-bootstrap-01",
    approval_id: str = "ktritod-c7-20261118",
    project_id: str = "proj-c7",
    milestone_id: str = "milestone-c7",
    commander_id: str = "K.TRITOD",
    baseline_commit_sha: str = C7_BASELINE_SHA,
    authorized_workstreams: tuple[str, ...] = C7_WORKSTREAMS,
    allowed_path_prefixes: tuple[str, ...] = (
        "src/",
        "tests/",
        "docs/",
        ".agents/",
        "projects/",
        "schemas/",
        "tools/",
    ),
) -> MilestoneAuthorizationTrustContext:
    trusted_root = (tmp_path / "controller_trusted_root").resolve()
    candidate_root = (tmp_path / "simulated_candidate").resolve()
    stable_root = (tmp_path / "simulated_stable").resolve()
    excluded_roots = (candidate_root, stable_root)
    record_path = trusted_root / filename
    raw_grant_bytes = (
        f"commander-grant:{project_id}:{milestone_id}:{approval_id}:{commander_id}"
    ).encode()
    record, record_sha256 = issue_milestone_authorization_record(
        raw_grant_bytes=raw_grant_bytes,
        record_id=record_id,
        approval_id=approval_id,
        project_id=project_id,
        milestone_id=milestone_id,
        commander_id=commander_id,
        approved_scope=ApprovedMilestoneScope(
            workstreams=authorized_workstreams,
            allowed_path_prefixes=allowed_path_prefixes,
            baseline_commit_sha=baseline_commit_sha,
        ),
        output_path=record_path,
        trusted_external_root=trusted_root,
        excluded_roots=excluded_roots,
    )
    return MilestoneAuthorizationTrustContext(
        record_path=record_path,
        expected_record_sha256=record_sha256,
        expected_approval_digest=record.approval_digest,
        trusted_external_root=trusted_root,
        excluded_roots=excluded_roots,
        expected_project_id=project_id,
        expected_milestone_id=milestone_id,
        expected_commander_id=commander_id,
        expected_approval_id=approval_id,
    )


def _c7_contract(
    *,
    task_id: str = "task-rce01-bootstrap",
    phase_id: str = "rce01",
    milestone_id: str = "milestone-c7",
    approval_id: str | None = "ktritod-c7-20261118",
    approval_evidence_path: str | None = None,
    in_scope: tuple[str, ...] = ("src/aipc/controller/task_queue.py",),
) -> ApprovedTaskContract:
    return ApprovedTaskContract(
        task_id=task_id,
        phase_id=phase_id,
        milestone_id=milestone_id,
        title=f"Implement {phase_id}",
        objective=f"Bounded scope bootstrap for {phase_id}",
        in_scope=in_scope,
        validation_commands=("pytest tests/unit/test_c7_scope_bootstrap.py -q",),
        approval_id=approval_id,
        approval_evidence_path=approval_evidence_path,
    )


def _c7_goal_spec(
    *,
    project_id: str = "proj-c7",
    milestone_id: str = "milestone-c7",
    phase_id: str = "rce01",
    phase_name: str = "RCE01 Scope Bootstrap",
    approval_granted: bool = True,
    approval_id: str | None = "ktritod-c7-20261118",
    approval_evidence_path: str | None = None,
    milestone_trust_context: MilestoneAuthorizationTrustContext | None = None,
) -> HumanGoalSpec:
    return HumanGoalSpec(
        project_id=project_id,
        project_name="AIPC",
        workspace=".",
        milestone_id=milestone_id,
        phase_id=phase_id,
        phase_name=phase_name,
        goal_statement="Execute bounded Milestone C.7 workstream.",
        acceptance_criteria=(AcceptanceCriterion(id="ac-1", statement="Verify C.7 scope"),),
        in_scope_paths=("src/aipc/controller/task_queue.py",),
        validation_commands=("pytest tests/unit/test_c7_scope_bootstrap.py -q",),
        approval_granted=approval_granted,
        approval_id=approval_id,
        approval_evidence_path=approval_evidence_path,
        milestone_trust_context=milestone_trust_context,
    )


def _c7_manifest(
    *,
    project_id: str = "proj-c7",
    milestone_id: str = "milestone-c7",
    approval_id: str | None = "ktritod-c7-20261118",
    approval_evidence_path: str | None = None,
) -> TrustedHarnessManifest:
    return TrustedHarnessManifest(
        manifest_id="manifest-c7-01",
        project_id=project_id,
        milestone_id=milestone_id,
        approval_id=approval_id,
        approval_evidence_path=approval_evidence_path,
        pinned_file_sha256={"tests/unit/test_c7_scope_bootstrap.py": DIGEST_1},
    )


@pytest.mark.parametrize("milestone_id", C7_IDENTIFIERS)
@pytest.mark.parametrize("workstream", C7_WORKSTREAMS)
def test_c7_all_identifiers_and_workstreams_allowed_with_persisted_evidence(
    tmp_path: Path, milestone_id: str, workstream: str
) -> None:
    ev_path, ev = _persist_c7_evidence(tmp_path, milestone_id="milestone-c7")
    assert ev.baseline_commit_sha == C7_BASELINE_SHA
    assert ev.authorized_workstreams == C7_WORKSTREAMS
    assert ev.evidence_digest is not None
    trust_ctx = _issue_c7_trust_context(tmp_path, milestone_id="milestone-c7")

    contract = _c7_contract(
        task_id=f"task-{workstream}-01",
        phase_id=workstream,
        milestone_id="milestone-c7",
        approval_evidence_path=str(ev_path),
    )
    queue = PersistentTaskQueue(tmp_path / "queue.json", milestone_trust_context=trust_ctx)
    snap = queue.initialize(
        project_id="proj-c7",
        milestone_id="milestone-c7",
        contracts=[contract],
        approval_id=ev.approval_id,
        approval_evidence_path=ev_path,
    )
    assert snap.milestone_id == "milestone-c7"
    dispatched = queue.begin_dispatch(
        contract.task_id,
        worker_id="worker-1",
        provider="gemini",
    )
    assert dispatched.status == QueuedTaskStatus.RUNNING

    # Canonical IDs reject underscores/dots; free-text helpers normalize aliases.
    if milestone_id != "milestone-c7":
        with pytest.raises(ValueError):
            MilestoneAuthorizationEvidence(
                approval_id="appr-non-canonical",
                project_id="proj-c7",
                milestone_id=milestone_id,
            )
        with pytest.raises(ValueError):
            _c7_contract(
                task_id=f"task-{workstream}-bad",
                phase_id=workstream,
                milestone_id=milestone_id,
                approval_evidence_path=str(ev_path),
            )

    verify_milestone_authorization_scope(
        ev,
        project_id="proj-c7",
        milestone_id=milestone_id,
        phase_id=workstream,
        in_scope_paths=("src/aipc/controller/task_queue.py",),
        expected_approval_id=ev.approval_id,
    )

    goal = _c7_goal_spec(
        milestone_id=milestone_id,
        phase_id=workstream,
        phase_name=f"Workstream {workstream}",
        approval_evidence_path=str(ev_path),
        milestone_trust_context=trust_ctx,
    )
    assert goal.milestone_id == milestone_id

    manifest = _c7_manifest(
        milestone_id=milestone_id,
        approval_evidence_path=str(ev_path),
    )
    assert manifest.milestone_id == milestone_id
    assert_milestone_c_scope_authorized(
        milestone_id,
        phase_id=workstream,
        task_id=f"task-{workstream}-01",
        approval_id=ev.approval_id,
        approval_evidence_path=ev_path,
        project_id="proj-c7",
        in_scope_paths=("src/aipc/controller/task_queue.py",),
    )


def test_c7_missing_evidence_and_project_yaml_never_infer_authorization(tmp_path: Path) -> None:
    # Even if PROJECT.yaml mentions milestone-c7 and rce01, it never authorizes C.7.
    (tmp_path / "PROJECT.yaml").write_text(
        "project:\n  id: proj-c7\nmilestone: milestone-c7\nworkstreams: [rce01, rce02, rce03]\n",
        encoding="utf-8",
    )
    missing_path = tmp_path / "nonexistent_c7_approval.json"

    with pytest.raises(ValueError, match="requires an explicit approval_id"):
        _c7_contract(approval_id=None, approval_evidence_path=str(missing_path))

    with pytest.raises(ValueError, match="not found"):
        _c7_contract(approval_evidence_path=str(missing_path))

    with pytest.raises(ValueError, match="approval_evidence_path"):
        _c7_contract(approval_evidence_path=None)

    with pytest.raises(ValueError, match="approval_evidence_path"):
        _c7_contract(approval_evidence_path="   ")

    ev_path, ev = _persist_c7_evidence(tmp_path)
    with pytest.raises(ValueError, match="approval_id mismatch"):
        _c7_contract(approval_id="invented-approval-id", approval_evidence_path=str(ev_path))

    valid_contract = _c7_contract(approval_id=ev.approval_id, approval_evidence_path=str(ev_path))
    queue = PersistentTaskQueue(tmp_path / "queue.json")
    with pytest.raises(
        ControllerError,
        match="External milestone authorization trust context is required",
    ):
        queue.initialize(
            project_id="proj-c7",
            milestone_id="milestone-c7",
            contracts=[valid_contract],
            approval_id=ev.approval_id,
            approval_evidence_path=ev_path,
        )

    trust_ctx = _issue_c7_trust_context(tmp_path)
    trusted_queue = PersistentTaskQueue(
        tmp_path / "trusted_queue.json",
        milestone_trust_context=trust_ctx,
    )
    with pytest.raises(ControllerError, match="does not match queue project_id"):
        trusted_queue.initialize(
            project_id="wrong-project",
            milestone_id="milestone-c7",
            contracts=[valid_contract],
            approval_id=ev.approval_id,
            approval_evidence_path=ev_path,
        )
    with pytest.raises(ControllerError, match="Milestone C.7 authorization verification failed"):
        trusted_queue.initialize(
            project_id="proj-c7",
            milestone_id="milestone-c7",
            contracts=[valid_contract],
            approval_id=ev.approval_id,
            approval_evidence_path=missing_path,
        )

    with pytest.raises(ValueError, match="requires Controller-owned milestone_trust_context"):
        _c7_goal_spec(approval_granted=False, approval_evidence_path=None)

    with pytest.raises(ValueError, match="requires Controller-owned milestone_trust_context"):
        _c7_goal_spec(approval_granted=True, approval_evidence_path=str(missing_path))

    with pytest.raises(ValueError, match="unauthorized future milestone scope"):
        _c7_goal_spec(
            approval_granted=True,
            approval_evidence_path=str(missing_path),
            milestone_trust_context=trust_ctx,
        )

    with pytest.raises(ValueError, match=UNAUTHORIZED_MILESTONE_D_BLOCKED):
        _c7_manifest(approval_evidence_path=None)

    with pytest.raises(ValueError, match=UNAUTHORIZED_MILESTONE_D_BLOCKED):
        assert_milestone_c_scope_authorized(
            "milestone-c7",
            phase_id="rce01",
            task_id="task-rce01",
            approval_id="ktritod-c7-20261118",
            approval_evidence_path=None,
            project_id="proj-c7",
        )

    with pytest.raises(ValueError, match=UNAUTHORIZED_MILESTONE_D_BLOCKED):
        assert_milestone_c_scope_authorized(
            "milestone-c7",
            phase_id="rce01",
            task_id="task-rce01",
            approval_id="ktritod-c7-20261118",
            approval_evidence_path=missing_path,
            project_id="proj-c7",
        )


def test_c7_tampered_and_mismatched_evidence_rejected(tmp_path: Path) -> None:
    ev_path, ev = _persist_c7_evidence(tmp_path)

    # Tampered evidence_digest
    raw = json.loads(ev_path.read_text(encoding="utf-8"))
    raw["evidence_digest"] = "0" * 64
    tampered_path = tmp_path / "tampered_digest.json"
    tampered_path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="digest mismatch"):
        load_milestone_authorization_evidence(tampered_path)

    # Missing evidence_digest in persisted JSON
    raw_no_digest = json.loads(ev_path.read_text(encoding="utf-8"))
    raw_no_digest.pop("evidence_digest", None)
    no_digest_path = tmp_path / "no_digest.json"
    no_digest_path.write_text(json.dumps(raw_no_digest), encoding="utf-8")
    with pytest.raises(ValueError, match="requires a persisted evidence_digest"):
        load_milestone_authorization_evidence(no_digest_path)

    # Mismatched baseline_commit_sha
    with pytest.raises(ValueError, match="baseline_commit_sha"):
        MilestoneAuthorizationEvidence(
            approval_id="ktritod-c7-bad-sha",
            project_id="proj-c7",
            milestone_id="milestone-c7",
            baseline_commit_sha=C6_BASELINE_SHA,
            authorized_workstreams=C7_WORKSTREAMS,
        )

    # Non-APPROVED decision
    denied_path, denied_ev = _persist_c7_evidence(
        tmp_path,
        filename="denied_c7.json",
        decision="DENIED",
    )
    with pytest.raises(ValueError, match="not APPROVED"):
        verify_milestone_authorization_scope(
            denied_ev,
            project_id="proj-c7",
            milestone_id="milestone-c7",
            phase_id="rce01",
            in_scope_paths=("src/aipc/controller/task_queue.py",),
        )
    with pytest.raises(ValueError, match=UNAUTHORIZED_MILESTONE_D_BLOCKED):
        _c7_manifest(approval_evidence_path=str(denied_path))

    # Mismatched project_id and approval_id
    with pytest.raises(ValueError, match="project_id mismatch"):
        verify_milestone_authorization_scope(
            ev,
            project_id="other-project",
            milestone_id="milestone-c7",
            phase_id="rce01",
            in_scope_paths=("src/aipc/controller/task_queue.py",),
        )
    with pytest.raises(ValueError, match="approval_id mismatch"):
        verify_milestone_authorization_scope(
            ev,
            project_id="proj-c7",
            milestone_id="milestone-c7",
            phase_id="rce01",
            in_scope_paths=("src/aipc/controller/task_queue.py",),
            expected_approval_id="wrong-approval",
        )

    # Out-of-prefix in_scope path
    with pytest.raises(ValueError, match="outside authorized Milestone C.7 path prefixes"):
        verify_milestone_authorization_scope(
            ev,
            project_id="proj-c7",
            milestone_id="milestone-c7",
            phase_id="rce01",
            in_scope_paths=("secrets/private.txt",),
        )


def test_c_c5_c6_approvals_cannot_cross_authorize_c7_and_vice_versa(tmp_path: Path) -> None:
    c_path, c_ev = _persist_c7_evidence(
        tmp_path,
        filename="c_approval.json",
        approval_id="appr-c",
        milestone_id="milestone-c",
        baseline_commit_sha=C_BASELINE_SHA,
        authorized_workstreams=("c01", "c02", "c03"),
    )
    c5_path, c5_ev = _persist_c7_evidence(
        tmp_path,
        filename="c5_approval.json",
        approval_id="appr-c5",
        milestone_id="milestone-c5",
        baseline_commit_sha=C5_BASELINE_SHA,
        authorized_workstreams=("rg01", "rg02", "rg03", "rg04"),
    )
    c6_path, c6_ev = _persist_c7_evidence(
        tmp_path,
        filename="c6_approval.json",
        approval_id="appr-c6",
        milestone_id="milestone-c6",
        baseline_commit_sha=C6_BASELINE_SHA,
        authorized_workstreams=("cisl01", "cisl02", "cisl03"),
    )
    c7_path, c7_ev = _persist_c7_evidence(tmp_path)

    for prior_path, prior_ev in ((c_path, c_ev), (c5_path, c5_ev), (c6_path, c6_ev)):
        with pytest.raises(ValueError, match="milestone_id mismatch"):
            verify_milestone_authorization_scope(
                prior_ev,
                project_id="proj-c7",
                milestone_id="milestone-c7",
                phase_id="rce01",
                in_scope_paths=("src/aipc/controller/task_queue.py",),
                expected_approval_id=prior_ev.approval_id,
            )
        with pytest.raises(ValueError, match="milestone_id mismatch"):
            _c7_contract(
                approval_id=prior_ev.approval_id,
                approval_evidence_path=str(prior_path),
            )
        with pytest.raises(ValueError, match="unauthorized future milestone scope"):
            _c7_goal_spec(
                approval_id=prior_ev.approval_id,
                approval_evidence_path=str(prior_path),
            )
        with pytest.raises(ValueError, match=UNAUTHORIZED_MILESTONE_D_BLOCKED):
            _c7_manifest(
                approval_id=prior_ev.approval_id,
                approval_evidence_path=str(prior_path),
            )

    # C.7 evidence cannot authorize C, C.5, or C.6, nor use the Milestone C verifier.
    with pytest.raises(ValueError, match="cannot use Milestone C verifier"):
        verify_milestone_c_authorization_scope(
            c7_ev,
            project_id="proj-c7",
            milestone_id="milestone-c7",
            phase_id="rce01",
            in_scope_paths=("src/aipc/controller/task_queue.py",),
        )
    for target_ms, target_phase in (
        ("milestone-c", "c01"),
        ("milestone-c5", "rg01"),
        ("milestone-c6", "cisl01"),
    ):
        with pytest.raises(ValueError, match="milestone_id mismatch"):
            verify_milestone_authorization_scope(
                c7_ev,
                project_id="proj-c7",
                milestone_id=target_ms,
                phase_id=target_phase,
                in_scope_paths=("src/aipc/controller/task_queue.py",),
                expected_approval_id=c7_ev.approval_id,
            )
        with pytest.raises(ValueError, match=UNAUTHORIZED_MILESTONE_D_BLOCKED):
            assert_milestone_c_scope_authorized(
                target_ms,
                phase_id=target_phase,
                approval_id=c7_ev.approval_id,
                approval_evidence_path=c7_path,
                project_id="proj-c7",
            )


@pytest.mark.parametrize(
    "blocked_scope",
    (
        "milestone-d",
        "milestone_d",
        "workstream-d",
        "d01",
        "d02",
        "d03",
        "rce00",
        "rce04",
        "phase-25",
        "aipc-2.0",
    ),
)
def test_milestone_d_and_unauthorized_future_tokens_remain_blocked(
    tmp_path: Path, blocked_scope: str
) -> None:
    ev_path, ev = _persist_c7_evidence(tmp_path)

    with pytest.raises(ValueError):
        MilestoneAuthorizationEvidence(
            approval_id="appr-blocked",
            project_id="proj-c7",
            milestone_id=blocked_scope,
        )

    with pytest.raises(ValueError):
        _c7_contract(
            task_id=f"task-{blocked_scope}",
            phase_id=blocked_scope,
            milestone_id=blocked_scope,
            approval_id=ev.approval_id,
            approval_evidence_path=str(ev_path),
        )

    with pytest.raises(ValueError):
        _c7_contract(
            task_id=f"task-{blocked_scope}",
            phase_id=blocked_scope,
            milestone_id="milestone-c7",
            approval_id=ev.approval_id,
            approval_evidence_path=str(ev_path),
        )

    with pytest.raises(ValueError, match="unauthorized future milestone scope"):
        _c7_goal_spec(
            milestone_id=blocked_scope,
            phase_id="rce01",
            approval_id=ev.approval_id,
            approval_evidence_path=str(ev_path),
        )

    with pytest.raises(ValueError, match="unauthorized future milestone scope"):
        _c7_goal_spec(
            milestone_id="milestone-c7",
            phase_id=blocked_scope,
            approval_id=ev.approval_id,
            approval_evidence_path=str(ev_path),
        )

    with pytest.raises(ValueError, match=UNAUTHORIZED_MILESTONE_D_BLOCKED):
        _c7_manifest(
            milestone_id=blocked_scope,
            approval_id=ev.approval_id,
            approval_evidence_path=str(ev_path),
        )

    with pytest.raises(ValueError, match=UNAUTHORIZED_MILESTONE_D_BLOCKED):
        assert_milestone_c_scope_authorized(
            "milestone-c7",
            phase_id=blocked_scope,
            approval_id=ev.approval_id,
            approval_evidence_path=ev_path,
            project_id="proj-c7",
        )


def test_legacy_c_c5_c6_behavior_preserved(tmp_path: Path) -> None:
    c_path, c_ev = _persist_c7_evidence(
        tmp_path,
        filename="c_approval.json",
        approval_id="appr-c",
        milestone_id="milestone-c",
        baseline_commit_sha=C_BASELINE_SHA,
        authorized_workstreams=("c01", "c02", "c03"),
    )
    c5_path, c5_ev = _persist_c7_evidence(
        tmp_path,
        filename="c5_approval.json",
        approval_id="appr-c5",
        milestone_id="milestone-c5",
        baseline_commit_sha=C5_BASELINE_SHA,
        authorized_workstreams=("rg01", "rg02", "rg03", "rg04"),
    )
    c6_path, c6_ev = _persist_c7_evidence(
        tmp_path,
        filename="c6_approval.json",
        approval_id="appr-c6",
        milestone_id="milestone-c6",
        baseline_commit_sha=C6_BASELINE_SHA,
        authorized_workstreams=("cisl01", "cisl02", "cisl03"),
    )

    verify_milestone_c_authorization_scope(
        c_ev,
        project_id="proj-c7",
        milestone_id="milestone-c",
        phase_id="c01",
        in_scope_paths=("src/aipc/controller/task_queue.py",),
        expected_approval_id=c_ev.approval_id,
    )
    verify_milestone_authorization_scope(
        c5_ev,
        project_id="proj-c7",
        milestone_id="milestone-c5",
        phase_id="rg01",
        in_scope_paths=("src/aipc/controller/task_queue.py",),
        expected_approval_id=c5_ev.approval_id,
    )
    verify_milestone_authorization_scope(
        c6_ev,
        project_id="proj-c7",
        milestone_id="milestone-c6",
        phase_id="cisl01",
        in_scope_paths=("src/aipc/controller/task_queue.py",),
        expected_approval_id=c6_ev.approval_id,
    )
    assert_milestone_c_scope_authorized(
        "milestone-c",
        phase_id="c01",
        approval_id=c_ev.approval_id,
        approval_evidence_path=c_path,
        project_id="proj-c7",
    )
    assert_milestone_c_scope_authorized(
        "milestone-c5",
        phase_id="rg01",
        approval_id=c5_ev.approval_id,
        approval_evidence_path=c5_path,
        project_id="proj-c7",
    )
    assert_milestone_c_scope_authorized(
        "milestone-c6",
        phase_id="cisl01",
        approval_id=c6_ev.approval_id,
        approval_evidence_path=c6_path,
        project_id="proj-c7",
    )
