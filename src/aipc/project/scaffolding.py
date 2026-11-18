"""Minimal per-project runtime scaffolding, path isolation, and template validation.

Provides deterministic, idempotent initialization of external per-project runtime
directories (``projects/<project_id>/runtime/{state,audit,worktrees}``) and
fail-closed validation for starter ``PROJECT.yaml`` and ``review_governance.yaml``
templates. YAML templates can never authorize a milestone; external Controller-owned
JSON records remain mandatory.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import yaml
from pydantic import TypeAdapter, ValidationError

from aipc.domain.errors import ProjectDefinitionError
from aipc.domain.models import CanonicalRecord
from aipc.hardening.errors import SecretExposureError
from aipc.hardening.secrets import assert_no_secrets
from aipc.project.models import ProjectDefinition
from aipc.project.project_loader import load_project
from aipc.review.governance_policy import (
    ReviewGovernancePolicy,
    ReviewGovernancePolicyError,
    _UniqueKeySafeLoader,
)
from aipc.storage.json_store import load_json, write_json_atomic
from aipc.version.models import Identifier

_IDENTIFIER_ADAPTER = TypeAdapter(Identifier)

_FORBIDDEN_YAML_AUTH_KEYS: frozenset[str] = frozenset(
    {
        "milestone_authorization",
        "controller_authorization",
        "controller_milestone_authorization",
        "milestone_grant",
        "approval_grant",
        "approved_milestone",
        "authorized_milestones",
        "commander_approval",
        "unlock_milestone",
        "authorize_milestone",
        "approval_digest",
        "authorized_by",
        "milestone_d",
    }
)

_FORBIDDEN_MILESTONE_D_TOKENS: tuple[str, ...] = (
    "milestone-d",
    "milestone_d",
    "workstream-d",
    "d01",
    "d02",
    "d03",
    "phase-25",
    "phase_25",
    "phase25",
)


class ProjectScaffoldingError(ProjectDefinitionError):
    """Fail-closed error for unsafe project paths, overlap, or invalid templates."""


class ProjectRuntimeLayout(CanonicalRecord):
    """Validated, isolated external runtime layout for a single project."""

    project_id: Identifier
    project_root: Path
    runtime_root: Path
    state_dir: Path
    audit_dir: Path
    worktrees_dir: Path
    project_state_path: Path
    queue_path: Path
    budget_ledger_path: Path
    receipt_dir: Path
    lock_path: Path
    scaffold_manifest_path: Path


def validate_project_identifier(project_id: str) -> Identifier:
    """Validate project_id against canonical Identifier and reject traversal."""
    if not isinstance(project_id, str):
        raise ProjectScaffoldingError("project_id must be a string")
    if "/" in project_id or "\\" in project_id or ".." in project_id or "\x00" in project_id:
        raise ProjectScaffoldingError(f"Unsafe characters in project_id: {project_id!r}")
    try:
        validated = _IDENTIFIER_ADAPTER.validate_python(project_id)
    except ValidationError as exc:
        raise ProjectScaffoldingError(
            f"project_id must match canonical Identifier pattern: {project_id!r}"
        ) from exc
    if any(token in validated for token in _FORBIDDEN_MILESTONE_D_TOKENS):
        raise ProjectScaffoldingError(f"project_id references forbidden scope: {validated!r}")
    return validated


def _check_no_traversal_or_symlink(path: Path, *, label: str) -> None:
    if "\x00" in str(path):
        raise ProjectScaffoldingError(f"Null byte in {label} path")
    if ".." in path.parts:
        raise ProjectScaffoldingError(f"Parent traversal '..' is forbidden in {label}: {path}")
    if path.is_symlink():
        raise ProjectScaffoldingError(f"Symlink path is forbidden for {label}: {path}")


def _require_absolute_clean_path(path: Path, *, label: str) -> Path:
    if not isinstance(path, Path):
        raise ProjectScaffoldingError(f"{label} must be a Path instance")
    _check_no_traversal_or_symlink(path, label=label)
    if not path.is_absolute():
        raise ProjectScaffoldingError(f"{label} must be an absolute path: {path}")
    return path


def _paths_overlap(path_a: Path, path_b: Path) -> bool:
    """Return True if two paths are equal or one is nested inside the other."""
    return path_a == path_b or path_a.is_relative_to(path_b) or path_b.is_relative_to(path_a)


def _assert_disjoint(
    target_raw: Path,
    target_resolved: Path,
    forbidden_roots: Sequence[tuple[str, Path]],
    *,
    target_label: str,
) -> None:
    for root_label, root_path in forbidden_roots:
        root_resolved = root_path.resolve()
        if _paths_overlap(target_raw, root_path) or _paths_overlap(target_resolved, root_resolved):
            raise ProjectScaffoldingError(
                f"{target_label} ({target_resolved}) overlaps {root_label} ({root_resolved})"
            )


def _check_no_yaml_milestone_grant(data: Any, *, label: str) -> None:
    """Reject any YAML keys or values attempting to grant milestone authority."""
    if isinstance(data, dict):
        for raw_key, value in data.items():
            key_norm = str(raw_key).strip().lower().replace("-", "_")
            if key_norm in _FORBIDDEN_YAML_AUTH_KEYS or key_norm.startswith("milestone_auth"):
                raise ProjectScaffoldingError(
                    f"{label} cannot declare milestone grant key {raw_key!r} in YAML; "
                    "external Controller-owned JSON record is required"
                )
            _check_no_yaml_milestone_grant(value, label=label)
    elif isinstance(data, list):
        for item in data:
            _check_no_yaml_milestone_grant(item, label=label)


def _load_safe_yaml_mapping(path: Path, *, label: str) -> tuple[str, dict[str, Any]]:
    _check_no_traversal_or_symlink(path, label=label)
    if not path.exists() or not path.is_file():
        raise ProjectScaffoldingError(f"{label} file does not exist: {path}")
    try:
        raw_text = path.read_text(encoding="utf-8")
        assert_no_secrets(raw_text)
        parsed = yaml.load(raw_text, Loader=_UniqueKeySafeLoader)
    except SecretExposureError as exc:
        raise ProjectScaffoldingError(f"{label} contains prohibited credential text") from exc
    except (OSError, UnicodeError, yaml.YAMLError, RecursionError) as exc:
        raise ProjectScaffoldingError(f"Cannot parse {label} YAML: {path}") from exc
    if not isinstance(parsed, dict):
        raise ProjectScaffoldingError(f"{label} root must be a YAML mapping: {path}")
    try:
        assert_no_secrets(parsed)
    except SecretExposureError as exc:
        raise ProjectScaffoldingError(f"{label} contains prohibited credential mapping") from exc
    _check_no_yaml_milestone_grant(parsed, label=label)
    return raw_text, parsed


def validate_project_template(path: Path | str) -> ProjectDefinition:
    """Validate a starter PROJECT.yaml against C01/ProjectDefinition and YAML grant rules."""
    template_path = Path(path)
    _, parsed = _load_safe_yaml_mapping(template_path, label="PROJECT.yaml")
    try:
        project_def = load_project(template_path)
    except ProjectDefinitionError as exc:
        raise ProjectScaffoldingError(f"Invalid PROJECT.yaml template: {exc}") from exc
    validate_project_identifier(project_def.project.id)
    for phase in project_def.phases:
        low_id = phase.id.lower()
        if any(token in low_id for token in _FORBIDDEN_MILESTONE_D_TOKENS):
            raise ProjectScaffoldingError(
                f"PROJECT.yaml phase references forbidden scope: {phase.id!r}"
            )
    if "milestone" in parsed:
        raise ProjectScaffoldingError("PROJECT.yaml cannot declare top-level milestone authority")
    return project_def


def validate_governance_template(path: Path | str) -> ReviewGovernancePolicy:
    """Validate a starter review_governance.yaml against v1.0 governance schema."""
    template_path = Path(path)
    _, parsed = _load_safe_yaml_mapping(template_path, label="review_governance.yaml")
    try:
        policy = ReviewGovernancePolicy.model_validate(parsed)
    except (ReviewGovernancePolicyError, SecretExposureError, ValidationError, ValueError) as exc:
        raise ProjectScaffoldingError(f"Invalid review_governance.yaml template: {exc}") from exc
    return policy


def build_project_runtime_layout(
    *,
    projects_root: Path,
    project_id: str,
    candidate_root: Path,
    stable_root: Path,
    candidate_workspace_root: Path | None = None,
    other_project_roots: Sequence[Path] = (),
) -> ProjectRuntimeLayout:
    """Validate disjoint paths and return deterministic per-project runtime layout."""
    validated_id = validate_project_identifier(project_id)
    clean_projects_root = _require_absolute_clean_path(projects_root, label="projects_root")
    clean_candidate_root = _require_absolute_clean_path(candidate_root, label="candidate_root")
    clean_stable_root = _require_absolute_clean_path(stable_root, label="stable_root")

    if clean_projects_root.exists() and clean_projects_root.is_symlink():
        raise ProjectScaffoldingError(f"projects_root cannot be a symlink: {clean_projects_root}")

    project_root = clean_projects_root / validated_id
    runtime_root = project_root / "runtime"
    state_dir = runtime_root / "state"
    audit_dir = runtime_root / "audit"
    worktrees_dir = runtime_root / "worktrees"
    receipt_dir = audit_dir / "receipts"

    for dir_label, dir_path in (
        ("project_root", project_root),
        ("runtime_root", runtime_root),
        ("state_dir", state_dir),
        ("audit_dir", audit_dir),
        ("worktrees_dir", worktrees_dir),
        ("receipt_dir", receipt_dir),
    ):
        _check_no_traversal_or_symlink(dir_path, label=dir_label)

    resolved_project_root = project_root.resolve()
    resolved_runtime_root = runtime_root.resolve()

    forbidden_for_project: list[tuple[str, Path]] = [
        ("candidate_root", clean_candidate_root),
        ("stable_root", clean_stable_root),
    ]
    for idx, raw_other in enumerate(other_project_roots):
        clean_other = _require_absolute_clean_path(raw_other, label=f"other_project_roots[{idx}]")
        forbidden_for_project.append((f"other_project_roots[{idx}]", clean_other))

    if clean_projects_root.exists() and clean_projects_root.is_dir():
        for sibling in clean_projects_root.iterdir():
            if sibling.name == validated_id:
                continue
            forbidden_for_project.append((f"sibling_project[{sibling.name}]", sibling))

    _assert_disjoint(
        project_root,
        resolved_project_root,
        forbidden_for_project,
        target_label="project_root",
    )
    _assert_disjoint(
        runtime_root,
        resolved_runtime_root,
        forbidden_for_project,
        target_label="runtime_root",
    )

    if candidate_workspace_root is not None:
        clean_workspace = _require_absolute_clean_path(
            candidate_workspace_root, label="candidate_workspace_root"
        )
        resolved_workspace = clean_workspace.resolve()
        _assert_disjoint(
            clean_workspace,
            resolved_workspace,
            [
                ("runtime_root", runtime_root),
                ("stable_root", clean_stable_root),
                ("candidate_root", clean_candidate_root),
                *[
                    (name, path)
                    for name, path in forbidden_for_project
                    if name.startswith(("other_project_roots", "sibling_project"))
                ],
            ],
            target_label="candidate_workspace_root",
        )

    return ProjectRuntimeLayout(
        project_id=validated_id,
        project_root=resolved_project_root,
        runtime_root=resolved_runtime_root,
        state_dir=state_dir.resolve(),
        audit_dir=audit_dir.resolve(),
        worktrees_dir=worktrees_dir.resolve(),
        project_state_path=(state_dir / "project_state.json").resolve(),
        queue_path=(state_dir / "canonical_queue.json").resolve(),
        budget_ledger_path=(state_dir / "repair_budget_ledger.json").resolve(),
        receipt_dir=receipt_dir.resolve(),
        lock_path=(state_dir / "controller.lock").resolve(),
        scaffold_manifest_path=(audit_dir / "scaffold_manifest.json").resolve(),
    )


def resolve_project_runtime_path(
    layout: ProjectRuntimeLayout,
    target: Path | str,
    *,
    candidate_root: Path,
    stable_root: Path,
    other_project_roots: Sequence[Path] = (),
) -> Path:
    """Resolve a path strictly inside ``layout.runtime_root``, rejecting crossover/traversal."""
    raw_target = Path(target)
    _check_no_traversal_or_symlink(raw_target, label="runtime_target")

    joined = raw_target if raw_target.is_absolute() else layout.runtime_root / raw_target
    _check_no_traversal_or_symlink(joined, label="runtime_joined")

    # Reject any intermediate symlink between runtime_root and joined.
    current = joined
    while current != layout.runtime_root and current != current.parent:
        if current.is_symlink():
            raise ProjectScaffoldingError(f"Symlink component forbidden inside runtime: {current}")
        current = current.parent

    resolved = joined.resolve()
    if not resolved.is_relative_to(layout.runtime_root):
        raise ProjectScaffoldingError(
            f"Path escapes project runtime_root ({layout.runtime_root}): {resolved}"
        )

    forbidden: list[tuple[str, Path]] = [
        ("candidate_root", _require_absolute_clean_path(candidate_root, label="candidate_root")),
        ("stable_root", _require_absolute_clean_path(stable_root, label="stable_root")),
    ]
    for idx, other in enumerate(other_project_roots):
        forbidden.append(
            (
                f"other_project_roots[{idx}]",
                _require_absolute_clean_path(other, label=f"other_project_roots[{idx}]"),
            )
        )
    _assert_disjoint(joined, resolved, forbidden, target_label="runtime_target")
    return resolved


def initialize_project_runtime(
    *,
    projects_root: Path,
    project_id: str,
    candidate_root: Path,
    stable_root: Path,
    candidate_workspace_root: Path | None = None,
    other_project_roots: Sequence[Path] = (),
) -> ProjectRuntimeLayout:
    """Idempotently initialize external ``projects/<project_id>/runtime/{state,audit,worktrees}``.

    Preserves all existing state, queue, budget-ledger, receipt, lock, and unknown files.
    """
    layout = build_project_runtime_layout(
        projects_root=projects_root,
        project_id=project_id,
        candidate_root=candidate_root,
        stable_root=stable_root,
        candidate_workspace_root=candidate_workspace_root,
        other_project_roots=other_project_roots,
    )

    for directory in (
        layout.project_root,
        layout.runtime_root,
        layout.state_dir,
        layout.audit_dir,
        layout.worktrees_dir,
        layout.receipt_dir,
    ):
        if directory.exists() and not directory.is_dir():
            raise ProjectScaffoldingError(f"Expected directory but found file: {directory}")
        directory.mkdir(parents=True, exist_ok=True)

    manifest_path = layout.scaffold_manifest_path
    if manifest_path.exists():
        if manifest_path.is_symlink() or not manifest_path.is_file():
            raise ProjectScaffoldingError(f"Invalid existing scaffold manifest: {manifest_path}")
        existing = load_json(manifest_path)
        if not isinstance(existing, dict) or existing.get("project_id") != layout.project_id:
            raise ProjectScaffoldingError(
                f"Existing scaffold manifest project_id mismatch at {manifest_path}"
            )
    else:
        manifest_payload: dict[str, Any] = {
            "schema_version": "1.0",
            "project_id": layout.project_id,
            "runtime_root": str(layout.runtime_root),
            "state_dir": str(layout.state_dir),
            "audit_dir": str(layout.audit_dir),
            "worktrees_dir": str(layout.worktrees_dir),
        }
        assert_no_secrets(manifest_payload)
        write_json_atomic(manifest_path, manifest_payload)

    return layout


def _build_cli_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m aipc.project.scaffolding",
        description="Validate starter templates or initialize isolated per-project runtime dirs.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    val = sub.add_parser("validate-templates", help="Validate PROJECT.yaml and governance YAML.")
    val.add_argument("--project", type=Path, required=True, help="Path to PROJECT.yaml")
    val.add_argument(
        "--governance", type=Path, required=True, help="Path to review_governance.yaml"
    )

    init = sub.add_parser("init", help="Idempotently initialize external project runtime dirs.")
    init.add_argument("--projects-root", type=Path, required=True)
    init.add_argument("--project-id", type=str, required=True)
    init.add_argument("--candidate-root", type=Path, required=True)
    init.add_argument("--stable-root", type=Path, required=True)
    init.add_argument("--candidate-workspace", type=Path, default=None)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Narrow CLI entrypoint for template validation and idempotent runtime init."""
    parser = _build_cli_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.command == "validate-templates":
        project_def = validate_project_template(args.project)
        policy = validate_governance_template(args.governance)
        print(
            json.dumps(
                {
                    "status": "VALID",
                    "project_id": project_def.project.id,
                    "policy_id": policy.policy_id,
                }
            )
        )
        return 0
    if args.command == "init":
        layout = initialize_project_runtime(
            projects_root=args.projects_root,
            project_id=args.project_id,
            candidate_root=args.candidate_root,
            stable_root=args.stable_root,
            candidate_workspace_root=args.candidate_workspace,
        )
        print(json.dumps(layout.model_dump(mode="json")))
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
