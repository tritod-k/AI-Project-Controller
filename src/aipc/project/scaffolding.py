"""Minimal per-project runtime scaffolding, path isolation, and template validation.

Provides deterministic, idempotent initialization of external per-project runtime
directories (``projects/<project_id>/runtime/{state,audit,worktrees}``) and
fail-closed validation for starter ``PROJECT.yaml`` and ``review_governance.yaml``
templates. YAML templates can never authorize a milestone; external Controller-owned
JSON records remain mandatory.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import sys
import tempfile
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
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
from aipc.storage.json_store import load_json
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


class ApprovedPlanSnapshot(CanonicalRecord):
    """Immutable approved plan snapshot metadata."""

    plan_version: str
    plan_dir: Path
    master_plan_path: Path
    manifest_path: Path
    manifest_sha256_path: Path
    master_plan_sha256: str
    manifest_sha256: str


_PLAN_VERSION_RE = re.compile(r"^plan-v[0-9]{4,}$")


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


def _assert_not_symlink_lstat(path: Path, *, label: str) -> os.stat_result | None:
    """Inspect path via lstat without following symlinks; reject symlinks and errors."""
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return None
    except NotADirectoryError as exc:
        raise ProjectScaffoldingError(f"Non-directory ancestor in {label}: {path}") from exc
    except OSError as exc:
        raise ProjectScaffoldingError(f"Cannot lstat {label} ({path}): {exc}") from exc
    if stat.S_ISLNK(st.st_mode):
        raise ProjectScaffoldingError(f"Symlink path is forbidden for {label}: {path}")
    return st


def _check_no_traversal_or_symlink(path: Path, *, label: str) -> None:
    if "\x00" in str(path):
        raise ProjectScaffoldingError(f"Null byte in {label} path")
    if ".." in path.parts:
        raise ProjectScaffoldingError(f"Parent traversal '..' is forbidden in {label}: {path}")
    _assert_not_symlink_lstat(path, label=label)


def _assert_no_ancestor_symlinks(path: Path, *, label: str) -> None:
    """Inspect all lexical path components via lstat without following symlinks."""
    if not path.parts:
        return
    current = Path(path.parts[0])
    for part in path.parts[1:]:
        current = current / part
        _assert_not_symlink_lstat(current, label=label)


def _assert_lexical_chain_and_containment(
    root: Path,
    target: Path,
    *,
    label: str,
    expect_dir: bool | None = None,
) -> Path:
    """Verify no symlinks in root..target via lstat and verify lexical+resolved containment."""
    _check_no_traversal_or_symlink(root, label=f"{label}_root")
    if root.is_absolute():
        _assert_no_ancestor_symlinks(root, label=f"{label}_root")
    _check_no_traversal_or_symlink(target, label=label)
    if target != root and not target.is_relative_to(root):
        raise ProjectScaffoldingError(f"{label} ({target}) is lexically outside {root}")

    _assert_not_symlink_lstat(root, label=f"{label}_root")
    rel = target.relative_to(root)
    current = root
    for part in rel.parts:
        current = current / part
        _assert_not_symlink_lstat(current, label=label)

    st = _assert_not_symlink_lstat(target, label=label)
    if st is not None and expect_dir is not None:
        if expect_dir and not stat.S_ISDIR(st.st_mode):
            raise ProjectScaffoldingError(f"Expected directory for {label}: {target}")
        if not expect_dir and not stat.S_ISREG(st.st_mode):
            raise ProjectScaffoldingError(f"Expected regular file for {label}: {target}")

    resolved_root = root.resolve()
    resolved_target = target.resolve()
    if resolved_target != resolved_root and not resolved_target.is_relative_to(resolved_root):
        raise ProjectScaffoldingError(f"{label} escapes {root} after resolution: {resolved_target}")
    return resolved_target


def _require_absolute_clean_path(path: Path, *, label: str) -> Path:
    if not isinstance(path, Path):
        raise ProjectScaffoldingError(f"{label} must be a Path instance")
    _check_no_traversal_or_symlink(path, label=label)
    if not path.is_absolute():
        raise ProjectScaffoldingError(f"{label} must be an absolute path: {path}")
    _assert_no_ancestor_symlinks(path, label=label)
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


def _validate_template_file_path(path: Path, *, label: str) -> Path:
    """Validate template path lexically (no traversal, no ancestor/leaf symlinks, regular file)."""
    _check_no_traversal_or_symlink(path, label=label)
    lexical_path = path if path.is_absolute() else Path.cwd() / path
    _check_no_traversal_or_symlink(lexical_path, label=label)
    _assert_no_ancestor_symlinks(lexical_path, label=label)
    st = _assert_not_symlink_lstat(lexical_path, label=label)
    if st is None or not stat.S_ISREG(st.st_mode):
        raise ProjectScaffoldingError(f"{label} file does not exist: {path}")
    return lexical_path


def _load_safe_yaml_mapping(path: Path, *, label: str) -> tuple[str, dict[str, Any]]:
    _validate_template_file_path(path, label=label)
    try:
        _validate_template_file_path(path, label=label)
        raw_text = path.read_text(encoding="utf-8")
        assert_no_secrets(raw_text)
        parsed = yaml.load(raw_text, Loader=_UniqueKeySafeLoader)
    except ProjectScaffoldingError:
        raise
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
    _validate_template_file_path(template_path, label="PROJECT.yaml")
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

    _assert_not_symlink_lstat(clean_projects_root, label="projects_root")
    base_projects_root = clean_projects_root.resolve()

    project_root = base_projects_root / validated_id
    runtime_root = project_root / "runtime"
    state_dir = runtime_root / "state"
    audit_dir = runtime_root / "audit"
    worktrees_dir = runtime_root / "worktrees"
    receipt_dir = audit_dir / "receipts"
    project_state_path = state_dir / "project_state.json"
    queue_path = state_dir / "canonical_queue.json"
    budget_ledger_path = state_dir / "repair_budget_ledger.json"
    lock_path = state_dir / "controller.lock"
    scaffold_manifest_path = audit_dir / "scaffold_manifest.json"

    for dir_label, dir_path in (
        ("project_root", project_root),
        ("runtime_root", runtime_root),
        ("state_dir", state_dir),
        ("audit_dir", audit_dir),
        ("worktrees_dir", worktrees_dir),
        ("receipt_dir", receipt_dir),
    ):
        _assert_lexical_chain_and_containment(
            base_projects_root, dir_path, label=dir_label, expect_dir=True
        )

    for file_label, file_path in (
        ("project_state_path", project_state_path),
        ("queue_path", queue_path),
        ("budget_ledger_path", budget_ledger_path),
        ("lock_path", lock_path),
        ("scaffold_manifest_path", scaffold_manifest_path),
    ):
        _assert_lexical_chain_and_containment(
            runtime_root, file_path, label=file_label, expect_dir=False
        )

    resolved_project_root = project_root.resolve()
    resolved_runtime_root = runtime_root.resolve()
    if resolved_project_root != base_projects_root / validated_id:
        raise ProjectScaffoldingError(
            f"project_root resolved outside expected location: {resolved_project_root}"
        )
    if resolved_runtime_root != resolved_project_root / "runtime":
        raise ProjectScaffoldingError(
            f"runtime_root resolved outside expected location: {resolved_runtime_root}"
        )

    forbidden_for_project: list[tuple[str, Path]] = [
        ("candidate_root", clean_candidate_root),
        ("stable_root", clean_stable_root),
    ]
    for idx, raw_other in enumerate(other_project_roots):
        clean_other = _require_absolute_clean_path(raw_other, label=f"other_project_roots[{idx}]")
        forbidden_for_project.append((f"other_project_roots[{idx}]", clean_other))

    if base_projects_root.exists() and base_projects_root.is_dir():
        for sibling in base_projects_root.iterdir():
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
        project_root=project_root,
        runtime_root=runtime_root,
        state_dir=state_dir,
        audit_dir=audit_dir,
        worktrees_dir=worktrees_dir,
        project_state_path=project_state_path,
        queue_path=queue_path,
        budget_ledger_path=budget_ledger_path,
        receipt_dir=receipt_dir,
        lock_path=lock_path,
        scaffold_manifest_path=scaffold_manifest_path,
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

    _assert_lexical_chain_and_containment(
        layout.project_root, layout.runtime_root, label="runtime_root", expect_dir=True
    )
    joined = raw_target if raw_target.is_absolute() else layout.runtime_root / raw_target
    resolved = _assert_lexical_chain_and_containment(
        layout.runtime_root, joined, label="runtime_target"
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


def _write_json_atomic_guarded(
    root: Path,
    target: Path,
    data: dict[str, Any],
    *,
    label: str,
) -> None:
    """Atomically write JSON after revalidating lexical chain and containment."""
    _assert_lexical_chain_and_containment(
        root, target.parent, label=f"{label}_dir", expect_dir=True
    )
    _assert_lexical_chain_and_containment(root, target, label=label, expect_dir=False)

    serialized = json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    temporary: Path | None = None
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        _assert_lexical_chain_and_containment(
            root, target.parent, label=f"{label}_dir", expect_dir=True
        )
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(serialized)
            stream.flush()
            os.fsync(stream.fileno())
        _assert_lexical_chain_and_containment(root, target, label=label, expect_dir=False)
        _assert_not_symlink_lstat(temporary, label=f"{label}_tmp")
        os.replace(temporary, target)
        temporary = None
    except ProjectScaffoldingError:
        raise
    except (OSError, ValueError, TypeError) as exc:
        raise ProjectScaffoldingError(f"Cannot atomically save {label}: {target}") from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


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
        _assert_lexical_chain_and_containment(
            layout.project_root, directory, label="runtime_dir", expect_dir=True
        )
        directory.mkdir(parents=True, exist_ok=True)
        _assert_lexical_chain_and_containment(
            layout.project_root, directory, label="runtime_dir", expect_dir=True
        )

    manifest_path = layout.scaffold_manifest_path
    _assert_lexical_chain_and_containment(
        layout.runtime_root, manifest_path, label="scaffold_manifest", expect_dir=False
    )
    manifest_st = _assert_not_symlink_lstat(manifest_path, label="scaffold_manifest")
    if manifest_st is not None:
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
        _write_json_atomic_guarded(
            layout.runtime_root,
            manifest_path,
            manifest_payload,
            label="scaffold_manifest",
        )

    v12_dirs = (
        layout.runtime_root / "plans",
        layout.runtime_root / "plans" / "proposals",
        layout.runtime_root / "plans" / "approved",
        layout.runtime_root / "trusted",
        layout.audit_dir / "approvals",
        layout.runtime_root / "logs",
    )
    for directory in v12_dirs:
        _assert_lexical_chain_and_containment(
            layout.project_root, directory, label="runtime_dir", expect_dir=True
        )
        directory.mkdir(parents=True, exist_ok=True)
        _assert_lexical_chain_and_containment(
            layout.project_root, directory, label="runtime_dir", expect_dir=True
        )

    return layout


def _write_text_atomic_guarded(
    root: Path,
    target: Path,
    text: str,
    *,
    label: str,
) -> bytes:
    """Atomically write LF-normalized UTF-8 text after revalidating containment."""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    _assert_lexical_chain_and_containment(
        root, target.parent, label=f"{label}_dir", expect_dir=True
    )
    _assert_lexical_chain_and_containment(root, target, label=label, expect_dir=False)

    temporary: Path | None = None
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        _assert_lexical_chain_and_containment(
            root, target.parent, label=f"{label}_dir", expect_dir=True
        )
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(normalized)
            stream.flush()
            os.fsync(stream.fileno())
        _assert_lexical_chain_and_containment(root, target, label=label, expect_dir=False)
        _assert_not_symlink_lstat(temporary, label=f"{label}_tmp")
        os.replace(temporary, target)
        temporary = None
    except ProjectScaffoldingError:
        raise
    except OSError as exc:
        raise ProjectScaffoldingError(f"Cannot atomically save {label}: {target}") from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return normalized.encode("utf-8")


def _validate_nonempty_token(value: str, *, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise ProjectScaffoldingError(f"{label} must be a non-empty string")
    return value.strip()


def _publish_dir_noreplace(src: Path, dst: Path) -> None:
    """Atomically publish directory ``src`` to ``dst`` without replacing an existing ``dst``."""
    if sys.platform.startswith("linux"):
        import ctypes
        import ctypes.util
        import errno

        at_fdcwd = -100
        rename_noreplace = 1
        libc_name = ctypes.util.find_library("c") or "libc.so.6"
        libc = ctypes.CDLL(libc_name, use_errno=True)
        renameat2 = getattr(libc, "renameat2", None)
        if renameat2 is None:
            libc = ctypes.CDLL(None, use_errno=True)
            renameat2 = libc.renameat2
        renameat2.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        renameat2.restype = ctypes.c_int
        rc = renameat2(at_fdcwd, os.fsencode(src), at_fdcwd, os.fsencode(dst), rename_noreplace)
        if rc != 0:
            err = ctypes.get_errno()
            if err == errno.ENOTEMPTY:
                err = errno.EEXIST
            raise OSError(err, os.strerror(err), str(src), None, str(dst))
        return
    os.rename(src, dst)


def snapshot_approved_plan(
    runtime_root: Path | str,
    *,
    plan_version: str,
    source_plan_path: Path | str,
    approved_by: str,
    project_id: str,
    milestone_id: str,
    change_impact_path: Path | str | None = None,
) -> ApprovedPlanSnapshot:
    """Write an immutable approved plan snapshot under ``plans/approved/<plan_version>/``."""
    if (
        not isinstance(plan_version, str)
        or "/" in plan_version
        or "\\" in plan_version
        or ".." in plan_version
        or "\x00" in plan_version
        or _PLAN_VERSION_RE.fullmatch(plan_version) is None
    ):
        raise ProjectScaffoldingError(f"Invalid plan_version: {plan_version!r}")

    clean_approved_by = _validate_nonempty_token(approved_by, label="approved_by")
    clean_project_id = _validate_nonempty_token(project_id, label="project_id")
    clean_milestone_id = _validate_nonempty_token(milestone_id, label="milestone_id")

    raw_rt = Path(runtime_root)
    _check_no_traversal_or_symlink(raw_rt, label="runtime_root")
    lexical_rt = raw_rt if raw_rt.is_absolute() else Path.cwd() / raw_rt
    _check_no_traversal_or_symlink(lexical_rt, label="runtime_root")
    _assert_no_ancestor_symlinks(lexical_rt, label="runtime_root")
    rt_st = _assert_not_symlink_lstat(lexical_rt, label="runtime_root")
    if rt_st is None or not stat.S_ISDIR(rt_st.st_mode):
        raise ProjectScaffoldingError(f"runtime_root must be an existing directory: {lexical_rt}")

    source_file = _validate_template_file_path(Path(source_plan_path), label="source_plan_path")
    try:
        plan_text = source_file.read_text(encoding="utf-8")
        assert_no_secrets(plan_text)
    except SecretExposureError as exc:
        raise ProjectScaffoldingError("source_plan_path contains prohibited secret text") from exc
    except (OSError, UnicodeError) as exc:
        raise ProjectScaffoldingError(f"Cannot read source_plan_path: {source_file}") from exc

    change_impact_sha256: str | None = None
    change_impact_source: str | None = None
    if change_impact_path is not None:
        impact_file = _validate_template_file_path(
            Path(change_impact_path), label="change_impact_path"
        )
        try:
            impact_text = impact_file.read_text(encoding="utf-8")
            assert_no_secrets(impact_text)
        except SecretExposureError as exc:
            raise ProjectScaffoldingError(
                "change_impact_path contains prohibited secret text"
            ) from exc
        except (OSError, UnicodeError) as exc:
            raise ProjectScaffoldingError(f"Cannot read change_impact_path: {impact_file}") from exc
        impact_bytes = impact_text.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")
        change_impact_sha256 = hashlib.sha256(impact_bytes).hexdigest()
        change_impact_source = impact_file.name

    plans_dir = lexical_rt / "plans"
    approved_root = plans_dir / "approved"
    for d in (plans_dir, approved_root):
        _assert_lexical_chain_and_containment(lexical_rt, d, label="plans_dir", expect_dir=True)
        d.mkdir(parents=True, exist_ok=True)
        _assert_lexical_chain_and_containment(lexical_rt, d, label="plans_dir", expect_dir=True)

    target_dir = approved_root / plan_version
    _assert_lexical_chain_and_containment(
        approved_root, target_dir, label="plan_version_dir", expect_dir=True
    )
    if _assert_not_symlink_lstat(target_dir, label="plan_version_dir") is not None:
        raise ProjectScaffoldingError(
            f"Approved plan version already exists (immutable): {plan_version}"
        )

    staging_dir = Path(tempfile.mkdtemp(prefix=f".tmp-{plan_version}-", dir=str(approved_root)))
    published = False
    try:
        staging_master_plan = staging_dir / "MASTER_PLAN.md"
        master_plan_bytes = _write_text_atomic_guarded(
            approved_root, staging_master_plan, plan_text, label="MASTER_PLAN.md"
        )
        master_plan_sha256 = hashlib.sha256(master_plan_bytes).hexdigest()

        manifest_payload: dict[str, Any] = {
            "schema_version": "1.0",
            "project_id": clean_project_id,
            "milestone_id": clean_milestone_id,
            "plan_version": plan_version,
            "approved_by": clean_approved_by,
            "approved_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "master_plan_filename": "MASTER_PLAN.md",
            "master_plan_sha256": master_plan_sha256,
            "change_impact_source": change_impact_source,
            "change_impact_sha256": change_impact_sha256,
        }
        try:
            assert_no_secrets(manifest_payload)
        except SecretExposureError as exc:
            raise ProjectScaffoldingError("plan_manifest contains prohibited secret text") from exc

        manifest_text = json.dumps(manifest_payload, indent=2, ensure_ascii=False, allow_nan=False)
        manifest_text += "\n"
        staging_manifest = staging_dir / "plan_manifest.json"
        manifest_bytes = _write_text_atomic_guarded(
            approved_root, staging_manifest, manifest_text, label="plan_manifest.json"
        )
        manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()

        staging_manifest_sha256 = staging_dir / "plan_manifest.json.sha256"
        _write_text_atomic_guarded(
            approved_root,
            staging_manifest_sha256,
            f"{manifest_sha256}  plan_manifest.json\n",
            label="plan_manifest.json.sha256",
        )

        if _assert_not_symlink_lstat(target_dir, label="plan_version_dir") is not None:
            raise ProjectScaffoldingError(
                f"Approved plan version already exists (immutable): {plan_version}"
            )
        try:
            _publish_dir_noreplace(staging_dir, target_dir)
            published = True
        except FileExistsError as exc:
            raise ProjectScaffoldingError(
                f"Approved plan version already exists (immutable): {plan_version}"
            ) from exc
        except OSError as exc:
            if _assert_not_symlink_lstat(target_dir, label="plan_version_dir") is not None:
                raise ProjectScaffoldingError(
                    f"Approved plan version already exists (immutable): {plan_version}"
                ) from exc
            raise ProjectScaffoldingError(f"Cannot publish plan directory: {target_dir}") from exc
    finally:
        if not published:
            shutil.rmtree(staging_dir, ignore_errors=True)

    return ApprovedPlanSnapshot(
        plan_version=plan_version,
        plan_dir=target_dir,
        master_plan_path=target_dir / "MASTER_PLAN.md",
        manifest_path=target_dir / "plan_manifest.json",
        manifest_sha256_path=target_dir / "plan_manifest.json.sha256",
        master_plan_sha256=master_plan_sha256,
        manifest_sha256=manifest_sha256,
    )


def build_short_worktree_name(task_id: str, attempt_id: str | int) -> str:
    """Return deterministic short worktree directory name ``wt-<8-hex>`` (<=12 chars)."""
    clean_task = _validate_nonempty_token(str(task_id), label="task_id")
    clean_attempt = _validate_nonempty_token(str(attempt_id), label="attempt_id")
    digest = hashlib.sha256(f"{clean_task}\x00{clean_attempt}".encode()).hexdigest()[:8]
    return f"wt-{digest}"


def _remove_tree_no_symlinks(root: Path, target: Path) -> None:
    """Recursively remove a stale directory tree without following symlinks."""
    _assert_lexical_chain_and_containment(root, target, label="stale_worktree")
    st = _assert_not_symlink_lstat(target, label="stale_worktree")
    if st is None:
        return
    if stat.S_ISDIR(st.st_mode):
        for child in sorted(target.iterdir(), key=lambda p: p.name):
            _remove_tree_no_symlinks(root, child)
        target.rmdir()
    elif stat.S_ISREG(st.st_mode):
        target.unlink()
    else:
        raise ProjectScaffoldingError(f"Unsupported file type in stale worktree: {target}")


def cleanup_stale_worktrees(
    worktrees_dir: Path | str,
    *,
    active_names: Iterable[str] = (),
) -> tuple[str, ...]:
    """Remove stale entries under ``worktrees_dir`` while preserving ``active_names``."""
    raw_wt = Path(worktrees_dir)
    _check_no_traversal_or_symlink(raw_wt, label="worktrees_dir")
    lexical_wt = raw_wt if raw_wt.is_absolute() else Path.cwd() / raw_wt
    _check_no_traversal_or_symlink(lexical_wt, label="worktrees_dir")
    _assert_no_ancestor_symlinks(lexical_wt, label="worktrees_dir")

    wt_st = _assert_not_symlink_lstat(lexical_wt, label="worktrees_dir")
    if wt_st is None:
        return ()
    if not stat.S_ISDIR(wt_st.st_mode):
        raise ProjectScaffoldingError(f"worktrees_dir is not a directory: {lexical_wt}")

    active_set: set[str] = set()
    for name in active_names:
        if (
            not isinstance(name, str)
            or not name
            or "/" in name
            or "\\" in name
            or ".." in name
            or "\x00" in name
        ):
            raise ProjectScaffoldingError(f"Invalid active worktree name: {name!r}")
        active_set.add(name)

    removed: list[str] = []
    for entry in sorted(lexical_wt.iterdir(), key=lambda p: p.name):
        _assert_not_symlink_lstat(entry, label="worktree_entry")
        _assert_lexical_chain_and_containment(lexical_wt, entry, label="worktree_entry")
        if entry.name in active_set:
            continue
        _remove_tree_no_symlinks(lexical_wt, entry)
        removed.append(entry.name)
    return tuple(removed)


def _extract_queue_tasks(queue_data: Any) -> list[dict[str, Any]]:
    if isinstance(queue_data, dict):
        raw_tasks = queue_data.get("tasks")
        if raw_tasks is None:
            raw_tasks = queue_data.get("items", [])
        if isinstance(raw_tasks, dict):
            return [v for v in raw_tasks.values() if isinstance(v, dict)]
        if isinstance(raw_tasks, list):
            return [v for v in raw_tasks if isinstance(v, dict)]
    elif isinstance(queue_data, list):
        return [v for v in queue_data if isinstance(v, dict)]
    return []


def _list_safe_json_files(directory: Path, *, label: str) -> list[Path]:
    _check_no_traversal_or_symlink(directory, label=label)
    lexical_dir = directory if directory.is_absolute() else Path.cwd() / directory
    _assert_no_ancestor_symlinks(lexical_dir, label=label)
    st = _assert_not_symlink_lstat(lexical_dir, label=label)
    if st is None:
        return []
    if not stat.S_ISDIR(st.st_mode):
        raise ProjectScaffoldingError(f"{label} is not a directory: {lexical_dir}")
    files: list[Path] = []
    for entry in sorted(lexical_dir.iterdir(), key=lambda p: p.name):
        entry_st = _assert_not_symlink_lstat(entry, label=f"{label}_entry")
        if entry_st is not None and stat.S_ISREG(entry_st.st_mode) and entry.suffix == ".json":
            files.append(entry)
    return files


def render_current_status_md(
    project_root: Path | str,
    *,
    queue_path: Path | str,
    budget_ledger_path: Path | str | None = None,
    project_state_path: Path | str | None = None,
    approvals_dir: Path | str | None = None,
    receipts_dir: Path | str | None = None,
) -> Path:
    """Render deterministic LF ``CURRENT_STATUS.md`` from read-only Controller JSON stores."""
    raw_root = Path(project_root)
    _check_no_traversal_or_symlink(raw_root, label="project_root")
    lexical_root = raw_root if raw_root.is_absolute() else Path.cwd() / raw_root
    _check_no_traversal_or_symlink(lexical_root, label="project_root")
    _assert_no_ancestor_symlinks(lexical_root, label="project_root")
    root_st = _assert_not_symlink_lstat(lexical_root, label="project_root")
    if root_st is None or not stat.S_ISDIR(root_st.st_mode):
        raise ProjectScaffoldingError(f"project_root must be an existing directory: {lexical_root}")

    validated_queue = _validate_template_file_path(Path(queue_path), label="queue_path")
    authoritative_inputs: list[Path] = [validated_queue]
    queue_data = load_json(validated_queue)
    tasks = _extract_queue_tasks(queue_data)

    status_counts: dict[str, int] = {}
    task_rows: list[tuple[str, str, str]] = []
    for idx, task in enumerate(tasks):
        contract = task.get("contract") if isinstance(task.get("contract"), dict) else {}
        tid = str(contract.get("task_id") or task.get("task_id") or task.get("id") or f"task-{idx}")
        status = str(task.get("status") or "UNKNOWN")
        phase = str(
            contract.get("phase_id")
            or contract.get("milestone_id")
            or task.get("phase_id")
            or task.get("milestone_id")
            or "-"
        )
        status_counts[status] = status_counts.get(status, 0) + 1
        task_rows.append((tid, status, phase))
    task_rows.sort(key=lambda row: (row[0], row[1], row[2]))

    lines: list[str] = [
        "# Current Project Status",
        "",
        "> **DERIVED / NON-AUTHORITATIVE**: This file is a read-only derived view from "
        "Controller JSON state. Do not edit manually or treat as authoritative.",
        "",
    ]

    if project_state_path is not None:
        state_file = _validate_template_file_path(
            Path(project_state_path), label="project_state_path"
        )
        authoritative_inputs.append(state_file)
        state_data = load_json(state_file)
        if isinstance(state_data, dict):
            lines.extend(["## Project State", ""])
            for key in sorted(state_data):
                val = state_data[key]
                if isinstance(val, str | int | float | bool) or val is None:
                    lines.append(f"- `{key}`: `{val}`")
            lines.append("")

    lines.extend(
        [
            "## Queue Summary",
            "",
            f"- Total tasks: `{len(tasks)}`",
        ]
    )
    for status_name in sorted(status_counts):
        lines.append(f"- `{status_name}`: `{status_counts[status_name]}`")
    if task_rows:
        lines.extend(["", "| Task ID | Status | Phase |", "| --- | --- | --- |"])
        for tid, status, phase in task_rows:
            lines.append(f"| `{tid}` | `{status}` | `{phase}` |")
    lines.append("")

    if budget_ledger_path is not None:
        ledger_file = _validate_template_file_path(
            Path(budget_ledger_path), label="budget_ledger_path"
        )
        authoritative_inputs.append(ledger_file)
        ledger_data = load_json(ledger_file)
        if isinstance(ledger_data, dict):
            lines.extend(["## Repair Budget", ""])
            for key in sorted(ledger_data):
                val = ledger_data[key]
                if isinstance(val, str | int | float | bool) or val is None:
                    lines.append(f"- `{key}`: `{val}`")
            lines.append("")

    if approvals_dir is not None:
        approval_files = _list_safe_json_files(Path(approvals_dir), label="approvals_dir")
        authoritative_inputs.extend(approval_files)
        lines.extend(
            [
                "## Approvals",
                "",
                f"- Approval records: `{len(approval_files)}`",
            ]
        )
        for af in approval_files:
            lines.append(f"- `{af.name}`")
        lines.append("")

    if receipts_dir is not None:
        receipt_files = _list_safe_json_files(Path(receipts_dir), label="receipts_dir")
        authoritative_inputs.extend(receipt_files)
        lines.extend(
            [
                "## Receipts",
                "",
                f"- Receipt records: `{len(receipt_files)}`",
            ]
        )
        for rf in receipt_files:
            lines.append(f"- `{rf.name}`")
        lines.append("")

    rendered = "\n".join(lines).rstrip("\n") + "\n"
    try:
        assert_no_secrets(rendered)
    except SecretExposureError as exc:
        raise ProjectScaffoldingError(
            "Rendered CURRENT_STATUS.md contains prohibited secret text"
        ) from exc

    target = lexical_root / "CURRENT_STATUS.md"
    resolved_target = target.resolve()
    for input_path in authoritative_inputs:
        if input_path.resolve() == resolved_target:
            raise ProjectScaffoldingError(
                f"Refusing to overwrite authoritative JSON input at {target}"
            )
    _write_text_atomic_guarded(lexical_root, target, rendered, label="CURRENT_STATUS.md")
    return target


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
