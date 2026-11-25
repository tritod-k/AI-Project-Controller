"""Milestone D D03: 2-project E2E, fault-injection, FINAL-05 isolation, closeout & Git restore."""

from __future__ import annotations

import errno
import hashlib
import importlib
import json
import os
import pkgutil
import stat
import subprocess
import types
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml

import aipc
from aipc.controller.errors import ControllerError
from aipc.controller.milestone_authorization import (
    MilestoneAuthorizationError,
    sign_approval_record_hmac,
    verify_approval_record_hmac,
)
from aipc.controller.repair_budget import DurableRepairBudgetLedger
from aipc.controller.task_queue import (
    _MILESTONE_C5_BASELINE_SHA,
    ApprovedTaskContract,
    MilestoneAuthorizationEvidence,
    PersistentTaskQueue,
    QueuedTaskStatus,
    TaskAttemptLedger,
    persist_milestone_authorization_evidence,
    reconcile_queue_and_budget_consistency,
)
from aipc.dashboard.multi_project import (
    discover_and_aggregate_projects,
    render_multi_project_html,
)
from aipc.project.isolation_and_closeout import (
    IsolationLevelDisclosure,
    IsolationPolicyError,
    _collect_sealed_archive_records,
    build_minimal_docker_verify_argv,
    compute_sealed_archive_manifest,
    export_release_closeout_zip,
    verify_and_restore_closeout_zip,
    verify_sealed_archive_untouched,
    write_sealed_archive_baseline_manifest,
)
from aipc.project.scaffolding import (
    initialize_project_runtime,
    render_current_status_md,
    snapshot_approved_plan,
)
from aipc.review.governance_policy import (
    PolicyTrustContext,
    default_review_governance_policy,
    load_review_governance_policy,
)


def _setup_trusted_ledger_and_queue(
    *,
    project_id: str,
    layout_root: Path,
    state_dir: Path,
    trusted_root: Path,
    excluded_roots: tuple[Path, ...],
) -> tuple[PersistentTaskQueue, DurableRepairBudgetLedger, Any]:
    """Initialize a genuine policy-bound DurableRepairBudgetLedger and PersistentTaskQueue."""
    approval_id = f"app-{project_id}"
    policy_model = default_review_governance_policy(owner="K.TRITOD")
    policy_path = trusted_root / f"{project_id}_governance.yaml"
    policy_path.parent.mkdir(parents=True, exist_ok=True)
    policy_text = yaml.safe_dump(policy_model.model_dump(mode="json"), sort_keys=False)
    policy_bytes = policy_text.replace("\r\n", "\n").encode("utf-8")
    policy_path.write_bytes(policy_bytes)
    policy_sha = hashlib.sha256(policy_bytes).hexdigest()

    approval_path = trusted_root / f"{project_id}_approval.json"
    evidence = MilestoneAuthorizationEvidence(
        approval_id=approval_id,
        project_id=project_id,
        milestone_id="milestone-c5",
        decision="APPROVED",
        authorized_by="K.TRITOD",
        baseline_commit_sha=_MILESTONE_C5_BASELINE_SHA,
        authorized_workstreams=("rg01", "rg02", "rg03", "rg04"),
    )
    persist_milestone_authorization_evidence(approval_path, evidence)

    trust_ctx = PolicyTrustContext(
        policy_path=policy_path.resolve(),
        expected_sha256=policy_sha,
        trusted_external_root=trusted_root.resolve(),
        excluded_roots=tuple(r.resolve() for r in excluded_roots),
        approval_path=approval_path.resolve(),
        expected_approval_id=approval_id,
        expected_owner="K.TRITOD",
        expected_project_id=project_id,
        expected_milestone_id="milestone-c5",
    )
    loaded_policy = load_review_governance_policy(trust_ctx)

    task_id = f"task-{project_id}-01"
    queue_path = state_dir / "canonical_queue.json"
    ledger_path = state_dir / "repair_budget_ledger.json"

    ledger = DurableRepairBudgetLedger(ledger_path)
    ledger.initialize(
        loaded_policy=loaded_policy,
        registered_roots=[task_id],
        trusted_external_root=layout_root.resolve(),
        excluded_roots=tuple(r.resolve() for r in excluded_roots),
    )

    queue = PersistentTaskQueue(queue_path, repair_budget_path=ledger_path)
    contract = ApprovedTaskContract(
        task_id=task_id,
        phase_id="phase-01",
        milestone_id="phase-24",
        title=f"Implement {project_id} module",
        objective=f"Verify {project_id} end-to-end execution",
        in_scope=("src/app.py",),
        validation_commands=("pytest -q",),
    )
    queue.initialize(
        project_id=project_id,
        milestone_id="phase-24",
        contracts=(contract,),
        approval_id=approval_id,
    )
    return queue, ledger, loaded_policy


def _snapshot_tree_hashes(root: Path) -> dict[str, tuple[str, int]]:
    """Read-only snapshot of file SHA-256 and mtime_ns under root."""
    out: dict[str, tuple[str, int]] = {}
    for current_dir, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for fname in sorted(filenames):
            fpath = Path(current_dir) / fname
            st = os.lstat(fpath)
            digest = hashlib.sha256(fpath.read_bytes()).hexdigest()
            out[fpath.relative_to(root).as_posix()] = (digest, int(st.st_mtime_ns))
    return out


def _git_run(cwd: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_CONFIG_SYSTEM=os.devnull,
        GIT_TERMINAL_PROMPT="0",
        GIT_AUTHOR_NAME="AIPC Test",
        GIT_AUTHOR_EMAIL="test@example.invalid",
        GIT_COMMITTER_NAME="AIPC Test",
        GIT_COMMITTER_EMAIL="test@example.invalid",
    )
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        env=env,
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )


def _find_verify_workspace_git_quiescence() -> Any | None:
    """Locate verify_workspace_git_quiescence from aipc package if exported."""
    for mod_info in pkgutil.walk_packages(aipc.__path__, prefix="aipc."):
        try:
            mod = importlib.import_module(mod_info.name)
        except Exception:
            continue
        fn = getattr(mod, "verify_workspace_git_quiescence", None)
        if callable(fn):
            return fn
    return None


def test_a_two_project_interleaved_e2e_and_readonly_dashboard(tmp_path: Path) -> None:
    """(a) Simultaneous/interleaved 2-project E2E with HMAC, queue+ledger, status, dashboard."""
    projects_root = tmp_path / "projects"
    candidate_root = tmp_path / "candidate"
    stable_root = tmp_path / "stable"
    trusted_root = tmp_path / "trusted_external"
    operator_dir = tmp_path / "operator_keys"
    for d in (projects_root, candidate_root, stable_root, trusted_root, operator_dir):
        d.mkdir(parents=True, exist_ok=True)

    hmac_key_path = operator_dir / "operator_hmac.key"
    hmac_key_path.write_bytes(b"a" * 32)

    layouts = {}
    queues = {}
    ledgers = {}

    for pid in ("project-alpha", "project-beta"):
        layout = initialize_project_runtime(
            projects_root=projects_root,
            project_id=pid,
            candidate_root=candidate_root,
            stable_root=stable_root,
        )
        layouts[pid] = layout

        plan_src = trusted_root / f"{pid}_plan.md"
        plan_src.write_text(f"# Master Plan for {pid}\n\n- Step 1\n", encoding="utf-8")
        snap = snapshot_approved_plan(
            layout.runtime_root,
            plan_version="plan-v0001",
            source_plan_path=plan_src,
            approved_by="K.TRITOD",
            project_id=pid,
            milestone_id="milestone-d",
        )
        assert snap.master_plan_path.is_file()
        assert snap.manifest_sha256_path.is_file()

        approval_payload = {
            "approval_id": f"app-{pid}",
            "commander_id": "K.TRITOD",
            "project_id": pid,
            "milestone_id": "milestone-d",
            "approved_scope": {"workstreams": ["d01", "d02", "d03"]},
            "budget_limits": {"max_repairs": 2},
            "expires_at": (datetime.now(UTC) + timedelta(days=7)).isoformat(),
            "revision": 1,
        }
        signed = sign_approval_record_hmac(
            approval_payload,
            hmac_key_path,
            forbidden_roots=(candidate_root, projects_root),
        )
        assert verify_approval_record_hmac(
            signed,
            hmac_key_path,
            forbidden_roots=(candidate_root, projects_root),
        )
        # Reject key inside workspace/projects_root
        bad_key = candidate_root / "leak.key"
        bad_key.write_bytes(b"b" * 32)
        with pytest.raises(MilestoneAuthorizationError):
            verify_approval_record_hmac(signed, bad_key, forbidden_roots=(candidate_root,))

        approval_file = layout.audit_dir / "approvals" / f"app-{pid}.json"
        approval_file.write_text(json.dumps(signed, indent=2) + "\n", encoding="utf-8")

        raw_ev = layout.audit_dir / "evidence_raw.log"
        raw_bytes = f"1 passed for {pid}\n".encode()
        raw_ev.write_bytes(raw_bytes)
        raw_sha = hashlib.sha256(raw_bytes).hexdigest()
        receipt_file = layout.receipt_dir / f"rcpt-{pid}.json"
        receipt_file.write_text(
            json.dumps(
                {
                    "receipt_id": f"rcpt-{pid}",
                    "raw_path": "runtime/audit/evidence_raw.log",
                    "raw_sha256": raw_sha,
                    "review_verdict": "PASS",
                    "model_eligibility": {"gemini-3.8-flash-medium": "QUALIFIED"},
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

        q, ldg, _ = _setup_trusted_ledger_and_queue(
            project_id=pid,
            layout_root=layout.project_root,
            state_dir=layout.state_dir,
            trusted_root=trusted_root,
            excluded_roots=(candidate_root, stable_root),
        )
        queues[pid] = q
        ledgers[pid] = ldg

    # Interleaved Queue + Ledger reserve / dispatch / finalize / complete across alpha & beta
    op_alpha = "op-alpha-01"
    op_beta = "op-beta-01"
    res_alpha = ledgers["project-alpha"].reserve_attempt_budget(
        task_id="task-project-alpha-01",
        operation_id=op_alpha,
        attempt_id="att-01-task-project-alpha-01",
    )
    res_beta = ledgers["project-beta"].reserve_attempt_budget(
        task_id="task-project-beta-01",
        operation_id=op_beta,
        attempt_id="att-01-task-project-beta-01",
    )
    assert res_alpha.status == "RESERVED"
    assert res_beta.status == "RESERVED"

    queues["project-alpha"].begin_dispatch(
        "task-project-alpha-01",
        worker_id="w-alpha",
        provider="mock",
        operation_id=op_alpha,
    )
    queues["project-beta"].begin_dispatch(
        "task-project-beta-01",
        worker_id="w-beta",
        provider="mock",
        operation_id=op_beta,
    )

    ledgers["project-alpha"].finalize_attempt_budget(operation_id=op_alpha)
    ledgers["project-beta"].finalize_attempt_budget(operation_id=op_beta)

    rec_alpha = reconcile_queue_and_budget_consistency(
        queues["project-alpha"], ledgers["project-alpha"]
    )
    assert rec_alpha.status == "CONSISTENT"
    rec_beta = reconcile_queue_and_budget_consistency(
        queues["project-beta"], ledgers["project-beta"]
    )
    assert rec_beta.status == "CONSISTENT"

    queues["project-alpha"].record_attempt_outcome(
        "task-project-alpha-01",
        candidate_sha="a" * 40,
        checkpoint_id="chk-alpha-01",
        evidence_root=str(layouts["project-alpha"].receipt_dir),
        review_id="rev-alpha-01",
        review_verdict="PASS",
        gate_verdict="PASS",
    )
    queues["project-beta"].record_attempt_outcome(
        "task-project-beta-01",
        candidate_sha="b" * 40,
        checkpoint_id="chk-beta-01",
        evidence_root=str(layouts["project-beta"].receipt_dir),
        review_id="rev-beta-01",
        review_verdict="PASS",
        gate_verdict="PASS",
    )

    for pid, layout in layouts.items():
        status_md = render_current_status_md(
            layout.project_root,
            queue_path=layout.queue_path,
            budget_ledger_path=layout.budget_ledger_path,
            approvals_dir=layout.audit_dir / "approvals",
            receipts_dir=layout.receipt_dir,
        )
        assert status_md.is_file()
        status_text = status_md.read_text(encoding="utf-8")
        assert "DERIVED / NON-AUTHORITATIVE" in status_text
        assert f"task-{pid}-01" in status_text
        assert f"app-{pid}.json" in status_text
        assert f"rcpt-{pid}.json" in status_text

    # Zero-mutation check around read-only multi-project dashboard aggregation
    before_tree = _snapshot_tree_hashes(projects_root)
    dash = discover_and_aggregate_projects(projects_root, verify_evidence=True)
    html_out = render_multi_project_html(dash)
    after_tree = _snapshot_tree_hashes(projects_root)

    assert before_tree == after_tree
    assert dash.read_only is True
    assert dash.total_projects == 2
    by_id = {p.project_id: p for p in dash.projects}
    assert set(by_id) == {"project-alpha", "project-beta"}
    assert by_id["project-alpha"].queue.completed == 1
    assert by_id["project-beta"].queue.completed == 1
    assert by_id["project-alpha"].evidence_items[0].state == "PRESENT_VERIFIED"
    assert by_id["project-beta"].evidence_items[0].state == "PRESENT_VERIFIED"
    assert "project-alpha" in html_out
    assert "project-beta" in html_out
    # Zero cross-project leakage in CURRENT_STATUS.md
    alpha_md = (layouts["project-alpha"].project_root / "CURRENT_STATUS.md").read_text("utf-8")
    beta_md = (layouts["project-beta"].project_root / "CURRENT_STATUS.md").read_text("utf-8")
    assert "project-beta" not in alpha_md
    assert "project-alpha" not in beta_md


def test_b_queue_ledger_crash_fault_injection(tmp_path: Path) -> None:
    """(b) Orphan RESERVED release, IN_PROGRESS+RESERVED halt, mismatched operation_id halt."""
    projects_root = tmp_path / "projects"
    candidate_root = tmp_path / "candidate"
    stable_root = tmp_path / "stable"
    trusted_root = tmp_path / "trusted"
    for d in (projects_root, candidate_root, stable_root, trusted_root):
        d.mkdir(parents=True, exist_ok=True)

    # 1. Orphan RESERVED release with zero budget leak
    layout1 = initialize_project_runtime(
        projects_root=projects_root,
        project_id="fault-orphan",
        candidate_root=candidate_root,
        stable_root=stable_root,
    )
    q1, ldg1, _ = _setup_trusted_ledger_and_queue(
        project_id="fault-orphan",
        layout_root=layout1.project_root,
        state_dir=layout1.state_dir,
        trusted_root=trusted_root,
        excluded_roots=(candidate_root, stable_root),
    )
    res1 = ldg1.reserve_attempt_budget(
        task_id="task-fault-orphan-01",
        operation_id="op-orphan-01",
        attempt_id="att-01-task-fault-orphan-01",
    )
    assert ldg1.load().milestone_consumed == 1
    rec1 = reconcile_queue_and_budget_consistency(q1, ldg1)
    assert rec1.status == "RECONCILED_ORPHANS"
    assert res1.reservation_id in rec1.released_reservation_ids
    assert ldg1.load().milestone_consumed == 0
    assert ldg1.load().registered_roots["task-fault-orphan-01"].consumed == 0

    # 2. IN_PROGRESS (RUNNING) + RESERVED ambiguous crash -> RECONCILIATION_REQUIRED
    layout2 = initialize_project_runtime(
        projects_root=projects_root,
        project_id="fault-reserved",
        candidate_root=candidate_root,
        stable_root=stable_root,
    )
    q2, ldg2, _ = _setup_trusted_ledger_and_queue(
        project_id="fault-reserved",
        layout_root=layout2.project_root,
        state_dir=layout2.state_dir,
        trusted_root=trusted_root,
        excluded_roots=(candidate_root, stable_root),
    )
    ldg2.reserve_attempt_budget(
        task_id="task-fault-reserved-01",
        operation_id="op-ambig-01",
        attempt_id="att-01-task-fault-reserved-01",
    )
    q2.begin_dispatch(
        "task-fault-reserved-01",
        worker_id="w-2",
        provider="mock",
        operation_id="op-ambig-01",
    )
    rec2 = reconcile_queue_and_budget_consistency(q2, ldg2)
    assert rec2.status == "RECONCILIATION_REQUIRED"
    assert q2.load().tasks[0].status == QueuedTaskStatus.RECONCILIATION_REQUIRED

    # 3. IN_PROGRESS (RUNNING) + mismatched operation_id -> RECONCILIATION_REQUIRED
    layout3 = initialize_project_runtime(
        projects_root=projects_root,
        project_id="fault-mismatch",
        candidate_root=candidate_root,
        stable_root=stable_root,
    )
    q3, ldg3, _ = _setup_trusted_ledger_and_queue(
        project_id="fault-mismatch",
        layout_root=layout3.project_root,
        state_dir=layout3.state_dir,
        trusted_root=trusted_root,
        excluded_roots=(candidate_root, stable_root),
    )
    ldg3.reserve_attempt_budget(
        task_id="task-fault-mismatch-01",
        operation_id="op-ledger-a",
        attempt_id="att-01-task-fault-mismatch-01",
    )
    ldg3.finalize_attempt_budget(operation_id="op-ledger-a")
    with pytest.raises(ControllerError):
        q3.begin_dispatch(
            "task-fault-mismatch-01",
            worker_id="w-3",
            provider="mock",
            operation_id="op-queue-b",
        )
    snap3 = q3.load()
    crashed_attempt = TaskAttemptLedger(
        attempt=1,
        attempt_id="att-01-task-fault-mismatch-01",
        operation_id="op-queue-b",
        worker_id="w-3",
        provider="mock",
        dispatch_stage="DISPATCHED",
        started_at=datetime.now(UTC),
    )
    crashed_task = snap3.tasks[0].model_copy(
        update={
            "status": QueuedTaskStatus.RUNNING,
            "current_attempt": 1,
            "active_attempt_id": "att-01-task-fault-mismatch-01",
            "operation_id": "op-queue-b",
            "attempts": (crashed_attempt,),
        }
    )
    q3._replace_task(snap3, crashed_task)
    rec3 = reconcile_queue_and_budget_consistency(q3, ldg3)
    assert rec3.status == "RECONCILIATION_REQUIRED"
    assert q3.load().tasks[0].status == QueuedTaskStatus.RECONCILIATION_REQUIRED


def test_c_final05_minimal_docker_argv_and_level_c_disclosure(tmp_path: Path) -> None:
    """(c) FINAL-05 minimal Docker argv positive, forbidden mounts/network, Level C disclosure."""
    workspace_root = tmp_path / "workspace_root"
    candidate = tmp_path / "candidate_wt"
    harness = tmp_path / "trusted_harness"
    sibling_proj = tmp_path / "projects" / "sibling"
    for d in (workspace_root, candidate, harness, sibling_proj):
        d.mkdir(parents=True, exist_ok=True)

    argv, disclosure = build_minimal_docker_verify_argv(
        image="aipc-cisl-verify:py313-v1",
        candidate_worktree=candidate,
        command=["pytest", "-q"],
        trusted_harness_dir=harness,
        network_mode="none",
        read_only_root=True,
        forbidden_mount_roots=(workspace_root, sibling_proj),
    )
    assert isinstance(argv, tuple)
    assert isinstance(disclosure, IsolationLevelDisclosure)
    assert "--network" in argv and argv[argv.index("--network") + 1] == "none"
    assert "--read-only" in argv
    assert f"{candidate.resolve()}:/workspace:ro" in argv
    assert f"{harness.resolve()}:/aipc_harness:ro" in argv
    assert "Windows" in disclosure.level_c_host_os_disclosure
    assert "agy.exe" in disclosure.level_c_host_os_disclosure
    assert "codex.cmd" in disclosure.level_c_host_os_disclosure

    # Non-none network fails closed
    with pytest.raises(IsolationPolicyError):
        build_minimal_docker_verify_argv(
            image="aipc-cisl-verify:py313-v1",
            candidate_worktree=candidate,
            command=["pytest"],
            network_mode="bridge",
        )

    # read_only_root=False fails closed (FINAL-05)
    with pytest.raises(IsolationPolicyError, match="read_only_root=True"):
        build_minimal_docker_verify_argv(
            image="aipc-cisl-verify:py313-v1",
            candidate_worktree=candidate,
            command=["pytest"],
            read_only_root=False,
        )

    # Forbidden mount roots (workspace root & sibling project)
    with pytest.raises(IsolationPolicyError):
        build_minimal_docker_verify_argv(
            image="aipc-cisl-verify:py313-v1",
            candidate_worktree=workspace_root,
            command=["pytest"],
            forbidden_mount_roots=(workspace_root,),
        )
    with pytest.raises(IsolationPolicyError):
        build_minimal_docker_verify_argv(
            image="aipc-cisl-verify:py313-v1",
            candidate_worktree=candidate,
            trusted_harness_dir=sibling_proj,
            command=["pytest"],
            forbidden_mount_roots=(sibling_proj,),
        )

    # Forbidden state/, audit/approvals/, ledger, queue, *.key, .aipc_operator (incl. .git/)
    for rel in (
        "state",
        "audit/approvals",
        "repair_budget_ledger.json",
        "canonical_queue.json",
        "secret.key",
        ".aipc_operator",
        ".git/leak.key",
        ".git/.aipc_operator",
        ".git/hooks/state",
    ):
        bad_dir = tmp_path / f"bad_{rel.replace('/', '_')}"
        bad_dir.mkdir(parents=True, exist_ok=True)
        target = bad_dir / rel
        if rel.endswith((".json", ".key", ".aipc_operator")):
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("x\n", encoding="utf-8")
        else:
            target.mkdir(parents=True, exist_ok=True)
        with pytest.raises(IsolationPolicyError):
            build_minimal_docker_verify_argv(
                image="aipc-cisl-verify:py313-v1",
                candidate_worktree=bad_dir,
                command=["pytest"],
            )


def test_d_deterministic_closeout_zip_export_tamper_and_restore(tmp_path: Path) -> None:
    """(d) Deterministic byte-identical closeout zip, exclusions, tamper detection, restore."""
    rt = tmp_path / "project" / "runtime"
    (rt / "audit" / "receipts").mkdir(parents=True)
    (rt / "state").mkdir(parents=True)
    (rt / "plans" / "approved" / "v1").mkdir(parents=True)
    (rt / "worktrees" / "wt-1").mkdir(parents=True)
    (rt / "logs").mkdir(parents=True)

    (rt / "audit" / "receipts" / "r1.json").write_text('{"ok": true}\n', encoding="utf-8")
    (rt / "state" / "project_state.json").write_text('{"status": "COMPLETED"}\n', encoding="utf-8")
    (rt / "plans" / "approved" / "v1" / "MASTER_PLAN.md").write_text("# Plan\n", encoding="utf-8")
    # Excluded entries
    (rt / "worktrees" / "wt-1" / "scratch.txt").write_text("skip\n", encoding="utf-8")
    (rt / "logs" / "run.log").write_text("skip\n", encoding="utf-8")
    (rt / "state" / "controller.lock").write_text("skip\n", encoding="utf-8")
    (rt / "audit" / "private.key").write_text("skip\n", encoding="utf-8")
    (rt / "audit" / ".env.local").write_text("skip\n", encoding="utf-8")

    zip1 = tmp_path / "out1" / "closeout.zip"
    zip2 = tmp_path / "out2" / "closeout.zip"
    rec1 = export_release_closeout_zip(rt, zip1)
    rec2 = export_release_closeout_zip(rt, zip2)

    assert zip1.read_bytes() == zip2.read_bytes()
    assert Path(f"{zip1}.sha256").read_bytes() == Path(f"{zip2}.sha256").read_bytes()
    assert b"\r\n" not in Path(f"{zip1}.sha256").read_bytes()
    assert rec1.zip_sha256 == rec2.zip_sha256
    assert set(rec1.included_files) == {
        "audit/receipts/r1.json",
        "plans/approved/v1/MASTER_PLAN.md",
        "state/project_state.json",
    }

    restore_dir = tmp_path / "restored"
    restored = verify_and_restore_closeout_zip(zip1, restore_dir, expected_sha256=rec1.zip_sha256)
    assert restored.restored_file_count == 3
    restored_state = (restore_dir / "state" / "project_state.json").read_text("utf-8")
    assert restored_state == '{"status": "COMPLETED"}\n'

    # Tamper detection on zip
    tampered_zip = tmp_path / "tampered" / "closeout.zip"
    tampered_zip.parent.mkdir(parents=True)
    tampered_zip.write_bytes(zip1.read_bytes() + b"tamper")
    Path(f"{tampered_zip}.sha256").write_text(
        Path(f"{zip1}.sha256").read_text("utf-8"), encoding="utf-8"
    )
    with pytest.raises(IsolationPolicyError):
        verify_and_restore_closeout_zip(tampered_zip, tmp_path / "bad_restore")

    # Zip-slip rejection
    evil_zip = tmp_path / "evil" / "evil.zip"
    evil_zip.parent.mkdir(parents=True)
    with zipfile.ZipFile(evil_zip, mode="w") as zf:
        zf.writestr("../escape.txt", b"bad")
    evil_sha = hashlib.sha256(evil_zip.read_bytes()).hexdigest()
    Path(f"{evil_zip}.sha256").write_text(f"{evil_sha}  evil.zip\n", encoding="utf-8")
    with pytest.raises(IsolationPolicyError):
        verify_and_restore_closeout_zip(evil_zip, tmp_path / "evil_restore")


def test_e_sealed_archive_untouched_pass_and_tamper_detection(tmp_path: Path) -> None:
    """(e) Sealed archive untouched pass and tamper detection without mutating archive_root."""
    archive = tmp_path / "sealed_archive"
    (archive / "sub").mkdir(parents=True)
    (archive / "receipt.json").write_text('{"id": "r1"}\n', encoding="utf-8")
    (archive / "sub" / "log.txt").write_text("clean\n", encoding="utf-8")

    manifest_path = tmp_path / "baseline_manifest.json"
    write_sealed_archive_baseline_manifest(archive, manifest_path)

    before = _snapshot_tree_hashes(archive)
    report_ok = verify_sealed_archive_untouched(archive, manifest_path)
    after = _snapshot_tree_hashes(archive)

    assert before == after
    assert report_ok.passed is True
    assert report_ok.expected_digest == report_ok.observed_digest
    assert report_ok.expected_file_count == 2
    assert report_ok.observed_file_count == 2

    # Tamper file in archive
    (archive / "receipt.json").write_text('{"id": "r1-tampered"}\n', encoding="utf-8")
    report_bad = verify_sealed_archive_untouched(archive, manifest_path)
    assert report_bad.passed is False
    assert report_bad.expected_digest != report_bad.observed_digest


def test_e2_sealed_archive_same_size_mtime_preserved_tamper_and_dest_containment(
    tmp_path: Path,
) -> None:
    """(e2) Same-size 1-byte tamper with preserved mtime fails; dest inside archive rejected."""
    archive = tmp_path / "sealed_archive"
    (archive / "sub").mkdir(parents=True)
    target_file = archive / "sub" / "payload.txt"
    target_file.write_bytes(b"abcdef1234\n")

    # Baseline manifest destination inside archive_root must fail closed without creating dirs/files
    bad_manifest = archive / "nested_bad" / "baseline_manifest.json"
    with pytest.raises(IsolationPolicyError):
        write_sealed_archive_baseline_manifest(archive, bad_manifest)
    assert not bad_manifest.exists()
    assert not bad_manifest.parent.exists()

    manifest_path = tmp_path / "baseline_manifest.json"
    payload = write_sealed_archive_baseline_manifest(archive, manifest_path)
    assert payload["hash_mode"] == "scandir_lstat_content_sha256"

    report_ok = verify_sealed_archive_untouched(archive, manifest_path)
    assert report_ok.passed is True
    assert report_ok.hash_mode == "scandir_lstat_content_sha256"

    # Modify 1 byte in place (exact same byte size) and restore exact st_mtime_ns
    orig_st = os.lstat(target_file)
    target_file.write_bytes(b"Zbcdef1234\n")
    os.utime(target_file, ns=(orig_st.st_atime_ns, orig_st.st_mtime_ns))

    assert os.lstat(target_file).st_size == orig_st.st_size
    assert os.lstat(target_file).st_mtime_ns == orig_st.st_mtime_ns
    report_tampered = verify_sealed_archive_untouched(archive, manifest_path)
    assert report_tampered.passed is False
    assert report_tampered.expected_digest != report_tampered.observed_digest


@pytest.mark.parametrize("error_kind", ["permission", "eio_winerror_1920"])
def test_e3_sealed_archive_regular_file_oserror_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    error_kind: str,
) -> None:
    """(e3) Any OSError reading a regular file must fail closed with IsolationPolicyError."""
    archive = tmp_path / "sealed_archive"
    (archive / "sub").mkdir(parents=True)
    target_file = archive / "sub" / "payload.txt"
    target_file.write_bytes(b"plain dummy payload\n")

    manifest_path = tmp_path / "baseline_manifest.json"
    write_sealed_archive_baseline_manifest(archive, manifest_path)

    orig_open = Path.open
    target_resolved = target_file.resolve()

    def _failing_open(self: Path, mode: str = "r", *args: Any, **kwargs: Any) -> Any:
        if "b" in mode and self.resolve(strict=False) == target_resolved:
            if error_kind == "permission":
                raise PermissionError(errno.EACCES, "simulated permission denied")
            err = OSError(errno.EIO, "simulated unreadable regular file")
            err.winerror = 1920
            raise err
        return orig_open(self, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", _failing_open)

    with pytest.raises(IsolationPolicyError, match="Cannot read archive file"):
        compute_sealed_archive_manifest(archive)

    with pytest.raises(IsolationPolicyError, match="Cannot read archive file"):
        verify_sealed_archive_untouched(archive, manifest_path)


def test_e4_sealed_archive_reparse_point_and_wsl_symlink_handling(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """(e4) Reparse points and WSL symlinks hash metadata deterministically without file open."""
    archive = tmp_path / "sealed_archive"
    archive.mkdir(parents=True)
    reg_file = archive / "regular.txt"
    reg_file.write_bytes(b"regular bytes\n")
    wsl_link = archive / "wsl_symlink"
    wsl_link.write_bytes(b"")
    std_link = archive / "std_symlink"
    std_link.write_bytes(b"")

    orig_lstat = os.lstat
    orig_readlink = os.readlink
    orig_open = Path.open

    wsl_path_str = os.path.normcase(os.path.abspath(wsl_link.resolve()))
    std_path_str = os.path.normcase(os.path.abspath(std_link.resolve()))
    link_target = ["target_a"]
    reparse_tag_box = [0xA000001D]
    fail_lstat = [False]

    def _fake_lstat(path: Path | str, *args: Any, **kwargs: Any) -> Any:
        p_str = os.path.normcase(os.path.abspath(path))
        if p_str == wsl_path_str:
            if fail_lstat[0]:
                raise OSError(errno.EIO, "simulated lstat failure")
            return types.SimpleNamespace(
                st_mode=stat.S_IFREG | 0o644,
                st_size=0,
                st_file_attributes=getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400),
                st_reparse_tag=reparse_tag_box[0],
            )
        if p_str == std_path_str:
            return types.SimpleNamespace(
                st_mode=stat.S_IFLNK | 0o777,
                st_size=len(link_target[0]),
                st_file_attributes=0,
                st_reparse_tag=0,
            )
        return orig_lstat(path, *args, **kwargs)

    def _fake_readlink(path: Path | str, *args: Any, **kwargs: Any) -> str:
        p_str = os.path.normcase(os.path.abspath(path))
        if p_str == wsl_path_str:
            raise OSError(errno.EINVAL, "cannot readlink wsl reparse point")
        if p_str == std_path_str:
            return link_target[0]
        return orig_readlink(path, *args, **kwargs)

    def _guarded_open(self: Path, mode: str = "r", *args: Any, **kwargs: Any) -> Any:
        assert self.name not in {"wsl_symlink", "std_symlink"}
        return orig_open(self, mode, *args, **kwargs)

    monkeypatch.setattr(os, "lstat", _fake_lstat)
    monkeypatch.setattr(os, "readlink", _fake_readlink)
    monkeypatch.setattr(Path, "open", _guarded_open)

    records = _collect_sealed_archive_records(archive.resolve())
    by_name = {rec.relpath: rec for rec in records}
    assert set(by_name) == {"regular.txt", "std_symlink", "wsl_symlink"}

    assert by_name["regular.txt"].entry_kind == "regular_file"
    assert by_name["regular.txt"].content_sha256 == hashlib.sha256(b"regular bytes\n").hexdigest()
    assert by_name["regular.txt"].metadata_sha256 is None

    for link_name in ("wsl_symlink", "std_symlink"):
        assert by_name[link_name].entry_kind == "reparse_point"
        assert by_name[link_name].content_sha256 is None
        assert by_name[link_name].metadata_sha256 is not None
        assert len(by_name[link_name].metadata_sha256) == 64

    manifest_path = tmp_path / "baseline_manifest.json"
    write_sealed_archive_baseline_manifest(archive, manifest_path)
    report_ok = verify_sealed_archive_untouched(archive, manifest_path)
    assert report_ok.passed is True
    assert report_ok.observed_file_count == 3

    link_target[0] = "target_b"
    assert verify_sealed_archive_untouched(archive, manifest_path).passed is False

    link_target[0] = "target_a"
    reparse_tag_box[0] = 0xA0000003
    assert verify_sealed_archive_untouched(archive, manifest_path).passed is False

    fail_lstat[0] = True
    with pytest.raises(IsolationPolicyError, match="Cannot lstat archive entry"):
        compute_sealed_archive_manifest(archive)


def test_e5_sealed_archive_reparse_point_readlink_valueerror(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """(e5) Windows Python 3.13 ValueError from os.readlink on WSL reparse point falls back."""
    archive = tmp_path / "sealed_archive"
    archive.mkdir(parents=True)
    reg_file = archive / "regular.txt"
    reg_file.write_bytes(b"regular bytes\n")
    wsl_link = archive / "wsl_lx_symlink"
    wsl_link.write_bytes(b"")

    orig_lstat = os.lstat
    orig_readlink = os.readlink
    orig_open = Path.open

    wsl_path_str = os.path.normcase(os.path.abspath(wsl_link.resolve()))
    reparse_attr = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    reparse_tag = 0xA000001D
    st_mode = stat.S_IFREG | 0o644

    def _fake_lstat(path: Path | str, *args: Any, **kwargs: Any) -> Any:
        p_str = os.path.normcase(os.path.abspath(path))
        if p_str == wsl_path_str:
            return types.SimpleNamespace(
                st_mode=st_mode,
                st_size=0,
                st_file_attributes=reparse_attr,
                st_reparse_tag=reparse_tag,
            )
        return orig_lstat(path, *args, **kwargs)

    def _fake_readlink(path: Path | str, *args: Any, **kwargs: Any) -> str:
        p_str = os.path.normcase(os.path.abspath(path))
        if p_str == wsl_path_str:
            raise ValueError("not a symbolic link")
        return orig_readlink(path, *args, **kwargs)

    def _guarded_open(self: Path, mode: str = "r", *args: Any, **kwargs: Any) -> Any:
        assert self.name != "wsl_lx_symlink"
        return orig_open(self, mode, *args, **kwargs)

    monkeypatch.setattr(os, "lstat", _fake_lstat)
    monkeypatch.setattr(os, "readlink", _fake_readlink)
    monkeypatch.setattr(Path, "open", _guarded_open)

    records = _collect_sealed_archive_records(archive.resolve())
    by_name = {rec.relpath: rec for rec in records}
    assert set(by_name) == {"regular.txt", "wsl_lx_symlink"}

    expected_payload = (
        f"reparse_tag={reparse_tag}|file_attributes={reparse_attr}|st_size=0|st_mode={st_mode}"
    )
    expected_meta_sha = hashlib.sha256(expected_payload.encode("utf-8")).hexdigest()

    wsl_rec = by_name["wsl_lx_symlink"]
    assert wsl_rec.entry_kind == "reparse_point"
    assert wsl_rec.content_sha256 is None
    assert wsl_rec.metadata_sha256 == expected_meta_sha

    manifest_1 = compute_sealed_archive_manifest(archive)
    manifest_2 = compute_sealed_archive_manifest(archive)
    assert manifest_1 == manifest_2

    manifest_path = tmp_path / "baseline_manifest.json"
    write_sealed_archive_baseline_manifest(archive, manifest_path)
    report_ok = verify_sealed_archive_untouched(archive, manifest_path)
    assert report_ok.passed is True
    assert report_ok.observed_file_count == 2
    assert report_ok.expected_digest == report_ok.observed_digest


def test_f_clean_git_checkout_restore_lf_and_quiescence(tmp_path: Path) -> None:
    """(f) Clean Git checkout restore with .gitattributes eol=lf, .gitignore, receipts+.sha256."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git_run(repo, ["init"])
    _git_run(repo, ["config", "core.autocrlf", "false"])

    (repo / ".gitattributes").write_bytes(b"* text=auto eol=lf\n*.json text eol=lf\n")
    (repo / ".gitignore").write_bytes(b"*.key\n.env\n.env*\n*.lock\nlogs/\nstable_c/\nworktrees/\n")

    receipt_bytes = b'{\n  "receipt": "canonical",\n  "status": "PASS"\n}\n'
    receipt_path = repo / "canonical_receipt.json"
    receipt_path.write_bytes(receipt_bytes)
    receipt_sha = hashlib.sha256(receipt_bytes).hexdigest()
    sha_bytes = f"{receipt_sha}  canonical_receipt.json\n".encode()
    (repo / "canonical_receipt.json.sha256").write_bytes(sha_bytes)

    _git_run(repo, ["add", "."])
    _git_run(repo, ["commit", "-m", "Add canonical receipts with LF"])

    quiescence_fn = _find_verify_workspace_git_quiescence()
    if quiescence_fn is not None:
        q_res = quiescence_fn(repo)
        assert (
            q_res is True
            or getattr(q_res, "passed", None) is True
            or getattr(q_res, "quiescent", None) is True
            or getattr(q_res, "status", None) == "PASS"
        )
    else:
        status_proc = _git_run(repo, ["status", "--porcelain=v1"])
        assert status_proc.stdout.strip() == ""

    cloned = tmp_path / "cloned_repo"
    _git_run(tmp_path, ["clone", str(repo), str(cloned)])

    cloned_receipt = (cloned / "canonical_receipt.json").read_bytes()
    cloned_sha_file = (cloned / "canonical_receipt.json.sha256").read_bytes()
    assert b"\r\n" not in cloned_receipt
    assert b"\r\n" not in cloned_sha_file
    assert hashlib.sha256(cloned_receipt).hexdigest() == receipt_sha
    assert cloned_sha_file.decode("utf-8").split()[0] == receipt_sha


def test_g_docker_verify_argv_rejects_option_shaped_or_invalid_image_refs(tmp_path: Path) -> None:
    """(g) Strict Docker image reference validation rejects option injection and invalid refs."""
    candidate = tmp_path / "candidate_wt"
    candidate.mkdir(parents=True, exist_ok=True)

    for bad_image in (
        "--privileged",
        "-v",
        "--network=host",
        "-e=FOO=BAR",
        "img --privileged",
        " aipc-cisl-verify:py313-v1",
        "aipc-cisl-verify:py313-v1\n",
        "img@sha256:" + "a" * 63,
    ):
        with pytest.raises(IsolationPolicyError):
            build_minimal_docker_verify_argv(
                image=bad_image,
                candidate_worktree=candidate,
                command=["pytest", "-q"],
            )

    for valid_image in (
        "aipc-cisl-verify:py313-v1",
        "ghcr.io/org/img:v1.0@sha256:" + "a" * 64,
    ):
        argv, _disclosure = build_minimal_docker_verify_argv(
            image=valid_image,
            candidate_worktree=candidate,
            command=["pytest", "-q"],
        )
        assert valid_image in argv
