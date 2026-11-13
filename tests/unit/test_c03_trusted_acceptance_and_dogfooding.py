"""Unit tests for Workstream C03 — Trusted Acceptance Harness & Self-Development Dogfooding."""

from __future__ import annotations

from pathlib import Path

import pytest

import aipc.review as review_pkg
from aipc.controller.self_dev_runner import (
    CandidateImportOriginReport,
    CandidateVerificationReceipt,
    StableCandidateConfig,
)
from aipc.domain.enums import ChangeType, ReviewVerdict
from aipc.domain.models import FileChange, TestSummary
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


def _setup_candidate_workspace(tmp_path: Path) -> tuple[Path, Path, TrustedHarnessManifest]:
    stable_root = tmp_path / "stable_checkout"
    candidate_root = tmp_path / "candidate_worktree"
    (stable_root / "src" / "aipc").mkdir(parents=True)
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
        )


def test_dogfooding_coordinator_clean_pass_stops_at_human_merge_review(tmp_path: Path) -> None:
    stable_root, candidate_root, manifest = _setup_candidate_workspace(tmp_path)
    config = StableCandidateConfig(
        stable_root=str(stable_root),
        candidate_worktree_root=str(candidate_root),
        pinned_stable_sha=_STABLE_SHA,
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
        pinned_stable_sha=_STABLE_SHA,
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
