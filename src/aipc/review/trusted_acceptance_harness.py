"""Workstream C03 — Trusted Acceptance Harness & Self-Development Dogfooding."""

from __future__ import annotations

import ast
import fnmatch
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Literal, Self
from uuid import uuid4

from pydantic import Field, StrictBool, StrictInt, model_validator

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
from aipc.review.context import context_fingerprint, digest, review_provenance
from aipc.review.errors import PhaseGateError, ReviewContextError
from aipc.review.gate import PhaseGate
from aipc.review.models import Digest, ReviewContext
from aipc.review.reviewer import bind_review
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


def _compute_manifest_digest(
    manifest_id: str,
    project_id: str,
    milestone_id: str,
    manifest_authority: Literal["trusted_operator", "stable_controller"],
    pinned_file_sha256: dict[str, str],
    protected_harness_globs: tuple[str, ...] | list[str],
    required_criterion_ids: tuple[str, ...] | list[str] = (),
    require_behavioral_assertions: bool = True,
    forbid_test_deletion: bool = True,
) -> str:
    """Compute canonical SHA-256 digest over TrustedHarnessManifest policy fields."""
    payload = (
        manifest_id,
        project_id,
        milestone_id,
        manifest_authority,
        dict(sorted(pinned_file_sha256.items())),
        list(protected_harness_globs),
        list(required_criterion_ids),
        bool(require_behavioral_assertions),
        bool(forbid_test_deletion),
    )
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class TrustedHarnessManifest(CanonicalRecord):
    """Manifest pinning protected trusted acceptance harness paths and SHA-256 digests."""

    manifest_id: NonBlank
    project_id: NonBlank
    milestone_id: NonBlank
    manifest_authority: Literal["trusted_operator", "stable_controller"] = "trusted_operator"
    manifest_digest: Digest | None = None
    trusted_manifest_path: str | None = None
    protected_harness_globs: tuple[NonBlank, ...] = _DEFAULT_PROTECTED_HARNESS_GLOBS
    pinned_file_sha256: dict[str, Digest] = Field(default_factory=dict)
    pre_workspace_snapshot: dict[str, Digest] = Field(default_factory=dict)
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
        if self.manifest_digest is not None:
            expected_digest = _compute_manifest_digest(
                self.manifest_id,
                self.project_id,
                self.milestone_id,
                self.manifest_authority,
                self.pinned_file_sha256,
                self.protected_harness_globs,
                self.required_criterion_ids,
                self.require_behavioral_assertions,
                self.forbid_test_deletion,
            )
            if self.manifest_digest != expected_digest:
                raise ValueError(f"{TRUSTED_HARNESS_TAMPER_DETECTED}: manifest_digest mismatch")
        assert_no_secrets(self.model_dump(mode="json"))
        return self


_SNAPSHOT_IGNORED_DIRS = frozenset({".git", "__pycache__", ".pytest_cache", ".ruff_cache", ".venv"})


def _snapshot_workspace_files(workspace_root: Path) -> dict[str, str]:
    """Capture normalized relative path -> SHA-256 hex digest for all workspace files."""
    snapshot: dict[str, str] = {}
    if not workspace_root.exists() or not workspace_root.is_dir():
        return snapshot
    for path in sorted(workspace_root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(workspace_root)
        if any(part in _SNAPSHOT_IGNORED_DIRS for part in rel.parts):
            continue
        norm_rel = _normalize_rel_path(rel.as_posix())
        snapshot[norm_rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    return snapshot


def build_trusted_harness_manifest(
    *,
    project_id: str,
    milestone_id: str,
    workspace: Path,
    protected_rel_paths: tuple[str, ...],
    manifest_id: str | None = None,
    manifest_authority: Literal["trusted_operator", "stable_controller"] = "trusted_operator",
    protected_harness_globs: tuple[str, ...] = _DEFAULT_PROTECTED_HARNESS_GLOBS,
    required_criterion_ids: tuple[str, ...] = (),
    require_behavioral_assertions: bool = True,
    forbid_test_deletion: bool = True,
    trusted_manifest_file: Path | None = None,
    trusted_manifest_dir: Path | None = None,
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

    pre_snap = _snapshot_workspace_files(workspace_root)
    resolved_manifest_id = manifest_id or f"manifest-{uuid4().hex[:12]}"
    if trusted_manifest_file is not None:
        resolved_manifest = Path(trusted_manifest_file).resolve()
        if resolved_manifest == workspace_root or resolved_manifest.is_relative_to(workspace_root):
            raise ValueError(
                f"{TRUSTED_HARNESS_TAMPER_DETECTED}: trusted_manifest_file "
                "must not reside inside candidate workspace"
            )
        trusted_path_str = str(resolved_manifest)
    else:
        manifest_dir = (
            Path(trusted_manifest_dir).resolve()
            if trusted_manifest_dir is not None
            else (
                workspace_root.parent / f".aipc_trusted_manifests_{workspace_root.name}"
            ).resolve()
        )
        resolved_manifest = (manifest_dir / f"{resolved_manifest_id}.json").resolve()
        if resolved_manifest == workspace_root or resolved_manifest.is_relative_to(workspace_root):
            raise ValueError(
                f"{TRUSTED_HARNESS_TAMPER_DETECTED}: trusted_manifest_dir "
                "must not reside inside candidate workspace"
            )
        trusted_path_str = str(resolved_manifest)

    manifest_digest_val = _compute_manifest_digest(
        resolved_manifest_id,
        project_id,
        milestone_id,
        manifest_authority,
        pinned,
        protected_harness_globs,
        required_criterion_ids,
        require_behavioral_assertions,
        forbid_test_deletion,
    )
    manifest = TrustedHarnessManifest(
        manifest_id=resolved_manifest_id,
        project_id=project_id,
        milestone_id=milestone_id,
        manifest_authority=manifest_authority,
        manifest_digest=manifest_digest_val,
        trusted_manifest_path=trusted_path_str,
        protected_harness_globs=protected_harness_globs,
        pinned_file_sha256=pinned,
        pre_workspace_snapshot=pre_snap,
        required_criterion_ids=required_criterion_ids,
        require_behavioral_assertions=require_behavioral_assertions,
        forbid_test_deletion=forbid_test_deletion,
    )
    out_path = Path(trusted_path_str)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
    return manifest


def _verify_manifest_integrity(
    manifest: TrustedHarnessManifest,
    workspace_root: Path,
    trusted_manifest_file: Path | None = None,
) -> list[RewardHackingFinding]:
    """Verify manifest policy digest and trusted manifest storage boundary."""
    findings: list[RewardHackingFinding] = []
    if manifest.manifest_authority not in ("trusted_operator", "stable_controller"):
        findings.append(
            RewardHackingFinding(
                rule_id="MANIFEST_AUTHORITY_INVALID",
                file_path="manifest",
                description=(
                    f"{TRUSTED_HARNESS_TAMPER_DETECTED}: invalid manifest_authority "
                    f"'{manifest.manifest_authority}'"
                ),
            )
        )
    expected_digest = _compute_manifest_digest(
        manifest.manifest_id,
        manifest.project_id,
        manifest.milestone_id,
        manifest.manifest_authority,
        manifest.pinned_file_sha256,
        manifest.protected_harness_globs,
        manifest.required_criterion_ids,
        manifest.require_behavioral_assertions,
        manifest.forbid_test_deletion,
    )
    if manifest.manifest_digest is None or manifest.manifest_digest != expected_digest:
        findings.append(
            RewardHackingFinding(
                rule_id="MANIFEST_DIGEST_MISMATCH",
                file_path="manifest",
                description=(
                    f"{TRUSTED_HARNESS_TAMPER_DETECTED}: manifest_digest missing or "
                    "mismatched against policy fields"
                ),
            )
        )

    manifest_path_str = (
        str(trusted_manifest_file)
        if trusted_manifest_file is not None
        else manifest.trusted_manifest_path
    )
    if manifest_path_str is None or not manifest_path_str.strip():
        findings.append(
            RewardHackingFinding(
                rule_id="TRUSTED_MANIFEST_PATH_MISSING",
                file_path="manifest",
                description=(
                    f"{TRUSTED_HARNESS_TAMPER_DETECTED}: trusted_manifest_path is "
                    "required and must reside outside candidate worktree"
                ),
            )
        )
    else:
        resolved_manifest_path = Path(manifest_path_str).resolve()
        if resolved_manifest_path == workspace_root or resolved_manifest_path.is_relative_to(
            workspace_root
        ):
            findings.append(
                RewardHackingFinding(
                    rule_id="MANIFEST_INSIDE_CANDIDATE_WORKTREE",
                    file_path=str(resolved_manifest_path),
                    description=(
                        f"{TRUSTED_HARNESS_TAMPER_DETECTED}: trusted manifest path "
                        "must not reside inside candidate workspace"
                    ),
                )
            )
        elif not resolved_manifest_path.is_file():
            findings.append(
                RewardHackingFinding(
                    rule_id="TRUSTED_MANIFEST_MISSING",
                    file_path=str(resolved_manifest_path),
                    description=(
                        f"{TRUSTED_HARNESS_TAMPER_DETECTED}: trusted manifest file "
                        "not found on disk"
                    ),
                )
            )
        else:
            try:
                raw_text = resolved_manifest_path.read_text(encoding="utf-8")
                disk_manifest = TrustedHarnessManifest.model_validate_json(raw_text)
                if disk_manifest != manifest:
                    findings.append(
                        RewardHackingFinding(
                            rule_id="TRUSTED_MANIFEST_DISK_MISMATCH",
                            file_path=str(resolved_manifest_path),
                            description=(
                                f"{TRUSTED_HARNESS_TAMPER_DETECTED}: on-disk trusted "
                                "manifest does not match supplied manifest"
                            ),
                        )
                    )
            except (OSError, ValueError) as exc:
                findings.append(
                    RewardHackingFinding(
                        rule_id="TRUSTED_MANIFEST_UNREADABLE",
                        file_path=str(resolved_manifest_path),
                        description=(
                            f"{TRUSTED_HARNESS_TAMPER_DETECTED}: failed to load trusted "
                            f"manifest from disk ({exc})"
                        ),
                    )
                )
    return findings


class CriterionExecutionReceipt(CanonicalRecord):
    """Structured execution receipt binding a criterion to candidate_sha and manifest_digest."""

    criterion_id: NonBlank
    candidate_sha: Commit
    manifest_digest: Digest
    test_node_id: NonBlank
    passed: StrictBool
    exit_code: StrictInt
    log_sha256: Digest | None = None


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


def _eval_static_constant(expr: ast.expr) -> tuple[bool, object]:
    """Evaluate a side-effect-free static AST expression, returning (is_static, value)."""
    if isinstance(expr, ast.Constant):
        return True, expr.value
    if isinstance(expr, (ast.Tuple, ast.List, ast.Set)):
        items: list[object] = []
        for elt in expr.elts:
            ok, val = _eval_static_constant(elt)
            if not ok:
                return False, None
            items.append(val)
        if isinstance(expr, ast.Tuple):
            return True, tuple(items)
        if isinstance(expr, ast.List):
            return True, items
        try:
            return True, set(items)
        except TypeError:
            return False, None
    if isinstance(expr, ast.Dict):
        dict_pairs: list[tuple[object, object]] = []
        for key_node, val_node in zip(expr.keys, expr.values, strict=True):
            if key_node is None:
                return False, None
            k_ok, k_val = _eval_static_constant(key_node)
            v_ok, v_val = _eval_static_constant(val_node)
            if not k_ok or not v_ok:
                return False, None
            dict_pairs.append((k_val, v_val))
        try:
            return True, dict(dict_pairs)
        except TypeError:
            return False, None
    if isinstance(expr, ast.UnaryOp):
        ok, operand_val = _eval_static_constant(expr.operand)
        if not ok:
            return False, None
        try:
            if isinstance(expr.op, ast.Not):
                return True, not operand_val
            if isinstance(expr.op, ast.UAdd):
                return True, +operand_val  # type: ignore[operator]
            if isinstance(expr.op, ast.USub):
                return True, -operand_val  # type: ignore[operator]
            if isinstance(expr.op, ast.Invert):
                return True, ~operand_val  # type: ignore[operator]
        except TypeError:
            return False, None
        return False, None
    if isinstance(expr, ast.BoolOp):
        is_and = isinstance(expr.op, ast.And)
        last_val: object = None
        for val_node in expr.values:
            ok, val = _eval_static_constant(val_node)
            if not ok:
                return False, None
            last_val = val
            if is_and and not val:
                return True, val
            if not is_and and val:
                return True, val
        return True, last_val
    if isinstance(expr, ast.BinOp):
        l_ok, l_val = _eval_static_constant(expr.left)
        r_ok, r_val = _eval_static_constant(expr.right)
        if not l_ok or not r_ok:
            return False, None
        try:
            if isinstance(expr.op, ast.Add):
                return True, l_val + r_val  # type: ignore[operator]
            if isinstance(expr.op, ast.Sub):
                return True, l_val - r_val  # type: ignore[operator]
            if isinstance(expr.op, ast.Mult):
                return True, l_val * r_val  # type: ignore[operator]
            if isinstance(expr.op, ast.Mod):
                return True, l_val % r_val  # type: ignore[operator]
            if isinstance(expr.op, ast.BitAnd):
                return True, l_val & r_val  # type: ignore[operator]
            if isinstance(expr.op, ast.BitOr):
                return True, l_val | r_val  # type: ignore[operator]
            if isinstance(expr.op, ast.BitXor):
                return True, l_val ^ r_val  # type: ignore[operator]
        except (TypeError, ZeroDivisionError, ValueError):
            return False, None
        return False, None
    if isinstance(expr, ast.Compare):
        operands = [expr.left, *expr.comparators]
        evals = [_eval_static_constant(op_node) for op_node in operands]
        if all(ok for ok, _ in evals):
            vals = [val for _, val in evals]
            try:
                for op, l_val, r_val in zip(expr.ops, vals, vals[1:], strict=False):
                    if isinstance(op, ast.Eq):
                        res = l_val == r_val
                    elif isinstance(op, ast.NotEq):
                        res = l_val != r_val
                    elif isinstance(op, ast.Lt):
                        res = l_val < r_val  # type: ignore[operator]
                    elif isinstance(op, ast.LtE):
                        res = l_val <= r_val  # type: ignore[operator]
                    elif isinstance(op, ast.Gt):
                        res = l_val > r_val  # type: ignore[operator]
                    elif isinstance(op, ast.GtE):
                        res = l_val >= r_val  # type: ignore[operator]
                    elif isinstance(op, ast.Is):
                        res = l_val is r_val
                    elif isinstance(op, ast.IsNot):
                        res = l_val is not r_val
                    elif isinstance(op, ast.In):
                        res = l_val in r_val  # type: ignore[operator]
                    elif isinstance(op, ast.NotIn):
                        res = l_val not in r_val  # type: ignore[operator]
                    else:
                        return False, None
                    if not res:
                        return True, False
                return True, True
            except TypeError:
                return False, None
        for op, left_op, right_op in zip(expr.ops, operands, operands[1:], strict=False):
            if ast.dump(left_op) == ast.dump(right_op):
                if isinstance(op, (ast.Eq, ast.Is, ast.LtE, ast.GtE)):
                    return True, True
                if isinstance(op, (ast.NotEq, ast.IsNot, ast.Lt, ast.Gt)):
                    return True, False
        return False, None
    if isinstance(expr, ast.IfExp):
        t_ok, t_val = _eval_static_constant(expr.test)
        if t_ok:
            return _eval_static_constant(expr.body if t_val else expr.orelse)
        return False, None
    return False, None


def _is_trivial_tautological_assert(node: ast.Assert) -> bool:
    return _eval_static_constant(node.test)[0]


_UNARY_UNITTEST_ASSERT_METHODS = frozenset(
    {"assertTrue", "assertFalse", "assertIsNone", "assertIsNotNone"}
)
_BINARY_UNITTEST_ASSERT_METHODS = frozenset(
    {"assertEqual", "assertNotEqual", "assertIs", "assertIsNot", "assertIn", "assertNotIn"}
)


def _is_trivial_assertion_call(node: ast.Call) -> bool:
    if isinstance(node.func, ast.Attribute) and node.func.attr in _UNITTEST_ASSERT_METHODS:
        if node.func.attr in _UNARY_UNITTEST_ASSERT_METHODS:
            return len(node.args) >= 1 and _eval_static_constant(node.args[0])[0]
        if node.func.attr in _BINARY_UNITTEST_ASSERT_METHODS:
            if len(node.args) < 2:
                return False
            both_static = (
                _eval_static_constant(node.args[0])[0] and _eval_static_constant(node.args[1])[0]
            )
            return both_static or ast.dump(node.args[0]) == ast.dump(node.args[1])
    return False


def _is_empty_static_iterable(expr: ast.expr) -> bool:
    if isinstance(expr, (ast.Tuple, ast.List, ast.Set)):
        return len(expr.elts) == 0
    if isinstance(expr, ast.Dict):
        return len(expr.keys) == 0
    if isinstance(expr, ast.Constant) and isinstance(expr.value, (str, bytes)):
        return len(expr.value) == 0
    if isinstance(expr, ast.Call) and isinstance(expr.func, ast.Name) and not expr.keywords:
        if expr.func.id in {"tuple", "list", "set", "dict"} and not expr.args:
            return True
        if expr.func.id == "range" and 1 <= len(expr.args) <= 3:
            evals = [_eval_static_constant(arg) for arg in expr.args]
            if all(ok and isinstance(val, int) for ok, val in evals):
                nums = [int(val) for _, val in evals]  # type: ignore[arg-type]
                if len(nums) == 1:
                    return nums[0] <= 0
                if len(nums) == 2:
                    return nums[0] >= nums[1]
                step = nums[2]
                return (step > 0 and nums[0] >= nums[1]) or (step < 0 and nums[0] <= nums[1])
    return False


def _inspect_function_assertions(
    fn_node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> tuple[bool, bool, bool]:
    """Return (has_real_assertion, has_trivial_assertion, has_unreachable_assertion)."""
    has_real = False
    has_trivial = False
    has_unreachable = False

    def _scan_expr(expr: ast.expr | None, reachable: bool) -> None:
        nonlocal has_real, has_trivial, has_unreachable
        if expr is None:
            return
        for sub in ast.walk(expr):
            if isinstance(sub, ast.Call) and _is_assertion_call(sub):
                if not reachable:
                    has_unreachable = True
                elif _is_trivial_assertion_call(sub):
                    has_trivial = True
                else:
                    has_real = True

    def _stmt_contains_break(stmts: list[ast.stmt]) -> bool:
        for s in stmts:
            if isinstance(s, ast.Break):
                return True
            if isinstance(
                s,
                (
                    ast.For,
                    ast.AsyncFor,
                    ast.While,
                    ast.FunctionDef,
                    ast.AsyncFunctionDef,
                    ast.ClassDef,
                ),
            ):
                continue
            if isinstance(s, ast.If):
                if _stmt_contains_break(s.body) or _stmt_contains_break(s.orelse):
                    return True
            elif isinstance(s, (ast.With, ast.AsyncWith)):
                if _stmt_contains_break(s.body):
                    return True
            elif isinstance(s, ast.Try):
                if (
                    _stmt_contains_break(s.body)
                    or any(_stmt_contains_break(h.body) for h in s.handlers)
                    or _stmt_contains_break(s.orelse)
                    or _stmt_contains_break(s.finalbody)
                ):
                    return True
            elif isinstance(s, ast.Match):
                if any(_stmt_contains_break(c.body) for c in s.cases):
                    return True
        return False

    def _walk_stmts(stmts: list[ast.stmt], reachable: bool) -> bool:
        nonlocal has_real, has_trivial, has_unreachable
        terminated = False
        for stmt in stmts:
            stmt_reachable = reachable and not terminated
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            if isinstance(stmt, ast.If):
                _scan_expr(stmt.test, stmt_reachable)
                is_const, const_val = _eval_static_constant(stmt.test)
                if is_const and bool(const_val):
                    body_term = _walk_stmts(stmt.body, stmt_reachable)
                    _walk_stmts(stmt.orelse, False)
                    if body_term:
                        terminated = True
                elif is_const and not bool(const_val):
                    _walk_stmts(stmt.body, False)
                    else_term = _walk_stmts(stmt.orelse, stmt_reachable)
                    if else_term:
                        terminated = True
                else:
                    body_term = _walk_stmts(stmt.body, stmt_reachable)
                    else_term = _walk_stmts(stmt.orelse, stmt_reachable) if stmt.orelse else False
                    if stmt.orelse and body_term and else_term:
                        terminated = True
            elif isinstance(stmt, ast.While):
                _scan_expr(stmt.test, stmt_reachable)
                w_const, w_val = _eval_static_constant(stmt.test)
                if w_const and not bool(w_val):
                    _walk_stmts(stmt.body, False)
                    else_term = _walk_stmts(stmt.orelse, stmt_reachable) if stmt.orelse else False
                    if else_term:
                        terminated = True
                elif w_const and bool(w_val):
                    body_term = _walk_stmts(stmt.body, stmt_reachable)
                    has_brk = _stmt_contains_break(stmt.body)
                    else_term = (
                        _walk_stmts(stmt.orelse, stmt_reachable and has_brk)
                        if stmt.orelse
                        else False
                    )
                    if not has_brk or (body_term and else_term):
                        terminated = True
                else:
                    body_term = _walk_stmts(stmt.body, stmt_reachable)
                    else_term = _walk_stmts(stmt.orelse, stmt_reachable) if stmt.orelse else False
                    if body_term and else_term and not _stmt_contains_break(stmt.body):
                        terminated = True
            elif isinstance(stmt, (ast.For, ast.AsyncFor)):
                _scan_expr(stmt.iter, stmt_reachable)
                is_empty = _is_empty_static_iterable(stmt.iter)
                body_term = _walk_stmts(stmt.body, stmt_reachable and not is_empty)
                else_term = _walk_stmts(stmt.orelse, stmt_reachable) if stmt.orelse else False
                if is_empty and else_term:
                    terminated = True
                elif (
                    not is_empty and body_term and else_term and not _stmt_contains_break(stmt.body)
                ):
                    terminated = True
            elif isinstance(stmt, (ast.With, ast.AsyncWith)):
                for item in stmt.items:
                    _scan_expr(item.context_expr, stmt_reachable)
                if _walk_stmts(stmt.body, stmt_reachable):
                    terminated = True
            elif isinstance(stmt, ast.Try):
                body_term = _walk_stmts(stmt.body, stmt_reachable)
                handlers_term: list[bool] = []
                broad_seen = False
                for h in stmt.handlers:
                    h_reachable = stmt_reachable and not broad_seen
                    h_term = _walk_stmts(h.body, h_reachable)
                    if h_reachable:
                        handlers_term.append(h_term)
                    is_broad = h.type is None or (
                        isinstance(h.type, ast.Name) and h.type.id in ("Exception", "BaseException")
                    )
                    if is_broad:
                        broad_seen = True
                else_term = (
                    _walk_stmts(stmt.orelse, stmt_reachable and not body_term)
                    if stmt.orelse
                    else False
                )
                final_term = (
                    _walk_stmts(stmt.finalbody, stmt_reachable) if stmt.finalbody else False
                )
                if final_term or ((body_term or else_term) and broad_seen and all(handlers_term)):
                    terminated = True
            elif isinstance(stmt, ast.Match):
                case_terms = [_walk_stmts(c.body, stmt_reachable) for c in stmt.cases]
                has_wildcard = any(
                    isinstance(c.pattern, ast.MatchAs) and c.pattern.pattern is None
                    for c in stmt.cases
                )
                if case_terms and all(case_terms) and has_wildcard:
                    terminated = True
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
                        elif _is_trivial_assertion_call(sub):
                            has_trivial = True
                        else:
                            has_real = True
            if isinstance(stmt, (ast.Return, ast.Raise, ast.Break, ast.Continue)):
                terminated = True
        return terminated

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
        test_fn_count = 0
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
                    test_fn_count += 1
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

        norm_rel = _normalize_rel_path(rel_path)
        filename = Path(norm_rel).name
        if (
            test_fn_count == 0
            and norm_rel.startswith("tests/")
            and (filename.startswith("test_") or filename.endswith("_test.py"))
        ):
            findings.append(
                RewardHackingFinding(
                    rule_id="VACUOUS_TEST_WITHOUT_ASSERT",
                    file_path=rel_path,
                    description="Test module defines no test_* functions",
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
        verification_receipt: CandidateVerificationReceipt | None = None,
        review_context: ReviewContext | None = None,
        trusted_manifest_file: Path | None = None,
        criterion_execution_receipts: tuple[CriterionExecutionReceipt, ...] = (),
    ) -> TrustedAcceptanceEvaluation:
        """Evaluate candidate changes and test evidence against TrustedHarnessManifest."""
        assert_milestone_c_scope_authorized(manifest.milestone_id, task_id=task_id)
        workspace_root = Path(workspace).resolve()
        findings: list[RewardHackingFinding] = []
        tamper_detected = False
        reward_hacking_detected = False

        manifest_findings = _verify_manifest_integrity(
            manifest,
            workspace_root,
            trusted_manifest_file=trusted_manifest_file,
        )
        if manifest_findings:
            findings.extend(manifest_findings)
            tamper_detected = True

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

        if manifest.pre_workspace_snapshot:
            current_snap = _snapshot_workspace_files(workspace_root)
            declared_set = {_normalize_rel_path(fc.path) for fc in changed_files}
            for rel_path, cur_digest in current_snap.items():
                prev_digest = manifest.pre_workspace_snapshot.get(rel_path)
                if prev_digest != cur_digest and rel_path not in declared_set:
                    findings.append(
                        RewardHackingFinding(
                            rule_id="UNDECLARED_WORKSPACE_MODIFICATION",
                            file_path=rel_path,
                            description=(
                                f"Workspace file '{rel_path}' modified or added outside "
                                "declared changed_files"
                            ),
                        )
                    )
                    reward_hacking_detected = True
            for rel_path in manifest.pre_workspace_snapshot:
                if rel_path not in current_snap and rel_path not in declared_set:
                    findings.append(
                        RewardHackingFinding(
                            rule_id="UNDECLARED_WORKSPACE_MODIFICATION",
                            file_path=rel_path,
                            description=(
                                f"Workspace file '{rel_path}' deleted outside "
                                "declared changed_files"
                            ),
                        )
                    )
                    reward_hacking_detected = True

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

        if manifest.require_behavioral_assertions and verification_receipt is None:
            findings.append(
                RewardHackingFinding(
                    rule_id="UNVERIFIED_EXECUTION_RECEIPT",
                    file_path="verification_receipt",
                    description=(
                        "Behavioral acceptance requires a verified "
                        "CandidateVerificationReceipt bound to candidate_sha"
                    ),
                )
            )
            reward_hacking_detected = True

        if verification_receipt is not None and (
            not verification_receipt.passed
            or verification_receipt.exit_code != 0
            or verification_receipt.isolation_mode == "blocked"
            or not verification_receipt.sanitized_env_verified
            or verification_receipt.candidate_sha != candidate_sha
        ):
            findings.append(
                RewardHackingFinding(
                    rule_id="UNVERIFIED_EXECUTION_RECEIPT",
                    file_path="verification_receipt",
                    description="CandidateVerificationReceipt failed integrity/execution checks",
                )
            )
            reward_hacking_detected = True

        if manifest.required_criterion_ids or criterion_results is not None:
            if manifest.required_criterion_ids and (
                verification_receipt is None
                or not verification_receipt.passed
                or verification_receipt.exit_code != 0
                or verification_receipt.candidate_sha != candidate_sha
            ):
                findings.append(
                    RewardHackingFinding(
                        rule_id="REQUIRED_CRITERION_UNVERIFIED",
                        file_path="verification_receipt",
                        description=(
                            "Required criteria demand a verified "
                            "CandidateVerificationReceipt bound to candidate_sha"
                        ),
                    )
                )
                reward_hacking_detected = True

            def _parse_summary_receipts(summary: str) -> list[CriterionExecutionReceipt]:
                stripped = summary.strip()
                if not stripped or stripped[0] not in ("{", "["):
                    return []
                try:
                    parsed = json.loads(stripped)
                except ValueError:
                    return []
                raw_items: object = (
                    parsed.get("criterion_execution_receipts")
                    if isinstance(parsed, dict)
                    else parsed
                )
                if not isinstance(raw_items, list):
                    return []
                out: list[CriterionExecutionReceipt] = []
                for item in raw_items:
                    try:
                        out.append(CriterionExecutionReceipt.model_validate(item))
                    except ValueError:
                        continue
                return out

            all_exec_receipts = list(criterion_execution_receipts)
            if verification_receipt is not None:
                all_exec_receipts.extend(_parse_summary_receipts(verification_receipt.summary))

            failed_receipt_cids = {
                rec.criterion_id
                for rec in all_exec_receipts
                if rec.criterion_id in manifest.required_criterion_ids
                and (not rec.passed or rec.exit_code != 0)
            }

            def _is_verified_context_artifact_for_criterion(ev: str, cid: str) -> bool:
                if review_context is None:
                    return False
                ev_root = Path(review_context.evidence_root).resolve()
                artifacts_by_path = {art.path: art for art in review_context.evidence.artifacts}
                for item in review_context.evidence.items:
                    if item.status != "collected" or item.path is None:
                        continue
                    if ev not in (item.evidence_id, item.path):
                        continue
                    art = artifacts_by_path.get(item.path)
                    if art is None:
                        return False
                    art_file = (ev_root / item.path).resolve()
                    if not art_file.is_relative_to(ev_root) or not art_file.is_file():
                        return False
                    raw_bytes = art_file.read_bytes()
                    if hashlib.sha256(raw_bytes).hexdigest() != art.sha256:
                        return False
                    content = raw_bytes.decode("utf-8", errors="replace")
                    return cid in content and candidate_sha in content and "PASS" in content
                return False

            def _valid_criterion_evidence(ev: str, cid: str) -> bool:
                if cid in failed_receipt_cids:
                    return False
                if _is_verified_context_artifact_for_criterion(ev, cid):
                    return True
                if (
                    verification_receipt is None
                    or not verification_receipt.passed
                    or verification_receipt.exit_code != 0
                    or verification_receipt.candidate_sha != candidate_sha
                    or manifest.manifest_digest is None
                ):
                    return False
                expected_ev = (
                    f"receipt:{verification_receipt.candidate_sha}:{cid}:{manifest.manifest_digest}"
                )
                for exec_rec in all_exec_receipts:
                    if (
                        exec_rec.criterion_id != cid
                        or exec_rec.candidate_sha != candidate_sha
                        or exec_rec.manifest_digest != manifest.manifest_digest
                        or not exec_rec.passed
                        or exec_rec.exit_code != 0
                    ):
                        continue
                    parts = exec_rec.test_node_id.split("::")
                    if len(parts) < 2 or not parts[-1].startswith("test_"):
                        continue
                    norm_test_file = _normalize_rel_path(parts[0])
                    if (
                        Path(norm_test_file).is_absolute()
                        or not norm_test_file.startswith("tests/")
                        or not norm_test_file.endswith(".py")
                    ):
                        continue
                    test_path = (workspace_root / norm_test_file).resolve()
                    if not test_path.is_relative_to(workspace_root) or not test_path.is_file():
                        continue
                    source = test_path.read_text(encoding="utf-8")
                    if len(self.inspect_python_test_source(norm_test_file, source)) != 0:
                        continue
                    if ev in (exec_rec.test_node_id, norm_test_file, expected_ev):
                        return True
                return False

            criterion_map: dict[str, bool] = {}
            if isinstance(criterion_results, dict):
                for k in criterion_results:
                    criterion_map[str(k)] = False
                for req_id in manifest.required_criterion_ids:
                    criterion_map[req_id] = False
                findings.append(
                    RewardHackingFinding(
                        rule_id="REQUIRED_CRITERION_UNVERIFIED",
                        file_path="criteria",
                        description=(
                            "Boolean criterion map lacks structured execution evidence; "
                            "provide CriterionResult items"
                        ),
                    )
                )
                reward_hacking_detected = True
            elif criterion_results is not None:
                for cr in criterion_results:
                    if cr.criterion_id in manifest.required_criterion_ids:
                        has_provenance = cr.criterion_id not in failed_receipt_cids and any(
                            _valid_criterion_evidence(ev, cr.criterion_id) for ev in cr.evidence
                        )
                        criterion_map[cr.criterion_id] = (
                            cr.status == CriterionStatus.PASS
                            and len(cr.evidence) > 0
                            and has_provenance
                        )
                    else:
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
    reviewer_gate_decision: SubscriptionReviewerGateDecision | None = None
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
        cand_src_root = (cand_root / "src").resolve()
        cand_pkg_root = (cand_root / "src" / "aipc").resolve()
        expected_src = Path(self.import_origin_report.expected_candidate_src).resolve()
        actual_file_raw = self.import_origin_report.actual_imported_file.strip()
        actual_imported_ok = bool(actual_file_raw) and (
            Path(actual_file_raw).resolve().is_relative_to(cand_pkg_root)
        )
        if self.dogfooding_passed and (
            not self.import_origin_report.verified
            or not self.verification_receipt.passed
            or not self.trusted_acceptance.passed
            or self.review_verdict != ReviewVerdict.PASS
            or self.gate_verdict != "PASS"
            or self.reviewer_gate_decision is None
            or self.reviewer_gate_decision.gate_outcome != "PASS"
            or self.reviewer_gate_decision.candidate_commit_sha != self.candidate_sha
            or self.reviewer_gate_decision.task_id != self.task_id
            or self.next_authorized_action != "HUMAN_MERGE_REVIEW"
            or self.stable_worktree_mutated
            or self.verification_receipt.candidate_sha != self.candidate_sha
            or self.trusted_acceptance.candidate_sha != self.candidate_sha
            or not self.verification_receipt.import_origin_verified
            or bool(self.import_origin_report.leaked_stable_paths)
            or expected_src not in (cand_src_root, cand_pkg_root)
            or not actual_imported_ok
        ):
            raise ValueError(
                "DogfoodingAuditBundle.dogfooding_passed cannot be True unless all "
                "isolation, verification, trusted acceptance, review, and gate checks pass "
                "and next_authorized_action is HUMAN_MERGE_REVIEW"
            )
        assert_no_secrets(self.model_dump(mode="json"))
        return self


def _check_candidate_git_integrity(
    c_root: Path,
    expected_candidate_sha: str,
) -> tuple[bool, tuple[str, ...]]:
    """Verify candidate checkout is a clean git repository at expected_candidate_sha."""
    if not c_root.exists() or not c_root.is_dir() or not (c_root / ".git").exists():
        return False, ()
    env = build_sanitized_child_env()
    try:
        top_res = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=str(c_root),
            env=env,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            check=False,
        )
        top_raw = (top_res.stdout or "").strip()
        if top_res.returncode != 0 or not top_raw or Path(top_raw).resolve() != c_root.resolve():
            return False, ()
        head_res = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(c_root),
            env=env,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            check=False,
        )
        actual_head = (head_res.stdout or "").strip().lower()
        if head_res.returncode != 0 or actual_head != expected_candidate_sha.strip().lower():
            return False, ()
        status_res = subprocess.run(
            ["git", "status", "--porcelain", "-uall"],
            cwd=str(c_root),
            env=env,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            check=False,
        )
        if status_res.returncode != 0 or bool((status_res.stdout or "").strip()):
            return False, ()
    except OSError:
        return False, ()
    return True, ()


def _check_stable_git_integrity(s_root: Path, pinned_stable_sha: str) -> bool:
    """Return True if Stable git checkout is missing, mutated, or HEAD mismatches."""
    if not s_root.exists() or not s_root.is_dir() or not (s_root / ".git").exists():
        return True
    env = build_sanitized_child_env()
    try:
        top_res = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=str(s_root),
            env=env,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            check=False,
        )
        top_raw = (top_res.stdout or "").strip()
        if top_res.returncode != 0 or not top_raw or Path(top_raw).resolve() != s_root.resolve():
            return True
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
    except OSError:
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
        criterion_results: tuple[CriterionResult, ...] | dict[str, bool] | None = None,
        review_context: ReviewContext | None = None,
    ) -> DogfoodingAuditBundle:
        """Run trusted acceptance & isolation audit for a self-development candidate."""
        assert_milestone_c_scope_authorized(manifest.milestone_id, task_id=task_id)
        s_root = Path(stable_candidate_config.stable_root).resolve()
        c_root = Path(stable_candidate_config.candidate_worktree_root).resolve()
        if s_root == c_root or s_root.is_relative_to(c_root) or c_root.is_relative_to(s_root):
            raise ValueError("Stable root and Candidate worktree root must be disjoint")
        if stable_candidate_config.allow_auto_promote_stable is not False:
            raise ValueError("allow_auto_promote_stable must be False")

        effective_criterion_results = criterion_results
        if (
            effective_criterion_results is None
            and manifest.required_criterion_ids
            and reviewer_gate_decision is not None
            and gate_verdict == "PASS"
        ):
            effective_criterion_results = tuple(reviewer_gate_decision.review_result.criteria)

        gate_ctx: ReviewContext | None = None
        if review_context is not None:
            gate_ctx = (
                review_context
                if review_context.state.last_review is None
                else review_context.model_copy(
                    update={"state": review_context.state.model_copy(update={"last_review": None})}
                )
            )

        trusted_acceptance = self._validator.evaluate_candidate(
            manifest=manifest,
            workspace=c_root,
            task_id=task_id,
            candidate_sha=stable_candidate_config.candidate_sha,
            changed_files=changed_files,
            tests_summary=tests_summary,
            criterion_results=effective_criterion_results,
            verification_receipt=verification_receipt,
            review_context=gate_ctx,
        )

        blockers: list[str] = []
        if not import_origin_report.verified:
            blockers.append(f"IMPORT_ORIGIN_UNVERIFIED:{import_origin_report.status_code}")
        if (
            not verification_receipt.passed
            or verification_receipt.exit_code != 0
            or verification_receipt.isolation_mode == "blocked"
            or not verification_receipt.sanitized_env_verified
        ):
            verif_blocker = f"VERIFICATION_FAILED:{verification_receipt.status_code}"
            if verif_blocker not in blockers:
                blockers.append(verif_blocker)
        if verification_receipt.candidate_sha != stable_candidate_config.candidate_sha:
            blockers.append("VERIFICATION_RECEIPT_SHA_MISMATCH")

        cand_src_root = (c_root / "src").resolve()
        expected_pkg_root = (c_root / "src" / "aipc").resolve()
        expected_cand_src = Path(import_origin_report.expected_candidate_src).resolve()
        actual_file_raw = import_origin_report.actual_imported_file.strip()
        actual_imported_ok = bool(actual_file_raw) and (
            Path(actual_file_raw).resolve().is_relative_to(expected_pkg_root)
        )
        if (
            not verification_receipt.import_origin_verified
            or verification_receipt.import_origin_report != import_origin_report
            or expected_cand_src not in (cand_src_root, expected_pkg_root)
            or not actual_imported_ok
            or len(import_origin_report.leaked_stable_paths) > 0
        ):
            blockers.append("IMPORT_ORIGIN_WORKSPACE_MISMATCH")

        cand_git_ok, _ = _check_candidate_git_integrity(
            c_root,
            stable_candidate_config.candidate_sha,
        )
        if not cand_git_ok:
            blockers.append("CANDIDATE_WORKTREE_DIRTY_OR_SHA_MISMATCH")

        stable_mutated = any(
            (Path(fc.path).is_absolute() and Path(fc.path).resolve().is_relative_to(s_root))
            or (c_root / _normalize_rel_path(fc.path)).resolve().is_relative_to(s_root)
            for fc in changed_files
        )
        if not verify_stable_git_integrity:
            blockers.append("STABLE_GIT_INTEGRITY_UNVERIFIED")
        elif not stable_mutated:
            stable_mutated = _check_stable_git_integrity(
                s_root,
                stable_candidate_config.pinned_stable_sha,
            )
        if stable_mutated:
            blockers.append("STABLE_WORKTREE_MUTATED_OR_SHA_MISMATCH")

        if review_context is None and (
            review_verdict == ReviewVerdict.PASS or gate_verdict == "PASS"
        ):
            blockers.append("MISSING_REVIEW_CONTEXT")

        decision_mismatch = False
        if reviewer_gate_decision is None:
            if review_verdict == ReviewVerdict.PASS or gate_verdict == "PASS":
                blockers.append("MISSING_REVIEWER_GATE_DECISION")
        else:
            cand_sha = stable_candidate_config.candidate_sha
            decision_mismatch = (
                reviewer_gate_decision.task_id != task_id
                or reviewer_gate_decision.candidate_commit_sha != cand_sha
                or reviewer_gate_decision.reviewed_commit_sha != cand_sha
                or reviewer_gate_decision.gate_outcome != gate_verdict
                or reviewer_gate_decision.review_result.verdict != review_verdict
            )
            if not decision_mismatch and gate_verdict == "PASS":
                bound_rev = reviewer_gate_decision.bound_review
                gate_dec = reviewer_gate_decision.phase_gate_decision
                sess = reviewer_gate_decision.session_audit
                rev_id = reviewer_gate_decision.review_result.review_id
                nested_ok = (
                    reviewer_gate_decision.review_result.task_id == task_id
                    and bound_rev is not None
                    and bound_rev.review == reviewer_gate_decision.review_result
                    and bound_rev.review.review_id == rev_id
                    and bound_rev.review.task_id == task_id
                    and bound_rev.provenance.task_id == task_id
                    and bound_rev.provenance.git_commit == cand_sha
                    and gate_dec is not None
                    and gate_dec.verdict == ReviewVerdict.PASS
                    and gate_dec.task_id == task_id
                    and gate_dec.review_id == rev_id
                    and sess.task_id == task_id
                    and sess.candidate_commit_sha == cand_sha
                    and not sess.workspace_mutated
                    and sess.fresh_isolated_session
                    and sess.read_only_sandbox_enforced
                    and not sess.inherited_chat_history
                    and sess.independence_level in ("cross_provider", "reduced_independence")
                    and sess.workspace_snapshot_before_sha256
                    == sess.workspace_snapshot_after_sha256
                )
                if not nested_ok:
                    decision_mismatch = True
                elif bound_rev.review_digest != digest(reviewer_gate_decision.review_result):
                    decision_mismatch = True
                elif gate_ctx is not None:
                    try:
                        SubscriptionReviewerGateDecision.model_validate(
                            reviewer_gate_decision.model_dump(mode="json")
                        )
                        rebound = bind_review(
                            gate_ctx,
                            reviewer_gate_decision.review_result,
                            authority=bound_rev.authority,
                            reviewed=bound_rev.provenance,
                        )
                        if (
                            gate_ctx.task.task_id != task_id
                            or gate_ctx.checkpoint.git_commit != cand_sha
                            or gate_ctx.agent_result.task_id != task_id
                            or gate_ctx.agent_result.tests != tests_summary
                            or gate_ctx.evidence.git.head != cand_sha
                            or bound_rev.context_digest != context_fingerprint(gate_ctx)
                            or bound_rev.review_digest
                            != digest(reviewer_gate_decision.review_result)
                            or bound_rev.provenance != review_provenance(gate_ctx)
                            or bound_rev.provenance.agent_result_digest
                            != digest(gate_ctx.agent_result)
                            or bound_rev.provenance.evidence_digest != digest(gate_ctx.evidence)
                            or rebound != bound_rev
                            or PhaseGate().evaluate(bound_rev, gate_ctx) != gate_dec
                        ):
                            decision_mismatch = True
                    except (ReviewContextError, PhaseGateError, ValueError):
                        decision_mismatch = True
            if decision_mismatch:
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
            "CANDIDATE_WORKTREE_DIRTY_OR_SHA_MISMATCH",
            "STABLE_GIT_INTEGRITY_UNVERIFIED",
            "STABLE_WORKTREE_MUTATED_OR_SHA_MISMATCH",
            "REVIEW_GATE_DECISION_MISMATCH",
            "MISSING_REVIEWER_GATE_DECISION",
            "MISSING_REVIEW_CONTEXT",
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
            reviewer_gate_decision=(
                reviewer_gate_decision
                if reviewer_gate_decision is not None and not decision_mismatch
                else None
            ),
            dogfooding_passed=dogfooding_passed,
            stable_worktree_mutated=stable_mutated,
            auto_promote_stable_performed=False,
            human_merge_approval_required=True,
            next_authorized_action=next_action,
            blockers=tuple(blockers),
        )
