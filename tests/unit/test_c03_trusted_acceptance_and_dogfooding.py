"""Unit tests for Workstream C03 — Trusted Acceptance Harness & Self-Development Dogfooding."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError

import aipc.review as review_pkg
from aipc.controller.self_dev_runner import (
    CandidateImportOriginReport,
    CandidateVerificationReceipt,
    StableCandidateConfig,
)
from aipc.domain.enums import ChangeType, CriterionStatus, ReviewVerdict
from aipc.domain.models import CriterionResult, FileChange, ReviewResult, TestSummary
from aipc.review.models import BoundReview, GateAction, PhaseGateDecision, ReviewProvenance
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
    )

    assert evaluation.passed is True
    assert evaluation.harness_intact is True
    assert evaluation.behavioral_tests_verified is True
    assert evaluation.findings == ()
    assert evaluation.rejection_code is None


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


def test_dogfooding_coordinator_clean_pass_stops_at_human_merge_review(tmp_path: Path) -> None:
    stable_root, candidate_root, manifest = _setup_candidate_workspace(tmp_path)
    config = StableCandidateConfig(
        stable_root=str(stable_root),
        candidate_worktree_root=str(candidate_root),
        pinned_stable_sha=_git_head_sha(stable_root),
        candidate_sha=_CANDIDATE_SHA,
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
        candidate_sha=_CANDIDATE_SHA,
        isolation_mode="isolated_subprocess",
        import_origin_verified=True,
        import_origin_report=import_report,
        sanitized_env_verified=True,
        exit_code=0,
        passed=True,
        status_code="OK",
        summary="Isolated candidate tests passed",
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


def test_required_criterion_unverified_fails_closed(tmp_path: Path) -> None:
    _, candidate_root, base_manifest = _setup_candidate_workspace(tmp_path)
    manifest = base_manifest.model_copy(update={"required_criterion_ids": ("AC-01", "AC-02")})
    validator = TrustedAcceptanceHarnessValidator()

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
    )
    assert eval_failed.passed is False
    assert eval_failed.rejection_code == REWARD_HACKING_DETECTED
    assert any(f.rule_id == "REQUIRED_CRITERION_UNVERIFIED" for f in eval_failed.findings)


def _make_clean_dogfood_inputs(
    tmp_path: Path,
) -> tuple[
    StableCandidateConfig,
    TrustedHarnessManifest,
    CandidateImportOriginReport,
    CandidateVerificationReceipt,
]:
    stable_root, candidate_root, manifest = _setup_candidate_workspace(tmp_path)
    config = StableCandidateConfig(
        stable_root=str(stable_root),
        candidate_worktree_root=str(candidate_root),
        pinned_stable_sha=_git_head_sha(stable_root),
        candidate_sha=_CANDIDATE_SHA,
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
        candidate_sha=_CANDIDATE_SHA,
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
        candidate_sha=_CANDIDATE_SHA,
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
        review_digest=digest_zero,
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
        review_digest=digest_zero,
        authority="codex",
        provenance=ReviewProvenance(
            project_id="aipc",
            run_id="run-1",
            phase_id="c03",
            attempt=1,
            task_id=task_id,
            attempt_checkpoint_id="cp-1",
            git_commit=_CANDIDATE_SHA,
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
    gate_decision = SubscriptionReviewerGateDecision(
        gate_outcome="PASS",
        task_id=task_id,
        candidate_commit_sha=_CANDIDATE_SHA,
        reviewed_commit_sha=_CANDIDATE_SHA,
        review_result=review_result,
        bound_review=bound_review,
        phase_gate_decision=phase_gate,
        session_audit=SessionIsolationAuditReceipt(
            audit_id="audit-1",
            task_id=task_id,
            candidate_commit_sha=_CANDIDATE_SHA,
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
