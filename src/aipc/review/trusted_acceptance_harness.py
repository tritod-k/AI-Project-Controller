"""Workstream C03 — Trusted Acceptance Harness & Self-Development Dogfooding."""

from __future__ import annotations

import ast
import fnmatch
import hashlib
from pathlib import Path
from typing import Literal, Self
from uuid import uuid4

from pydantic import Field, StrictBool, model_validator

from aipc.controller.milestone_b_orchestrator import HandoffNextAuthorizedAction
from aipc.controller.self_dev_runner import (
    CandidateImportOriginReport,
    CandidateVerificationReceipt,
    StableCandidateConfig,
)
from aipc.domain.enums import ChangeType, ReviewVerdict
from aipc.domain.models import CanonicalRecord, FileChange, NonBlank, TestSummary
from aipc.hardening.secrets import assert_no_secrets
from aipc.review.models import Digest
from aipc.version.models import Commit

UNAUTHORIZED_MILESTONE_D_BLOCKED = "UNAUTHORIZED_MILESTONE_D_BLOCKED"
TRUSTED_HARNESS_TAMPER_DETECTED = "TRUSTED_HARNESS_TAMPER_DETECTED"
REWARD_HACKING_DETECTED = "REWARD_HACKING_DETECTED"

AUTHORIZED_MILESTONE_IDS: tuple[str, ...] = (
    "phase-24",
    "milestone-a",
    "milestone-b",
    "milestone-c",
)
UNAUTHORIZED_MILESTONE_PREFIXES: tuple[str, ...] = (
    "milestone-d",
    "d01",
    "d02",
    "d03",
)

_DEFAULT_PROTECTED_HARNESS_GLOBS: tuple[NonBlank, ...] = (
    "tests/acceptance/**",
    "tests/trusted/**",
    "src/aipc/review/gate.py",
    "src/aipc/review/trusted_acceptance_harness.py",
    "src/aipc/controller/task_queue.py",
    "src/aipc/approvals/**",
)

_UNITTEST_ASSERT_METHODS = frozenset(
    {
        "assertEqual",
        "assertNotEqual",
        "assertTrue",
        "assertFalse",
        "assertRaises",
        "assertIn",
        "assertNotIn",
        "assertIs",
        "assertIsNone",
        "assertIsNotNone",
    }
)


def _normalize_rel_path(path: str) -> str:
    normalized = path.replace("\\", "/").strip()
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def _contains_unauthorized_prefix(value: str) -> bool:
    norm = value.strip().lower()
    return any(prefix in norm for prefix in UNAUTHORIZED_MILESTONE_PREFIXES)


def assert_milestone_c_scope_authorized(
    milestone_id: str,
    phase_id: str | None = None,
    task_id: str | None = None,
) -> None:
    """Fail closed if Milestone D or any unauthorized milestone scope is referenced."""
    norm_milestone = milestone_id.strip().lower()
    if _contains_unauthorized_prefix(norm_milestone) or (
        norm_milestone not in AUTHORIZED_MILESTONE_IDS
    ):
        raise ValueError(
            f"{UNAUTHORIZED_MILESTONE_D_BLOCKED}: milestone '{milestone_id}' is not authorized"
        )
    if phase_id is not None and _contains_unauthorized_prefix(phase_id):
        raise ValueError(
            f"{UNAUTHORIZED_MILESTONE_D_BLOCKED}: phase_id '{phase_id}' references "
            "unauthorized Milestone D scope"
        )
    if task_id is not None and _contains_unauthorized_prefix(task_id):
        raise ValueError(
            f"{UNAUTHORIZED_MILESTONE_D_BLOCKED}: task_id '{task_id}' references "
            "unauthorized Milestone D scope"
        )


class TrustedHarnessManifest(CanonicalRecord):
    """Manifest pinning protected trusted acceptance harness paths and SHA-256 digests."""

    manifest_id: NonBlank
    project_id: NonBlank
    milestone_id: NonBlank
    protected_harness_globs: tuple[NonBlank, ...] = _DEFAULT_PROTECTED_HARNESS_GLOBS
    pinned_file_sha256: dict[str, Digest] = Field(default_factory=dict)
    require_behavioral_assertions: StrictBool = True
    forbid_test_deletion: StrictBool = True

    @model_validator(mode="after")
    def validate_manifest(self) -> Self:
        assert_milestone_c_scope_authorized(self.milestone_id)
        assert_no_secrets(self.model_dump(mode="json"))
        return self


def build_trusted_harness_manifest(
    *,
    project_id: str,
    milestone_id: str,
    workspace: Path,
    protected_rel_paths: tuple[str, ...],
    manifest_id: str | None = None,
) -> TrustedHarnessManifest:
    """Build a TrustedHarnessManifest with SHA-256 digests of protected files in workspace."""
    assert_milestone_c_scope_authorized(milestone_id)
    workspace_root = Path(workspace).resolve()
    pinned: dict[str, str] = {}
    for rel_path in protected_rel_paths:
        norm_rel = _normalize_rel_path(rel_path)
        target = (workspace_root / norm_rel).resolve()
        if not target.is_relative_to(workspace_root):
            raise ValueError(f"Protected path escapes workspace: {rel_path}")
        if not target.is_file():
            raise FileNotFoundError(f"Protected file not found in workspace: {norm_rel}")
        pinned[norm_rel] = hashlib.sha256(target.read_bytes()).hexdigest()

    return TrustedHarnessManifest(
        manifest_id=manifest_id or f"manifest-{uuid4().hex[:12]}",
        project_id=project_id,
        milestone_id=milestone_id,
        pinned_file_sha256=pinned,
    )


class RewardHackingFinding(CanonicalRecord):
    """Structured finding for harness tampering or reward-hacking behavior."""

    rule_id: NonBlank
    file_path: NonBlank
    description: NonBlank


class TrustedAcceptanceEvaluation(CanonicalRecord):
    """Outcome of evaluating a candidate against the trusted acceptance harness."""

    evaluation_id: NonBlank
    project_id: NonBlank
    milestone_id: NonBlank
    task_id: NonBlank
    candidate_sha: Commit
    passed: StrictBool
    harness_intact: StrictBool
    behavioral_tests_verified: StrictBool
    findings: tuple[RewardHackingFinding, ...] = ()
    rejection_code: str | None = None

    @model_validator(mode="after")
    def validate_evaluation(self) -> Self:
        assert_milestone_c_scope_authorized(self.milestone_id, task_id=self.task_id)
        if self.passed and (
            not self.harness_intact
            or not self.behavioral_tests_verified
            or len(self.findings) > 0
            or self.rejection_code is not None
        ):
            raise ValueError(
                "TrustedAcceptanceEvaluation.passed cannot be True when harness is violated, "
                "behavioral tests are unverified, findings exist, or rejection_code is set"
            )
        assert_no_secrets(self.model_dump(mode="json"))
        return self


def _is_pytest_skip_or_xfail_call(node: ast.Call) -> bool:
    func = node.func
    if isinstance(func, ast.Attribute) and func.attr in {"skip", "xfail"}:
        if isinstance(func.value, ast.Name) and func.value.id == "pytest":
            return True
    if isinstance(func, ast.Name) and func.id in {"skip", "xfail"}:
        return True
    return False


def _is_pytest_skip_or_xfail_decorator(dec: ast.expr) -> bool:
    target = dec.func if isinstance(dec, ast.Call) else dec
    if isinstance(target, ast.Attribute) and target.attr in {"skip", "skipif", "xfail"}:
        if isinstance(target.value, ast.Attribute) and target.value.attr == "mark":
            return True
        if isinstance(target.value, ast.Name) and target.value.id == "mark":
            return True
    return False


def _is_assertion_call(node: ast.Call) -> bool:
    func = node.func
    if isinstance(func, ast.Attribute):
        if isinstance(func.value, ast.Name) and func.value.id == "pytest":
            if func.attr in {"raises", "warns", "fail"}:
                return True
        if func.attr.startswith("assert_") or func.attr in _UNITTEST_ASSERT_METHODS:
            return True
    elif isinstance(func, ast.Name) and func.id.startswith("assert_"):
        return True
    return False


def _function_has_real_assertion(fn_node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    for node in ast.walk(fn_node):
        if isinstance(node, ast.Assert):
            return True
        if isinstance(node, ast.Call) and _is_assertion_call(node):
            return True
    return False


class TrustedAcceptanceHarnessValidator:
    """Validates candidate changes against trusted harness integrity and anti-reward-hacking."""

    @staticmethod
    def inspect_python_test_source(
        rel_path: str,
        source_code: str,
    ) -> tuple[RewardHackingFinding, ...]:
        """Inspect Python test source via AST for vacuous tests or skip/xfail bypasses."""
        findings: list[RewardHackingFinding] = []
        try:
            tree = ast.parse(source_code, filename=rel_path)
        except SyntaxError as exc:
            return (
                RewardHackingFinding(
                    rule_id="VACUOUS_TEST_WITHOUT_ASSERT",
                    file_path=rel_path,
                    description=f"Unable to parse test file '{rel_path}': {exc}",
                ),
            )

        skip_flagged = False
        for node in ast.walk(tree):
            if not skip_flagged and isinstance(node, ast.Call):
                if _is_pytest_skip_or_xfail_call(node):
                    findings.append(
                        RewardHackingFinding(
                            rule_id="PYTEST_SKIP_OR_XFAIL_INJECTED",
                            file_path=rel_path,
                            description=f"Detected pytest skip/xfail call in '{rel_path}'",
                        )
                    )
                    skip_flagged = True
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if not skip_flagged and any(
                    _is_pytest_skip_or_xfail_decorator(dec) for dec in node.decorator_list
                ):
                    findings.append(
                        RewardHackingFinding(
                            rule_id="PYTEST_SKIP_OR_XFAIL_INJECTED",
                            file_path=rel_path,
                            description=(
                                f"Detected pytest.mark skip/xfail decorator on '{node.name}'"
                            ),
                        )
                    )
                    skip_flagged = True

            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name.startswith("test_") and not _function_has_real_assertion(node):
                    findings.append(
                        RewardHackingFinding(
                            rule_id="VACUOUS_TEST_WITHOUT_ASSERT",
                            file_path=rel_path,
                            description=(
                                f"Test function '{node.name}' lacks assertions or pytest.raises"
                            ),
                        )
                    )

        return tuple(findings)

    def evaluate_candidate(
        self,
        *,
        manifest: TrustedHarnessManifest,
        workspace: Path,
        task_id: str,
        candidate_sha: str,
        changed_files: tuple[FileChange, ...],
        tests_summary: TestSummary | None,
    ) -> TrustedAcceptanceEvaluation:
        """Evaluate candidate changes and test evidence against TrustedHarnessManifest."""
        assert_milestone_c_scope_authorized(manifest.milestone_id, task_id=task_id)
        workspace_root = Path(workspace).resolve()
        findings: list[RewardHackingFinding] = []
        tamper_detected = False
        reward_hacking_detected = False

        for fc in changed_files:
            norm_path = _normalize_rel_path(fc.path)
            if any(
                fnmatch.fnmatchcase(norm_path, glob_pat)
                for glob_pat in manifest.protected_harness_globs
            ):
                findings.append(
                    RewardHackingFinding(
                        rule_id="PROTECTED_HARNESS_MODIFIED",
                        file_path=norm_path,
                        description=(
                            f"Candidate modified protected trusted harness path '{norm_path}'"
                        ),
                    )
                )
                tamper_detected = True

            if (
                manifest.forbid_test_deletion
                and fc.change_type == ChangeType.DELETED
                and (norm_path.startswith("tests/") or fnmatch.fnmatchcase(norm_path, "tests/**"))
            ):
                findings.append(
                    RewardHackingFinding(
                        rule_id="TEST_FILE_DELETED",
                        file_path=norm_path,
                        description=f"Candidate deleted test file '{norm_path}'",
                    )
                )
                reward_hacking_detected = True

            if (
                fc.change_type in (ChangeType.ADDED, ChangeType.MODIFIED)
                and norm_path.startswith("tests/")
                and norm_path.endswith(".py")
            ):
                file_path = (workspace_root / norm_path).resolve()
                if file_path.is_relative_to(workspace_root) and file_path.is_file():
                    source_code = file_path.read_text(encoding="utf-8")
                    ast_findings = self.inspect_python_test_source(norm_path, source_code)
                    if ast_findings:
                        findings.extend(ast_findings)
                        reward_hacking_detected = True

        for rel_path, expected_digest in manifest.pinned_file_sha256.items():
            norm_rel = _normalize_rel_path(rel_path)
            target_path = (workspace_root / norm_rel).resolve()
            if not target_path.is_relative_to(workspace_root) or not target_path.is_file():
                findings.append(
                    RewardHackingFinding(
                        rule_id="PINNED_DIGEST_MISMATCH",
                        file_path=norm_rel,
                        description=f"Pinned trusted harness file missing: '{norm_rel}'",
                    )
                )
                tamper_detected = True
                continue
            actual_digest = hashlib.sha256(target_path.read_bytes()).hexdigest()
            if actual_digest != expected_digest:
                findings.append(
                    RewardHackingFinding(
                        rule_id="PINNED_DIGEST_MISMATCH",
                        file_path=norm_rel,
                        description=(
                            f"Pinned SHA-256 mismatch for '{norm_rel}': "
                            f"expected {expected_digest}, got {actual_digest}"
                        ),
                    )
                )
                tamper_detected = True

        tests_ok = (
            tests_summary is not None
            and tests_summary.passed > 0
            and tests_summary.failed == 0
        )
        if not tests_ok:
            reward_hacking_detected = True
            if manifest.require_behavioral_assertions:
                findings.append(
                    RewardHackingFinding(
                        rule_id="VACUOUS_TEST_WITHOUT_ASSERT",
                        file_path="tests",
                        description="Zero passing behavioral tests or failing tests reported",
                    )
                )

        harness_intact = not tamper_detected
        behavioral_tests_verified = tests_ok and not reward_hacking_detected
        passed = harness_intact and behavioral_tests_verified and len(findings) == 0

        rejection_code: str | None = None
        if tamper_detected:
            rejection_code = TRUSTED_HARNESS_TAMPER_DETECTED
        elif not passed:
            rejection_code = REWARD_HACKING_DETECTED

        return TrustedAcceptanceEvaluation(
            evaluation_id=f"eval-{uuid4().hex[:12]}",
            project_id=manifest.project_id,
            milestone_id=manifest.milestone_id,
            task_id=task_id,
            candidate_sha=candidate_sha,
            passed=passed,
            harness_intact=harness_intact,
            behavioral_tests_verified=behavioral_tests_verified,
            findings=tuple(findings),
            rejection_code=rejection_code,
        )


class DogfoodingAuditBundle(CanonicalRecord):
    """Audited self-development dogfooding bundle stopping at HUMAN_MERGE_REVIEW."""

    bundle_id: NonBlank
    project_id: NonBlank
    milestone_id: NonBlank
    task_id: NonBlank
    stable_root: NonBlank
    candidate_worktree_root: NonBlank
    pinned_stable_sha: Commit
    candidate_sha: Commit
    import_origin_report: CandidateImportOriginReport
    verification_receipt: CandidateVerificationReceipt
    trusted_acceptance: TrustedAcceptanceEvaluation
    review_verdict: ReviewVerdict
    gate_verdict: Literal["PASS", "REVISE", "BLOCKED"]
    dogfooding_passed: StrictBool
    stable_worktree_mutated: Literal[False] = False
    auto_promote_stable_performed: Literal[False] = False
    human_merge_approval_required: Literal[True] = True
    next_authorized_action: HandoffNextAuthorizedAction
    blockers: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_bundle(self) -> Self:
        assert_milestone_c_scope_authorized(self.milestone_id, task_id=self.task_id)
        if self.dogfooding_passed and (
            not self.import_origin_report.verified
            or not self.verification_receipt.passed
            or not self.trusted_acceptance.passed
            or self.review_verdict != ReviewVerdict.PASS
            or self.gate_verdict != "PASS"
            or self.next_authorized_action != "HUMAN_MERGE_REVIEW"
        ):
            raise ValueError(
                "DogfoodingAuditBundle.dogfooding_passed cannot be True unless all "
                "isolation, verification, trusted acceptance, review, and gate checks pass "
                "and next_authorized_action is HUMAN_MERGE_REVIEW"
            )
        assert_no_secrets(self.model_dump(mode="json"))
        return self


class SelfDevelopmentDogfoodingCoordinator:
    """Coordinates isolated self-development dogfooding audits stopping at human merge review."""

    def __init__(self, validator: TrustedAcceptanceHarnessValidator | None = None) -> None:
        self._validator = validator or TrustedAcceptanceHarnessValidator()

    def run_dogfooding_audit(
        self,
        *,
        stable_candidate_config: StableCandidateConfig,
        manifest: TrustedHarnessManifest,
        task_id: str,
        changed_files: tuple[FileChange, ...],
        tests_summary: TestSummary,
        import_origin_report: CandidateImportOriginReport,
        verification_receipt: CandidateVerificationReceipt,
        review_verdict: ReviewVerdict,
        gate_verdict: Literal["PASS", "REVISE", "BLOCKED"],
    ) -> DogfoodingAuditBundle:
        """Run trusted acceptance & isolation audit for a self-development candidate."""
        assert_milestone_c_scope_authorized(manifest.milestone_id, task_id=task_id)
        s_root = Path(stable_candidate_config.stable_root).resolve()
        c_root = Path(stable_candidate_config.candidate_worktree_root).resolve()
        if s_root == c_root or s_root.is_relative_to(c_root) or c_root.is_relative_to(s_root):
            raise ValueError("Stable root and Candidate worktree root must be disjoint")
        if stable_candidate_config.allow_auto_promote_stable is not False:
            raise ValueError("allow_auto_promote_stable must be False")

        trusted_acceptance = self._validator.evaluate_candidate(
            manifest=manifest,
            workspace=c_root,
            task_id=task_id,
            candidate_sha=stable_candidate_config.candidate_sha,
            changed_files=changed_files,
            tests_summary=tests_summary,
        )

        blockers: list[str] = []
        if not import_origin_report.verified:
            blockers.append(f"IMPORT_ORIGIN_UNVERIFIED:{import_origin_report.status_code}")
        if not verification_receipt.passed:
            blockers.append(f"VERIFICATION_FAILED:{verification_receipt.status_code}")
        if not trusted_acceptance.passed:
            blockers.append(trusted_acceptance.rejection_code or REWARD_HACKING_DETECTED)
        if review_verdict != ReviewVerdict.PASS:
            blockers.append(f"REVIEW_VERDICT_{review_verdict.value}")
        if gate_verdict != "PASS":
            blockers.append(f"GATE_VERDICT_{gate_verdict}")

        dogfooding_passed = len(blockers) == 0
        if dogfooding_passed:
            next_action: HandoffNextAuthorizedAction = "HUMAN_MERGE_REVIEW"
        elif (
            trusted_acceptance.rejection_code
            in {TRUSTED_HARNESS_TAMPER_DETECTED, REWARD_HACKING_DETECTED}
            or not import_origin_report.verified
            or not verification_receipt.passed
            or gate_verdict == "BLOCKED"
            or review_verdict == ReviewVerdict.BLOCKED
        ):
            next_action = "HALT_ON_BLOCKER"
        else:
            next_action = "DISPATCH_BOUNDED_REPAIR"

        return DogfoodingAuditBundle(
            bundle_id=f"dogfood-{uuid4().hex[:12]}",
            project_id=manifest.project_id,
            milestone_id=manifest.milestone_id,
            task_id=task_id,
            stable_root=stable_candidate_config.stable_root,
            candidate_worktree_root=stable_candidate_config.candidate_worktree_root,
            pinned_stable_sha=stable_candidate_config.pinned_stable_sha,
            candidate_sha=stable_candidate_config.candidate_sha,
            import_origin_report=import_origin_report,
            verification_receipt=verification_receipt,
            trusted_acceptance=trusted_acceptance,
            review_verdict=review_verdict,
            gate_verdict=gate_verdict,
            dogfooding_passed=dogfooding_passed,
            stable_worktree_mutated=False,
            auto_promote_stable_performed=False,
            human_merge_approval_required=True,
            next_authorized_action=next_action,
            blockers=tuple(blockers),
        )
