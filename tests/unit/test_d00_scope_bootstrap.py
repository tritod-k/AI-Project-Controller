"""Unit and contract tests for Milestone D (D01-D03) scope bootstrap."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from aipc.controller import (
    ApprovedTaskContract,
    ControllerError,
    MilestoneAuthorizationEvidence,
    PersistentTaskQueue,
    QueuedTaskStatus,
)
from aipc.controller.milestone_authorization import (
    ApprovedMilestoneScope,
    ControllerMilestoneAuthorizationRecord,
    MilestoneAuthorizationError,
    MilestoneAuthorizationTrustContext,
    issue_milestone_authorization_record,
    load_and_verify_milestone_authorization,
)
from aipc.controller.planner_engine import HumanGoalSpec
from aipc.project.models import AcceptanceCriterion
from aipc.review.trusted_acceptance_harness import (
    UNAUTHORIZED_MILESTONE_D_BLOCKED,
    TrustedHarnessManifest,
    assert_milestone_c_scope_authorized,
)

DIGEST_1 = "a" * 64
D_WORKSTREAMS = ("d01", "d02", "d03")


def _issue_d_trust_context(
    tmp_path: Path,
    *,
    filename: str = "external_d_grant.json",
    record_id: str = "rec-d-bootstrap-01",
    approval_id: str = "ktritod-d-20261120",
    project_id: str = "proj-d",
    milestone_id: str = "milestone-d",
    commander_id: str = "K.TRITOD",
    workstreams: tuple[str, ...] = D_WORKSTREAMS,
    allowed_path_prefixes: tuple[str, ...] = ("src/", "tests/"),
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
            workstreams=workstreams,
            allowed_path_prefixes=allowed_path_prefixes,
        ),
        output_path=record_path,
        trusted_external_root=trusted_root,
        excluded_roots=excluded_roots,
        issued_at=datetime(2026, 11, 20, 10, 0, tzinfo=UTC),
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


def _d_contract(
    *,
    task_id: str = "task-d01-bootstrap",
    phase_id: str = "d01",
    milestone_id: str = "milestone-d",
    approval_id: str | None = "ktritod-d-20261120",
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
        validation_commands=("pytest tests/unit/test_d00_scope_bootstrap.py -q",),
        approval_id=approval_id,
        approval_evidence_path=approval_evidence_path,
    )


def _d_goal_spec(
    *,
    project_id: str = "proj-d",
    milestone_id: str = "milestone-d",
    phase_id: str = "d01",
    phase_name: str = "D01 Scope Bootstrap",
    approval_granted: bool = True,
    approval_id: str | None = "ktritod-d-20261120",
    milestone_trust_context: MilestoneAuthorizationTrustContext | None = None,
    in_scope_paths: tuple[str, ...] = ("src/aipc/controller/task_queue.py",),
) -> HumanGoalSpec:
    return HumanGoalSpec(
        project_id=project_id,
        project_name="AIPC",
        workspace=".",
        milestone_id=milestone_id,
        phase_id=phase_id,
        phase_name=phase_name,
        goal_statement="Execute bounded Milestone D workstream.",
        acceptance_criteria=(AcceptanceCriterion(id="ac-1", statement="Verify D scope"),),
        in_scope_paths=in_scope_paths,
        validation_commands=("pytest tests/unit/test_d00_scope_bootstrap.py -q",),
        approval_granted=approval_granted,
        approval_id=approval_id,
        milestone_trust_context=milestone_trust_context,
    )


@pytest.mark.parametrize("workstream", D_WORKSTREAMS)
def test_milestone_d_workstreams_allowed_with_valid_external_trust_context(
    tmp_path: Path, workstream: str
) -> None:
    trust_ctx = _issue_d_trust_context(tmp_path)
    verified = load_and_verify_milestone_authorization(
        trust_ctx,
        phase_id=workstream,
        in_scope_paths=("src/aipc/controller/task_queue.py",),
    )
    assert verified.milestone_id == "milestone-d"
    assert verified.project_id == "proj-d"

    contract = _d_contract(task_id=f"task-{workstream}-01", phase_id=workstream)
    queue = PersistentTaskQueue(tmp_path / "queue.json", milestone_trust_context=trust_ctx)
    snap = queue.initialize(
        project_id="proj-d",
        milestone_id="milestone-d",
        contracts=[contract],
        approval_id="ktritod-d-20261120",
    )
    assert snap.milestone_id == "milestone-d"
    dispatched = queue.begin_dispatch(
        contract.task_id,
        worker_id="worker-1",
        provider="gemini",
    )
    assert dispatched.status == QueuedTaskStatus.RUNNING

    goal = _d_goal_spec(
        phase_id=workstream,
        phase_name=f"Workstream {workstream}",
        milestone_trust_context=trust_ctx,
    )
    assert goal.milestone_id == "milestone-d"
    assert goal.phase_id == workstream

    manifest = TrustedHarnessManifest(
        manifest_id="manifest-d-01",
        project_id="proj-d",
        milestone_id="milestone-d",
        approval_id="ktritod-d-20261120",
        milestone_trust_context=trust_ctx,
        pinned_file_sha256={"tests/unit/test_d00_scope_bootstrap.py": DIGEST_1},
    )
    assert manifest.milestone_id == "milestone-d"
    assert_milestone_c_scope_authorized(
        "milestone-d",
        phase_id=workstream,
        task_id=f"task-{workstream}-01",
        approval_id="ktritod-d-20261120",
        project_id="proj-d",
        in_scope_paths=("src/aipc/controller/task_queue.py",),
        milestone_trust_context=trust_ctx,
    )


def test_milestone_d_rejected_when_trust_context_missing_or_yaml_claimed(
    tmp_path: Path,
) -> None:
    (tmp_path / "PROJECT.yaml").write_text(
        "project:\n  id: proj-d\nmilestone: milestone-d\nworkstreams: [d01, d02, d03]\n",
        encoding="utf-8",
    )

    contract = _d_contract()
    queue = PersistentTaskQueue(tmp_path / "queue.json")
    with pytest.raises(
        ControllerError,
        match="External milestone authorization trust context is required",
    ):
        queue.initialize(
            project_id="proj-d",
            milestone_id="milestone-d",
            contracts=[contract],
            approval_id="ktritod-d-20261120",
        )

    with pytest.raises(ValueError, match="requires Controller-owned milestone_trust_context"):
        _d_goal_spec(milestone_trust_context=None)

    with pytest.raises(ValueError, match=UNAUTHORIZED_MILESTONE_D_BLOCKED):
        TrustedHarnessManifest(
            manifest_id="manifest-d-no-ctx",
            project_id="proj-d",
            milestone_id="milestone-d",
            approval_id="ktritod-d-20261120",
            pinned_file_sha256={"tests/unit/test_d00_scope_bootstrap.py": DIGEST_1},
        )

    with pytest.raises(ValueError, match=UNAUTHORIZED_MILESTONE_D_BLOCKED):
        assert_milestone_c_scope_authorized(
            "milestone-d",
            phase_id="d01",
            approval_id="ktritod-d-20261120",
            project_id="proj-d",
        )

    # Legacy MilestoneAuthorizationEvidence and YAML-only grants are rejected.
    with pytest.raises(ValueError):
        MilestoneAuthorizationEvidence(
            approval_id="ktritod-d-20261120",
            project_id="proj-d",
            milestone_id="milestone-d",
        )

    trust_ctx = _issue_d_trust_context(tmp_path)
    yaml_path = trust_ctx.trusted_external_root / "self_claimed_d.yaml"
    yaml_path.write_text(
        "milestone_id: milestone-d\ndecision: APPROVED\nworkstreams: [d01, d02, d03]\n",
        encoding="utf-8",
    )
    yaml_ctx = trust_ctx.model_copy(update={"record_path": yaml_path})
    with pytest.raises(MilestoneAuthorizationError, match="YAML-only"):
        load_and_verify_milestone_authorization(yaml_ctx, phase_id="d01")
    with pytest.raises(ValueError, match="milestone_trust_context verification failed"):
        _d_goal_spec(milestone_trust_context=yaml_ctx)


def test_milestone_d_rejects_wrong_milestone_wrong_project_tampered_digest_and_scope(
    tmp_path: Path,
) -> None:
    c7_ctx = _issue_d_trust_context(
        tmp_path / "c7",
        milestone_id="milestone-c7",
        workstreams=("rce01", "rce02"),
        approval_id="appr-c7",
    )
    d_ctx = _issue_d_trust_context(tmp_path / "d", workstreams=("d01",))

    # Wrong milestone (milestone-c7) cannot authorize milestone-d / d01.
    with pytest.raises(MilestoneAuthorizationError):
        load_and_verify_milestone_authorization(c7_ctx, phase_id="d01")
    with pytest.raises(ControllerError, match="outside approved workstreams"):
        PersistentTaskQueue(tmp_path / "q_c7.json", milestone_trust_context=c7_ctx).initialize(
            project_id="proj-d",
            milestone_id="milestone-d",
            contracts=[_d_contract()],
            approval_id="ktritod-d-20261120",
        )
    with pytest.raises(ValueError, match="milestone_trust_context verification failed"):
        _d_goal_spec(milestone_trust_context=c7_ctx)
    with pytest.raises(ValueError, match=UNAUTHORIZED_MILESTONE_D_BLOCKED):
        assert_milestone_c_scope_authorized(
            "milestone-d",
            phase_id="d01",
            project_id="proj-d",
            milestone_trust_context=c7_ctx,
        )

    # Workstream outside approved_scope (d02 not in ("d01",)) rejected.
    with pytest.raises(MilestoneAuthorizationError, match="outside approved workstreams"):
        load_and_verify_milestone_authorization(d_ctx, phase_id="d02")
    with pytest.raises(ValueError, match="milestone_trust_context verification failed"):
        _d_goal_spec(phase_id="d02", milestone_trust_context=d_ctx)

    # Wrong project rejected.
    with pytest.raises(ControllerError, match="does not match queue project_id"):
        PersistentTaskQueue(tmp_path / "q_proj.json", milestone_trust_context=d_ctx).initialize(
            project_id="wrong-project",
            milestone_id="milestone-d",
            contracts=[_d_contract()],
            approval_id="ktritod-d-20261120",
        )
    with pytest.raises(ValueError, match="milestone_trust_context verification failed"):
        _d_goal_spec(project_id="wrong-project", milestone_trust_context=d_ctx)
    with pytest.raises(ValueError, match=UNAUTHORIZED_MILESTONE_D_BLOCKED):
        assert_milestone_c_scope_authorized(
            "milestone-d",
            phase_id="d01",
            project_id="wrong-project",
            milestone_trust_context=d_ctx,
        )

    # Tampered approval_digest and tampered record JSON rejected.
    tampered_digest_ctx = d_ctx.model_copy(update={"expected_approval_digest": "0" * 64})
    with pytest.raises(MilestoneAuthorizationError, match="approval_digest mismatch"):
        load_and_verify_milestone_authorization(tampered_digest_ctx, phase_id="d01")

    raw = json.loads(d_ctx.record_path.read_text(encoding="utf-8"))
    raw["commander_id"] = "OTHER"
    d_ctx.record_path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(MilestoneAuthorizationError, match="SHA-256 mismatch"):
        load_and_verify_milestone_authorization(d_ctx, phase_id="d01")
    with pytest.raises(ValueError, match="milestone_trust_context verification failed"):
        _d_goal_spec(milestone_trust_context=d_ctx)


def test_milestone_e_plus_strictly_rejected_even_with_external_grant(tmp_path: Path) -> None:
    d_ctx = _issue_d_trust_context(tmp_path)

    with pytest.raises(ValueError):
        ApprovedMilestoneScope(workstreams=("e01",), allowed_path_prefixes=("src/",))

    with pytest.raises(ValueError):
        ControllerMilestoneAuthorizationRecord(
            record_id="rec-e01",
            approval_id="appr-e01",
            project_id="proj-e",
            milestone_id="milestone-e",
            commander_id="K.TRITOD",
            approved_scope=ApprovedMilestoneScope(
                workstreams=("c01",),
                allowed_path_prefixes=("src/",),
            ),
            approval_digest=DIGEST_1,
            issued_at=datetime(2026, 11, 20, 10, 0, tzinfo=UTC),
        )

    with pytest.raises(ValueError):
        MilestoneAuthorizationTrustContext(
            record_path=d_ctx.record_path,
            expected_record_sha256=d_ctx.expected_record_sha256,
            expected_approval_digest=d_ctx.expected_approval_digest,
            trusted_external_root=d_ctx.trusted_external_root,
            excluded_roots=d_ctx.excluded_roots,
            expected_project_id="proj-d",
            expected_milestone_id="milestone-e",
            expected_commander_id="K.TRITOD",
        )

    for bad_ms, bad_phase in (
        ("milestone-e", "e01"),
        ("milestone-d", "e01"),
        ("milestone-d", "phase-26"),
        ("phase-26", "e01"),
    ):
        with pytest.raises(ValueError):
            _d_goal_spec(
                milestone_id=bad_ms,
                phase_id=bad_phase,
                milestone_trust_context=d_ctx,
            )
        with pytest.raises(ValueError):
            _d_contract(
                task_id=f"task-{bad_phase}",
                milestone_id=bad_ms,
                phase_id=bad_phase,
            )
        with pytest.raises(ValueError, match=UNAUTHORIZED_MILESTONE_D_BLOCKED):
            assert_milestone_c_scope_authorized(
                bad_ms,
                phase_id=bad_phase,
                project_id="proj-d",
                milestone_trust_context=d_ctx,
            )
