"""Unit tests for Milestone C.7 RCE03 per-project scaffolding and starter templates."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

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
    try:
        alias_project.symlink_to(layout1.project_root, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("Symlink creation requires elevated privileges on this host")

    with pytest.raises(ProjectScaffoldingError):
        initialize_project_runtime(
            projects_root=projects_root,
            project_id="project2",
            candidate_root=candidate_root,
            stable_root=stable_root,
        )

    escape_link = layout1.state_dir / "escape_dir"
    escape_link.symlink_to(candidate_root, target_is_directory=True)
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
