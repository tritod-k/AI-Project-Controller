"""Unit tests for Workstream C03 — Trusted Acceptance Harness & Self-Development Dogfooding."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError

import aipc.review as review_pkg
from aipc.controller.self_dev_runner import (
    CandidateImportOriginReport,
    CandidateVerificationReceipt,
    StableCandidateConfig,
    verify_candidate_import_origin,
)
from aipc.domain.enums import AgentStatus, ChangeType, CriterionStatus, ReviewVerdict
from aipc.domain.models import (
    AgentResult,
    CommandResult,
    CriterionResult,
    EvidenceReferences,
    FileChange,
    ReviewResult,
    TaskRequest,
    TestSummary,
)
from aipc.evidence.models import (
    ArtifactRecord,
    CommandEvidence,
    EvidenceBundle,
    EvidenceItem,
    GitEvidence,
    TestEvidence,
)
from aipc.project.models import (
    AcceptanceCriterion,
    AgentAssignments,
    ControllerConfig,
    PhaseDefinition,
    ProjectDefinition,
    ProjectIdentity,
)
from aipc.project.state_models import ProjectState, ProjectStatus
from aipc.review.context import digest, review_provenance
from aipc.review.gate import PhaseGate
from aipc.review.models import (
    BoundReview,
    GateAction,
    PhaseGateDecision,
    PhaseReviewStage,
    ReviewContext,
    ReviewProvenance,
)
from aipc.review.reviewer import bind_review
from aipc.review.subscription_reviewer_gate import (
    SessionIsolationAuditReceipt,
    SubscriptionReviewerGateDecision,
)
from aipc.review.trusted_acceptance_harness import (
    REWARD_HACKING_DETECTED,
    TRUSTED_HARNESS_TAMPER_DETECTED,
    UNAUTHORIZED_MILESTONE_D_BLOCKED,
    DogfoodingAuditBundle,
    RewardHackingFinding,
    SelfDevelopmentDogfoodingCoordinator,
    TrustedAcceptanceEvaluation,
    TrustedAcceptanceHarnessValidator,
    TrustedHarnessManifest,
    assert_milestone_c_scope_authorized,
    build_trusted_harness_manifest,
)
from aipc.version.models import CheckpointMetadata, CheckpointType

_STABLE_SHA = "1" * 40
_CANDIDATE_SHA = "2" * 40


def _init_git_repo(repo_root: Path) -> str:
    subprocess.run(
        ["git", "init"],
        cwd=str(repo_root),
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "AIPC Test"],
        cwd=str(repo_root),
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "aipc-test@example.invalid"],
        cwd=str(repo_root),
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "add", "-A"],
        cwd=str(repo_root),
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=str(repo_root),
        check=True,
        capture_output=True,
        text=True,
    )
    return _git_head_sha(repo_root)


def _git_head_sha(repo_root: Path) -> str:
    res = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(repo_root),
        check=True,
        capture_output=True,
        text=True,
    )
    return res.stdout.strip()


def _setup_candidate_workspace(tmp_path: Path) -> tuple[Path, Path, TrustedHarnessManifest]:
    stable_root = tmp_path / "stable_checkout"
    candidate_root = tmp_path / "candidate_worktree"
    (stable_root / "src" / "aipc").mkdir(parents=True)
    (stable_root / "src" / "aipc" / "__init__.py").write_text("", encoding="utf-8")
    _init_git_repo(stable_root)
    (candidate_root / "src" / "aipc" / "review").mkdir(parents=True)
    (candidate_root / "src" / "aipc" / "__init__.py").write_text("", encoding="utf-8")
    (candidate_root / "tests" / "trusted").mkdir(parents=True)
    (candidate_root / "tests" / "unit").mkdir(parents=True)

    gate_file = candidate_root / "src" / "aipc" / "review" / "gate.py"
    gate_file.write_text("def evaluate_gate() -> bool:\n    return True\n", encoding="utf-8")

    trusted_test = candidate_root / "tests" / "trusted" / "test_contract.py"
    trusted_test.write_text(
        "def test_trusted_contract() -> None:\n    assert 2 + 2 == 4\n",
        encoding="utf-8",
    )

    unit_test = candidate_root / "tests" / "unit" / "test_feature.py"
    unit_test.write_text(
        "def test_feature_behavior() -> None:\n    result = 10 * 2\n    assert result == 20\n",
        encoding="utf-8",
    )

    _init_git_repo(candidate_root)

    manifest = build_trusted_harness_manifest(
        project_id="aipc",
        milestone_id="milestone-c",
        workspace=candidate_root,
        protected_rel_paths=(
            "src/aipc/review/gate.py",
            "tests/trusted/test_contract.py",
        ),
        manifest_id="manifest-c03-01",
    )
    return stable_root, candidate_root, manifest


def test_review_package_lazy_exports_c03_symbols() -> None:
    expected_names = (
        "TrustedHarnessManifest",
        "TrustedAcceptanceHarnessValidator",
        "TrustedAcceptanceEvaluation",
        "RewardHackingFinding",
        "DogfoodingAuditBundle",
        "SelfDevelopmentDogfoodingCoordinator",
        "assert_milestone_c_scope_authorized",
        "UNAUTHORIZED_MILESTONE_D_BLOCKED",
        "TRUSTED_HARNESS_TAMPER_DETECTED",
        "REWARD_HACKING_DETECTED",
    )
    for name in expected_names:
        assert hasattr(review_pkg, name)
        assert name in review_pkg.__all__
    assert review_pkg.TrustedHarnessManifest is TrustedHarnessManifest
    assert review_pkg.SelfDevelopmentDogfoodingCoordinator is SelfDevelopmentDogfoodingCoordinator


def test_positive_candidate_evaluation_passes_when_harness_intact(tmp_path: Path) -> None:
    _, candidate_root, manifest = _setup_candidate_workspace(tmp_path)
    validator = TrustedAcceptanceHarnessValidator()

    evaluation = validator.evaluate_candidate(
        manifest=manifest,
        workspace=candidate_root,
        task_id="task-c03-01",
        candidate_sha=_CANDIDATE_SHA,
        changed_files=(
            FileChange(path="src/aipc/review/feature.py", change_type=ChangeType.ADDED),
            FileChange(path="tests/unit/test_feature.py", change_type=ChangeType.ADDED),
        ),
        tests_summary=TestSummary(total=2, passed=2, failed=0, skipped=0),
        verification_receipt=_make_valid_verification_receipt(candidate_root, _CANDIDATE_SHA),
    )

    assert evaluation.passed is True
    assert evaluation.harness_intact is True
    assert evaluation.behavioral_tests_verified is True
    assert evaluation.findings == ()
    assert evaluation.rejection_code is None


def test_positive_evaluation_requires_verification_receipt(tmp_path: Path) -> None:
    _, candidate_root, manifest = _setup_candidate_workspace(tmp_path)
    validator = TrustedAcceptanceHarnessValidator()

    evaluation = validator.evaluate_candidate(
        manifest=manifest,
        workspace=candidate_root,
        task_id="task-c03-no-receipt",
        candidate_sha=_CANDIDATE_SHA,
        changed_files=(
            FileChange(path="src/aipc/review/feature.py", change_type=ChangeType.ADDED),
            FileChange(path="tests/unit/test_feature.py", change_type=ChangeType.ADDED),
        ),
        tests_summary=TestSummary(total=2, passed=2, failed=0, skipped=0),
        verification_receipt=None,
    )

    assert evaluation.passed is False
    assert evaluation.behavioral_tests_verified is False
    assert evaluation.rejection_code == REWARD_HACKING_DETECTED
    assert any(f.rule_id == "UNVERIFIED_EXECUTION_RECEIPT" for f in evaluation.findings)


@pytest.mark.parametrize(
    "protected_path",
    [
        "src/aipc/review/gate.py",
        "tests/trusted/test_contract.py",
    ],
)
def test_protected_harness_modification_fails_closed(
    tmp_path: Path,
    protected_path: str,
) -> None:
    _, candidate_root, manifest = _setup_candidate_workspace(tmp_path)
    validator = TrustedAcceptanceHarnessValidator()

    evaluation = validator.evaluate_candidate(
        manifest=manifest,
        workspace=candidate_root,
        task_id="task-c03-tamper",
        candidate_sha=_CANDIDATE_SHA,
        changed_files=(FileChange(path=protected_path, change_type=ChangeType.MODIFIED),),
        tests_summary=TestSummary(total=1, passed=1, failed=0, skipped=0),
    )

    assert evaluation.passed is False
    assert evaluation.harness_intact is False
    assert evaluation.rejection_code == TRUSTED_HARNESS_TAMPER_DETECTED
    assert any(f.rule_id == "PROTECTED_HARNESS_MODIFIED" for f in evaluation.findings)


def test_pinned_sha256_tampered_on_disk_fails_closed(tmp_path: Path) -> None:
    _, candidate_root, manifest = _setup_candidate_workspace(tmp_path)
    gate_file = candidate_root / "src" / "aipc" / "review" / "gate.py"
    gate_file.write_text("def evaluate_gate() -> bool:\n    return False\n", encoding="utf-8")

    validator = TrustedAcceptanceHarnessValidator()
    evaluation = validator.evaluate_candidate(
        manifest=manifest,
        workspace=candidate_root,
        task_id="task-c03-digest",
        candidate_sha=_CANDIDATE_SHA,
        changed_files=(
            FileChange(path="tests/unit/test_feature.py", change_type=ChangeType.MODIFIED),
        ),
        tests_summary=TestSummary(total=1, passed=1, failed=0, skipped=0),
    )

    assert evaluation.passed is False
    assert evaluation.harness_intact is False
    assert evaluation.rejection_code == TRUSTED_HARNESS_TAMPER_DETECTED
    assert any(f.rule_id == "PINNED_DIGEST_MISMATCH" for f in evaluation.findings)


def test_deleting_test_file_fails_closed_with_reward_hacking(tmp_path: Path) -> None:
    _, candidate_root, manifest = _setup_candidate_workspace(tmp_path)
    validator = TrustedAcceptanceHarnessValidator()

    evaluation = validator.evaluate_candidate(
        manifest=manifest,
        workspace=candidate_root,
        task_id="task-c03-del",
        candidate_sha=_CANDIDATE_SHA,
        changed_files=(FileChange(path="tests/unit/test_old.py", change_type=ChangeType.DELETED),),
        tests_summary=TestSummary(total=1, passed=1, failed=0, skipped=0),
    )

    assert evaluation.passed is False
    assert evaluation.behavioral_tests_verified is False
    assert evaluation.rejection_code == REWARD_HACKING_DETECTED
    assert any(f.rule_id == "TEST_FILE_DELETED" for f in evaluation.findings)


def test_vacuous_test_without_assertions_fails_closed(tmp_path: Path) -> None:
    _, candidate_root, manifest = _setup_candidate_workspace(tmp_path)
    vacuous_test = candidate_root / "tests" / "unit" / "test_vacuous.py"
    vacuous_test.write_text("def test_fake():\n    pass\n", encoding="utf-8")

    validator = TrustedAcceptanceHarnessValidator()
    evaluation = validator.evaluate_candidate(
        manifest=manifest,
        workspace=candidate_root,
        task_id="task-c03-vacuous",
        candidate_sha=_CANDIDATE_SHA,
        changed_files=(
            FileChange(path="tests/unit/test_vacuous.py", change_type=ChangeType.ADDED),
        ),
        tests_summary=TestSummary(total=1, passed=1, failed=0, skipped=0),
    )

    assert evaluation.passed is False
    assert evaluation.behavioral_tests_verified is False
    assert evaluation.rejection_code == REWARD_HACKING_DETECTED
    assert any(f.rule_id == "VACUOUS_TEST_WITHOUT_ASSERT" for f in evaluation.findings)


def test_pytest_skip_injection_fails_closed(tmp_path: Path) -> None:
    _, candidate_root, manifest = _setup_candidate_workspace(tmp_path)
    skip_test = candidate_root / "tests" / "unit" / "test_skipped.py"
    skip_test.write_text(
        "import pytest\n\n"
        "def test_bypassed() -> None:\n"
        "    pytest.skip('bypass')\n"
        "    assert True\n",
        encoding="utf-8",
    )

    validator = TrustedAcceptanceHarnessValidator()
    evaluation = validator.evaluate_candidate(
        manifest=manifest,
        workspace=candidate_root,
        task_id="task-c03-skip",
        candidate_sha=_CANDIDATE_SHA,
        changed_files=(
            FileChange(path="tests/unit/test_skipped.py", change_type=ChangeType.ADDED),
        ),
        tests_summary=TestSummary(total=1, passed=1, failed=0, skipped=0),
    )

    assert evaluation.passed is False
    assert evaluation.rejection_code == REWARD_HACKING_DETECTED
    assert any(f.rule_id == "PYTEST_SKIP_OR_XFAIL_INJECTED" for f in evaluation.findings)


def test_unauthorized_milestone_d_scope_blocked() -> None:
    with pytest.raises(ValueError, match=UNAUTHORIZED_MILESTONE_D_BLOCKED):
        assert_milestone_c_scope_authorized("milestone-d")

    with pytest.raises(ValueError, match=UNAUTHORIZED_MILESTONE_D_BLOCKED):
        assert_milestone_c_scope_authorized("milestone-c", task_id="task-d01-claude")

    with pytest.raises(ValueError, match=UNAUTHORIZED_MILESTONE_D_BLOCKED):
        assert_milestone_c_scope_authorized("milestone-c", phase_id="d02-phase")

    with pytest.raises(ValueError, match=UNAUTHORIZED_MILESTONE_D_BLOCKED):
        TrustedHarnessManifest(
            manifest_id="m-blocked",
            project_id="aipc",
            milestone_id="milestone-d",
            pinned_file_sha256={"src/aipc/review/gate.py": "a" * 64},
        )


def _make_valid_gate_decision(
    task_id: str,
    candidate_sha: str = _CANDIDATE_SHA,
) -> SubscriptionReviewerGateDecision:
    digest_zero = "0" * 64
    review_result = ReviewResult(
        schema_version="1.0",
        review_id=f"rev-{task_id}",
        task_id=task_id,
        verdict=ReviewVerdict.PASS,
        confidence=0.95,
        criteria=[],
        failed_criteria=[],
        required_fixes=[],
        risks=[],
        next_phase_allowed=True,
    )
    bound_review = BoundReview(
        review=review_result,
        context_digest=digest_zero,
        review_digest=digest(review_result),
        authority="codex",
        provenance=ReviewProvenance(
            project_id="aipc",
            run_id="run-1",
            phase_id="c03",
            attempt=1,
            task_id=task_id,
            attempt_checkpoint_id="cp-1",
            git_commit=candidate_sha,
            agent_result_digest=digest_zero,
            evidence_digest=digest_zero,
        ),
    )
    phase_gate = PhaseGateDecision(
        verdict=ReviewVerdict.PASS,
        project_id="aipc",
        phase_id="c03",
        attempt=1,
        task_id=task_id,
        review_id=review_result.review_id,
        actions=(GateAction.MARK_PHASE_PASS,),
        next_phase_allowed=True,
        stable_checkpoint_allowed=True,
        repair_required=False,
        human_intervention_required=False,
        reasons=("ok",),
        failed_criteria=(),
        required_fixes=(),
    )
    return SubscriptionReviewerGateDecision(
        gate_outcome="PASS",
        task_id=task_id,
        candidate_commit_sha=candidate_sha,
        reviewed_commit_sha=candidate_sha,
        review_result=review_result,
        bound_review=bound_review,
        phase_gate_decision=phase_gate,
        session_audit=SessionIsolationAuditReceipt(
            audit_id="audit-1",
            task_id=task_id,
            candidate_commit_sha=candidate_sha,
            worker_agent_id="worker-1",
            worker_provider_family="google_antigravity",
            reviewer_binding_id="rev-bind-1",
            reviewer_provider_id="codex",
            reviewer_provider_family="openai_codex",
            reviewer_model_id="gpt-5.4",
            reviewer_session_id="sess-1",
            fresh_isolated_session=True,
            inherited_chat_history=False,
            read_only_sandbox_enforced=True,
            workspace_snapshot_before_sha256=digest_zero,
            workspace_snapshot_after_sha256=digest_zero,
            workspace_mutated=False,
            independence_level="cross_provider",
            reduced_independence=False,
        ),
        receipt_path="evidence/rev.json",
    )


def _make_valid_review_context_and_gate_decision(
    tmp_path: Path,
    task_id: str,
    candidate_sha: str,
    workspace_root: Path,
    changed_files: tuple[FileChange, ...] | None = None,
    tests_summary: TestSummary | None = None,
) -> tuple[ReviewContext, SubscriptionReviewerGateDecision]:
    if changed_files is None:
        changed_files = (
            FileChange(path="tests/unit/test_feature.py", change_type=ChangeType.ADDED),
        )
    if tests_summary is None:
        tests_summary = TestSummary(total=2, passed=2, failed=0, skipped=0)
    ev_root = tmp_path / f".ev_{task_id}"
    ev_root.mkdir(parents=True, exist_ok=True)

    project = ProjectDefinition(
        schema_version="1.0",
        project=ProjectIdentity(id="aipc", name="AIPC", workspace=str(workspace_root)),
        controller=ControllerConfig(),
        agents=AgentAssignments(planner="codex", worker="agy", reviewer="codex"),
        phases=(
            PhaseDefinition(
                id="c03",
                name="C03",
                objective="Trusted acceptance",
                acceptance_criteria=(
                    AcceptanceCriterion(
                        id="AC-01",
                        statement="Behavioral tests pass",
                        required=True,
                    ),
                ),
            ),
        ),
    )
    state = ProjectState(
        schema_version="1.0",
        project_id="aipc",
        run_id="run-1",
        current_phase="c03",
        current_attempt=1,
        status=ProjectStatus.RUNNING,
        completed_phases=(),
        blocked_phases=(),
        active_task_id=task_id,
        last_review=None,
        updated_at="2026-11-14T12:00:00Z",
    )
    task = TaskRequest(
        schema_version="1.0",
        task_id=task_id,
        project_id="aipc",
        phase_id="c03",
        attempt=1,
        task_type="coding",
        prompt="Implement C03 feature",
        workspace=str(workspace_root),
        allowed_tools=["read_file"],
        forbidden_actions=[],
        timeout_seconds=60,
    )
    agent_result = AgentResult(
        schema_version="1.0",
        task_id=task_id,
        agent_id="worker-1",
        provider="google_antigravity",
        status=AgentStatus.COMPLETED,
        started_at="2026-11-14T11:59:00Z",
        completed_at="2026-11-14T12:00:00Z",
        summary="Completed C03 feature",
        files_changed=list(changed_files),
        commands=[CommandResult(command="pytest", exit_code=0)],
        tests=tests_summary,
        artifacts=[],
        warnings=[],
        errors=[],
        approval_requests=[],
        worker_claimed_complete=True,
        evidence=EvidenceReferences(
            git_diff_path="git_diff.patch",
            test_log_path="test_log.txt",
            terminal_log_path="terminal_log.txt",
            screenshots=[],
        ),
        raw_output_ref="raw_output.txt",
    )
    checkpoint = CheckpointMetadata(
        checkpoint_id="phase-c03-attempt-01",
        checkpoint_type=CheckpointType.ATTEMPT,
        project_id="aipc",
        phase_id="c03",
        attempt=1,
        git_commit=candidate_sha,
        git_ref="refs/tags/aipc/aipc/phase-c03/attempt-01",
        source_branch="main",
        created_at="2026-11-14T12:00:00Z",
        parent_checkpoint="phase-c03-pre",
    )
    cmd_ev = (CommandEvidence(command="pytest", exit_code=0, terminal_log_ref="terminal_log.txt"),)
    file_specs: list[tuple[str, str, bytes]] = [
        ("task", "task.json", task.model_dump_json().encode("utf-8")),
        ("agent_result", "agent_result.json", agent_result.model_dump_json().encode("utf-8")),
        (
            "commands",
            "commands.json",
            json.dumps(
                {
                    "source": "agent_result",
                    "commands": [c.model_dump(mode="json") for c in cmd_ev],
                }
            ).encode("utf-8"),
        ),
        ("git_status", "git_status.txt", b""),
        ("git_diff", "git_diff.patch", b""),
        ("git_summary", "git_summary.txt", b"clean"),
        (
            "test_log",
            "test_log.txt",
            f"=== {tests_summary.passed} passed in 0.12s ===\n".encode(),
        ),
        ("terminal_log", "terminal_log.txt", b"ok\n"),
        ("raw_output", "raw_output.txt", b"ok\n"),
    ]
    artifacts: list[ArtifactRecord] = []
    items: list[EvidenceItem] = []
    for ev_id, rel_path, data in file_specs:
        (ev_root / rel_path).write_bytes(data)
        artifacts.append(
            ArtifactRecord(
                artifact_id=f"art-{ev_id}",
                path=rel_path,
                kind=ev_id,
                size=len(data),
                sha256=hashlib.sha256(data).hexdigest(),
                source="test",
            )
        )
        items.append(
            EvidenceItem(
                evidence_id=ev_id,
                kind=ev_id,
                status="collected",
                path=rel_path,
                source="test",
            )
        )
    idx_bytes = json.dumps({"artifacts": [a.model_dump(mode="json") for a in artifacts]}).encode(
        "utf-8"
    )
    (ev_root / "artifact_index.json").write_bytes(idx_bytes)
    artifacts.append(
        ArtifactRecord(
            artifact_id="art-artifact_index",
            path="artifact_index.json",
            kind="artifact_index",
            size=len(idx_bytes),
            sha256=hashlib.sha256(idx_bytes).hexdigest(),
            source="test",
        )
    )
    items.append(
        EvidenceItem(
            evidence_id="artifact_index",
            kind="artifact_index",
            status="collected",
            path="artifact_index.json",
            source="test",
        )
    )
    bundle = EvidenceBundle(
        project_id="aipc",
        run_id="run-1",
        phase_id="c03",
        attempt=1,
        task_id=task_id,
        status="COMPLETE",
        git=GitEvidence(
            status="collected",
            head=candidate_sha,
            branch="main",
            baseline=_STABLE_SHA,
            changes=changed_files,
        ),
        tests=TestEvidence(
            structured=tests_summary,
            parsed=tests_summary,
            parse_status="collected",
            log_ref="test_log.txt",
            agrees=True,
        ),
        commands=cmd_ev,
        items=tuple(items),
        artifacts=tuple(artifacts),
    )
    (ev_root / "manifest.json").write_text(bundle.model_dump_json(), encoding="utf-8")

    review_ctx = ReviewContext(
        project=project,
        state=state,
        task=task,
        agent_result=agent_result,
        evidence=bundle,
        evidence_root=str(ev_root),
        checkpoint=checkpoint,
        phase_stage=PhaseReviewStage.AWAITING_REVIEW,
    )
    review_result = ReviewResult(
        schema_version="1.0",
        review_id=f"rev-{task_id}",
        task_id=task_id,
        verdict=ReviewVerdict.PASS,
        confidence=0.95,
        criteria=[
            CriterionResult(
                criterion_id="AC-01",
                status=CriterionStatus.PASS,
                evidence=["test_log"],
                notes="verified",
            )
        ],
        failed_criteria=[],
        required_fixes=[],
        risks=[],
        next_phase_allowed=True,
    )
    bound_review = bind_review(
        review_ctx,
        review_result,
        authority="codex",
        reviewed=review_provenance(review_ctx),
    )
    phase_gate = PhaseGate().evaluate(bound_review, review_ctx)
    digest_zero = "0" * 64
    gate_decision = SubscriptionReviewerGateDecision(
        gate_outcome="PASS",
        task_id=task_id,
        candidate_commit_sha=candidate_sha,
        reviewed_commit_sha=candidate_sha,
        review_result=review_result,
        bound_review=bound_review,
        phase_gate_decision=phase_gate,
        session_audit=SessionIsolationAuditReceipt(
            audit_id="audit-1",
            task_id=task_id,
            candidate_commit_sha=candidate_sha,
            worker_agent_id="worker-1",
            worker_provider_family="google_antigravity",
            reviewer_binding_id="rev-bind-1",
            reviewer_provider_id="codex",
            reviewer_provider_family="openai_codex",
            reviewer_model_id="gpt-5.4",
            reviewer_session_id="sess-1",
            fresh_isolated_session=True,
            inherited_chat_history=False,
            read_only_sandbox_enforced=True,
            workspace_snapshot_before_sha256=digest_zero,
            workspace_snapshot_after_sha256=digest_zero,
            workspace_mutated=False,
            independence_level="cross_provider",
            reduced_independence=False,
        ),
        receipt_path="evidence/rev.json",
    )
    return review_ctx, gate_decision


def test_dogfooding_coordinator_clean_pass_stops_at_human_merge_review(tmp_path: Path) -> None:
    stable_root, candidate_root, manifest = _setup_candidate_workspace(tmp_path)
    cand_sha = _git_head_sha(candidate_root)
    config = StableCandidateConfig(
        stable_root=str(stable_root),
        candidate_worktree_root=str(candidate_root),
        pinned_stable_sha=_git_head_sha(stable_root),
        candidate_sha=cand_sha,
        verification_isolation="isolated_subprocess",
        allow_auto_promote_stable=False,
    )
    import_report = CandidateImportOriginReport(
        verified=True,
        expected_candidate_src=str(candidate_root / "src" / "aipc"),
        actual_imported_file=str(candidate_root / "src" / "aipc" / "__init__.py"),
        leaked_stable_paths=(),
        status_code="OK",
    )
    verification_receipt = CandidateVerificationReceipt(
        candidate_sha=cand_sha,
        isolation_mode="isolated_subprocess",
        import_origin_verified=True,
        import_origin_report=import_report,
        sanitized_env_verified=True,
        exit_code=0,
        passed=True,
        status_code="OK",
        summary="Isolated candidate tests passed",
    )
    changed = (FileChange(path="tests/unit/test_feature.py", change_type=ChangeType.ADDED),)
    tests_sum = TestSummary(total=2, passed=2, failed=0, skipped=0)
    review_ctx, gate_decision = _make_valid_review_context_and_gate_decision(
        tmp_path,
        "task-c03-dogfood",
        candidate_sha=cand_sha,
        workspace_root=candidate_root,
        changed_files=changed,
        tests_summary=tests_sum,
    )

    coordinator = SelfDevelopmentDogfoodingCoordinator()
    bundle = coordinator.run_dogfooding_audit(
        stable_candidate_config=config,
        manifest=manifest,
        task_id="task-c03-dogfood",
        changed_files=changed,
        tests_summary=tests_sum,
        import_origin_report=import_report,
        verification_receipt=verification_receipt,
        review_verdict=ReviewVerdict.PASS,
        gate_verdict="PASS",
        reviewer_gate_decision=gate_decision,
        review_context=review_ctx,
    )

    assert bundle.dogfooding_passed is True
    assert bundle.next_authorized_action == "HUMAN_MERGE_REVIEW"
    assert bundle.stable_worktree_mutated is False
    assert bundle.auto_promote_stable_performed is False
    assert bundle.human_merge_approval_required is True
    assert bundle.blockers == ()


def test_dogfooding_coordinator_blocks_on_import_origin_or_harness_failure(
    tmp_path: Path,
) -> None:
    stable_root, candidate_root, manifest = _setup_candidate_workspace(tmp_path)
    config = StableCandidateConfig(
        stable_root=str(stable_root),
        candidate_worktree_root=str(candidate_root),
        pinned_stable_sha=_git_head_sha(stable_root),
        candidate_sha=_CANDIDATE_SHA,
        verification_isolation="isolated_subprocess",
        allow_auto_promote_stable=False,
    )
    bad_import_report = CandidateImportOriginReport(
        verified=False,
        expected_candidate_src=str(candidate_root / "src" / "aipc"),
        actual_imported_file=str(stable_root / "src" / "aipc" / "__init__.py"),
        leaked_stable_paths=(str(stable_root / "src"),),
        status_code="IMPORT_ORIGIN_MISMATCH",
    )
    verification_receipt = CandidateVerificationReceipt(
        candidate_sha=_CANDIDATE_SHA,
        isolation_mode="isolated_subprocess",
        import_origin_verified=False,
        import_origin_report=bad_import_report,
        sanitized_env_verified=True,
        exit_code=1,
        passed=False,
        status_code="IMPORT_ORIGIN_MISMATCH",
        summary="Candidate import origin leaked into Stable",
    )

    coordinator = SelfDevelopmentDogfoodingCoordinator()
    bundle = coordinator.run_dogfooding_audit(
        stable_candidate_config=config,
        manifest=manifest,
        task_id="task-c03-blocked",
        changed_files=(
            FileChange(path="src/aipc/review/gate.py", change_type=ChangeType.MODIFIED),
        ),
        tests_summary=TestSummary(total=1, passed=1, failed=0, skipped=0),
        import_origin_report=bad_import_report,
        verification_receipt=verification_receipt,
        review_verdict=ReviewVerdict.PASS,
        gate_verdict="PASS",
    )

    assert bundle.dogfooding_passed is False
    assert bundle.next_authorized_action == "HALT_ON_BLOCKER"
    assert bundle.stable_worktree_mutated is False
    assert bundle.auto_promote_stable_performed is False
    assert TRUSTED_HARNESS_TAMPER_DETECTED in bundle.blockers
    assert any("IMPORT_ORIGIN_UNVERIFIED" in b for b in bundle.blockers)


def test_evaluation_and_bundle_validators_reject_inconsistent_pass_state() -> None:
    with pytest.raises(ValueError, match="cannot be True"):
        TrustedAcceptanceEvaluation(
            evaluation_id="eval-bad",
            project_id="aipc",
            milestone_id="milestone-c",
            task_id="task-c03-01",
            candidate_sha=_CANDIDATE_SHA,
            passed=True,
            harness_intact=False,
            behavioral_tests_verified=True,
            findings=(
                RewardHackingFinding(
                    rule_id="PROTECTED_HARNESS_MODIFIED",
                    file_path="src/aipc/review/gate.py",
                    description="Modified gate.py",
                ),
            ),
            rejection_code=TRUSTED_HARNESS_TAMPER_DETECTED,
        )

    assert DogfoodingAuditBundle is not None


def test_empty_pinned_manifest_raises_validation_error() -> None:
    with pytest.raises(ValidationError, match="pinned_file_sha256"):
        TrustedHarnessManifest(
            manifest_id="m-empty",
            project_id="aipc",
            milestone_id="milestone-c",
            pinned_file_sha256={},
        )


@pytest.mark.parametrize(
    "test_body",
    [
        "def test_trivial_const() -> None:\n    assert True\n",
        "def test_trivial_eq() -> None:\n    x = 1\n    assert x == x\n",
        "def test_not_false() -> None:\n    assert not False\n",
        "def test_unittest_true(self) -> None:\n    self.assertTrue(True)\n",
        "def test_unittest_eq_const(self) -> None:\n    self.assertEqual(1, 1)\n",
        "def test_unittest_eq_same(self) -> None:\n    x = 1\n    self.assertEqual(x, x)\n",
    ],
)
def test_trivial_tautological_assert_fails_closed(tmp_path: Path, test_body: str) -> None:
    _, candidate_root, manifest = _setup_candidate_workspace(tmp_path)
    taut_test = candidate_root / "tests" / "unit" / "test_taut.py"
    taut_test.write_text(test_body, encoding="utf-8")

    validator = TrustedAcceptanceHarnessValidator()
    evaluation = validator.evaluate_candidate(
        manifest=manifest,
        workspace=candidate_root,
        task_id="task-c03-taut",
        candidate_sha=_CANDIDATE_SHA,
        changed_files=(FileChange(path="tests/unit/test_taut.py", change_type=ChangeType.ADDED),),
        tests_summary=TestSummary(total=1, passed=1, failed=0, skipped=0),
    )

    assert evaluation.passed is False
    assert evaluation.rejection_code == REWARD_HACKING_DETECTED
    assert any(f.rule_id == "TRIVIAL_TAUTOLOGICAL_ASSERT" for f in evaluation.findings)


@pytest.mark.parametrize(
    "test_body",
    [
        "def test_dead() -> None:\n    return\n    assert 1 + 1 == 2\n",
        "def test_dead_if() -> None:\n    if False:\n        assert 1 + 1 == 2\n",
        (
            "def test_empty_tuple_loop() -> None:\n"
            "    x = 1\n"
            "    for _ in ():\n"
            "        assert x == 1\n"
        ),
        (
            "def test_empty_range_loop() -> None:\n"
            "    x = 1\n"
            "    for _ in range(0):\n"
            "        assert x == 1\n"
        ),
        (
            "def test_after_if_else_return(cond: bool) -> None:\n"
            "    if cond:\n"
            "        return\n"
            "    else:\n"
            "        return\n"
            "    assert 1 + 1 == 2\n"
        ),
    ],
)
def test_unreachable_assertion_fails_closed(tmp_path: Path, test_body: str) -> None:
    _, candidate_root, manifest = _setup_candidate_workspace(tmp_path)
    dead_test = candidate_root / "tests" / "unit" / "test_dead.py"
    dead_test.write_text(test_body, encoding="utf-8")

    validator = TrustedAcceptanceHarnessValidator()
    evaluation = validator.evaluate_candidate(
        manifest=manifest,
        workspace=candidate_root,
        task_id="task-c03-dead",
        candidate_sha=_CANDIDATE_SHA,
        changed_files=(FileChange(path="tests/unit/test_dead.py", change_type=ChangeType.ADDED),),
        tests_summary=TestSummary(total=1, passed=1, failed=0, skipped=0),
    )

    assert evaluation.passed is False
    assert evaluation.rejection_code == REWARD_HACKING_DETECTED
    assert any(f.rule_id == "UNREACHABLE_ASSERTION" for f in evaluation.findings)
    assert any(f.rule_id == "VACUOUS_TEST_WITHOUT_ASSERT" for f in evaluation.findings)


def test_missing_declared_test_file_fails_closed(tmp_path: Path) -> None:
    _, candidate_root, manifest = _setup_candidate_workspace(tmp_path)
    validator = TrustedAcceptanceHarnessValidator()

    evaluation = validator.evaluate_candidate(
        manifest=manifest,
        workspace=candidate_root,
        task_id="task-c03-missing-test",
        candidate_sha=_CANDIDATE_SHA,
        changed_files=(
            FileChange(path="tests/unit/test_nonexistent.py", change_type=ChangeType.ADDED),
        ),
        tests_summary=TestSummary(total=1, passed=1, failed=0, skipped=0),
    )

    assert evaluation.passed is False
    assert evaluation.rejection_code == REWARD_HACKING_DETECTED
    assert any(f.rule_id == "MISSING_DECLARED_TEST_FILE" for f in evaluation.findings)


def _make_valid_verification_receipt(
    candidate_root: Path,
    candidate_sha: str,
) -> CandidateVerificationReceipt:
    import_report = CandidateImportOriginReport(
        verified=True,
        expected_candidate_src=str(candidate_root / "src" / "aipc"),
        actual_imported_file=str(candidate_root / "src" / "aipc" / "__init__.py"),
        leaked_stable_paths=(),
        status_code="OK",
    )
    return CandidateVerificationReceipt(
        candidate_sha=candidate_sha,
        isolation_mode="isolated_subprocess",
        import_origin_verified=True,
        import_origin_report=import_report,
        sanitized_env_verified=True,
        exit_code=0,
        passed=True,
        status_code="OK",
        summary="Isolated candidate tests passed",
    )


def test_required_criterion_unverified_fails_closed(tmp_path: Path) -> None:
    _, candidate_root, _ = _setup_candidate_workspace(tmp_path)
    manifest = build_trusted_harness_manifest(
        project_id="aipc",
        milestone_id="milestone-c",
        workspace=candidate_root,
        protected_rel_paths=("src/aipc/review/gate.py", "tests/trusted/test_contract.py"),
        manifest_id="manifest-c03-crit",
        required_criterion_ids=("AC-01", "AC-02"),
    )
    validator = TrustedAcceptanceHarnessValidator()
    receipt = _make_valid_verification_receipt(candidate_root, _CANDIDATE_SHA)

    eval_missing = validator.evaluate_candidate(
        manifest=manifest,
        workspace=candidate_root,
        task_id="task-c03-crit-missing",
        candidate_sha=_CANDIDATE_SHA,
        changed_files=(
            FileChange(path="tests/unit/test_feature.py", change_type=ChangeType.ADDED),
        ),
        tests_summary=TestSummary(total=1, passed=1, failed=0, skipped=0),
        criterion_results={"AC-01": True},
        verification_receipt=receipt,
    )
    assert eval_missing.passed is False
    assert eval_missing.rejection_code == REWARD_HACKING_DETECTED
    assert any(f.rule_id == "REQUIRED_CRITERION_UNVERIFIED" for f in eval_missing.findings)

    eval_failed = validator.evaluate_candidate(
        manifest=manifest,
        workspace=candidate_root,
        task_id="task-c03-crit-failed",
        candidate_sha=_CANDIDATE_SHA,
        changed_files=(
            FileChange(path="tests/unit/test_feature.py", change_type=ChangeType.ADDED),
        ),
        tests_summary=TestSummary(total=1, passed=1, failed=0, skipped=0),
        criterion_results=(
            CriterionResult(
                criterion_id="AC-01",
                status=CriterionStatus.PASS,
                evidence=["ok"],
                notes="passed",
            ),
            CriterionResult(
                criterion_id="AC-02",
                status=CriterionStatus.FAIL,
                evidence=["err"],
                notes="failed",
            ),
        ),
        verification_receipt=receipt,
    )
    assert eval_failed.passed is False
    assert eval_failed.rejection_code == REWARD_HACKING_DETECTED
    assert any(f.rule_id == "REQUIRED_CRITERION_UNVERIFIED" for f in eval_failed.findings)

    eval_bool_dict = validator.evaluate_candidate(
        manifest=manifest,
        workspace=candidate_root,
        task_id="task-c03-crit-bool-dict",
        candidate_sha=_CANDIDATE_SHA,
        changed_files=(
            FileChange(path="tests/unit/test_feature.py", change_type=ChangeType.ADDED),
        ),
        tests_summary=TestSummary(total=1, passed=1, failed=0, skipped=0),
        criterion_results={"AC-01": True, "AC-02": True},
        verification_receipt=receipt,
    )
    assert eval_bool_dict.passed is False
    assert eval_bool_dict.rejection_code == REWARD_HACKING_DETECTED
    assert any(f.rule_id == "REQUIRED_CRITERION_UNVERIFIED" for f in eval_bool_dict.findings)

    eval_weak_ev = validator.evaluate_candidate(
        manifest=manifest,
        workspace=candidate_root,
        task_id="task-c03-crit-weak-ev",
        candidate_sha=_CANDIDATE_SHA,
        changed_files=(
            FileChange(path="tests/unit/test_feature.py", change_type=ChangeType.ADDED),
        ),
        tests_summary=TestSummary(total=1, passed=1, failed=0, skipped=0),
        criterion_results=(
            CriterionResult(
                criterion_id="AC-01",
                status=CriterionStatus.PASS,
                evidence=["ok"],
                notes="arbitrary evidence",
            ),
            CriterionResult(
                criterion_id="AC-02",
                status=CriterionStatus.PASS,
                evidence=["ok"],
                notes="arbitrary evidence",
            ),
        ),
        verification_receipt=receipt,
    )
    assert eval_weak_ev.passed is False
    assert eval_weak_ev.rejection_code == REWARD_HACKING_DETECTED
    assert any(f.rule_id == "REQUIRED_CRITERION_UNVERIFIED" for f in eval_weak_ev.findings)

    eval_valid = validator.evaluate_candidate(
        manifest=manifest,
        workspace=candidate_root,
        task_id="task-c03-crit-valid",
        candidate_sha=_CANDIDATE_SHA,
        changed_files=(
            FileChange(path="tests/unit/test_feature.py", change_type=ChangeType.ADDED),
        ),
        tests_summary=TestSummary(total=1, passed=1, failed=0, skipped=0),
        criterion_results=(
            CriterionResult(
                criterion_id="AC-01",
                status=CriterionStatus.PASS,
                evidence=[f"receipt:{_CANDIDATE_SHA}:AC-01:{manifest.manifest_digest}"],
                notes="bound to sha, criterion, and manifest digest",
            ),
            CriterionResult(
                criterion_id="AC-02",
                status=CriterionStatus.PASS,
                evidence=["tests/unit/test_feature.py"],
                notes="verified behavioral test file path",
            ),
        ),
        verification_receipt=receipt,
    )
    assert eval_valid.passed is True
    assert eval_valid.findings == ()
    assert eval_valid.rejection_code is None


def test_required_criterion_without_verification_receipt_fails_closed(tmp_path: Path) -> None:
    _, candidate_root, _ = _setup_candidate_workspace(tmp_path)
    manifest = build_trusted_harness_manifest(
        project_id="aipc",
        milestone_id="milestone-c",
        workspace=candidate_root,
        protected_rel_paths=("src/aipc/review/gate.py", "tests/trusted/test_contract.py"),
        manifest_id="manifest-c03-no-rcpt",
        required_criterion_ids=("AC-01",),
    )
    validator = TrustedAcceptanceHarnessValidator()

    evaluation = validator.evaluate_candidate(
        manifest=manifest,
        workspace=candidate_root,
        task_id="task-c03-crit-no-receipt",
        candidate_sha=_CANDIDATE_SHA,
        changed_files=(
            FileChange(path="tests/unit/test_feature.py", change_type=ChangeType.ADDED),
        ),
        tests_summary=TestSummary(total=1, passed=1, failed=0, skipped=0),
        criterion_results=(
            CriterionResult(
                criterion_id="AC-01",
                status=CriterionStatus.PASS,
                evidence=[f"pytest:{_CANDIDATE_SHA}"],
                notes="passed",
            ),
        ),
        verification_receipt=None,
    )
    assert evaluation.passed is False
    assert evaluation.rejection_code == REWARD_HACKING_DETECTED
    assert any(
        f.rule_id == "REQUIRED_CRITERION_UNVERIFIED" and f.file_path == "verification_receipt"
        for f in evaluation.findings
    )


def _make_clean_dogfood_inputs(
    tmp_path: Path,
) -> tuple[
    StableCandidateConfig,
    TrustedHarnessManifest,
    CandidateImportOriginReport,
    CandidateVerificationReceipt,
]:
    stable_root, candidate_root, manifest = _setup_candidate_workspace(tmp_path)
    cand_sha = _git_head_sha(candidate_root)
    config = StableCandidateConfig(
        stable_root=str(stable_root),
        candidate_worktree_root=str(candidate_root),
        pinned_stable_sha=_git_head_sha(stable_root),
        candidate_sha=cand_sha,
        verification_isolation="isolated_subprocess",
        allow_auto_promote_stable=False,
    )
    import_report = CandidateImportOriginReport(
        verified=True,
        expected_candidate_src=str(candidate_root / "src" / "aipc"),
        actual_imported_file=str(candidate_root / "src" / "aipc" / "__init__.py"),
        leaked_stable_paths=(),
        status_code="OK",
    )
    verification_receipt = CandidateVerificationReceipt(
        candidate_sha=cand_sha,
        isolation_mode="isolated_subprocess",
        import_origin_verified=True,
        import_origin_report=import_report,
        sanitized_env_verified=True,
        exit_code=0,
        passed=True,
        status_code="OK",
        summary="Isolated candidate tests passed",
    )
    return config, manifest, import_report, verification_receipt


def test_dogfooding_rejects_stale_verification_receipt_sha(tmp_path: Path) -> None:
    config, manifest, import_report, verification_receipt = _make_clean_dogfood_inputs(tmp_path)
    stale_receipt = verification_receipt.model_copy(update={"candidate_sha": "9" * 40})

    coordinator = SelfDevelopmentDogfoodingCoordinator()
    bundle = coordinator.run_dogfooding_audit(
        stable_candidate_config=config,
        manifest=manifest,
        task_id="task-c03-stale-sha",
        changed_files=(
            FileChange(path="tests/unit/test_feature.py", change_type=ChangeType.ADDED),
        ),
        tests_summary=TestSummary(total=2, passed=2, failed=0, skipped=0),
        import_origin_report=import_report,
        verification_receipt=stale_receipt,
        review_verdict=ReviewVerdict.PASS,
        gate_verdict="PASS",
    )

    assert bundle.dogfooding_passed is False
    assert bundle.next_authorized_action == "HALT_ON_BLOCKER"
    assert "VERIFICATION_RECEIPT_SHA_MISMATCH" in bundle.blockers


def test_dogfooding_rejects_import_origin_from_other_workspace(tmp_path: Path) -> None:
    config, manifest, _, _ = _make_clean_dogfood_inputs(tmp_path)
    other_root = tmp_path / "other_workspace"
    (other_root / "src" / "aipc").mkdir(parents=True)
    other_import_report = CandidateImportOriginReport(
        verified=True,
        expected_candidate_src=str(other_root / "src" / "aipc"),
        actual_imported_file=str(other_root / "src" / "aipc" / "__init__.py"),
        leaked_stable_paths=(),
        status_code="OK",
    )
    other_receipt = CandidateVerificationReceipt(
        candidate_sha=config.candidate_sha,
        isolation_mode="isolated_subprocess",
        import_origin_verified=True,
        import_origin_report=other_import_report,
        sanitized_env_verified=True,
        exit_code=0,
        passed=True,
        status_code="OK",
        summary="Passed in wrong workspace",
    )

    coordinator = SelfDevelopmentDogfoodingCoordinator()
    bundle = coordinator.run_dogfooding_audit(
        stable_candidate_config=config,
        manifest=manifest,
        task_id="task-c03-wrong-ws",
        changed_files=(
            FileChange(path="tests/unit/test_feature.py", change_type=ChangeType.ADDED),
        ),
        tests_summary=TestSummary(total=2, passed=2, failed=0, skipped=0),
        import_origin_report=other_import_report,
        verification_receipt=other_receipt,
        review_verdict=ReviewVerdict.PASS,
        gate_verdict="PASS",
    )

    assert bundle.dogfooding_passed is False
    assert bundle.next_authorized_action == "HALT_ON_BLOCKER"
    assert "IMPORT_ORIGIN_WORKSPACE_MISMATCH" in bundle.blockers


def test_dogfooding_detects_mutated_or_wrong_head_stable_git_repo(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, manifest, import_report, verification_receipt = _make_clean_dogfood_inputs(tmp_path)
    (Path(config.stable_root) / ".git").mkdir(exist_ok=True)

    def _fake_git_run(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        if cmd[:2] == ["git", "rev-parse"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="deadbeef" * 5 + "\n", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout=" M src/aipc/x.py\n", stderr="")

    monkeypatch.setattr("aipc.review.trusted_acceptance_harness.subprocess.run", _fake_git_run)

    coordinator = SelfDevelopmentDogfoodingCoordinator()
    bundle = coordinator.run_dogfooding_audit(
        stable_candidate_config=config,
        manifest=manifest,
        task_id="task-c03-stable-dirty",
        changed_files=(
            FileChange(path="tests/unit/test_feature.py", change_type=ChangeType.ADDED),
        ),
        tests_summary=TestSummary(total=2, passed=2, failed=0, skipped=0),
        import_origin_report=import_report,
        verification_receipt=verification_receipt,
        review_verdict=ReviewVerdict.PASS,
        gate_verdict="PASS",
    )

    assert bundle.dogfooding_passed is False
    assert bundle.stable_worktree_mutated is True
    assert bundle.next_authorized_action == "HALT_ON_BLOCKER"
    assert "STABLE_WORKTREE_MUTATED_OR_SHA_MISMATCH" in bundle.blockers


def test_dogfooding_rejects_mismatched_reviewer_gate_decision(tmp_path: Path) -> None:
    config, manifest, import_report, verification_receipt = _make_clean_dogfood_inputs(tmp_path)
    digest_zero = "0" * 64
    review_result = ReviewResult(
        schema_version="1.0",
        review_id="rev-other",
        task_id="task-other",
        verdict=ReviewVerdict.PASS,
        confidence=0.95,
        criteria=[],
        failed_criteria=[],
        required_fixes=[],
        risks=[],
        next_phase_allowed=True,
    )
    bound_review = BoundReview(
        review=review_result,
        context_digest=digest_zero,
        review_digest=digest(review_result),
        authority="codex",
        provenance=ReviewProvenance(
            project_id="aipc",
            run_id="run-1",
            phase_id="c03",
            attempt=1,
            task_id="task-other",
            attempt_checkpoint_id="cp-1",
            git_commit="3" * 40,
            agent_result_digest=digest_zero,
            evidence_digest=digest_zero,
        ),
    )
    phase_gate = PhaseGateDecision(
        verdict=ReviewVerdict.PASS,
        project_id="aipc",
        phase_id="c03",
        attempt=1,
        task_id="task-other",
        review_id="rev-other",
        actions=(GateAction.MARK_PHASE_PASS,),
        next_phase_allowed=True,
        stable_checkpoint_allowed=True,
        repair_required=False,
        human_intervention_required=False,
        reasons=("ok",),
        failed_criteria=(),
        required_fixes=(),
    )
    gate_decision = SubscriptionReviewerGateDecision(
        gate_outcome="PASS",
        task_id="task-other",
        candidate_commit_sha="3" * 40,
        reviewed_commit_sha="3" * 40,
        review_result=review_result,
        bound_review=bound_review,
        phase_gate_decision=phase_gate,
        session_audit=SessionIsolationAuditReceipt(
            audit_id="audit-1",
            task_id="task-other",
            candidate_commit_sha="3" * 40,
            worker_agent_id="worker-1",
            worker_provider_family="google_antigravity",
            reviewer_binding_id="rev-bind-1",
            reviewer_provider_id="codex",
            reviewer_provider_family="openai_codex",
            reviewer_model_id="gpt-5.4",
            reviewer_session_id="sess-1",
            fresh_isolated_session=True,
            inherited_chat_history=False,
            read_only_sandbox_enforced=True,
            workspace_snapshot_before_sha256=digest_zero,
            workspace_snapshot_after_sha256=digest_zero,
            workspace_mutated=False,
            independence_level="cross_provider",
            reduced_independence=False,
        ),
        receipt_path="evidence/rev.json",
    )

    coordinator = SelfDevelopmentDogfoodingCoordinator()
    bundle = coordinator.run_dogfooding_audit(
        stable_candidate_config=config,
        manifest=manifest,
        task_id="task-c03-dogfood",
        changed_files=(
            FileChange(path="tests/unit/test_feature.py", change_type=ChangeType.ADDED),
        ),
        tests_summary=TestSummary(total=2, passed=2, failed=0, skipped=0),
        import_origin_report=import_report,
        verification_receipt=verification_receipt,
        review_verdict=ReviewVerdict.PASS,
        gate_verdict="PASS",
        reviewer_gate_decision=gate_decision,
    )

    assert bundle.dogfooding_passed is False
    assert bundle.next_authorized_action == "HALT_ON_BLOCKER"
    assert "REVIEW_GATE_DECISION_MISMATCH" in bundle.blockers


@pytest.mark.parametrize("failure_mode", ["missing_root", "missing_git", "dirty_worktree"])
def test_dogfooding_stable_git_integrity_fails_closed(
    tmp_path: Path,
    failure_mode: str,
) -> None:
    config, manifest, import_report, verification_receipt = _make_clean_dogfood_inputs(tmp_path)
    stable_path = Path(config.stable_root)
    if failure_mode == "missing_root":
        config = config.model_copy(update={"stable_root": str(tmp_path / "nonexistent_stable")})
    elif failure_mode == "missing_git":
        no_git_dir = tmp_path / "stable_without_git"
        no_git_dir.mkdir(parents=True, exist_ok=True)
        config = config.model_copy(update={"stable_root": str(no_git_dir)})
    else:
        (stable_path / "dirty.txt").write_text("uncommitted", encoding="utf-8")

    coordinator = SelfDevelopmentDogfoodingCoordinator()
    bundle = coordinator.run_dogfooding_audit(
        stable_candidate_config=config,
        manifest=manifest,
        task_id="task-c03-stable-fail",
        changed_files=(
            FileChange(path="tests/unit/test_feature.py", change_type=ChangeType.ADDED),
        ),
        tests_summary=TestSummary(total=2, passed=2, failed=0, skipped=0),
        import_origin_report=import_report,
        verification_receipt=verification_receipt,
        review_verdict=ReviewVerdict.PASS,
        gate_verdict="PASS",
    )

    assert bundle.dogfooding_passed is False
    assert bundle.stable_worktree_mutated is True
    assert bundle.next_authorized_action == "HALT_ON_BLOCKER"
    assert "STABLE_WORKTREE_MUTATED_OR_SHA_MISMATCH" in bundle.blockers


def test_dogfooding_rejects_mismatched_nested_phase_gate_decision_task_id(
    tmp_path: Path,
) -> None:
    config, manifest, import_report, verification_receipt = _make_clean_dogfood_inputs(tmp_path)
    cand_sha = config.candidate_sha
    digest_zero = "0" * 64
    task_id = "task-c03-dogfood"
    review_result = ReviewResult(
        schema_version="1.0",
        review_id="rev-match",
        task_id=task_id,
        verdict=ReviewVerdict.PASS,
        confidence=0.95,
        criteria=[],
        failed_criteria=[],
        required_fixes=[],
        risks=[],
        next_phase_allowed=True,
    )
    bound_review = BoundReview(
        review=review_result,
        context_digest=digest_zero,
        review_digest=digest(review_result),
        authority="codex",
        provenance=ReviewProvenance(
            project_id="aipc",
            run_id="run-1",
            phase_id="c03",
            attempt=1,
            task_id=task_id,
            attempt_checkpoint_id="cp-1",
            git_commit=cand_sha,
            agent_result_digest=digest_zero,
            evidence_digest=digest_zero,
        ),
    )
    phase_gate = PhaseGateDecision(
        verdict=ReviewVerdict.PASS,
        project_id="aipc",
        phase_id="c03",
        attempt=1,
        task_id="task-other",
        review_id="rev-match",
        actions=(GateAction.MARK_PHASE_PASS,),
        next_phase_allowed=True,
        stable_checkpoint_allowed=True,
        repair_required=False,
        human_intervention_required=False,
        reasons=("ok",),
        failed_criteria=(),
        required_fixes=(),
    )
    with pytest.raises(ValidationError):
        SubscriptionReviewerGateDecision(
            gate_outcome="PASS",
            task_id=task_id,
            candidate_commit_sha=cand_sha,
            reviewed_commit_sha=cand_sha,
            review_result=review_result,
            bound_review=bound_review,
            phase_gate_decision=phase_gate,
            session_audit=SessionIsolationAuditReceipt(
                audit_id="audit-1",
                task_id=task_id,
                candidate_commit_sha=cand_sha,
                worker_agent_id="worker-1",
                worker_provider_family="google_antigravity",
                reviewer_binding_id="rev-bind-1",
                reviewer_provider_id="codex",
                reviewer_provider_family="openai_codex",
                reviewer_model_id="gpt-5.4",
                reviewer_session_id="sess-1",
                fresh_isolated_session=True,
                inherited_chat_history=False,
                read_only_sandbox_enforced=True,
                workspace_snapshot_before_sha256=digest_zero,
                workspace_snapshot_after_sha256=digest_zero,
                workspace_mutated=False,
                independence_level="cross_provider",
                reduced_independence=False,
            ),
            receipt_path="evidence/rev.json",
        )
    gate_decision = SubscriptionReviewerGateDecision.model_construct(
        gate_outcome="PASS",
        task_id=task_id,
        candidate_commit_sha=cand_sha,
        reviewed_commit_sha=cand_sha,
        review_result=review_result,
        bound_review=bound_review,
        phase_gate_decision=phase_gate,
        session_audit=SessionIsolationAuditReceipt(
            audit_id="audit-1",
            task_id=task_id,
            candidate_commit_sha=cand_sha,
            worker_agent_id="worker-1",
            worker_provider_family="google_antigravity",
            reviewer_binding_id="rev-bind-1",
            reviewer_provider_id="codex",
            reviewer_provider_family="openai_codex",
            reviewer_model_id="gpt-5.4",
            reviewer_session_id="sess-1",
            fresh_isolated_session=True,
            inherited_chat_history=False,
            read_only_sandbox_enforced=True,
            workspace_snapshot_before_sha256=digest_zero,
            workspace_snapshot_after_sha256=digest_zero,
            workspace_mutated=False,
            independence_level="cross_provider",
            reduced_independence=False,
        ),
        receipt_path="evidence/rev.json",
        reasons=(),
    )

    coordinator = SelfDevelopmentDogfoodingCoordinator()
    bundle = coordinator.run_dogfooding_audit(
        stable_candidate_config=config,
        manifest=manifest,
        task_id=task_id,
        changed_files=(
            FileChange(path="tests/unit/test_feature.py", change_type=ChangeType.ADDED),
        ),
        tests_summary=TestSummary(total=2, passed=2, failed=0, skipped=0),
        import_origin_report=import_report,
        verification_receipt=verification_receipt,
        review_verdict=ReviewVerdict.PASS,
        gate_verdict="PASS",
        reviewer_gate_decision=gate_decision,
    )

    assert bundle.dogfooding_passed is False
    assert bundle.next_authorized_action == "HALT_ON_BLOCKER"
    assert "REVIEW_GATE_DECISION_MISMATCH" in bundle.blockers


def test_dogfooding_requires_reviewer_gate_decision_on_pass(tmp_path: Path) -> None:
    config, manifest, import_report, verification_receipt = _make_clean_dogfood_inputs(tmp_path)
    coordinator = SelfDevelopmentDogfoodingCoordinator()
    bundle = coordinator.run_dogfooding_audit(
        stable_candidate_config=config,
        manifest=manifest,
        task_id="task-c03-missing-gate",
        changed_files=(
            FileChange(path="tests/unit/test_feature.py", change_type=ChangeType.ADDED),
        ),
        tests_summary=TestSummary(total=2, passed=2, failed=0, skipped=0),
        import_origin_report=import_report,
        verification_receipt=verification_receipt,
        review_verdict=ReviewVerdict.PASS,
        gate_verdict="PASS",
        reviewer_gate_decision=None,
    )

    assert bundle.dogfooding_passed is False
    assert bundle.next_authorized_action == "HALT_ON_BLOCKER"
    assert "MISSING_REVIEWER_GATE_DECISION" in bundle.blockers


@pytest.mark.parametrize(
    "receipt_override",
    [
        {"exit_code": 1},
        {"isolation_mode": "blocked"},
        {"sanitized_env_verified": False},
    ],
)
def test_dogfooding_rejects_forged_verification_receipt_invariants(
    tmp_path: Path,
    receipt_override: dict[str, object],
) -> None:
    config, manifest, import_report, verification_receipt = _make_clean_dogfood_inputs(tmp_path)
    forged_receipt = verification_receipt.model_copy(update=receipt_override)
    coordinator = SelfDevelopmentDogfoodingCoordinator()
    bundle = coordinator.run_dogfooding_audit(
        stable_candidate_config=config,
        manifest=manifest,
        task_id="task-c03-forged-receipt",
        changed_files=(
            FileChange(path="tests/unit/test_feature.py", change_type=ChangeType.ADDED),
        ),
        tests_summary=TestSummary(total=2, passed=2, failed=0, skipped=0),
        import_origin_report=import_report,
        verification_receipt=forged_receipt,
        review_verdict=ReviewVerdict.PASS,
        gate_verdict="PASS",
        reviewer_gate_decision=_make_valid_gate_decision(
            "task-c03-forged-receipt",
            candidate_sha=config.candidate_sha,
        ),
    )

    assert bundle.dogfooding_passed is False
    assert bundle.next_authorized_action == "HALT_ON_BLOCKER"
    assert any(b.startswith("VERIFICATION_FAILED:") for b in bundle.blockers)
    assert REWARD_HACKING_DETECTED in bundle.blockers


def test_dogfooding_rejects_actual_imported_file_outside_candidate_src_aipc(
    tmp_path: Path,
) -> None:
    config, manifest, _, _ = _make_clean_dogfood_inputs(tmp_path)
    candidate_root = Path(config.candidate_worktree_root)
    (candidate_root / "other").mkdir(parents=True, exist_ok=True)
    bad_import_report = CandidateImportOriginReport(
        verified=True,
        expected_candidate_src=str(candidate_root / "src" / "aipc"),
        actual_imported_file=str(candidate_root / "other" / "aipc.py"),
        leaked_stable_paths=(),
        status_code="OK",
    )
    receipt = CandidateVerificationReceipt(
        candidate_sha=config.candidate_sha,
        isolation_mode="isolated_subprocess",
        import_origin_verified=True,
        import_origin_report=bad_import_report,
        sanitized_env_verified=True,
        exit_code=0,
        passed=True,
        status_code="OK",
        summary="Imported outside src/aipc",
    )

    coordinator = SelfDevelopmentDogfoodingCoordinator()
    bundle = coordinator.run_dogfooding_audit(
        stable_candidate_config=config,
        manifest=manifest,
        task_id="task-c03-bad-pkg-root",
        changed_files=(
            FileChange(path="tests/unit/test_feature.py", change_type=ChangeType.ADDED),
        ),
        tests_summary=TestSummary(total=2, passed=2, failed=0, skipped=0),
        import_origin_report=bad_import_report,
        verification_receipt=receipt,
        review_verdict=ReviewVerdict.PASS,
        gate_verdict="PASS",
        reviewer_gate_decision=_make_valid_gate_decision(
            "task-c03-bad-pkg-root",
            candidate_sha=config.candidate_sha,
        ),
    )

    assert bundle.dogfooding_passed is False
    assert bundle.next_authorized_action == "HALT_ON_BLOCKER"
    assert "IMPORT_ORIGIN_WORKSPACE_MISMATCH" in bundle.blockers


@pytest.mark.parametrize(
    "test_body",
    [
        (
            "def test_after_with_return(ctx: object) -> None:\n"
            "    with ctx:\n"
            "        return\n"
            "    assert 1 + 1 == 2\n"
        ),
        (
            "def test_after_try_except_return() -> None:\n"
            "    try:\n"
            "        return\n"
            "    except Exception:\n"
            "        return\n"
            "    assert 1 + 1 == 2\n"
        ),
        (
            "def test_after_for_else_return() -> None:\n"
            "    for _ in ():\n"
            "        pass\n"
            "    else:\n"
            "        return\n"
            "    assert 1 + 1 == 2\n"
        ),
        (
            "def test_after_while_false_else_return() -> None:\n"
            "    while False:\n"
            "        pass\n"
            "    else:\n"
            "        return\n"
            "    assert 1 + 1 == 2\n"
        ),
        (
            "def test_after_while_true_return() -> None:\n"
            "    while True:\n"
            "        return\n"
            "    assert 1 + 1 == 2\n"
        ),
        (
            "def test_dead_narrow_except_after_broad() -> None:\n"
            "    try:\n"
            "        return\n"
            "    except Exception:\n"
            "        return\n"
            "    except ValueError:\n"
            "        assert 1 + 1 == 2\n"
        ),
    ],
)
def test_unreachable_assertion_after_with_or_try_return_fails_closed(
    tmp_path: Path,
    test_body: str,
) -> None:
    _, candidate_root, manifest = _setup_candidate_workspace(tmp_path)
    dead_test = candidate_root / "tests" / "unit" / "test_dead_ctrl.py"
    dead_test.write_text(test_body, encoding="utf-8")

    validator = TrustedAcceptanceHarnessValidator()
    evaluation = validator.evaluate_candidate(
        manifest=manifest,
        workspace=candidate_root,
        task_id="task-c03-dead-ctrl",
        candidate_sha=_CANDIDATE_SHA,
        changed_files=(
            FileChange(path="tests/unit/test_dead_ctrl.py", change_type=ChangeType.ADDED),
        ),
        tests_summary=TestSummary(total=1, passed=1, failed=0, skipped=0),
    )

    assert evaluation.passed is False
    assert evaluation.rejection_code == REWARD_HACKING_DETECTED
    assert any(f.rule_id == "UNREACHABLE_ASSERTION" for f in evaluation.findings)


def test_required_criterion_pass_without_evidence_fails_closed(tmp_path: Path) -> None:
    _, candidate_root, _ = _setup_candidate_workspace(tmp_path)
    manifest = build_trusted_harness_manifest(
        project_id="aipc",
        milestone_id="milestone-c",
        workspace=candidate_root,
        protected_rel_paths=("src/aipc/review/gate.py", "tests/trusted/test_contract.py"),
        manifest_id="manifest-c03-no-ev",
        required_criterion_ids=("AC-01",),
    )
    validator = TrustedAcceptanceHarnessValidator()
    receipt = _make_valid_verification_receipt(candidate_root, _CANDIDATE_SHA)

    evaluation = validator.evaluate_candidate(
        manifest=manifest,
        workspace=candidate_root,
        task_id="task-c03-crit-no-ev",
        candidate_sha=_CANDIDATE_SHA,
        changed_files=(
            FileChange(path="tests/unit/test_feature.py", change_type=ChangeType.ADDED),
        ),
        tests_summary=TestSummary(total=1, passed=1, failed=0, skipped=0),
        criterion_results=(
            CriterionResult(
                criterion_id="AC-01",
                status=CriterionStatus.PASS,
                evidence=[],
                notes="claimed pass without evidence",
            ),
        ),
        verification_receipt=receipt,
    )

    assert evaluation.passed is False
    assert evaluation.rejection_code == REWARD_HACKING_DETECTED
    assert any(f.rule_id == "REQUIRED_CRITERION_UNVERIFIED" for f in evaluation.findings)


@pytest.mark.parametrize("failure_mode", ["missing_git", "dirty_worktree", "sha_mismatch"])
def test_dogfooding_candidate_git_integrity_fails_closed(
    tmp_path: Path,
    failure_mode: str,
) -> None:
    config, manifest, import_report, verification_receipt = _make_clean_dogfood_inputs(tmp_path)
    cand_path = Path(config.candidate_worktree_root)
    if failure_mode == "missing_git":
        no_git_cand = tmp_path / "candidate_without_git"
        (no_git_cand / "src" / "aipc" / "review").mkdir(parents=True)
        (no_git_cand / "src" / "aipc" / "__init__.py").write_text("", encoding="utf-8")
        (no_git_cand / "tests" / "trusted").mkdir(parents=True)
        (no_git_cand / "tests" / "unit").mkdir(parents=True)
        (no_git_cand / "src" / "aipc" / "review" / "gate.py").write_text(
            "def evaluate_gate() -> bool:\n    return True\n",
            encoding="utf-8",
        )
        (no_git_cand / "tests" / "trusted" / "test_contract.py").write_text(
            "def test_trusted_contract() -> None:\n    assert 2 + 2 == 4\n",
            encoding="utf-8",
        )
        (no_git_cand / "tests" / "unit" / "test_feature.py").write_text(
            "def test_feature_behavior() -> None:\n    result = 10 * 2\n    assert result == 20\n",
            encoding="utf-8",
        )
        manifest = build_trusted_harness_manifest(
            project_id="aipc",
            milestone_id="milestone-c",
            workspace=no_git_cand,
            protected_rel_paths=("src/aipc/review/gate.py", "tests/trusted/test_contract.py"),
            manifest_id="manifest-c03-nogit",
        )
        import_report = CandidateImportOriginReport(
            verified=True,
            expected_candidate_src=str(no_git_cand / "src" / "aipc"),
            actual_imported_file=str(no_git_cand / "src" / "aipc" / "__init__.py"),
            leaked_stable_paths=(),
            status_code="OK",
        )
        verification_receipt = verification_receipt.model_copy(
            update={"import_origin_report": import_report}
        )
        config = config.model_copy(update={"candidate_worktree_root": str(no_git_cand)})
    elif failure_mode == "dirty_worktree":
        (cand_path / "untracked.txt").write_text("dirty", encoding="utf-8")
    else:
        wrong_sha = "f" * 40
        config = config.model_copy(update={"candidate_sha": wrong_sha})
        verification_receipt = verification_receipt.model_copy(update={"candidate_sha": wrong_sha})

    coordinator = SelfDevelopmentDogfoodingCoordinator()
    bundle = coordinator.run_dogfooding_audit(
        stable_candidate_config=config,
        manifest=manifest,
        task_id="task-c03-cand-git-fail",
        changed_files=(
            FileChange(path="tests/unit/test_feature.py", change_type=ChangeType.ADDED),
        ),
        tests_summary=TestSummary(total=2, passed=2, failed=0, skipped=0),
        import_origin_report=import_report,
        verification_receipt=verification_receipt,
        review_verdict=ReviewVerdict.PASS,
        gate_verdict="PASS",
        reviewer_gate_decision=_make_valid_gate_decision(
            "task-c03-cand-git-fail",
            candidate_sha=config.candidate_sha,
        ),
    )

    assert bundle.dogfooding_passed is False
    assert bundle.next_authorized_action == "HALT_ON_BLOCKER"
    assert "CANDIDATE_WORKTREE_DIRTY_OR_SHA_MISMATCH" in bundle.blockers


def test_dogfooding_accepts_genuine_verify_candidate_import_origin_report(tmp_path: Path) -> None:
    stable_root, candidate_root, manifest = _setup_candidate_workspace(tmp_path)
    cand_sha = _git_head_sha(candidate_root)
    config = StableCandidateConfig(
        stable_root=str(stable_root),
        candidate_worktree_root=str(candidate_root),
        pinned_stable_sha=_git_head_sha(stable_root),
        candidate_sha=cand_sha,
        verification_isolation="isolated_subprocess",
        allow_auto_promote_stable=False,
    )
    import_report = verify_candidate_import_origin(
        stable_root=stable_root,
        candidate_root=candidate_root,
    )
    verification_receipt = CandidateVerificationReceipt(
        candidate_sha=cand_sha,
        isolation_mode="isolated_subprocess",
        import_origin_verified=True,
        import_origin_report=import_report,
        sanitized_env_verified=True,
        exit_code=0,
        passed=True,
        status_code="OK",
        summary="Isolated candidate tests passed",
    )

    changed = (FileChange(path="tests/unit/test_feature.py", change_type=ChangeType.ADDED),)
    tests_sum = TestSummary(total=2, passed=2, failed=0, skipped=0)
    review_ctx, gate_decision = _make_valid_review_context_and_gate_decision(
        tmp_path,
        "task-c03-real-origin",
        candidate_sha=cand_sha,
        workspace_root=candidate_root,
        changed_files=changed,
        tests_summary=tests_sum,
    )

    coordinator = SelfDevelopmentDogfoodingCoordinator()
    bundle = coordinator.run_dogfooding_audit(
        stable_candidate_config=config,
        manifest=manifest,
        task_id="task-c03-real-origin",
        changed_files=changed,
        tests_summary=tests_sum,
        import_origin_report=import_report,
        verification_receipt=verification_receipt,
        review_verdict=ReviewVerdict.PASS,
        gate_verdict="PASS",
        reviewer_gate_decision=gate_decision,
        review_context=review_ctx,
    )

    assert bundle.dogfooding_passed is True
    assert bundle.next_authorized_action == "HUMAN_MERGE_REVIEW"
    assert bundle.blockers == ()


def test_dogfooding_disabling_stable_git_integrity_blocks_pass(tmp_path: Path) -> None:
    config, manifest, import_report, verification_receipt = _make_clean_dogfood_inputs(tmp_path)
    coordinator = SelfDevelopmentDogfoodingCoordinator()
    bundle = coordinator.run_dogfooding_audit(
        stable_candidate_config=config,
        manifest=manifest,
        task_id="task-c03-skip-stable-git",
        changed_files=(
            FileChange(path="tests/unit/test_feature.py", change_type=ChangeType.ADDED),
        ),
        tests_summary=TestSummary(total=2, passed=2, failed=0, skipped=0),
        import_origin_report=import_report,
        verification_receipt=verification_receipt,
        review_verdict=ReviewVerdict.PASS,
        gate_verdict="PASS",
        reviewer_gate_decision=_make_valid_gate_decision(
            "task-c03-skip-stable-git",
            candidate_sha=config.candidate_sha,
        ),
        verify_stable_git_integrity=False,
    )

    assert bundle.dogfooding_passed is False
    assert bundle.next_authorized_action == "HALT_ON_BLOCKER"
    assert "STABLE_GIT_INTEGRITY_UNVERIFIED" in bundle.blockers


def test_dogfooding_requires_review_context_on_pass(tmp_path: Path) -> None:
    config, manifest, import_report, verification_receipt = _make_clean_dogfood_inputs(tmp_path)
    coordinator = SelfDevelopmentDogfoodingCoordinator()
    bundle = coordinator.run_dogfooding_audit(
        stable_candidate_config=config,
        manifest=manifest,
        task_id="task-c03-missing-ctx",
        changed_files=(
            FileChange(path="tests/unit/test_feature.py", change_type=ChangeType.ADDED),
        ),
        tests_summary=TestSummary(total=2, passed=2, failed=0, skipped=0),
        import_origin_report=import_report,
        verification_receipt=verification_receipt,
        review_verdict=ReviewVerdict.PASS,
        gate_verdict="PASS",
        reviewer_gate_decision=_make_valid_gate_decision(
            "task-c03-missing-ctx",
            candidate_sha=config.candidate_sha,
        ),
        review_context=None,
    )

    assert bundle.dogfooding_passed is False
    assert bundle.next_authorized_action == "HALT_ON_BLOCKER"
    assert "MISSING_REVIEW_CONTEXT" in bundle.blockers


def test_dogfooding_rejects_placeholder_digests_against_review_context(tmp_path: Path) -> None:
    config, manifest, import_report, verification_receipt = _make_clean_dogfood_inputs(tmp_path)
    changed = (FileChange(path="tests/unit/test_feature.py", change_type=ChangeType.ADDED),)
    tests_sum = TestSummary(total=2, passed=2, failed=0, skipped=0)
    review_ctx, _ = _make_valid_review_context_and_gate_decision(
        tmp_path,
        "task-c03-placeholder",
        candidate_sha=config.candidate_sha,
        workspace_root=Path(config.candidate_worktree_root),
        changed_files=changed,
        tests_summary=tests_sum,
    )
    placeholder_gate = _make_valid_gate_decision(
        "task-c03-placeholder",
        candidate_sha=config.candidate_sha,
    )

    coordinator = SelfDevelopmentDogfoodingCoordinator()
    bundle = coordinator.run_dogfooding_audit(
        stable_candidate_config=config,
        manifest=manifest,
        task_id="task-c03-placeholder",
        changed_files=changed,
        tests_summary=tests_sum,
        import_origin_report=import_report,
        verification_receipt=verification_receipt,
        review_verdict=ReviewVerdict.PASS,
        gate_verdict="PASS",
        reviewer_gate_decision=placeholder_gate,
        review_context=review_ctx,
    )

    assert bundle.dogfooding_passed is False
    assert bundle.next_authorized_action == "HALT_ON_BLOCKER"
    assert "REVIEW_GATE_DECISION_MISMATCH" in bundle.blockers


@pytest.mark.parametrize(
    "policy_override",
    [
        {"required_criterion_ids": ("AC-01",)},
        {"require_behavioral_assertions": False},
        {"forbid_test_deletion": False},
    ],
)
def test_manifest_policy_mutation_without_digest_recompute_rejected(
    tmp_path: Path,
    policy_override: dict[str, object],
) -> None:
    _, candidate_root, base_manifest = _setup_candidate_workspace(tmp_path)
    mutated_manifest = base_manifest.model_copy(update=policy_override)
    validator = TrustedAcceptanceHarnessValidator()
    receipt = _make_valid_verification_receipt(candidate_root, _CANDIDATE_SHA)

    evaluation = validator.evaluate_candidate(
        manifest=mutated_manifest,
        workspace=candidate_root,
        task_id="task-c03-policy-mutate",
        candidate_sha=_CANDIDATE_SHA,
        changed_files=(
            FileChange(path="tests/unit/test_feature.py", change_type=ChangeType.ADDED),
        ),
        tests_summary=TestSummary(total=1, passed=1, failed=0, skipped=0),
        verification_receipt=receipt,
    )

    assert evaluation.passed is False
    assert evaluation.harness_intact is False
    assert evaluation.rejection_code == TRUSTED_HARNESS_TAMPER_DETECTED


def test_manifest_inside_candidate_worktree_rejected(tmp_path: Path) -> None:
    _, candidate_root, base_manifest = _setup_candidate_workspace(tmp_path)
    in_worktree_path = candidate_root / "manifest_inside.json"
    tampered_manifest = base_manifest.model_copy(
        update={"trusted_manifest_path": str(in_worktree_path)}
    )
    in_worktree_path.write_text(tampered_manifest.model_dump_json(indent=2), encoding="utf-8")

    validator = TrustedAcceptanceHarnessValidator()
    receipt = _make_valid_verification_receipt(candidate_root, _CANDIDATE_SHA)
    evaluation = validator.evaluate_candidate(
        manifest=tampered_manifest,
        workspace=candidate_root,
        task_id="task-c03-in-worktree-manifest",
        candidate_sha=_CANDIDATE_SHA,
        changed_files=(
            FileChange(path="tests/unit/test_feature.py", change_type=ChangeType.ADDED),
        ),
        tests_summary=TestSummary(total=1, passed=1, failed=0, skipped=0),
        verification_receipt=receipt,
    )

    assert evaluation.passed is False
    assert evaluation.harness_intact is False
    assert evaluation.rejection_code == TRUSTED_HARNESS_TAMPER_DETECTED


def test_criterion_evidence_requires_digest_bound_receipt_or_behavioral_test_file(
    tmp_path: Path,
) -> None:
    _, candidate_root, _ = _setup_candidate_workspace(tmp_path)
    manifest = build_trusted_harness_manifest(
        project_id="aipc",
        milestone_id="milestone-c",
        workspace=candidate_root,
        protected_rel_paths=("src/aipc/review/gate.py", "tests/trusted/test_contract.py"),
        manifest_id="manifest-c03-ev-strict",
        required_criterion_ids=("AC-01",),
    )
    validator = TrustedAcceptanceHarnessValidator()
    receipt = _make_valid_verification_receipt(candidate_root, _CANDIDATE_SHA)

    for bad_ev in (f"pytest:{_CANDIDATE_SHA}:AC-01", "pytest_log"):
        bad_eval = validator.evaluate_candidate(
            manifest=manifest,
            workspace=candidate_root,
            task_id="task-c03-bad-ev",
            candidate_sha=_CANDIDATE_SHA,
            changed_files=(
                FileChange(path="tests/unit/test_feature.py", change_type=ChangeType.ADDED),
            ),
            tests_summary=TestSummary(total=1, passed=1, failed=0, skipped=0),
            criterion_results=(
                CriterionResult(
                    criterion_id="AC-01",
                    status=CriterionStatus.PASS,
                    evidence=[bad_ev],
                    notes="unverified evidence string",
                ),
            ),
            verification_receipt=receipt,
        )
        assert bad_eval.passed is False
        assert bad_eval.rejection_code == REWARD_HACKING_DETECTED
        assert any(f.rule_id == "REQUIRED_CRITERION_UNVERIFIED" for f in bad_eval.findings)

    for good_ev in (
        f"receipt:{_CANDIDATE_SHA}:AC-01:{manifest.manifest_digest}",
        "tests/unit/test_feature.py",
    ):
        good_eval = validator.evaluate_candidate(
            manifest=manifest,
            workspace=candidate_root,
            task_id="task-c03-good-ev",
            candidate_sha=_CANDIDATE_SHA,
            changed_files=(
                FileChange(path="tests/unit/test_feature.py", change_type=ChangeType.ADDED),
            ),
            tests_summary=TestSummary(total=1, passed=1, failed=0, skipped=0),
            criterion_results=(
                CriterionResult(
                    criterion_id="AC-01",
                    status=CriterionStatus.PASS,
                    evidence=[good_ev],
                    notes="verified evidence",
                ),
            ),
            verification_receipt=receipt,
        )
        assert good_eval.passed is True
        assert good_eval.findings == ()
        assert good_eval.rejection_code is None
