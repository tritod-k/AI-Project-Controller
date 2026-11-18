"""Unit tests for Milestone C.5 Section 3A scope & worker gate compatibility bootstrap."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from aipc.controller.planner_engine import HumanGoalSpec
from aipc.controller.task_queue import (
    ApprovedTaskContract,
    ControllerError,
    MilestoneAuthorizationEvidence,
    PersistentTaskQueue,
    QueuedTaskStatus,
    load_milestone_authorization_evidence,
    persist_milestone_authorization_evidence,
    verify_milestone_c_authorization_scope,
)
from aipc.project.models import AcceptanceCriterion
from aipc.review.trusted_acceptance_harness import (
    UNAUTHORIZED_MILESTONE_D_BLOCKED,
    assert_milestone_c_scope_authorized,
    build_trusted_harness_manifest,
)

C5_BASELINE_SHA = "4b0d19e3fd3ee30d06f9731ae90564581204f2be"
C6_BASELINE_SHA = "a66d19f2dbf3fe806317472c5599b2e0a381175e"
C_BASELINE_SHA = "849c86684867487f6f0ad8eb54c603cba240dd34"


def _persist_c5_approval(
    path: Path,
    *,
    approval_id: str = "appr-c5-01",
    project_id: str = "aipc",
    decision: str = "APPROVED",
    workstreams: tuple[str, ...] = ("rg01", "rg02", "rg03", "rg04"),
    baseline_sha: str = C5_BASELINE_SHA,
) -> Path:
    evidence = MilestoneAuthorizationEvidence(
        approval_id=approval_id,
        project_id=project_id,
        milestone_id="milestone-c5",
        decision=decision,  # type: ignore[arg-type]
        baseline_commit_sha=baseline_sha,
        authorized_workstreams=workstreams,
    )
    return persist_milestone_authorization_evidence(path, evidence)


def _persist_c6_approval(
    path: Path,
    *,
    approval_id: str = "appr-c6-01",
    project_id: str = "aipc",
    decision: str = "APPROVED",
    workstreams: tuple[str, ...] = ("cisl01", "cisl02", "cisl03"),
    baseline_sha: str = C6_BASELINE_SHA,
) -> Path:
    evidence = MilestoneAuthorizationEvidence(
        approval_id=approval_id,
        project_id=project_id,
        milestone_id="milestone-c6",
        decision=decision,  # type: ignore[arg-type]
        baseline_commit_sha=baseline_sha,
        authorized_workstreams=workstreams,
    )
    return persist_milestone_authorization_evidence(path, evidence)


def _persist_c_approval(
    path: Path,
    *,
    approval_id: str = "appr-c-01",
    project_id: str = "aipc",
) -> Path:
    evidence = MilestoneAuthorizationEvidence(
        approval_id=approval_id,
        project_id=project_id,
        milestone_id="milestone-c",
        decision="APPROVED",
        baseline_commit_sha=C_BASELINE_SHA,
        authorized_workstreams=("c01", "c02", "c03"),
    )
    return persist_milestone_authorization_evidence(path, evidence)


def _goal_spec(
    *,
    milestone_id: str,
    phase_id: str,
    phase_name: str = "Milestone Phase",
    approval_granted: bool = False,
    approval_id: str | None = None,
    approval_evidence_path: str | None = None,
) -> HumanGoalSpec:
    return HumanGoalSpec(
        project_id="aipc",
        project_name="AI Project Controller",
        workspace="H:/000ProjectCode/AI_Project_Controller",
        milestone_id=milestone_id,
        phase_id=phase_id,
        phase_name=phase_name,
        goal_statement="Bounded Section 3A scope validation check",
        acceptance_criteria=(
            AcceptanceCriterion(
                id="AC-01",
                statement="Scope validation passes only for authorized milestones",
            ),
        ),
        in_scope_paths=("src/aipc/controller/task_queue.py",),
        validation_commands=("pytest -q",),
        approval_granted=approval_granted,
        approval_id=approval_id,
        approval_evidence_path=approval_evidence_path,
    )


def test_c5_authorization_evidence_roundtrip_and_digest_verification(tmp_path: Path) -> None:
    auth_path = _persist_c5_approval(tmp_path / "milestone_c5_approval.json")
    loaded = load_milestone_authorization_evidence(auth_path)
    assert loaded.milestone_id == "milestone-c5"
    assert loaded.baseline_commit_sha == C5_BASELINE_SHA
    assert loaded.authorized_workstreams == ("rg01", "rg02", "rg03", "rg04")
    assert loaded.evidence_digest is not None


def test_c5_authorization_evidence_rejects_tampered_missing_digest_and_wrong_workstreams(
    tmp_path: Path,
) -> None:
    auth_path = _persist_c5_approval(tmp_path / "milestone_c5_approval.json")

    raw = json.loads(auth_path.read_text(encoding="utf-8"))
    raw["authorized_workstreams"] = ["rg01"]
    tampered_path = tmp_path / "tampered.json"
    tampered_path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="digest mismatch"):
        load_milestone_authorization_evidence(tampered_path)

    no_digest_raw = json.loads(auth_path.read_text(encoding="utf-8"))
    no_digest_raw["evidence_digest"] = None
    no_digest_path = tmp_path / "no_digest.json"
    no_digest_path.write_text(json.dumps(no_digest_raw), encoding="utf-8")
    with pytest.raises(ValueError, match="evidence_digest"):
        load_milestone_authorization_evidence(no_digest_path)

    with pytest.raises(ValueError):
        MilestoneAuthorizationEvidence(
            approval_id="appr-bad-1",
            project_id="aipc",
            milestone_id="milestone-c5",
            authorized_workstreams=("c01",),
        )

    with pytest.raises(ValueError):
        MilestoneAuthorizationEvidence(
            approval_id="appr-bad-2",
            project_id="aipc",
            milestone_id="milestone-c",
            authorized_workstreams=("rg01",),
        )

    with pytest.raises(ValueError):
        MilestoneAuthorizationEvidence(
            approval_id="appr-bad-3",
            project_id="aipc",
            milestone_id="milestone-c5",
            baseline_commit_sha=C_BASELINE_SHA,
            authorized_workstreams=("rg01",),
        )


def test_task_queue_c5_rg01_to_rg04_positive_with_valid_persisted_approval(
    tmp_path: Path,
) -> None:
    auth_path = _persist_c5_approval(tmp_path / "milestone_c5_approval.json")
    contract = ApprovedTaskContract(
        task_id="task-c5-rg01",
        phase_id="rg01",
        milestone_id="milestone-c5",
        title="Implement RG01 external review policy",
        objective="Bounded RG01 policy loader",
        in_scope=("src/aipc/controller/task_queue.py",),
        validation_commands=("pytest -q",),
        approval_id="appr-c5-01",
        approval_evidence_path=str(auth_path),
    )
    queue = PersistentTaskQueue(tmp_path / "queue.json")
    snap = queue.initialize(
        project_id="aipc",
        milestone_id="milestone-c5",
        contracts=(contract,),
        approval_id="appr-c5-01",
        approval_evidence_path=auth_path,
    )
    assert snap.tasks[0].status == QueuedTaskStatus.ELIGIBLE
    running = queue.begin_dispatch("task-c5-rg01", worker_id="agy-hydrogen", provider="gemini")
    assert running.status == QueuedTaskStatus.RUNNING


def test_task_queue_c5_rejects_missing_denied_mismatched_approval_and_wrong_workstream(
    tmp_path: Path,
) -> None:
    auth_path = _persist_c5_approval(
        tmp_path / "milestone_c5_approval.json",
        workstreams=("rg01",),
    )

    with pytest.raises(ValueError):
        ApprovedTaskContract(
            task_id="task-c5-rg01",
            phase_id="rg01",
            milestone_id="milestone-c5",
            title="Missing approval id",
            objective="Should fail",
            in_scope=("src/aipc/controller/task_queue.py",),
            validation_commands=("pytest -q",),
            approval_id=None,
        )

    contract = ApprovedTaskContract(
        task_id="task-c5-rg01",
        phase_id="rg01",
        milestone_id="milestone-c5",
        title="Valid contract for queue checks",
        objective="Bounded RG01",
        in_scope=("src/aipc/controller/task_queue.py",),
        validation_commands=("pytest -q",),
        approval_id="appr-c5-01",
    )

    # Missing persisted approval file
    missing_dir = tmp_path / "missing"
    missing_dir.mkdir()
    with pytest.raises(ControllerError):
        PersistentTaskQueue(missing_dir / "queue.json").initialize(
            project_id="aipc",
            milestone_id="milestone-c5",
            contracts=(contract,),
            approval_id="appr-c5-01",
        )

    # Denied approval
    denied_dir = tmp_path / "denied"
    denied_dir.mkdir()
    denied_path = _persist_c5_approval(denied_dir / "milestone_c5_approval.json", decision="DENIED")
    with pytest.raises(ControllerError):
        PersistentTaskQueue(denied_dir / "queue.json").initialize(
            project_id="aipc",
            milestone_id="milestone-c5",
            contracts=(contract,),
            approval_id="appr-c5-01",
            approval_evidence_path=denied_path,
        )

    # Mismatched approval_id
    with pytest.raises(ValueError):
        ApprovedTaskContract(
            task_id="task-c5-rg01",
            phase_id="rg01",
            milestone_id="milestone-c5",
            title="Mismatched approval id",
            objective="Should fail",
            in_scope=("src/aipc/controller/task_queue.py",),
            validation_commands=("pytest -q",),
            approval_id="appr-wrong",
            approval_evidence_path=str(auth_path),
        )

    # Unauthorized workstream rg03 when only rg01 is approved
    rg03_contract = ApprovedTaskContract(
        task_id="task-c5-rg03",
        phase_id="rg03",
        milestone_id="milestone-c5",
        title="Unapproved RG03",
        objective="Should fail",
        in_scope=("src/aipc/controller/task_queue.py",),
        validation_commands=("pytest -q",),
        approval_id="appr-c5-01",
    )
    with pytest.raises(ControllerError):
        PersistentTaskQueue(tmp_path / "queue_rg03.json").initialize(
            project_id="aipc",
            milestone_id="milestone-c5",
            contracts=(rg03_contract,),
            approval_id="appr-c5-01",
            approval_evidence_path=auth_path,
        )

    # Substring trap: Milestone C approval must NOT permit Milestone C.5
    c_auth_path = _persist_c_approval(tmp_path / "milestone_c_approval.json")
    with pytest.raises(ValueError):
        ApprovedTaskContract(
            task_id="task-c5-rg01",
            phase_id="rg01",
            milestone_id="milestone-c5",
            title="C approval used for C5",
            objective="Must fail",
            in_scope=("src/aipc/controller/task_queue.py",),
            validation_commands=("pytest -q",),
            approval_id="appr-c-01",
            approval_evidence_path=str(c_auth_path),
        )
    c_evidence = load_milestone_authorization_evidence(c_auth_path)
    with pytest.raises(ValueError):
        verify_milestone_c_authorization_scope(
            c_evidence,
            project_id="aipc",
            milestone_id="milestone-c5",
            phase_id="rg01",
            in_scope_paths=("src/aipc/controller/task_queue.py",),
            expected_approval_id="appr-c-01",
        )


def test_task_queue_rejects_milestone_d_and_all_unapproved_future_or_unknown_milestones(
    tmp_path: Path,
) -> None:
    bad_milestones = (
        "milestone-d",
        "milestone_d",
        "d01",
        "phase-25",
        "aipc-2.0",
        "milestone-e",
        "milestone-c6",
        "unknown-milestone",
    )
    for bad_ms in bad_milestones:
        with pytest.raises(ValueError):
            ApprovedTaskContract(
                task_id="task-bad-01",
                phase_id="phase-01",
                milestone_id=bad_ms,
                title="Unauthorized milestone",
                objective="Must fail",
                in_scope=("src/aipc/controller/task_queue.py",),
                validation_commands=("pytest -q",),
            )

    valid_legacy_contract = ApprovedTaskContract(
        task_id="task-24-1",
        phase_id="phase-24",
        milestone_id="phase-24",
        title="Legacy task",
        objective="Should not initialize under unapproved milestone",
        in_scope=("src/aipc/controller/task_queue.py",),
        validation_commands=("pytest -q",),
    )
    for bad_ms in bad_milestones:
        with pytest.raises(ControllerError):
            PersistentTaskQueue(tmp_path / f"q_{bad_ms}.json").initialize(
                project_id="aipc",
                milestone_id=bad_ms,
                contracts=(valid_legacy_contract,),
            )

    for bad_phase in ("rg05", "c04", "d01", "milestone-d"):
        with pytest.raises(ValueError):
            ApprovedTaskContract(
                task_id="task-c5-bad",
                phase_id=bad_phase,
                milestone_id="milestone-c5",
                title="Bad phase",
                objective="Must fail",
                in_scope=("src/aipc/controller/task_queue.py",),
                validation_commands=("pytest -q",),
                approval_id="appr-c5-01",
            )


def test_task_queue_preserves_legacy_phase24_and_milestone_c_compatibility(
    tmp_path: Path,
) -> None:
    legacy_contract = ApprovedTaskContract(
        task_id="task-24-1",
        phase_id="phase-24",
        milestone_id="phase-24",
        title="Phase 24 task",
        objective="Legacy compatibility",
        in_scope=("src/aipc/controller/task_queue.py",),
        validation_commands=("pytest -q",),
    )
    p24_queue = PersistentTaskQueue(tmp_path / "p24_queue.json")
    p24_snap = p24_queue.initialize(
        project_id="aipc",
        milestone_id="phase-24",
        contracts=(legacy_contract,),
    )
    assert p24_snap.tasks[0].status == QueuedTaskStatus.ELIGIBLE

    c_dir = tmp_path / "mc"
    c_dir.mkdir()
    _persist_c_approval(c_dir / "milestone_c_approval.json")
    c_contract = ApprovedTaskContract(
        task_id="task-c01",
        phase_id="c01",
        milestone_id="milestone-c",
        title="Milestone C task",
        objective="Old C compatibility",
        in_scope=("src/aipc/controller/task_queue.py",),
        validation_commands=("pytest -q",),
        approval_id="appr-c-01",
    )
    c_queue = PersistentTaskQueue(c_dir / "queue.json")
    c_snap = c_queue.initialize(
        project_id="aipc",
        milestone_id="milestone-c",
        contracts=(c_contract,),
    )
    assert c_snap.tasks[0].status == QueuedTaskStatus.ELIGIBLE


def test_planner_human_goal_spec_c5_approval_and_rejections(tmp_path: Path) -> None:
    c5_auth_path = _persist_c5_approval(tmp_path / "c5_approval.json")
    c_auth_path = _persist_c_approval(tmp_path / "c_approval.json")
    denied_c5_path = _persist_c5_approval(tmp_path / "c5_denied.json", decision="DENIED")

    # Positive C5 RG01
    goal = _goal_spec(
        milestone_id="milestone-c5",
        phase_id="rg01",
        phase_name="External Review Policy",
        approval_granted=True,
        approval_id="appr-c5-01",
        approval_evidence_path=str(c5_auth_path),
    )
    assert goal.milestone_id == "milestone-c5"

    # Unapproved C5 rejected
    with pytest.raises(ValueError):
        _goal_spec(milestone_id="milestone-c5", phase_id="rg01", approval_granted=False)

    with pytest.raises(ValueError):
        _goal_spec(
            milestone_id="milestone-c5",
            phase_id="rg01",
            approval_granted=True,
            approval_id="appr-c5-01",
            approval_evidence_path=str(denied_c5_path),
        )

    # Cross-milestone evidence rejected (C approval cannot unlock C5, C5 cannot unlock C)
    with pytest.raises(ValueError):
        _goal_spec(
            milestone_id="milestone-c5",
            phase_id="rg01",
            approval_granted=True,
            approval_id="appr-c-01",
            approval_evidence_path=str(c_auth_path),
        )
    with pytest.raises(ValueError):
        _goal_spec(
            milestone_id="milestone-c",
            phase_id="c01",
            approval_granted=True,
            approval_id="appr-c5-01",
            approval_evidence_path=str(c5_auth_path),
        )

    # Milestone D and all unknown/future milestones rejected
    for bad_ms, bad_phase in (
        ("milestone-d", "d01"),
        ("milestone-e", "e01"),
        ("milestone-c6", "rg01"),
        ("phase-25", "phase-25"),
        ("unknown-milestone", "phase-01"),
    ):
        with pytest.raises(ValueError):
            _goal_spec(
                milestone_id=bad_ms,
                phase_id=bad_phase,
                approval_granted=True,
                approval_id="appr-c5-01",
                approval_evidence_path=str(c5_auth_path),
            )

    # Legacy compatibility: phase-24, milestone-a, and approved milestone-c still work
    assert _goal_spec(milestone_id="phase-24", phase_id="phase-24").milestone_id == "phase-24"
    assert _goal_spec(milestone_id="milestone-a", phase_id="a01").milestone_id == "milestone-a"
    assert (
        _goal_spec(
            milestone_id="milestone-c",
            phase_id="c01",
            approval_granted=True,
            approval_id="appr-c-01",
            approval_evidence_path=str(c_auth_path),
        ).milestone_id
        == "milestone-c"
    )


def test_trusted_harness_c5_requires_persisted_approval_and_blocks_d_unknown(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "candidate"
    (workspace / "src" / "aipc" / "review").mkdir(parents=True)
    gate_file = workspace / "src" / "aipc" / "review" / "gate.py"
    gate_file.write_text("def ok() -> bool:\n    return True\n", encoding="utf-8")

    # C5 without persisted approval fails closed
    with pytest.raises(ValueError, match=UNAUTHORIZED_MILESTONE_D_BLOCKED):
        assert_milestone_c_scope_authorized("milestone-c5", phase_id="rg01", task_id="task-rg01")

    with pytest.raises(ValueError, match=UNAUTHORIZED_MILESTONE_D_BLOCKED):
        build_trusted_harness_manifest(
            project_id="aipc",
            milestone_id="milestone-c5",
            workspace=workspace,
            protected_rel_paths=("src/aipc/review/gate.py",),
            trusted_manifest_dir=tmp_path / "manifests",
        )

    # C5 with valid external persisted approval succeeds
    c5_auth_path = _persist_c5_approval(tmp_path / "external_c5_approval.json")
    assert_milestone_c_scope_authorized(
        "milestone-c5",
        phase_id="rg01",
        task_id="task-rg01",
        approval_id="appr-c5-01",
        approval_evidence_path=c5_auth_path,
        project_id="aipc",
    )
    manifest_c5 = build_trusted_harness_manifest(
        project_id="aipc",
        milestone_id="milestone-c5",
        workspace=workspace,
        protected_rel_paths=("src/aipc/review/gate.py",),
        trusted_manifest_dir=tmp_path / "manifests",
        approval_id="appr-c5-01",
        approval_evidence_path=c5_auth_path,
    )
    assert manifest_c5.milestone_id == "milestone-c5"
    assert manifest_c5.approval_id == "appr-c5-01"

    # Denied or mismatched approval fails closed
    denied_path = _persist_c5_approval(tmp_path / "denied_c5.json", decision="DENIED")
    with pytest.raises(ValueError, match=UNAUTHORIZED_MILESTONE_D_BLOCKED):
        assert_milestone_c_scope_authorized(
            "milestone-c5",
            phase_id="rg01",
            approval_id="appr-c5-01",
            approval_evidence_path=denied_path,
            project_id="aipc",
        )

    # Milestone D and unknown milestones fail closed
    for bad_ms in ("milestone-d", "d01", "milestone-e", "milestone-c6", "unknown-milestone"):
        with pytest.raises(ValueError, match=UNAUTHORIZED_MILESTONE_D_BLOCKED):
            assert_milestone_c_scope_authorized(bad_ms)

    # Milestone C cannot smuggle rg01 task_id without C5 approval
    with pytest.raises(ValueError, match=UNAUTHORIZED_MILESTONE_D_BLOCKED):
        assert_milestone_c_scope_authorized("milestone-c", task_id="task-rg01")

    # Old Milestone C backward compatibility preserved without approval args
    assert_milestone_c_scope_authorized("milestone-c", phase_id="c03", task_id="task-c03")
    manifest_c = build_trusted_harness_manifest(
        project_id="aipc",
        milestone_id="milestone-c",
        workspace=workspace,
        protected_rel_paths=("src/aipc/review/gate.py",),
        trusted_manifest_dir=tmp_path / "manifests_c",
    )
    assert manifest_c.milestone_id == "milestone-c"


def test_trusted_harness_legacy_task_labels_and_future_scope_denials(tmp_path: Path) -> None:
    # Legitimate legacy Milestone C and Phase 00-24 task identifiers remain accepted
    assert_milestone_c_scope_authorized("milestone-c", phase_id="c03", task_id="task-c16-keyring")
    assert_milestone_c_scope_authorized("milestone-c", task_id="task-c16-keyring")
    assert_milestone_c_scope_authorized("milestone-c", phase_id="c02", task_id="task-c10-audit")
    for milestone_id, phase_id, task_id in (
        ("phase-24", "phase-00", "task-phase-00-bootstrap"),
        ("phase-24", "phase-12", "task-12-codex"),
        ("phase-24", "phase-24", "task-phase-24-01"),
        ("milestone-a", "a01", "task-a01-init"),
        ("milestone-b", "b03", "task-b03-repair"),
    ):
        assert_milestone_c_scope_authorized(milestone_id, phase_id=phase_id, task_id=task_id)

    # Declared future milestones remain blocked even when paired with a legacy task label
    for bad_ms in (
        "milestone-d",
        "milestone_d",
        "d01",
        "d02",
        "d03",
        "d04",
        "d99",
        "milestone-e",
        "milestone_e",
        "milestone-f",
        "milestone-z",
        "phase-25",
        "phase-100",
        "milestone-c6",
        "unknown-milestone",
    ):
        with pytest.raises(ValueError, match=UNAUTHORIZED_MILESTONE_D_BLOCKED):
            assert_milestone_c_scope_authorized(bad_ms, task_id="task-c16-keyring")

    # Unauthorized phase/workstream declarations remain blocked even with a legacy task label
    for bad_phase in (
        "c04",
        "c16",
        "d01",
        "d04",
        "d99",
        "milestone-d",
        "milestone_d",
        "milestone-e",
        "phase-25",
        "rg01",
        "rg05",
    ):
        with pytest.raises(ValueError, match=UNAUTHORIZED_MILESTONE_D_BLOCKED):
            assert_milestone_c_scope_authorized(
                "milestone-c",
                phase_id=bad_phase,
                task_id="task-c16-keyring",
            )

    # Future/unauthorized task tokens remain blocked under milestone-c
    for bad_task in (
        "task-c04",
        "task-d01",
        "task-d04",
        "task-d99",
        "task-milestone-d",
        "task-milestone_d",
        "task-milestone-e",
        "task-phase-25",
        "task-rg01",
        "task-rg05",
        "task-c16-d04",
        "task-c16-milestone-e",
        "task-c16-rg01",
    ):
        with pytest.raises(ValueError, match=UNAUTHORIZED_MILESTONE_D_BLOCKED):
            assert_milestone_c_scope_authorized("milestone-c", phase_id="c03", task_id=bad_task)

    # Legacy C task label is not permitted under milestone-c5, and C5 without approval still fails
    c5_auth_path = _persist_c5_approval(tmp_path / "c5_approval.json")
    with pytest.raises(ValueError, match=UNAUTHORIZED_MILESTONE_D_BLOCKED):
        assert_milestone_c_scope_authorized(
            "milestone-c5",
            phase_id="rg01",
            task_id="task-c16-keyring",
            approval_id="appr-c5-01",
            approval_evidence_path=c5_auth_path,
            project_id="aipc",
        )
    with pytest.raises(ValueError, match=UNAUTHORIZED_MILESTONE_D_BLOCKED):
        assert_milestone_c_scope_authorized("milestone-c5", phase_id="rg01", task_id="task-rg01")


def test_aipc_implementation_worker_authorizes_c5_rg01_rg04_and_preserves_restrictions() -> None:
    worker_md = Path(".agents/agents/aipc-implementation-worker.md").read_text(encoding="utf-8")
    lowered = worker_md.lower()
    assert "milestone-c5" in lowered or "c.5" in lowered
    assert "rg01" in lowered and "rg04" in lowered
    assert "never call `run_command` or shell tools" in lowered
    assert "never self-approve" in lowered
    assert "milestone d" in lowered


def test_c6_authorization_evidence_roundtrip_and_digest_verification(tmp_path: Path) -> None:
    default_ev = MilestoneAuthorizationEvidence(
        approval_id="appr-c6-default",
        project_id="aipc",
        milestone_id="milestone-c6",
    )
    assert default_ev.baseline_commit_sha == C6_BASELINE_SHA
    assert default_ev.authorized_workstreams == ("cisl01", "cisl02", "cisl03")

    auth_path = _persist_c6_approval(tmp_path / "milestone_c6_approval.json")
    loaded = load_milestone_authorization_evidence(auth_path)
    assert loaded.milestone_id == "milestone-c6"
    assert loaded.baseline_commit_sha == C6_BASELINE_SHA
    assert loaded.authorized_workstreams == ("cisl01", "cisl02", "cisl03")
    assert loaded.evidence_digest is not None

    raw = json.loads(auth_path.read_text(encoding="utf-8"))
    raw["authorized_workstreams"] = ["cisl01"]
    tampered_path = tmp_path / "tampered_c6.json"
    tampered_path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="digest mismatch"):
        load_milestone_authorization_evidence(tampered_path)

    no_digest_raw = json.loads(auth_path.read_text(encoding="utf-8"))
    no_digest_raw["evidence_digest"] = None
    no_digest_path = tmp_path / "no_digest_c6.json"
    no_digest_path.write_text(json.dumps(no_digest_raw), encoding="utf-8")
    with pytest.raises(ValueError, match="evidence_digest"):
        load_milestone_authorization_evidence(no_digest_path)

    for bad_ws, bad_sha in (
        (("rg01",), C6_BASELINE_SHA),
        (("cisl04",), C6_BASELINE_SHA),
        (("cisl01",), C5_BASELINE_SHA),
    ):
        with pytest.raises(ValueError):
            MilestoneAuthorizationEvidence(
                approval_id="appr-c6-bad",
                project_id="aipc",
                milestone_id="milestone-c6",
                baseline_commit_sha=bad_sha,
                authorized_workstreams=bad_ws,
            )


def test_task_queue_c6_cisl01_to_cisl03_positive_and_rejections(tmp_path: Path) -> None:
    auth_path = _persist_c6_approval(tmp_path / "milestone_c6_approval.json")
    for ws in ("cisl01", "cisl02", "cisl03"):
        contract = ApprovedTaskContract(
            task_id=f"task-c6-{ws}",
            phase_id=ws,
            milestone_id="milestone-c6",
            title=f"Implement {ws.upper()} task",
            objective=f"Bounded {ws.upper()} execution",
            in_scope=("src/aipc/controller/task_queue.py",),
            validation_commands=("pytest -q",),
            approval_id="appr-c6-01",
            approval_evidence_path=str(auth_path),
        )
        queue_dir = tmp_path / f"q_{ws}"
        queue_dir.mkdir()
        queue = PersistentTaskQueue(queue_dir / "queue.json")
        snap = queue.initialize(
            project_id="aipc",
            milestone_id=contract.milestone_id,
            contracts=(contract,),
            approval_id="appr-c6-01",
            approval_evidence_path=auth_path,
        )
        assert snap.tasks[0].status == QueuedTaskStatus.ELIGIBLE
        running = queue.begin_dispatch(
            f"task-c6-{ws}",
            worker_id="agy-hydrogen",
            provider="gemini",
        )
        assert running.status == QueuedTaskStatus.RUNNING

    # Unapproved milestone-c6 contracts must fail closed
    for bad_phase, bad_appr in (
        ("phase-01", "appr-c6-01"),
        ("cisl01", None),
        ("cisl04", "appr-c6-01"),
        ("rce01", "appr-c6-01"),
        ("d01", "appr-c6-01"),
    ):
        with pytest.raises(ValueError):
            ApprovedTaskContract(
                task_id="task-c6-bad",
                phase_id=bad_phase,
                milestone_id="milestone-c6",
                title="Invalid C6 contract",
                objective="Must fail",
                in_scope=("src/aipc/controller/task_queue.py",),
                validation_commands=("pytest -q",),
                approval_id=bad_appr,
            )

    # Cross-milestone C5 <-> C6 approval reuse must fail closed
    c5_auth_path = _persist_c5_approval(tmp_path / "milestone_c5_approval.json")
    with pytest.raises(ValueError):
        ApprovedTaskContract(
            task_id="task-c6-cisl01",
            phase_id="cisl01",
            milestone_id="milestone-c6",
            title="C5 approval used for C6",
            objective="Must fail",
            in_scope=("src/aipc/controller/task_queue.py",),
            validation_commands=("pytest -q",),
            approval_id="appr-c5-01",
            approval_evidence_path=str(c5_auth_path),
        )
    with pytest.raises(ValueError):
        ApprovedTaskContract(
            task_id="task-c5-rg01",
            phase_id="rg01",
            milestone_id="milestone-c5",
            title="C6 approval used for C5",
            objective="Must fail",
            in_scope=("src/aipc/controller/task_queue.py",),
            validation_commands=("pytest -q",),
            approval_id="appr-c6-01",
            approval_evidence_path=str(auth_path),
        )


def test_task_queue_and_planner_block_c7_rce_cisl04_and_milestone_d(tmp_path: Path) -> None:
    c6_auth_path = _persist_c6_approval(tmp_path / "c6_approval.json")
    c5_auth_path = _persist_c5_approval(tmp_path / "c5_approval.json")

    # Positive HumanGoalSpec for C.6
    goal = _goal_spec(
        milestone_id="milestone-c6",
        phase_id="cisl01",
        phase_name="CISL01 Scope Bootstrap",
        approval_granted=True,
        approval_id="appr-c6-01",
        approval_evidence_path=str(c6_auth_path),
    )
    assert goal.milestone_id == "milestone-c6"

    # Cross-milestone C5 <-> C6 rejected in HumanGoalSpec
    with pytest.raises(ValueError):
        _goal_spec(
            milestone_id="milestone-c6",
            phase_id="cisl01",
            approval_granted=True,
            approval_id="appr-c5-01",
            approval_evidence_path=str(c5_auth_path),
        )
    with pytest.raises(ValueError):
        _goal_spec(
            milestone_id="milestone-c5",
            phase_id="rg01",
            approval_granted=True,
            approval_id="appr-c6-01",
            approval_evidence_path=str(c6_auth_path),
        )

    # C.7, RCE01-RCE03, CISL04, and Milestone D rejected in ApprovedTaskContract & HumanGoalSpec
    for bad_ms, bad_phase in (
        ("milestone-c7", "rce01"),
        ("c.7", "rce02"),
        ("milestone-c6", "cisl04"),
        ("milestone-c6", "rce03"),
        ("milestone-d", "d01"),
        ("milestone-d", "d03"),
    ):
        with pytest.raises(ValueError):
            ApprovedTaskContract(
                task_id="task-bad-future",
                phase_id=bad_phase,
                milestone_id=bad_ms,
                title="Blocked future scope",
                objective="Must fail",
                in_scope=("src/aipc/controller/task_queue.py",),
                validation_commands=("pytest -q",),
                approval_id="appr-c6-01",
            )
        with pytest.raises(ValueError):
            _goal_spec(
                milestone_id=bad_ms,
                phase_id=bad_phase,
                approval_granted=True,
                approval_id="appr-c6-01",
                approval_evidence_path=str(c6_auth_path),
            )


def test_trusted_harness_c6_requires_approval_and_blocks_c7_cisl04_d(tmp_path: Path) -> None:
    workspace = tmp_path / "candidate_c6"
    (workspace / "src" / "aipc" / "review").mkdir(parents=True)
    gate_file = workspace / "src" / "aipc" / "review" / "gate.py"
    gate_file.write_text("def ok() -> bool:\n    return True\n", encoding="utf-8")

    # Without approval_evidence_path, C.6 must fail closed
    with pytest.raises(ValueError, match=UNAUTHORIZED_MILESTONE_D_BLOCKED):
        assert_milestone_c_scope_authorized(
            "milestone-c6", phase_id="cisl01", task_id="task-cisl01"
        )

    # With valid external C.6 approval, C.6 succeeds (including task_id extraction)
    c6_auth_path = _persist_c6_approval(tmp_path / "external_c6_approval.json")
    assert_milestone_c_scope_authorized(
        "milestone-c6",
        task_id="task-cisl02",
        approval_id="appr-c6-01",
        approval_evidence_path=c6_auth_path,
        project_id="aipc",
    )
    manifest_c6 = build_trusted_harness_manifest(
        project_id="aipc",
        milestone_id="milestone-c6",
        workspace=workspace,
        protected_rel_paths=("src/aipc/review/gate.py",),
        trusted_manifest_dir=tmp_path / "manifests_c6",
        approval_id="appr-c6-01",
        approval_evidence_path=c6_auth_path,
    )
    assert manifest_c6.milestone_id == "milestone-c6"
    assert manifest_c6.approval_id == "appr-c6-01"

    # Cross-milestone C5 <-> C6 and cisl01..03 under non-C6 milestones fail closed
    c5_auth_path = _persist_c5_approval(tmp_path / "external_c5_approval.json")
    with pytest.raises(ValueError, match=UNAUTHORIZED_MILESTONE_D_BLOCKED):
        assert_milestone_c_scope_authorized(
            "milestone-c6",
            phase_id="cisl01",
            approval_id="appr-c5-01",
            approval_evidence_path=c5_auth_path,
            project_id="aipc",
        )
    with pytest.raises(ValueError, match=UNAUTHORIZED_MILESTONE_D_BLOCKED):
        assert_milestone_c_scope_authorized("milestone-c", phase_id="cisl01")
    with pytest.raises(ValueError, match=UNAUTHORIZED_MILESTONE_D_BLOCKED):
        assert_milestone_c_scope_authorized("milestone-c", task_id="task-cisl01")

    # C.7, RCE01-RCE03, CISL04, and Milestone D fail closed
    for bad_ms, bad_phase, bad_task in (
        ("milestone-c7", "rce01", "task-rce01"),
        ("c.7", "rce02", "task-rce02"),
        ("milestone-c", "cisl04", "task-cisl04"),
        ("milestone-c", "rce03", "task-c16-rce03"),
        ("milestone-d", "d01", "task-d01"),
        ("milestone-d", "d03", "task-d03"),
    ):
        with pytest.raises(ValueError, match=UNAUTHORIZED_MILESTONE_D_BLOCKED):
            assert_milestone_c_scope_authorized(bad_ms, phase_id=bad_phase, task_id=bad_task)


def test_aipc_implementation_worker_authorizes_c6_cisl01_cisl03_and_blocks_c7_d() -> None:
    worker_md = Path(".agents/agents/aipc-implementation-worker.md").read_text(encoding="utf-8")
    lowered = worker_md.lower()
    assert "milestone-c6" in lowered or "c.6" in lowered
    assert "cisl01-cisl03" in lowered
    assert "milestone-c7" in lowered
    assert "rce01" in lowered and "rce03" in lowered
    assert "milestone d" in lowered
    assert "d01" in lowered and "d03" in lowered
