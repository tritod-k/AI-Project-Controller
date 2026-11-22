"""Unit tests for Milestone D D01-B intake, status rendering, and Git quiescence."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from aipc.controller.task_queue import ApprovedTaskContract, PersistentTaskQueue
from aipc.hardening.secrets import assert_no_secrets
from aipc.project.scaffolding import (
    ProjectScaffoldingError,
    build_short_worktree_name,
    cleanup_stale_worktrees,
    initialize_project_runtime,
    render_current_status_md,
    snapshot_approved_plan,
)
from aipc.project.workspace_git_checkpoint import (
    WorkspaceGitQuiescenceError,
    _case_key,
    _collect_pattern_boundaries,
    _segment_globs_intersect,
    _tokenize_segment_glob,
    acquire_controller_lock,
    verify_workspace_git_quiescence,
)
from aipc.project.workspace_git_checkpoint import (
    main as quiescence_main,
)
from aipc.review.llm.security import redact

_COLON = chr(58)
_EQ = chr(61)
_DQUOTE = chr(34)
_SQUOTE = chr(39)


def _join(parts: tuple[str, ...]) -> str:
    return "".join(parts)


def _kv_line(k_parts: tuple[str, ...], sep: str, v_parts: tuple[str, ...]) -> str:
    return f"{_join(k_parts)}{sep}{_join(v_parts)}"


def _kv_quoted(
    k_parts: tuple[str, ...],
    v_parts: tuple[str, ...],
    *,
    quote: str = _DQUOTE,
    braces: bool = False,
) -> str:
    body = f"{quote}{_join(k_parts)}{quote}{_COLON} {quote}{_join(v_parts)}{quote}"
    return f"{{{body}}}" if braces else body


def _create_symlink_or_skip(
    link_path: Path, target_path: Path, *, target_is_directory: bool = False
) -> None:
    try:
        link_path.symlink_to(target_path, target_is_directory=target_is_directory)
    except OSError as exc:
        if sys.platform == "win32" and getattr(exc, "winerror", None) == 1314:
            pytest.skip("Symlink creation requires WinError 1314 privilege on Windows")
        raise


def _init_project(tmp_path: Path, project_id: str = "proj-alpha") -> tuple[Path, Path, Path]:
    projects_root = tmp_path / "projects"
    candidate_root = tmp_path / "candidate_aipc"
    stable_root = tmp_path / "stable_aipc"
    for p in (projects_root, candidate_root, stable_root):
        p.mkdir(parents=True, exist_ok=True)
    layout = initialize_project_runtime(
        projects_root=projects_root,
        project_id=project_id,
        candidate_root=candidate_root,
        stable_root=stable_root,
    )
    return projects_root, layout.project_root, layout.runtime_root


def _setup_quiescent_workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    (ws / ".gitattributes").write_text("* text=auto eol=lf\n", encoding="utf-8", newline="\n")
    (ws / ".gitignore").write_text(
        "stable_c/\nworktrees/\nlogs/\n*.lock\n*.key\n.env*\n",
        encoding="utf-8",
        newline="\n",
    )
    return ws


def test_initialize_project_runtime_creates_v12_subdirs(tmp_path: Path) -> None:
    _, _, runtime_root = _init_project(tmp_path)
    for rel in (
        "plans/proposals",
        "plans/approved",
        "trusted",
        "audit/approvals",
        "logs",
        "state",
        "audit/receipts",
        "worktrees",
    ):
        assert (runtime_root / rel).is_dir()


def test_snapshot_approved_plan_positive_and_immutable_fail_closed(tmp_path: Path) -> None:
    _, _, runtime_root = _init_project(tmp_path)
    source_plan = tmp_path / "proposal_plan.md"
    source_plan.write_bytes(b"# Master Plan\r\n\r\nStep 1: Build.\r\n")
    impact_file = tmp_path / "change_impact.md"
    impact_file.write_bytes(b"# Impact\r\nNo breaking changes.\r\n")

    snap = snapshot_approved_plan(
        runtime_root,
        plan_version="plan-v0001",
        source_plan_path=source_plan,
        approved_by="commander-k",
        project_id="proj-alpha",
        milestone_id="milestone-d",
        change_impact_path=impact_file,
    )

    assert snap.master_plan_path.exists()
    assert snap.manifest_path.exists()
    assert snap.manifest_sha256_path.exists()
    assert b"\r" not in snap.master_plan_path.read_bytes()
    assert b"\r" not in snap.manifest_path.read_bytes()
    assert b"\r" not in snap.manifest_sha256_path.read_bytes()

    manifest = json.loads(snap.manifest_path.read_text(encoding="utf-8"))
    assert manifest["plan_version"] == "plan-v0001"
    assert manifest["approved_by"] == "commander-k"
    assert manifest["project_id"] == "proj-alpha"
    assert manifest["milestone_id"] == "milestone-d"
    assert manifest["change_impact_source"] == "change_impact.md"
    assert manifest["change_impact_sha256"] is not None

    actual_manifest_sha = hashlib.sha256(snap.manifest_path.read_bytes()).hexdigest()
    assert snap.manifest_sha256 == actual_manifest_sha
    sidecar_text = snap.manifest_sha256_path.read_text(encoding="utf-8")
    assert sidecar_text == f"{actual_manifest_sha}  plan_manifest.json\n"

    # Re-snapshotting same plan_version must fail closed
    with pytest.raises(ProjectScaffoldingError):
        snapshot_approved_plan(
            runtime_root,
            plan_version="plan-v0001",
            source_plan_path=source_plan,
            approved_by="commander-k",
            project_id="proj-alpha",
            milestone_id="milestone-d",
        )

    # Non-canonical, empty, whitespace, or traversal plan_version values must fail closed
    for bad_version in (
        "v1.0.0",
        "plan-v1",
        "plan-v12",
        "1.0",
        "",
        "   ",
        " plan-v0001",
        "plan-v0001 ",
        "../escape",
    ):
        with pytest.raises(ProjectScaffoldingError):
            snapshot_approved_plan(
                runtime_root,
                plan_version=bad_version,
                source_plan_path=source_plan,
                approved_by="commander-k",
                project_id="proj-alpha",
                milestone_id="milestone-d",
            )

    # Symlink source plan must fail closed
    symlink_plan = tmp_path / "link_plan.md"
    _create_symlink_or_skip(symlink_plan, source_plan, target_is_directory=False)
    with pytest.raises(ProjectScaffoldingError):
        snapshot_approved_plan(
            runtime_root,
            plan_version="plan-v0002",
            source_plan_path=symlink_plan,
            approved_by="commander-k",
            project_id="proj-alpha",
            milestone_id="milestone-d",
        )


def test_build_short_worktree_name_and_cleanup_stale_worktrees(tmp_path: Path) -> None:
    name1 = build_short_worktree_name("task-01", "attempt-1")
    name1_again = build_short_worktree_name("task-01", "attempt-1")
    name2 = build_short_worktree_name("task-01", "attempt-2")

    assert name1 == name1_again
    assert name1 != name2
    assert len(name1) <= 12
    assert re.fullmatch(r"wt-[0-9a-f]{8}", name1)

    wt_dir = tmp_path / "worktrees"
    wt_dir.mkdir(parents=True, exist_ok=True)
    active_dir = wt_dir / name1
    stale_dir = wt_dir / name2
    (active_dir / "nested").mkdir(parents=True, exist_ok=True)
    (active_dir / "nested" / "keep.txt").write_text("active\n", encoding="utf-8")
    (stale_dir / "nested").mkdir(parents=True, exist_ok=True)
    (stale_dir / "nested" / "old.txt").write_text("stale\n", encoding="utf-8")
    (wt_dir / "stale-note.txt").write_text("stale\n", encoding="utf-8")

    removed = cleanup_stale_worktrees(wt_dir, active_names=(name1,))
    assert set(removed) == {name2, "stale-note.txt"}
    assert active_dir.exists()
    assert not stale_dir.exists()
    assert not (wt_dir / "stale-note.txt").exists()

    # Symlink inside worktrees_dir must fail closed
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir(parents=True, exist_ok=True)
    (outside_dir / "precious.txt").write_text("keep\n", encoding="utf-8")
    _create_symlink_or_skip(wt_dir / "bad-link", outside_dir, target_is_directory=True)
    with pytest.raises(ProjectScaffoldingError):
        cleanup_stale_worktrees(wt_dir, active_names=(name1,))
    assert (outside_dir / "precious.txt").exists()


def test_render_current_status_md_deterministic_and_non_mutating(tmp_path: Path) -> None:
    _, project_root, runtime_root = _init_project(tmp_path)
    queue_path = runtime_root / "state" / "canonical_queue.json"
    budget_path = runtime_root / "state" / "repair_budget_ledger.json"
    state_path = runtime_root / "state" / "project_state.json"
    approvals_dir = runtime_root / "audit" / "approvals"
    receipts_dir = runtime_root / "audit" / "receipts"

    queue_bytes = (
        json.dumps(
            {
                "tasks": [
                    {"task_id": "t-02", "status": "DONE", "phase_id": "d01"},
                    {"task_id": "t-01", "status": "PENDING", "phase_id": "d01"},
                ]
            },
            indent=2,
        )
        + "\n"
    ).encode("utf-8")
    budget_bytes = (json.dumps({"used": 1, "limit": 5}, indent=2) + "\n").encode("utf-8")
    state_json = json.dumps({"project_id": "proj-alpha", "status": "ACTIVE"}, indent=2) + "\n"
    state_bytes = state_json.encode("utf-8")
    queue_path.write_bytes(queue_bytes)
    budget_path.write_bytes(budget_bytes)
    state_path.write_bytes(state_bytes)
    (approvals_dir / "apr-01.json").write_text('{"approved": true}\n', encoding="utf-8")
    (receipts_dir / "rec-01.json").write_text('{"ok": true}\n', encoding="utf-8")

    out1 = render_current_status_md(
        project_root,
        queue_path=queue_path,
        budget_ledger_path=budget_path,
        project_state_path=state_path,
        approvals_dir=approvals_dir,
        receipts_dir=receipts_dir,
    )
    first_bytes = out1.read_bytes()
    assert b"\r" not in first_bytes
    text = first_bytes.decode("utf-8")
    assert "DERIVED / NON-AUTHORITATIVE" in text
    assert "`t-01`" in text
    assert "`t-02`" in text

    out2 = render_current_status_md(
        project_root,
        queue_path=queue_path,
        budget_ledger_path=budget_path,
        project_state_path=state_path,
        approvals_dir=approvals_dir,
        receipts_dir=receipts_dir,
    )
    assert out2.read_bytes() == first_bytes
    assert queue_path.read_bytes() == queue_bytes
    assert budget_path.read_bytes() == budget_bytes
    assert state_path.read_bytes() == state_bytes


def test_verify_workspace_git_quiescence_positive_and_cli(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ws = _setup_quiescent_workspace(tmp_path)
    audit_dir = ws / "projects" / "proj-alpha" / "runtime" / "audit"
    state_dir = ws / "projects" / "proj-alpha" / "runtime" / "state"
    audit_dir.mkdir(parents=True, exist_ok=True)
    state_dir.mkdir(parents=True, exist_ok=True)

    (state_dir / "canonical_queue.json").write_text(
        '{"tasks": [{"task_id": "t-01", "status": "COMPLETED"}]}\n',
        encoding="utf-8",
        newline="\n",
    )
    target_json = audit_dir / "plan_manifest.json"
    target_bytes = b'{"plan_version": "plan-v0001"}\n'
    target_json.write_bytes(target_bytes)
    digest = hashlib.sha256(target_bytes).hexdigest()
    (audit_dir / "plan_manifest.json.sha256").write_bytes(
        f"{digest}  plan_manifest.json\n".encode()
    )

    report = verify_workspace_git_quiescence(ws)
    assert report.passed is True
    assert report.status == "PASS"
    assert report.violations == ()
    assert all(report.checks.values())

    rc = quiescence_main(["--workspace-root", str(ws)])
    assert rc == 0
    cli_out = json.loads(capsys.readouterr().out)
    assert cli_out["status"] == "PASS"


def test_verify_workspace_git_quiescence_fail_closed_a_through_e(tmp_path: Path) -> None:
    # (a) missing .gitattributes / .gitignore exclusions
    ws_a = tmp_path / "ws_a"
    ws_a.mkdir()
    (ws_a / ".gitattributes").write_text("* text=auto\n", encoding="utf-8")
    (ws_a / ".gitignore").write_text("stable_c/\n", encoding="utf-8")
    rep_a = verify_workspace_git_quiescence(ws_a)
    assert rep_a.passed is False
    assert rep_a.checks["git_hygiene"] is False

    # (b) checkpoint_manifest.json anywhere in workspace
    ws_b = _setup_quiescent_workspace(tmp_path / "b")
    (ws_b / "nested").mkdir()
    (ws_b / "nested" / "checkpoint_manifest.json").write_text("{}\n", encoding="utf-8")
    rep_b = verify_workspace_git_quiescence(ws_b)
    assert rep_b.passed is False
    assert rep_b.checks["no_checkpoint_manifest"] is False

    # (c) IN_PROGRESS or RECONCILIATION_REQUIRED in canonical_queue.json
    ws_c = _setup_quiescent_workspace(tmp_path / "c")
    state_c = ws_c / "projects" / "p1" / "runtime" / "state"
    state_c.mkdir(parents=True)
    (state_c / "canonical_queue.json").write_text(
        '{"tasks": [{"task_id": "t-1", "status": "RECONCILIATION_REQUIRED"}]}\n',
        encoding="utf-8",
    )
    rep_c = verify_workspace_git_quiescence(ws_c)
    assert rep_c.passed is False
    assert rep_c.checks["queue_quiescence"] is False

    # (d) .sha256 sidecar CRLF and digest mismatch
    ws_d1 = _setup_quiescent_workspace(tmp_path / "d1")
    audit_d1 = ws_d1 / "projects" / "p1" / "runtime" / "audit"
    audit_d1.mkdir(parents=True)
    (audit_d1 / "item.json").write_bytes(b'{"a": 1}\n')
    good_hex = hashlib.sha256(b'{"a": 1}\n').hexdigest()
    (audit_d1 / "item.json.sha256").write_bytes(f"{good_hex}  item.json\r\n".encode())
    rep_d1 = verify_workspace_git_quiescence(ws_d1)
    assert rep_d1.passed is False
    assert rep_d1.checks["audit_sha256_integrity"] is False

    ws_d2 = _setup_quiescent_workspace(tmp_path / "d2")
    audit_d2 = ws_d2 / "projects" / "p1" / "runtime" / "audit"
    audit_d2.mkdir(parents=True)
    (audit_d2 / "item.json").write_bytes(b'{"a": 1}\n')
    (audit_d2 / "item.json.sha256").write_bytes(f"{'0' * 64}  item.json\n".encode())
    rep_d2 = verify_workspace_git_quiescence(ws_d2)
    assert rep_d2.passed is False
    assert rep_d2.checks["audit_sha256_integrity"] is False

    # (e) credential pattern detected in workspace JSON/YAML/MD
    ws_e = _setup_quiescent_workspace(tmp_path / "e")
    sample_e = _kv_line(("api", "_k", "ey"), f"{_COLON} ", ("dummy_test_value_01",)) + "\n"
    (ws_e / "notes.md").write_text(sample_e, encoding="utf-8")
    rep_e = verify_workspace_git_quiescence(ws_e)
    assert rep_e.passed is False
    assert rep_e.checks["no_secrets"] is False


def test_verify_workspace_git_quiescence_git_tracked_forbidden_and_lock(tmp_path: Path) -> None:
    if shutil.which("git") is None:
        pytest.skip("git executable not available")

    ws = _setup_quiescent_workspace(tmp_path / "git_ws")
    subprocess.run(["git", "init"], cwd=ws, check=True, capture_output=True)
    subprocess.run(["git", "add", ".gitattributes", ".gitignore"], cwd=ws, check=True)

    rep_clean = verify_workspace_git_quiescence(ws)
    assert rep_clean.is_git_repo is True
    assert rep_clean.passed is True

    # Stage a forbidden file (e.g. .env.local forced into index)
    (ws / ".env.local").write_text("APP_MODE=dev\n", encoding="utf-8")
    subprocess.run(["git", "add", "-f", ".env.local"], cwd=ws, check=True, capture_output=True)
    rep_dirty = verify_workspace_git_quiescence(ws)
    assert rep_dirty.passed is False
    assert rep_dirty.checks["git_index_clean"] is False

    # Exclusive lock contention fails closed
    lock_file = ws / "controller.lock"
    with acquire_controller_lock(lock_file), pytest.raises(WorkspaceGitQuiescenceError):
        verify_workspace_git_quiescence(ws, lock_path=lock_file)


def test_verify_workspace_git_quiescence_rejects_running_malformed_queue_and_reserved_budget(
    tmp_path: Path,
) -> None:
    # (a) Canonical RUNNING task with nested contract.task_id fails quiescence closed
    ws_running = _setup_quiescent_workspace(tmp_path / "ws_running")
    state_running = ws_running / "projects" / "p1" / "runtime" / "state"
    state_running.mkdir(parents=True)
    (state_running / "canonical_queue.json").write_text(
        json.dumps(
            {
                "tasks": [
                    {
                        "contract": {"task_id": "t-nested-run", "phase_id": "d01"},
                        "status": "RUNNING",
                    }
                ]
            }
        )
        + "\n",
        encoding="utf-8",
    )
    rep_running = verify_workspace_git_quiescence(ws_running)
    assert rep_running.passed is False
    assert rep_running.checks["queue_quiescence"] is False
    assert any("t-nested-run" in v and "RUNNING" in v for v in rep_running.violations)

    # (b) Malformed queue task collections fail quiescence closed
    for idx, bad_payload in enumerate(
        (
            ["not-a-dict-root"],
            {"tasks": {"t-1": {"status": "COMPLETED"}}},
            {"tasks": ["not-a-task-dict"]},
        )
    ):
        ws_bad = _setup_quiescent_workspace(tmp_path / f"ws_bad_{idx}")
        state_bad = ws_bad / "projects" / "p1" / "runtime" / "state"
        state_bad.mkdir(parents=True)
        (state_bad / "canonical_queue.json").write_text(
            json.dumps(bad_payload) + "\n",
            encoding="utf-8",
        )
        rep_bad = verify_workspace_git_quiescence(ws_bad)
        assert rep_bad.passed is False
        assert rep_bad.checks["queue_quiescence"] is False

    # (c) Active RESERVED entry in repair_budget_ledger.json fails quiescence closed
    ws_res = _setup_quiescent_workspace(tmp_path / "ws_reserved")
    state_res = ws_res / "projects" / "p1" / "runtime" / "state"
    state_res.mkdir(parents=True)
    (state_res / "canonical_queue.json").write_text('{"tasks": []}\n', encoding="utf-8")
    (state_res / "repair_budget_ledger.json").write_text(
        json.dumps(
            {
                "reservations": {
                    "res-001": {
                        "reservation_id": "res-001",
                        "task_id": "t-01",
                        "status": "RESERVED",
                    }
                }
            }
        )
        + "\n",
        encoding="utf-8",
    )
    rep_res = verify_workspace_git_quiescence(ws_res)
    assert rep_res.passed is False
    assert rep_res.checks["queue_quiescence"] is False
    assert any("RESERVED" in v and "res-001" in v for v in rep_res.violations)


def test_render_current_status_md_uses_nested_contract_fields_from_persistent_queue(
    tmp_path: Path,
) -> None:
    _, project_root, runtime_root = _init_project(tmp_path)
    queue_path = runtime_root / "state" / "canonical_queue.json"
    queue = PersistentTaskQueue(queue_path)
    queue.initialize(
        project_id="proj-alpha",
        milestone_id="phase-24",
        contracts=(
            ApprovedTaskContract(
                task_id="nested-task-01",
                phase_id="phase-01",
                milestone_id="phase-24",
                title="Render nested contract",
                objective="Verify CURRENT_STATUS.md extracts nested contract fields.",
                in_scope=("src/aipc/project/scaffolding.py",),
                validation_commands=(
                    "pytest tests/unit/test_d01_intake_status_and_git_quiescence.py",
                ),
            ),
        ),
    )

    out_path = render_current_status_md(project_root, queue_path=queue_path)
    rendered = out_path.read_text(encoding="utf-8")
    assert "`nested-task-01`" in rendered
    assert "`phase-01`" in rendered
    assert "`task-0`" not in rendered
    assert "| `-` |" not in rendered


def test_verify_workspace_git_quiescence_strict_queue_allowlist_and_malformed_tasks(
    tmp_path: Path,
) -> None:
    bad_payloads: tuple[dict[str, object], ...] = (
        {"tasks": None},
        {"items": []},
        {},
        {"tasks": [{"task_id": "t-missing-status"}]},
        {"tasks": [{"task_id": "t-none-status", "status": None}]},
        {"tasks": [{"task_id": "t-empty-status", "status": "   "}]},
        {"tasks": [{"task_id": "t-int-status", "status": 42}]},
        {"tasks": [{"task_id": "t-bogus", "status": "BOGUS_STATUS"}]},
        {"tasks": [{"task_id": "t-done", "status": "DONE"}]},
        {
            "tasks": [
                {
                    "task_id": "t-reconcile",
                    "status": "COMPLETED",
                    "reconciliation_required": True,
                }
            ]
        },
        {"tasks": [{"task_id": "t-dispatch", "status": "DISPATCHING"}]},
        {"tasks": [{"task_id": "t-claim", "status": "CLAIMING"}]},
        {"tasks": [{"task_id": "t-interrupt", "status": "INTERRUPTED"}]},
        {"tasks": [{"task_id": "t-wait-prov", "status": "WAITING_PROVIDER"}]},
        {"tasks": [{"task_id": "t-wait-human", "status": "WAITING_HUMAN"}]},
        {"tasks": [{"task_id": "t-repair", "status": "REPAIR_REQUIRED"}]},
        {
            "reconciliation_required": True,
            "tasks": [{"task_id": "t-1", "status": "ELIGIBLE"}],
        },
        {
            "unresolved_reservation_ids": ["res-orphan-1"],
            "tasks": [{"task_id": "t-1", "status": "ELIGIBLE"}],
        },
    )
    for idx, payload in enumerate(bad_payloads):
        ws = _setup_quiescent_workspace(tmp_path / f"strict_bad_{idx}")
        state_dir = ws / "projects" / "p1" / "runtime" / "state"
        state_dir.mkdir(parents=True)
        (state_dir / "canonical_queue.json").write_text(
            json.dumps(payload) + "\n",
            encoding="utf-8",
        )
        rep = verify_workspace_git_quiescence(ws)
        assert rep.passed is False, f"Expected FAIL for payload {payload}"
        assert rep.checks["queue_quiescence"] is False, f"Expected queue FAIL for {payload}"


def test_verify_workspace_git_quiescence_portable_root_docs_glob_ignore_and_secrets(
    tmp_path: Path,
) -> None:
    if shutil.which("git") is None:
        pytest.skip("git executable not available")

    ws = tmp_path / "portable_ws"
    ws.mkdir(parents=True)
    (ws / ".gitattributes").write_text("* text=auto eol=lf\n", encoding="utf-8", newline="\n")
    (ws / ".gitignore").write_text(
        "stable_c/\n**/runtime/worktrees/\n**/runtime/logs/\n**/*.lock\n*.key\n.env*\n",
        encoding="utf-8",
        newline="\n",
    )

    gh_var = _join(("GH_T", "OKEN"))
    pw_eq = _join(("pa", "ssw", "ord", _EQ))
    ps_recipe = (
        "# Operator Guide\n\n"
        "```powershell\n"
        f"$env{_COLON}{gh_var} {_EQ} (($out | Where-Object {{ $_ -like '{pw_eq}*' }}) "
        f"-replace '^{pw_eq}','').Trim()\n"
        "```\n"
    )
    (ws / "GEMINI.md").write_text(ps_recipe, encoding="utf-8", newline="\n")
    (ws / "AGENTS.md").write_text("# Agents\nFollow controller rules.\n", encoding="utf-8")
    (ws / "README.md").write_text("# Workspace\n", encoding="utf-8")
    (ws / "AIPC_RUNBOOK.md").write_text("# Runbook\n", encoding="utf-8")
    (ws / "launch_aipc.bat").write_text(
        "@echo off\r\nset MODE=local\r\necho Ready\r\n",
        encoding="utf-8",
    )

    # Ignored ephemeral / nested engine dirs should be skipped by walk
    for ignored_dir in ("stable_c", ".venv", ".venv_stable", "runtime_c", "__pycache__"):
        d = ws / ignored_dir
        d.mkdir(parents=True, exist_ok=True)
        (d / "checkpoint_manifest.json").write_text("{}\n", encoding="utf-8")

    subprocess.run(["git", "init"], cwd=ws, check=True, capture_output=True)
    subprocess.run(
        [
            "git",
            "add",
            ".gitattributes",
            ".gitignore",
            "GEMINI.md",
            "AGENTS.md",
            "README.md",
            "AIPC_RUNBOOK.md",
            "launch_aipc.bat",
        ],
        cwd=ws,
        check=True,
        capture_output=True,
    )

    rep_ok = verify_workspace_git_quiescence(ws)
    assert rep_ok.passed is True
    assert rep_ok.status == "PASS"
    assert all(rep_ok.checks.values())

    # Synthetic credential pair in GEMINI.md must still fail no_secrets
    bad_line = _kv_line(("api", "_k", "ey"), _EQ, ("realvalue_dummy_01",))
    (ws / "GEMINI.md").write_text(
        f"{ps_recipe}\n{bad_line}\n",
        encoding="utf-8",
        newline="\n",
    )
    rep_flagged = verify_workspace_git_quiescence(ws)
    assert rep_flagged.passed is False
    assert rep_flagged.checks["no_secrets"] is False
    assert any("GEMINI.md" in v for v in rep_flagged.violations)


def test_verify_workspace_git_quiescence_quoted_json_yaml_secrets_and_ps_recipe(
    tmp_path: Path,
) -> None:
    ws_ok = _setup_quiescent_workspace(tmp_path / "ws_ps_ok")
    gh_var = _join(("GH_T", "OKEN"))
    pw_eq = _join(("pa", "ssw", "ord", _EQ))
    ps_doc = (
        "# Workspace Policy\n\n"
        "- Outer CLI sessions set `GEMINI_API_KEY` / `GOOGLE_*`.\n"
        "- Never trip `SecretExposureError`.\n\n"
        "```powershell\n"
        "$gh = 'H:\\AIPC_Workspace\\runtime_c\\gh_portable\\bin\\gh.exe'\n"
        '$req = "protocol=https$([char]10)host=github.com$([char]10)$([char]10)"\n'
        "$out = $req | git credential-manager get\n"
        f"$env{_COLON}{gh_var} {_EQ} (($out | Where-Object {{ $_ -like '{pw_eq}*' }}) "
        f"-replace '^{pw_eq}','').Trim()\n"
        "try { & $gh status } finally { "
        f"Remove-Item Env{_COLON}{gh_var} -ErrorAction SilentlyContinue }}\n"
        "```\n"
    )
    safe_placeholders = (
        _kv_quoted(("api", "_k", "ey"), ("${API_", "KEY}")),
        _kv_quoted(("to", "ken"), ("<placeholder>",)),
        _kv_quoted(("pa", "ssw", "ord"), ("*",)),
    )
    safe_doc = "# Placeholders\n\n" + "\n".join(safe_placeholders) + "\n"
    (ws_ok / "GEMINI.md").write_text(ps_doc + "\n" + safe_doc, encoding="utf-8", newline="\n")
    (ws_ok / "AGENTS.md").write_text(ps_doc, encoding="utf-8", newline="\n")
    (ws_ok / "README.md").write_text(safe_doc, encoding="utf-8", newline="\n")
    rep_ok = verify_workspace_git_quiescence(ws_ok)
    assert rep_ok.passed is True
    assert rep_ok.checks["no_secrets"] is True

    quoted_cases = (
        _kv_quoted(("api", "_k", "ey"), ("my_super_", "sec", "ret_credential_12345"), braces=True),
        _kv_quoted(("access_", "to", "ken"), ("tok_", "val_123"), quote=_SQUOTE),
        _kv_quoted(("client_", "sec", "ret"), ("cs_", "val_123")),
        _kv_quoted(("pa", "ssw", "ord"), ("pw_", "val_123")),
        _kv_quoted(("author", "ization"), ("Bas", "ic ", "dXNlcj", "pwYXNz")),
        _kv_quoted(("pa", "ssw", "ord"), ("$uper", "Sec", "retPa", "ssword123"), braces=True),
        _kv_line(
            ("api", "_k", "ey"),
            f"{_COLON} ",
            (_DQUOTE, "(my_", "sec", "ret_api_", "key_999)", _DQUOTE),
        ),
        _kv_quoted(("sec", "ret"), ("$2b", "$12", "$abcdefg", "hijklmnopqrstuv"), quote=_SQUOTE),
        _kv_quoted(("to", "ken"), ("{", "sec", "ret_tok_payload_123}")),
    )
    for idx, bad_entry in enumerate(quoted_cases):
        for doc_name in ("README.md", "GEMINI.md"):
            ws_bad = _setup_quiescent_workspace(tmp_path / f"ws_q_{idx}_{doc_name}")
            (ws_bad / doc_name).write_text(
                f"# Doc\n\n{bad_entry}\n",
                encoding="utf-8",
                newline="\n",
            )
            rep_bad = verify_workspace_git_quiescence(ws_bad)
            assert rep_bad.passed is False, f"Expected FAIL for {doc_name}: {bad_entry}"
            assert rep_bad.checks["no_secrets"] is False

    # Verify this test module source itself never trips secret scanners or review redaction
    source_text = Path(__file__).read_text(encoding="utf-8")
    assert_no_secrets(source_text)
    assert redact(source_text) == source_text


def test_verify_workspace_git_quiescence_gitignore_scope_and_negations(
    tmp_path: Path,
) -> None:
    base_rules = "stable_c/\n**/runtime/worktrees/\n**/runtime/logs/\n**/*.lock\n*.key\n.env*\n"
    for idx, valid_rules in enumerate(
        (
            base_rules,
            "stable_c/\n**/runtime/worktrees/\n**/runtime/logs/\n**/*.lock\n*.key\n**/.env*\n",
        )
    ):
        ws_valid = _setup_quiescent_workspace(tmp_path / f"ws_ign_valid_{idx}")
        (ws_valid / ".gitignore").write_text(valid_rules, encoding="utf-8", newline="\n")
        rep_valid = verify_workspace_git_quiescence(ws_valid)
        assert rep_valid.passed is True
        assert rep_valid.checks["git_hygiene"] is True

    scoped_cases = (
        "stable_c/\nworktrees/\nsubdir/logs/\n*.lock\n*.key\n.env*\n",
        "foo/stable_c/\nworktrees/\nlogs/\n*.lock\n*.key\n.env*\n",
        "stable_c/\nworktrees/\nlogs/\nsubdir/*.lock\n*.key\n.env*\n",
        "stable_c/\n/worktrees/\nlogs/\n*.lock\n*.key\n.env*\n",
        "stable_c/\nworktrees/\n/logs/\n*.lock\n*.key\n.env*\n",
        "stable_c/\nworktrees/\nlogs/\n/*.lock\n*.key\n.env*\n",
        "stable_c/\nworktrees/\nlogs/\n*.lock\nsubdir/*.key\n.env*\n",
        "stable_c/\nworktrees/\nlogs/\n*.lock\n*.key\nsubdir/.env*\n",
        "stable_c/\nworktrees/\nlogs/\n*.lock\n*.key\n/.env*\n",
    )
    for idx, content in enumerate(scoped_cases):
        ws_scoped = _setup_quiescent_workspace(tmp_path / f"ws_scoped_{idx}")
        (ws_scoped / ".gitignore").write_text(content, encoding="utf-8", newline="\n")
        rep_scoped = verify_workspace_git_quiescence(ws_scoped)
        assert rep_scoped.passed is False, f"Expected scoped FAIL for case {idx}"
        assert rep_scoped.checks["git_hygiene"] is False

    trailing_negations = (
        "!**/runtime/logs/",
        "!projects/p1/runtime/logs/x.log",
        "!projects/app/runtime/logs/keep.txt",
        "!stable_c/",
        "!stable_c/foo.py",
        "!worktrees/",
        "!projects/p1/runtime/worktrees/a.txt",
        "!controller.lock",
        "!projects/p1/a.lock",
        "!operator.key",
        "!.env",
        "!.env.local",
        "!a.l?ck",
        "!a.k?y",
        "!.en?",
        "!*[l]ock",
        "!**/s?able_c/*",
        "!*",
        "!ü.lock",
        "!ไฟล์.key",
        "!.env_ü",
        "![!a].lock",
        "![ü].lock",
    )
    for idx, neg_line in enumerate(trailing_negations):
        ws_neg = _setup_quiescent_workspace(tmp_path / f"ws_neg_{idx}")
        (ws_neg / ".gitignore").write_text(
            f"{base_rules}{neg_line}\n",
            encoding="utf-8",
            newline="\n",
        )
        rep_neg = verify_workspace_git_quiescence(ws_neg)
        assert rep_neg.passed is False, f"Expected negation FAIL for {neg_line}"
        assert rep_neg.checks["git_hygiene"] is False


def test_segment_globs_intersect_exact_unicode_interval_partition_counterexample(
    tmp_path: Path,
) -> None:
    wide_range = "[\u1000-\u3000]"
    punctured_negation = "[!\u1000-\u1001\u1fff-\u2001\u2fff-\u3000]"

    # Interior witness \u1002 intersects wide_range and punctured_negation
    assert _segment_globs_intersect(wide_range, punctured_negation) is True
    assert _segment_globs_intersect(punctured_negation, wide_range) is True

    # Complementary disjoint classes must not intersect
    assert _segment_globs_intersect("[\u1000-\u1001]", "[!\u1000-\u1001]") is False
    assert _segment_globs_intersect("[\u1000-\u3000]", "[!\u1000-\u3000]") is False

    # Case-insensitive ASCII and Unicode ranges and literals
    assert _segment_globs_intersect("[A-Z]", "[a-z]") is True
    assert _segment_globs_intersect("[A-Z]", "[!a-z]") is False
    assert _segment_globs_intersect("[Z-b]", "[@-A]") is True
    assert _segment_globs_intersect("[Z-b]", "[!Z-b]") is False
    assert _segment_globs_intersect("*.LOCK", "*.lock") is True
    assert _segment_globs_intersect("[\u00c0-\u00d6]", "[\u00e0-\u00f6]") is True
    assert _segment_globs_intersect("[\u00c0-\u00d6]", "[!\u00e0-\u00f6]") is False

    # Multi-char casefold expansion ('\u00df'.casefold() == 'ss') stays single-codepoint safe
    assert _segment_globs_intersect("[\u00df]", "\u00df") is True
    assert _segment_globs_intersect("[\u00df]", "[!\u00df]") is False

    # End-to-end quiescence check fails closed on adversarial wildcard negation
    ws = _setup_quiescent_workspace(tmp_path / "ws_unicode_neg")
    base_rules = "stable_c/\n**/runtime/worktrees/\n**/runtime/logs/\n**/*.lock\n*.key\n.env*\n"
    neg_line = f"!{punctured_negation}.lock"
    (ws / ".gitignore").write_text(
        f"{base_rules}{neg_line}\n",
        encoding="utf-8",
        newline="\n",
    )
    rep = verify_workspace_git_quiescence(ws)
    assert rep.passed is False
    assert rep.checks["git_hygiene"] is False
    assert any(neg_line in v for v in rep.violations)


def test_segment_globs_intersect_unicode_inverse_case_discontinuities() -> None:
    # Latin small letter long s (U+017F) case-folds to 's'/'S'
    assert _segment_globs_intersect("S", "[\u0170-\u0180]") is True
    assert _segment_globs_intersect("[\u0170-\u0180]", "S") is True

    # Interior-to-interior range intersection via U+017F <-> 's'
    assert _segment_globs_intersect("[r-t]", "[\u0170-\u0180]") is True
    assert _segment_globs_intersect("[\u0170-\u0180]", "[r-t]") is True

    # Kelvin sign (U+212A) case-folds to 'k'/'K'
    assert _segment_globs_intersect("K", "[\u2120-\u2130]") is True
    assert _segment_globs_intersect("[\u2120-\u2130]", "K") is True

    # Negated class excludes S when U+017F is covered, and includes S when stopped at U+017E
    assert _segment_globs_intersect("[!\u0170-\u0180]", "S") is False
    assert _segment_globs_intersect("S", "[!\u0170-\u0180]") is False
    assert _segment_globs_intersect("[!\u0170-\u017e]", "S") is True
    assert _segment_globs_intersect("S", "[!\u0170-\u017e]") is True


def test_unicode_elementary_interval_partition_constancy_invariant() -> None:
    surrogate_start = 0xD800
    surrogate_end = 0xE000  # 2,048 excluded surrogate code points (0xD800..0xDFFF)
    slash_cp = ord("/")  # 0x2F excluded path separator
    expected_valid_count = 0x110000 - (surrogate_end - surrogate_start) - 1
    assert expected_valid_count == 1_112_063

    # Precompute _case_key across the entire Unicode scalar domain 0x0000..0x10FFFF once
    cp_keys: list[str | None] = [None] * 0x110000
    valid_keys: list[str] = []
    checked_count = 0
    for cp in range(0x110000):
        if cp == slash_cp or surrogate_start <= cp < surrogate_end:
            continue
        ch = chr(cp)
        cf = ch.casefold()
        key = cf if len(cf) == 1 else ch.lower()
        cp_keys[cp] = key
        valid_keys.append(key)
        checked_count += 1
    assert checked_count == expected_valid_count
    assert len(valid_keys) == expected_valid_count

    for probe_cp in (ord("S"), ord("K"), 0x00DF, 0x017F, 0x212A, 0x10400, 0x10428):
        assert cp_keys[probe_cp] == _case_key(chr(probe_cp))

    def _ref_single_token_keys(pat: str) -> tuple[bool, frozenset[str]]:
        if pat.startswith("[") and pat.endswith("]"):
            inner = pat[1:-1]
            negated = inner.startswith(("!", "^"))
            if negated:
                inner = inner[1:]
            keys: set[str] = set()
            idx = 0
            while idx < len(inner):
                if idx + 2 < len(inner) and inner[idx + 1] == "-":
                    lo_ord = ord(inner[idx])
                    hi_ord = ord(inner[idx + 2])
                    for c in range(lo_ord, hi_ord + 1):
                        key = cp_keys[c]
                        if key is not None:
                            keys.add(key)
                    idx += 3
                else:
                    key = cp_keys[ord(inner[idx])]
                    if key is not None:
                        keys.add(key)
                    idx += 1
            return negated, frozenset(keys)
        key = cp_keys[ord(pat)]
        return False, frozenset({key}) if key is not None else frozenset()

    def _ref_pattern_tokens(pat: str) -> list[tuple[bool, frozenset[str]]]:
        tokens: list[tuple[bool, frozenset[str]]] = []
        idx = 0
        while idx < len(pat):
            if pat[idx] == "[":
                end = pat.index("]", idx + 1)
                tokens.append(_ref_single_token_keys(pat[idx : end + 1]))
                idx = end + 1
            else:
                tokens.append(_ref_single_token_keys(pat[idx]))
                idx += 1
        return tokens

    # Verify elementary-interval partition constancy across EVERY valid code point in [0, 0x110000)
    pattern_pairs: tuple[tuple[str, str], ...] = (
        ("S[\u0170-\u0180][!S]", "[a-z][\u2120-\u2130][!\u0170-\u0180]"),
        ("[\u0370-\u03ff]K[!\u0170-\u0180]", "[!S][\u2120-\u2130][a-z]"),
        ("\U00010400[!\U00010420-\U00010430]", "[\U00010420-\U00010430][!\U00010400]"),
    )
    for pat_a, pat_b in pattern_pairs:
        boundaries = (
            {0, slash_cp, slash_cp + 1, surrogate_start, surrogate_end, 0x110000}
            | _collect_pattern_boundaries(pat_a)
            | _collect_pattern_boundaries(pat_b)
        )
        sorted_b = sorted(b for b in boundaries if 0 <= b <= 0x110000)

        intervals: list[tuple[int, int]] = [
            (b_i, b_next)
            for b_i, b_next in zip(sorted_b, sorted_b[1:], strict=False)
            if b_i < b_next and b_i != slash_cp and not (surrogate_start <= b_i < surrogate_end)
        ]
        rep_alphabet = frozenset(chr(b_i) for b_i, _ in intervals)
        prod_tokens = [
            *_tokenize_segment_glob(pat_a, rep_alphabet),
            *_tokenize_segment_glob(pat_b, rep_alphabet),
        ]
        ref_tokens = [*_ref_pattern_tokens(pat_a), *_ref_pattern_tokens(pat_b)]
        assert len(prod_tokens) == len(ref_tokens)

        checked_interval_cps = 0
        for b_i, b_next in intervals:
            rep_ch = chr(b_i)
            rep_key = cp_keys[b_i]
            assert rep_key is not None
            prod_sig = tuple(rep_ch in chars for _, chars in prod_tokens)
            ref_sig = tuple(
                (rep_key not in ks) if neg else (rep_key in ks) for neg, ks in ref_tokens
            )
            assert prod_sig == ref_sig, f"Production/reference mismatch at {b_i:#x}"

            interval_slice = cp_keys[b_i:b_next]
            checked_interval_cps += len(interval_slice)
            interval_keys = set(interval_slice)
            msg = f"Mismatch in [{b_i:#x}, {b_next:#x})"
            assert None not in interval_keys, f"Excluded cp in [{b_i:#x}, {b_next:#x})"
            for _, key_set in ref_tokens:
                if rep_key in key_set:
                    assert interval_keys <= key_set, msg
                else:
                    assert interval_keys.isdisjoint(key_set), msg
        assert checked_interval_cps == expected_valid_count

    def _ref_intersects_full_unicode(pat_a: str, pat_b: str) -> bool:
        assert len(valid_keys) == expected_valid_count
        neg_a, keys_a = _ref_single_token_keys(pat_a)
        neg_b, keys_b = _ref_single_token_keys(pat_b)
        if not neg_a and not neg_b:
            target = keys_a & keys_b
            return any(key in target for key in valid_keys)
        if neg_a and not neg_b:
            target = keys_b - keys_a
            return any(key in target for key in valid_keys)
        if not neg_a and neg_b:
            target = keys_a - keys_b
            return any(key in target for key in valid_keys)
        excluded = keys_a | keys_b
        return any(key not in excluded for key in valid_keys)

    reference_pairs: tuple[tuple[str, str], ...] = (
        ("S", "[\u0170-\u0180]"),
        ("[\u0170-\u0180]", "S"),
        ("[r-t]", "[\u0170-\u0180]"),
        ("[\u0170-\u0180]", "[r-t]"),
        ("K", "[\u2120-\u2130]"),
        ("[\u2120-\u2130]", "K"),
        ("[!\u0170-\u0180]", "S"),
        ("[!\u0170-\u017e]", "S"),
        ("\U00010400", "[\U00010420-\U00010430]"),
        ("[\U00010420-\U00010430]", "\U00010400"),
        ("[!\U00010420-\U00010430]", "\U00010400"),
        ("[!\U00010420-\U00010427]", "\U00010400"),
    )
    for pat_a, pat_b in reference_pairs:
        expected = _ref_intersects_full_unicode(pat_a, pat_b)
        assert _segment_globs_intersect(pat_a, pat_b) is expected
    assert _segment_globs_intersect("\U00010400", "[\U00010420-\U00010430]") is True
    assert _segment_globs_intersect("[!\U00010420-\U00010430]", "\U00010400") is False
