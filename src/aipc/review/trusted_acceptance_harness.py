"""Workstream C03 — Trusted Acceptance Harness & Self-Development Dogfooding."""

from __future__ import annotations

import ast
import fnmatch
import hashlib
import subprocess
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
from aipc.domain.enums import ChangeType, CriterionStatus, ReviewVerdict
from aipc.domain.models import (
    CanonicalRecord,
    CriterionResult,
    FileChange,
    NonBlank,
    TestSummary,
)
from aipc.hardening.env_isolation import build_sanitized_child_env
from aipc.hardening.secrets import assert_no_secrets
from aipc.review.models import Digest
from aipc.review.subscription_reviewer_gate import SubscriptionReviewerGateDecision
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
    required_criterion_ids: tuple[NonBlank, ...] = ()
    require_behavioral_assertions: StrictBool = True
    forbid_test_deletion: StrictBool = True

    @model_validator(mode="after")
    def validate_manifest(self) -> Self:
        assert_milestone_c_scope_authorized(self.milestone_id)
        if not self.pinned_file_sha256:
            raise ValueError(
                "TrustedHarnessManifest requires at least one pinned_file_sha256 entry"
            )
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


def _is_trivial_tautological_assert(node: ast.Assert) -> bool:
    test = node.test
    if isinstance(test, (ast.Constant, ast.Tuple, ast.List, ast.Dict, ast.Set)):
        return True
    if isinstance(test, ast.Compare) and len(test.comparators) == 1:
        op = test.ops[0]
        if isinstance(op, (ast.Eq, ast.Is, ast.LtE, ast.GtE)):
            if ast.dump(test.left) == ast.dump(test.comparators[0]):
                return True
    return False


def _inspect_function_assertions(
    fn_node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> tuple[bool, bool, bool]:
    """Return (has_real_assertion, has_trivial_assertion, has_unreachable_assertion)."""
    has_real = False
    has_trivial = False
    has_unreachable = False

    def _scan_expr(expr: ast.expr | None, reachable: bool) -> None:
        nonlocal has_real, has_unreachable
        if expr is None:
            return
        for sub in ast.walk(expr):
            if isinstance(sub, ast.Call) and _is_assertion_call(sub):
                if reachable:
                    has_real = True
                else:
                    has_unreachable = True

    def _walk_stmts(stmts: list[ast.stmt], reachable: bool) -> None:
        nonlocal has_real, has_trivial, has_unreachable
        terminated = False
        for stmt in stmts:
            stmt_reachable = reachable and not terminated
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            if isinstance(stmt, ast.If):
                _scan_expr(stmt.test, stmt_reachable)
                if isinstance(stmt.test, ast.Constant):
                    body_reach = stmt_reachable and bool(stmt.test.value)
                    else_reach = stmt_reachable and not bool(stmt.test.value)
                else:
                    body_reach = stmt_reachable
                    else_reach = stmt_reachable
                _walk_stmts(stmt.body, body_reach)
                _walk_stmts(stmt.orelse, else_reach)
            elif isinstance(stmt, ast.While):
                _scan_expr(stmt.test, stmt_reachable)
                dead_while = isinstance(stmt.test, ast.Constant) and not bool(stmt.test.value)
                _walk_stmts(stmt.body, stmt_reachable and not dead_while)
                _walk_stmts(stmt.orelse, stmt_reachable)
            elif isinstance(stmt, (ast.For, ast.AsyncFor)):
                _scan_expr(stmt.iter, stmt_reachable)
                _walk_stmts(stmt.body, stmt_reachable)
                _walk_stmts(stmt.orelse, stmt_reachable)
            elif isinstance(stmt, (ast.With, ast.AsyncWith)):
                for item in stmt.items:
                    _scan_expr(item.context_expr, stmt_reachable)
                _walk_stmts(stmt.body, stmt_reachable)
            elif isinstance(stmt, ast.Try):
                _walk_stmts(stmt.body, stmt_reachable)
                for handler in stmt.handlers:
                    _walk_stmts(handler.body, stmt_reachable)
                _walk_stmts(stmt.orelse, stmt_reachable)
                _walk_stmts(stmt.finalbody, stmt_reachable)
            elif isinstance(stmt, ast.Match):
                for case in stmt.cases:
                    _walk_stmts(case.body, stmt_reachable)
            else:
                for sub in ast.walk(stmt):
                    if isinstance(sub, ast.Assert):
                        if not stmt_reachable:
                            has_unreachable = True
                        elif _is_trivial_tautological_assert(sub):
                            has_trivial = True
                        else:
                            has_real = True
                    elif isinstance(sub, ast.Call) and _is_assertion_call(sub):
                        if not stmt_reachable:
                            has_unreachable = True
                        else:
                            has_real = True
            if isinstance(stmt, (ast.Return, ast.Raise, ast.Break, ast.Continue)):
                terminated = True

    _walk_stmts(fn_node.body, reachable=True)
    return has_real, has_trivial, has_unreachable


def _function_has_real_assertion(fn_node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    has_real, _, _ = _inspect_function_assertions(fn_node)
    return has_real


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
                if node.name.startswith("test_"):
                    has_real, has_trivial, has_unreachable = _inspect_function_assertions(node)
                    if has_trivial:
                        findings.append(
                            RewardHackingFinding(
                                rule_id="TRIVIAL_TAUTOLOGICAL_ASSERT",
                                file_path=rel_path,
                                description=(
                                    f"Test function '{node.name}' contains trivial "
                                    "tautological assertion"
                                ),
                            )
                        )
                    if has_unreachable:
                        findings.append(
                            RewardHackingFinding(
                                rule_id="UNREACHABLE_ASSERTION",
                                file_path=rel_path,
                                description=(
                                    f"Test function '{node.name}' contains unreachable assertion"
                                ),
                            )
                        )
                    if not has_real:
                        findings.append(
                            RewardHackingFinding(
                                rule_id="VACUOUS_TEST_WITHOUT_ASSERT",
                                file_path=rel_path,
                                description=(
                                    f"Test function '{node.name}' lacks reachable "
                                    "non-trivial assertions or pytest.raises"
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
        criterion_results: tuple[CriterionResult, ...] | dict[str, bool] | None = None,
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
                if not file_path.is_relative_to(workspace_root) or not file_path.is_file():
                    findings.append(
                        RewardHackingFinding(
                            rule_id="MISSING_DECLARED_TEST_FILE",
                            file_path=norm_path,
                            description=(
                                f"Declared added/modified test file missing on disk: '{norm_path}'"
                            ),
                        )
                    )
                    reward_hacking_detected = True
                else:
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
            tests_summary is not None and tests_summary.passed > 0 and tests_summary.failed == 0
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

        if manifest.required_criterion_ids or criterion_results is not None:
            criterion_map: dict[str, bool] = {}
            if isinstance(criterion_results, dict):
                criterion_map = {str(k): bool(v) for k, v in criterion_results.items()}
            elif criterion_results is not None:
                for cr in criterion_results:
                    criterion_map[cr.criterion_id] = cr.status == CriterionStatus.PASS

            if manifest.required_criterion_ids and criterion_results is None:
                findings.append(
                    RewardHackingFinding(
                        rule_id="REQUIRED_CRITERION_UNVERIFIED",
                        file_path="criteria",
                        description=(
                            "Required criterion_results missing for manifest.required_criterion_ids"
                        ),
                    )
                )
                reward_hacking_detected = True
            else:
                for req_id in manifest.required_criterion_ids:
                    if not criterion_map.get(req_id, False):
                        findings.append(
                            RewardHackingFinding(
                                rule_id="REQUIRED_CRITERION_UNVERIFIED",
                                file_path="criteria",
                                description=(
                                    f"Required criterion '{req_id}' missing or failed verification"
                                ),
                            )
                        )
                        reward_hacking_detected = True
                for cid, c_passed in criterion_map.items():
                    if not c_passed and cid not in manifest.required_criterion_ids:
                        findings.append(
                            RewardHackingFinding(
                                rule_id="REQUIRED_CRITERION_UNVERIFIED",
                                file_path="criteria",
                                description=f"Supplied criterion '{cid}' failed verification",
                            )
                        )
                        reward_hacking_detected = True

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
    stable_worktree_mutated: StrictBool = False
    auto_promote_stable_performed: Literal[False] = False
    human_merge_approval_required: Literal[True] = True
    next_authorized_action: HandoffNextAuthorizedAction
    blockers: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_bundle(self) -> Self:
        assert_milestone_c_scope_authorized(self.milestone_id, task_id=self.task_id)
        cand_root = Path(self.candidate_worktree_root).resolve()
        expected_src = Path(self.import_origin_report.expected_candidate_src).resolve()
        if self.dogfooding_passed and (
            not self.import_origin_report.verified
            or not self.verification_receipt.passed
            or not self.trusted_acceptance.passed
            or self.review_verdict != ReviewVerdict.PASS
            or self.gate_verdict != "PASS"
            or self.next_authorized_action != "HUMAN_MERGE_REVIEW"
            or self.stable_worktree_mutated
            or self.verification_receipt.candidate_sha != self.candidate_sha
            or self.trusted_acceptance.candidate_sha != self.candidate_sha
            or not self.verification_receipt.import_origin_verified
            or bool(self.import_origin_report.leaked_stable_paths)
            or not expected_src.is_relative_to(cand_root)
        ):
            raise ValueError(
                "DogfoodingAuditBundle.dogfooding_passed cannot be True unless all "
                "isolation, verification, trusted acceptance, review, and gate checks pass "
                "and next_authorized_action is HUMAN_MERGE_REVIEW"
            )
        assert_no_secrets(self.model_dump(mode="json"))
        return self


def _check_stable_git_integrity(s_root: Path, pinned_stable_sha: str) -> bool:
    """Return True if Stable git checkout is mutated or HEAD mismatches pinned_stable_sha."""
    if not (s_root / ".git").exists():
        return False
    env = build_sanitized_child_env()
    head_res = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(s_root),
        env=env,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        check=False,
    )
    actual_head = (head_res.stdout or "").strip().lower()
    if head_res.returncode != 0 or actual_head != pinned_stable_sha.lower():
        return True
    status_res = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=str(s_root),
        env=env,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        check=False,
    )
    if status_res.returncode != 0 or bool((status_res.stdout or "").strip()):
        return True
    return False


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
        reviewer_gate_decision: SubscriptionReviewerGateDecision | None = None,
        verify_stable_git_integrity: bool = True,
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
        if verification_receipt.candidate_sha != stable_candidate_config.candidate_sha:
            blockers.append("VERIFICATION_RECEIPT_SHA_MISMATCH")

        expected_cand_src = Path(import_origin_report.expected_candidate_src).resolve()
        actual_file_raw = import_origin_report.actual_imported_file.strip()
        actual_imported_ok = bool(actual_file_raw) and (
            Path(actual_file_raw).resolve().is_relative_to(c_root)
        )
        if (
            not verification_receipt.import_origin_verified
            or verification_receipt.import_origin_report != import_origin_report
            or not expected_cand_src.is_relative_to(c_root)
            or not actual_imported_ok
            or len(import_origin_report.leaked_stable_paths) > 0
        ):
            blockers.append("IMPORT_ORIGIN_WORKSPACE_MISMATCH")

        stable_mutated = any(
            (Path(fc.path).is_absolute() and Path(fc.path).resolve().is_relative_to(s_root))
            or (c_root / _normalize_rel_path(fc.path)).resolve().is_relative_to(s_root)
            for fc in changed_files
        )
        if verify_stable_git_integrity and not stable_mutated:
            stable_mutated = _check_stable_git_integrity(
                s_root,
                stable_candidate_config.pinned_stable_sha,
            )
        if stable_mutated:
            blockers.append("STABLE_WORKTREE_MUTATED_OR_SHA_MISMATCH")

        if reviewer_gate_decision is not None:
            if (
                reviewer_gate_decision.task_id != task_id
                or reviewer_gate_decision.candidate_commit_sha
                != stable_candidate_config.candidate_sha
                or reviewer_gate_decision.reviewed_commit_sha
                != stable_candidate_config.candidate_sha
                or reviewer_gate_decision.gate_outcome != gate_verdict
                or reviewer_gate_decision.review_result.verdict != review_verdict
            ):
                blockers.append("REVIEW_GATE_DECISION_MISMATCH")

        if not trusted_acceptance.passed:
            blockers.append(trusted_acceptance.rejection_code or REWARD_HACKING_DETECTED)
        if review_verdict != ReviewVerdict.PASS:
            blockers.append(f"REVIEW_VERDICT_{review_verdict.value}")
        if gate_verdict != "PASS":
            blockers.append(f"GATE_VERDICT_{gate_verdict}")

        dogfooding_passed = len(blockers) == 0
        hard_blockers = {
            "VERIFICATION_RECEIPT_SHA_MISMATCH",
            "IMPORT_ORIGIN_WORKSPACE_MISMATCH",
            "STABLE_WORKTREE_MUTATED_OR_SHA_MISMATCH",
            "REVIEW_GATE_DECISION_MISMATCH",
        }
        if dogfooding_passed:
            next_action: HandoffNextAuthorizedAction = "HUMAN_MERGE_REVIEW"
        elif (
            trusted_acceptance.rejection_code
            in {TRUSTED_HARNESS_TAMPER_DETECTED, REWARD_HACKING_DETECTED}
            or not import_origin_report.verified
            or not verification_receipt.passed
            or gate_verdict == "BLOCKED"
            or review_verdict == ReviewVerdict.BLOCKED
            or stable_mutated
            or any(b in hard_blockers for b in blockers)
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
            stable_worktree_mutated=stable_mutated,
            auto_promote_stable_performed=False,
            human_merge_approval_required=True,
            next_authorized_action=next_action,
            blockers=tuple(blockers),
        )
