"""Unit tests for Milestone C.7 RCE03 per-project scaffolding and starter templates."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

import aipc.project.scaffolding as scaffolding_mod
from aipc.hardening.secrets import assert_no_secrets
from aipc.project.scaffolding import (
    ProjectScaffoldingError,
    build_project_runtime_layout,
    initialize_project_runtime,
    main,
    resolve_project_runtime_path,
    validate_governance_template,
    validate_project_template,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_starter_templates_validate_cleanly() -> None:
    project_yaml = REPO_ROOT / "templates" / "PROJECT.yaml"
    governance_yaml = REPO_ROOT / "templates" / "review_governance.yaml"

    project_def = validate_project_template(project_yaml)
    policy = validate_governance_template(governance_yaml)

    assert project_def.project.id == "sample-python-app"
    assert len(project_def.phases) >= 2
    assert policy.policy_id == "sample-project-governance-v1"
    assert policy.operating_profile == "subscription_only"
    assert policy.change_scope.no_milestone_d is True
    assert policy.change_scope.no_auto_merge is True


def test_project1_and_project2_isolated_runtime_paths_and_sentinels(tmp_path: Path) -> None:
    projects_root = tmp_path / "projects"
    candidate_root = tmp_path / "candidate_aipc"
    stable_root = tmp_path / "stable_aipc"
    ws1 = tmp_path / "workspaces" / "ws1"
    ws2 = tmp_path / "workspaces" / "ws2"
    for path in (projects_root, candidate_root, stable_root, ws1, ws2):
        path.mkdir(parents=True, exist_ok=True)

    layout1 = initialize_project_runtime(
        projects_root=projects_root,
        project_id="project1",
        candidate_root=candidate_root,
        stable_root=stable_root,
        candidate_workspace_root=ws1,
    )
    layout2 = initialize_project_runtime(
        projects_root=projects_root,
        project_id="project2",
        candidate_root=candidate_root,
        stable_root=stable_root,
        candidate_workspace_root=ws2,
        other_project_roots=(layout1.project_root,),
    )

    assert layout1.project_root != layout2.project_root
    assert layout1.state_dir != layout2.state_dir
    assert layout1.queue_path != layout2.queue_path
    assert layout1.budget_ledger_path != layout2.budget_ledger_path
    assert layout1.receipt_dir != layout2.receipt_dir
    assert layout1.lock_path != layout2.lock_path

    # Write distinct sentinels through resolve_project_runtime_path and verify zero crossover.
    sentinels = (
        ("state/project_state.json", '{"project_id": "project1", "sentinel": "s1"}\n'),
        ("state/canonical_queue.json", '{"project_id": "project1", "sentinel": "q1"}\n'),
        ("state/repair_budget_ledger.json", '{"project_id": "project1", "sentinel": "b1"}\n'),
        ("audit/receipts/r1.json", '{"project_id": "project1", "sentinel": "r1"}\n'),
        ("state/controller.lock", "lock-project1\n"),
    )
    for rel, content in sentinels:
        p1 = resolve_project_runtime_path(
            layout1,
            rel,
            candidate_root=candidate_root,
            stable_root=stable_root,
            other_project_roots=(layout2.project_root,),
        )
        p1.write_text(content, encoding="utf-8")

        p2 = resolve_project_runtime_path(
            layout2,
            rel,
            candidate_root=candidate_root,
            stable_root=stable_root,
            other_project_roots=(layout1.project_root,),
        )
        p2.write_text(content.replace("1", "2"), encoding="utf-8")

    assert '"sentinel": "q1"' in layout1.queue_path.read_text(encoding="utf-8")
    assert '"sentinel": "q2"' in layout2.queue_path.read_text(encoding="utf-8")
    assert '"sentinel": "b1"' in layout1.budget_ledger_path.read_text(encoding="utf-8")
    assert '"sentinel": "b2"' in layout2.budget_ledger_path.read_text(encoding="utf-8")
    assert layout1.lock_path.read_text(encoding="utf-8") == "lock-project1\n"
    assert layout2.lock_path.read_text(encoding="utf-8") == "lock-project2\n"

    # Cross-project read/write via helper must fail closed.
    with pytest.raises(ProjectScaffoldingError):
        resolve_project_runtime_path(
            layout1,
            layout2.queue_path,
            candidate_root=candidate_root,
            stable_root=stable_root,
            other_project_roots=(layout2.project_root,),
        )


def test_rejects_candidate_stable_workspace_overlap_and_traversal(tmp_path: Path) -> None:
    projects_root = tmp_path / "projects"
    candidate_root = tmp_path / "candidate_aipc"
    stable_root = tmp_path / "stable_aipc"
    for path in (projects_root, candidate_root, stable_root):
        path.mkdir(parents=True, exist_ok=True)

    # Candidate overlap
    with pytest.raises(ProjectScaffoldingError):
        initialize_project_runtime(
            projects_root=candidate_root / "nested",
            project_id="proj-a",
            candidate_root=candidate_root,
            stable_root=stable_root,
        )

    # Stable overlap
    with pytest.raises(ProjectScaffoldingError):
        initialize_project_runtime(
            projects_root=stable_root,
            project_id="proj-a",
            candidate_root=candidate_root,
            stable_root=stable_root,
        )

    # Candidate workspace overlapping project runtime
    with pytest.raises(ProjectScaffoldingError):
        build_project_runtime_layout(
            projects_root=projects_root,
            project_id="proj-a",
            candidate_root=candidate_root,
            stable_root=stable_root,
            candidate_workspace_root=projects_root / "proj-a" / "runtime",
        )

    # Candidate workspace overlapping Stable
    with pytest.raises(ProjectScaffoldingError):
        build_project_runtime_layout(
            projects_root=projects_root,
            project_id="proj-a",
            candidate_root=candidate_root,
            stable_root=stable_root,
            candidate_workspace_root=stable_root / "sub",
        )

    # Traversal in project_id and runtime target
    with pytest.raises(ProjectScaffoldingError):
        initialize_project_runtime(
            projects_root=projects_root,
            project_id="../escape",
            candidate_root=candidate_root,
            stable_root=stable_root,
        )

    with pytest.raises(ProjectScaffoldingError):
        initialize_project_runtime(
            projects_root=projects_root,
            project_id="Bad_Upper",
            candidate_root=candidate_root,
            stable_root=stable_root,
        )

    layout = initialize_project_runtime(
        projects_root=projects_root,
        project_id="proj-a",
        candidate_root=candidate_root,
        stable_root=stable_root,
    )
    with pytest.raises(ProjectScaffoldingError):
        resolve_project_runtime_path(
            layout,
            "state/../../other/file.json",
            candidate_root=candidate_root,
            stable_root=stable_root,
        )


def test_rejects_symlink_alias_paths(tmp_path: Path) -> None:
    projects_root = tmp_path / "projects"
    candidate_root = tmp_path / "candidate_aipc"
    stable_root = tmp_path / "stable_aipc"
    for path in (projects_root, candidate_root, stable_root):
        path.mkdir(parents=True, exist_ok=True)

    layout1 = initialize_project_runtime(
        projects_root=projects_root,
        project_id="project1",
        candidate_root=candidate_root,
        stable_root=stable_root,
    )
    alias_project = projects_root / "project2"
    _create_symlink_or_skip(alias_project, layout1.project_root, target_is_directory=True)

    with pytest.raises(ProjectScaffoldingError):
        initialize_project_runtime(
            projects_root=projects_root,
            project_id="project2",
            candidate_root=candidate_root,
            stable_root=stable_root,
        )

    escape_link = layout1.state_dir / "escape_dir"
    _create_symlink_or_skip(escape_link, candidate_root, target_is_directory=True)
    with pytest.raises(ProjectScaffoldingError):
        resolve_project_runtime_path(
            layout1,
            "state/escape_dir/leaked.json",
            candidate_root=candidate_root,
            stable_root=stable_root,
        )


def test_restart_is_idempotent_and_preserves_existing_state(tmp_path: Path) -> None:
    projects_root = tmp_path / "projects"
    candidate_root = tmp_path / "candidate_aipc"
    stable_root = tmp_path / "stable_aipc"
    for path in (projects_root, candidate_root, stable_root):
        path.mkdir(parents=True, exist_ok=True)

    layout = initialize_project_runtime(
        projects_root=projects_root,
        project_id="project1",
        candidate_root=candidate_root,
        stable_root=stable_root,
    )
    layout.project_state_path.write_text('{"status": "IN_PROGRESS"}\n', encoding="utf-8")
    layout.queue_path.write_text('{"queue_id": "keep-me"}\n', encoding="utf-8")
    layout.budget_ledger_path.write_text('{"consumed": 3}\n', encoding="utf-8")
    receipt_file = layout.receipt_dir / "existing_receipt.json"
    receipt_file.write_text('{"receipt": "preserved"}\n', encoding="utf-8")
    unknown_file = layout.state_dir / "custom_operator_note.txt"
    unknown_file.write_text("do-not-delete\n", encoding="utf-8")

    # Re-run initialization; all existing files and unknown files must remain untouched.
    layout_again = initialize_project_runtime(
        projects_root=projects_root,
        project_id="project1",
        candidate_root=candidate_root,
        stable_root=stable_root,
    )
    assert layout_again == layout
    assert layout.project_state_path.read_text(encoding="utf-8") == '{"status": "IN_PROGRESS"}\n'
    assert layout.queue_path.read_text(encoding="utf-8") == '{"queue_id": "keep-me"}\n'
    assert layout.budget_ledger_path.read_text(encoding="utf-8") == '{"consumed": 3}\n'
    assert receipt_file.read_text(encoding="utf-8") == '{"receipt": "preserved"}\n'
    assert unknown_file.read_text(encoding="utf-8") == "do-not-delete\n"


def test_rejects_malformed_templates_and_yaml_milestone_grant(tmp_path: Path) -> None:
    bad_project = tmp_path / "bad_project.yaml"
    bad_project.write_text("not: [valid: yaml\n", encoding="utf-8")
    with pytest.raises(ProjectScaffoldingError):
        validate_project_template(bad_project)

    yaml_grant_project = tmp_path / "grant_project.yaml"
    base_project_text = (REPO_ROOT / "templates" / "PROJECT.yaml").read_text(encoding="utf-8")
    yaml_grant_project.write_text(
        base_project_text + "\napproved_milestone:\n  approved: true\n",
        encoding="utf-8",
    )
    with pytest.raises(ProjectScaffoldingError):
        validate_project_template(yaml_grant_project)

    yaml_grant_gov = tmp_path / "grant_gov.yaml"
    base_gov_text = (REPO_ROOT / "templates" / "review_governance.yaml").read_text(encoding="utf-8")
    yaml_grant_gov.write_text(
        base_gov_text + "\ncommander_approval:\n  milestone: milestone-c7\n",
        encoding="utf-8",
    )
    with pytest.raises(ProjectScaffoldingError):
        validate_governance_template(yaml_grant_gov)


def test_module_cli_validate_templates_and_init(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc_val = main(
        [
            "validate-templates",
            "--project",
            str(REPO_ROOT / "templates" / "PROJECT.yaml"),
            "--governance",
            str(REPO_ROOT / "templates" / "review_governance.yaml"),
        ]
    )
    assert rc_val == 0
    val_out = json.loads(capsys.readouterr().out)
    assert val_out["status"] == "VALID"
    assert val_out["project_id"] == "sample-python-app"

    projects_root = tmp_path / "projects"
    candidate_root = tmp_path / "candidate"
    stable_root = tmp_path / "stable"
    for path in (projects_root, candidate_root, stable_root):
        path.mkdir(parents=True, exist_ok=True)

    rc_init = main(
        [
            "init",
            "--projects-root",
            str(projects_root),
            "--project-id",
            "proj-cli-01",
            "--candidate-root",
            str(candidate_root),
            "--stable-root",
            str(stable_root),
        ]
    )
    assert rc_init == 0
    init_out = json.loads(capsys.readouterr().out)
    assert init_out["project_id"] == "proj-cli-01"
    assert (projects_root / "proj-cli-01" / "runtime" / "state").is_dir()
    assert (projects_root / "proj-cli-01" / "runtime" / "audit").is_dir()
    assert (projects_root / "proj-cli-01" / "runtime" / "worktrees").is_dir()


def _create_symlink_or_skip(
    link_path: Path, target_path: Path, *, target_is_directory: bool = False
) -> None:
    try:
        link_path.symlink_to(target_path, target_is_directory=target_is_directory)
    except OSError as exc:
        if sys.platform == "win32" and getattr(exc, "winerror", None) == 1314:
            pytest.skip("Symlink creation requires WinError 1314 privilege on Windows")
        raise


def _make_roots(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    projects_root = tmp_path / "projects"
    candidate_root = tmp_path / "candidate_aipc"
    stable_root = tmp_path / "stable_aipc"
    external_dir = tmp_path / "external_target"
    for path in (projects_root, candidate_root, stable_root, external_dir):
        path.mkdir(parents=True, exist_ok=True)
    return projects_root, candidate_root, stable_root, external_dir


@pytest.mark.parametrize(
    ("filename", "sentinel_bytes"),
    [
        ("project_state.json", b'{"project_id": "project1", "sentinel": "ext-state"}\n'),
        ("canonical_queue.json", b'{"project_id": "project1", "sentinel": "ext-queue"}\n'),
        ("repair_budget_ledger.json", b'{"project_id": "project1", "sentinel": "ext-ledger"}\n'),
        ("controller.lock", b"external-lock-sentinel\n"),
    ],
)
def test_remediation01_rejects_preexisting_external_state_file_symlinks(
    tmp_path: Path, filename: str, sentinel_bytes: bytes
) -> None:
    projects_root, candidate_root, stable_root, external_dir = _make_roots(tmp_path)
    state_dir = projects_root / "project1" / "runtime" / "state"
    state_dir.mkdir(parents=True, exist_ok=True)

    external_target = external_dir / f"outside_{filename}"
    external_target.write_bytes(sentinel_bytes)
    _create_symlink_or_skip(state_dir / filename, external_target, target_is_directory=False)

    with pytest.raises(ProjectScaffoldingError):
        build_project_runtime_layout(
            projects_root=projects_root,
            project_id="project1",
            candidate_root=candidate_root,
            stable_root=stable_root,
        )

    with pytest.raises(ProjectScaffoldingError):
        initialize_project_runtime(
            projects_root=projects_root,
            project_id="project1",
            candidate_root=candidate_root,
            stable_root=stable_root,
        )

    assert external_target.read_bytes() == sentinel_bytes


def test_remediation01_rejects_external_manifest_symlink_with_matching_project_id(
    tmp_path: Path,
) -> None:
    projects_root, candidate_root, stable_root, external_dir = _make_roots(tmp_path)
    audit_dir = projects_root / "project1" / "runtime" / "audit"
    audit_dir.mkdir(parents=True, exist_ok=True)

    external_manifest = external_dir / "outside_manifest.json"
    sentinel_bytes = b'{"schema_version": "1.0", "project_id": "project1", "note": "outside"}\n'
    external_manifest.write_bytes(sentinel_bytes)
    _create_symlink_or_skip(
        audit_dir / "scaffold_manifest.json",
        external_manifest,
        target_is_directory=False,
    )

    with pytest.raises(ProjectScaffoldingError):
        build_project_runtime_layout(
            projects_root=projects_root,
            project_id="project1",
            candidate_root=candidate_root,
            stable_root=stable_root,
        )

    with pytest.raises(ProjectScaffoldingError):
        initialize_project_runtime(
            projects_root=projects_root,
            project_id="project1",
            candidate_root=candidate_root,
            stable_root=stable_root,
        )

    assert external_manifest.read_bytes() == sentinel_bytes


@pytest.mark.parametrize(
    ("subdir", "filename", "dangling_name"),
    [
        ("state", "project_state.json", "missing_state.json"),
        ("state", "canonical_queue.json", "missing_queue.json"),
        ("state", "repair_budget_ledger.json", "missing_ledger.json"),
        ("state", "controller.lock", "missing_lock.lock"),
        ("audit", "scaffold_manifest.json", "missing_manifest.json"),
    ],
)
def test_remediation01_rejects_dangling_file_and_manifest_symlinks(
    tmp_path: Path, subdir: str, filename: str, dangling_name: str
) -> None:
    projects_root, candidate_root, stable_root, external_dir = _make_roots(tmp_path)
    parent_dir = projects_root / "project1" / "runtime" / subdir
    parent_dir.mkdir(parents=True, exist_ok=True)

    dangling_target = external_dir / dangling_name
    assert not dangling_target.exists()
    _create_symlink_or_skip(parent_dir / filename, dangling_target, target_is_directory=False)

    with pytest.raises(ProjectScaffoldingError):
        build_project_runtime_layout(
            projects_root=projects_root,
            project_id="project1",
            candidate_root=candidate_root,
            stable_root=stable_root,
        )

    with pytest.raises(ProjectScaffoldingError):
        initialize_project_runtime(
            projects_root=projects_root,
            project_id="project1",
            candidate_root=candidate_root,
            stable_root=stable_root,
        )

    assert not dangling_target.exists()


def test_remediation01_rejects_nested_ancestor_directory_aliases_below_runtime_root(
    tmp_path: Path,
) -> None:
    projects_root, candidate_root, stable_root, external_dir = _make_roots(tmp_path)
    layout = initialize_project_runtime(
        projects_root=projects_root,
        project_id="project1",
        candidate_root=candidate_root,
        stable_root=stable_root,
    )

    outside_file = external_dir / "nested" / "deep.json"
    outside_file.parent.mkdir(parents=True, exist_ok=True)
    sentinel_bytes = b'{"sentinel": "outside-nested"}\n'
    outside_file.write_bytes(sentinel_bytes)

    state_alias = layout.state_dir / "nested_alias"
    _create_symlink_or_skip(state_alias, external_dir, target_is_directory=True)

    dangling_dir = external_dir / "missing_receipts_dir"
    assert not dangling_dir.exists()
    receipt_alias = layout.receipt_dir / "dangling_nested"
    _create_symlink_or_skip(receipt_alias, dangling_dir, target_is_directory=True)

    with pytest.raises(ProjectScaffoldingError):
        resolve_project_runtime_path(
            layout,
            "state/nested_alias/nested/deep.json",
            candidate_root=candidate_root,
            stable_root=stable_root,
        )

    with pytest.raises(ProjectScaffoldingError):
        resolve_project_runtime_path(
            layout,
            "audit/receipts/dangling_nested/sub/r1.json",
            candidate_root=candidate_root,
            stable_root=stable_root,
        )

    assert outside_file.read_bytes() == sentinel_bytes
    assert not dangling_dir.exists()


def test_remediation01_write_boundary_rejects_stale_check_manifest_symlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    projects_root, candidate_root, stable_root, external_dir = _make_roots(tmp_path)
    probe_target = external_dir / "probe.txt"
    probe_target.write_text("probe\n", encoding="utf-8")
    probe_link = tmp_path / "probe_link.txt"
    _create_symlink_or_skip(probe_link, probe_target, target_is_directory=False)
    probe_link.unlink()

    external_manifest = external_dir / "outside_manifest.json"
    sentinel_bytes = b'{"sentinel": "unchanged-outside"}\n'
    external_manifest.write_bytes(sentinel_bytes)

    injected = False

    def _inject_manifest_symlink(payload: object) -> None:
        nonlocal injected
        if not injected:
            injected = True
            manifest_link = (
                projects_root / "project1" / "runtime" / "audit" / "scaffold_manifest.json"
            )
            manifest_link.symlink_to(external_manifest, target_is_directory=False)
        assert_no_secrets(payload)

    monkeypatch.setattr(scaffolding_mod, "assert_no_secrets", _inject_manifest_symlink)

    with pytest.raises(ProjectScaffoldingError):
        initialize_project_runtime(
            projects_root=projects_root,
            project_id="project1",
            candidate_root=candidate_root,
            stable_root=stable_root,
        )

    assert injected is True
    assert external_manifest.read_bytes() == sentinel_bytes


def test_remediation01_write_boundary_rejects_stale_check_audit_dir_symlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    projects_root, candidate_root, stable_root, external_dir = _make_roots(tmp_path)
    probe_dir = external_dir / "probe_dir"
    probe_dir.mkdir(parents=True, exist_ok=True)
    probe_link = tmp_path / "probe_dir_link"
    _create_symlink_or_skip(probe_link, probe_dir, target_is_directory=True)
    probe_link.unlink()

    external_audit = external_dir / "outside_audit"
    external_audit.mkdir(parents=True, exist_ok=True)

    injected = False

    def _inject_audit_dir_symlink(payload: object) -> None:
        nonlocal injected
        if not injected:
            injected = True
            audit_dir = projects_root / "project1" / "runtime" / "audit"
            (audit_dir / "receipts").rmdir()
            audit_dir.rmdir()
            audit_dir.symlink_to(external_audit, target_is_directory=True)
        assert_no_secrets(payload)

    monkeypatch.setattr(scaffolding_mod, "assert_no_secrets", _inject_audit_dir_symlink)

    with pytest.raises(ProjectScaffoldingError):
        initialize_project_runtime(
            projects_root=projects_root,
            project_id="project1",
            candidate_root=candidate_root,
            stable_root=stable_root,
        )

    assert injected is True
    assert not (external_audit / "scaffold_manifest.json").exists()


def test_remediation01_rejects_receipt_deep_nested_ancestor_and_lexical_escape(
    tmp_path: Path,
) -> None:
    projects_root, candidate_root, stable_root, external_dir = _make_roots(tmp_path)
    layout = initialize_project_runtime(
        projects_root=projects_root,
        project_id="project1",
        candidate_root=candidate_root,
        stable_root=stable_root,
    )

    outside_receipt = external_dir / "nested" / "r1.json"
    outside_receipt.parent.mkdir(parents=True, exist_ok=True)
    sentinel_bytes = b'{"project_id": "project1", "sentinel": "outside-receipt"}\n'
    outside_receipt.write_bytes(sentinel_bytes)

    deep_parent = layout.receipt_dir / "level1" / "level2"
    deep_parent.mkdir(parents=True, exist_ok=True)
    _create_symlink_or_skip(deep_parent / "alias", external_dir, target_is_directory=True)

    with pytest.raises(ProjectScaffoldingError):
        resolve_project_runtime_path(
            layout,
            "audit/receipts/level1/level2/alias/nested/r1.json",
            candidate_root=candidate_root,
            stable_root=stable_root,
        )

    with pytest.raises(ProjectScaffoldingError):
        resolve_project_runtime_path(
            layout,
            "audit/receipts/../../outside.json",
            candidate_root=candidate_root,
            stable_root=stable_root,
        )

    with pytest.raises(ProjectScaffoldingError):
        resolve_project_runtime_path(
            layout,
            outside_receipt,
            candidate_root=candidate_root,
            stable_root=stable_root,
        )

    assert outside_receipt.read_bytes() == sentinel_bytes


def test_remediation02_rejects_preexisting_ancestor_symlink_above_projects_root(
    tmp_path: Path,
) -> None:
    _, candidate_root, stable_root, external_dir = _make_roots(tmp_path)
    external_projects = external_dir / "projects"
    external_projects.mkdir(parents=True, exist_ok=True)
    sentinel = external_projects / "sentinel.txt"
    sentinel_bytes = b"external-ancestor-sentinel\n"
    sentinel.write_bytes(sentinel_bytes)

    alias_parent = tmp_path / "alias_parent"
    _create_symlink_or_skip(alias_parent, external_dir, target_is_directory=True)
    aliased_projects_root = alias_parent / "projects"

    with pytest.raises(ProjectScaffoldingError):
        build_project_runtime_layout(
            projects_root=aliased_projects_root,
            project_id="project1",
            candidate_root=candidate_root,
            stable_root=stable_root,
        )

    with pytest.raises(ProjectScaffoldingError):
        initialize_project_runtime(
            projects_root=aliased_projects_root,
            project_id="project1",
            candidate_root=candidate_root,
            stable_root=stable_root,
        )

    assert sentinel.read_bytes() == sentinel_bytes
    assert not (external_projects / "project1").exists()
    assert list(external_projects.iterdir()) == [sentinel]


def test_remediation02_rejects_dangling_ancestor_symlink_above_projects_root(
    tmp_path: Path,
) -> None:
    _, candidate_root, stable_root, external_dir = _make_roots(tmp_path)
    dangling_parent = external_dir / "missing_parent"
    assert not dangling_parent.exists()

    alias_parent = tmp_path / "dangling_alias"
    _create_symlink_or_skip(alias_parent, dangling_parent, target_is_directory=True)
    aliased_projects_root = alias_parent / "projects"

    with pytest.raises(ProjectScaffoldingError):
        build_project_runtime_layout(
            projects_root=aliased_projects_root,
            project_id="project1",
            candidate_root=candidate_root,
            stable_root=stable_root,
        )

    with pytest.raises(ProjectScaffoldingError):
        initialize_project_runtime(
            projects_root=aliased_projects_root,
            project_id="project1",
            candidate_root=candidate_root,
            stable_root=stable_root,
        )

    assert not dangling_parent.exists()


def test_remediation02_rejects_ancestor_symlink_preserving_matching_project(
    tmp_path: Path,
) -> None:
    _, candidate_root, stable_root, external_dir = _make_roots(tmp_path)
    ext_project = external_dir / "projects" / "project1"
    ext_audit = ext_project / "runtime" / "audit"
    ext_state = ext_project / "runtime" / "state"
    ext_audit.mkdir(parents=True, exist_ok=True)
    ext_state.mkdir(parents=True, exist_ok=True)

    manifest_bytes = b'{"schema_version": "1.0", "project_id": "project1", "note": "ext"}\n'
    state_bytes = b'{"project_id": "project1", "sentinel": "ext-ancestor-state"}\n'
    ext_manifest = ext_audit / "scaffold_manifest.json"
    ext_state_file = ext_state / "project_state.json"
    ext_manifest.write_bytes(manifest_bytes)
    ext_state_file.write_bytes(state_bytes)

    alias_parent = tmp_path / "alias_parent"
    _create_symlink_or_skip(alias_parent, external_dir, target_is_directory=True)
    aliased_projects_root = alias_parent / "projects"

    with pytest.raises(ProjectScaffoldingError):
        build_project_runtime_layout(
            projects_root=aliased_projects_root,
            project_id="project1",
            candidate_root=candidate_root,
            stable_root=stable_root,
        )

    with pytest.raises(ProjectScaffoldingError):
        initialize_project_runtime(
            projects_root=aliased_projects_root,
            project_id="project1",
            candidate_root=candidate_root,
            stable_root=stable_root,
        )

    assert ext_manifest.read_bytes() == manifest_bytes
    assert ext_state_file.read_bytes() == state_bytes
