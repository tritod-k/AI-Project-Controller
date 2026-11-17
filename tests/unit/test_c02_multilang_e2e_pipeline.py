"""Unit tests for Workstream C02: Multi-Language Isolated Verification & E2E Pipeline."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

import aipc.controller as controller_pkg
from aipc.agents.role_registry import RoleModelBinding
from aipc.controller.errors import ControllerError
from aipc.controller.milestone_b_orchestrator import WorkerAttemptExecutionOutcome
from aipc.controller.milestone_c_pipeline import (
    ContainerResourceLimits,
    MilestoneCEndToEndPipeline,
    MultiLanguageIsolatedVerifier,
    MultiLanguageVerificationReceipt,
    PRReadinessBundle,
)
from aipc.controller.self_dev_runner import VERIFICATION_ISOLATION_BLOCKED
from aipc.controller.task_queue import (
    ApprovedTaskContract,
    MilestoneAuthorizationEvidence,
    PersistentTaskQueue,
    QueuedTaskStatus,
    persist_milestone_authorization_evidence,
)
from aipc.domain.enums import AgentStatus, ChangeType, CriterionStatus, ReviewVerdict
from aipc.domain.models import (
    AgentResult,
    CommandResult,
    CriterionResult,
    EvidenceReferences,
    FileChange,
    ReviewResult,
    TaskRequest,
    TestSummary,
)
from aipc.evidence.models import (
    ArtifactRecord,
    EvidenceBundle,
    EvidenceItem,
    GitEvidence,
    TestEvidence,
)
from aipc.hardening.errors import SecretExposureError
from aipc.project.models import AcceptanceCriterion, PhaseDefinition
from aipc.project.state_models import ProjectState, ProjectStatus
from aipc.project.universal_profiles import OnboardedProjectBundle, onboard_universal_project
from aipc.review.context import review_provenance
from aipc.review.gate import PhaseGate
from aipc.review.models import (
    BoundReview,
    CriterionRule,
    PhaseGateDecision,
    PhaseReviewStage,
    ReviewContext,
)
from aipc.review.reviewer import bind_review
from aipc.review.subscription_reviewer_gate import (
    SessionIsolationAuditReceipt,
    SubscriptionReviewerGateDecision,
)
from aipc.version.models import CheckpointMetadata, CheckpointType, GitWorkspace

_SHA_CANDIDATE = "a" * 40
_SHA256_DUMMY = "0" * 64


def _make_phases() -> tuple[PhaseDefinition, ...]:
    return (
        PhaseDefinition(
            id="phase-01",
            name="Verification",
            objective="Verify project candidate in isolation",
            acceptance_criteria=(
                AcceptanceCriterion(
                    id="AC-01",
                    statement="All tests pass in isolated verification",
                    required=True,
                ),
            ),
        ),
    )


def _git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        check=True,
    )
    return proc.stdout.strip()


def _commit_all_clean(ws: Path, message: str = "init") -> str:
    _git(ws, "config", "user.name", "AIPC Test")
    _git(ws, "config", "user.email", "aipc-test@example.com")
    _git(ws, "add", ".")
    _git(ws, "commit", "-m", message)
    return _git(ws, "rev-parse", "HEAD")


def _candidate_workspace(bundle: OnboardedProjectBundle) -> GitWorkspace:
    ws = Path(bundle.workspace_root)
    return GitWorkspace(
        root=str(ws),
        common_dir=_git(ws, "rev-parse", "--path-format=absolute", "--git-common-dir"),
        head=_git(ws, "rev-parse", "HEAD"),
        branch=_git(ws, "rev-parse", "--abbrev-ref", "HEAD"),
        status=b"",
    )


def _make_onboarded_python_project(
    tmp_path: Path,
    *,
    with_git_file: bool = False,
) -> tuple[OnboardedProjectBundle, str]:
    ws = tmp_path / "py-repo"
    if with_git_file:
        origin = tmp_path / "py-repo-origin"
        origin.mkdir(parents=True, exist_ok=True)
        _git(origin, "init")
        (origin / "README.md").write_text("origin\n", encoding="utf-8")
        _commit_all_clean(origin, "origin init")
        _git(origin, "worktree", "add", "-b", "cand", str(ws))
    else:
        ws.mkdir(parents=True, exist_ok=True)
        _git(ws, "init")

    (ws / "pyproject.toml").write_text("[project]\nname='demo-py'\n", encoding="utf-8")
    (ws / "tests").mkdir(exist_ok=True)
    (ws / "tests" / "test_ok.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    head_sha = _commit_all_clean(ws, "add python project")
    bundle = onboard_universal_project(
        workspace=ws,
        project_id="demo-py",
        project_name="Demo Python",
        phases=_make_phases(),
    )
    return bundle, head_sha


def _make_onboarded_node_project(tmp_path: Path) -> tuple[OnboardedProjectBundle, str]:
    ws = tmp_path / "node-repo"
    ws.mkdir(parents=True, exist_ok=True)
    _git(ws, "init")
    (ws / "package.json").write_text(
        '{"name":"demo-node","scripts":{"test":"npm test"}}',
        encoding="utf-8",
    )
    head_sha = _commit_all_clean(ws, "add node project")
    bundle = onboard_universal_project(
        workspace=ws,
        project_id="demo-node",
        project_name="Demo Node",
        phases=_make_phases(),
    )
    return bundle, head_sha


def _make_onboarded_gas_project(tmp_path: Path) -> tuple[OnboardedProjectBundle, str]:
    ws = tmp_path / "gas-repo"
    ws.mkdir(parents=True, exist_ok=True)
    _git(ws, "init")
    (ws / "appsscript.json").write_text('{"timeZone":"UTC"}', encoding="utf-8")
    (ws / "Code.gs").write_text("function doGet() { return 'ok'; }\n", encoding="utf-8")
    head_sha = _commit_all_clean(ws, "add gas project")
    bundle = onboard_universal_project(
        workspace=ws,
        project_id="demo-gas",
        project_name="Demo GAS",
        phases=_make_phases(),
    )
    return bundle, head_sha


def _make_onboarded_static_project(tmp_path: Path) -> tuple[OnboardedProjectBundle, str]:
    ws = tmp_path / "static-repo"
    ws.mkdir(parents=True, exist_ok=True)
    _git(ws, "init")
    (ws / "index.html").write_text("<html><body>hello</body></html>\n", encoding="utf-8")
    head_sha = _commit_all_clean(ws, "add static project")
    bundle = onboard_universal_project(
        workspace=ws,
        project_id="demo-static",
        project_name="Demo Static",
        phases=_make_phases(),
    )
    return bundle, head_sha


def _make_review_context(bundle: OnboardedProjectBundle, candidate_sha: str) -> ReviewContext:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    pid = bundle.project_definition.project.id
    task_id = "task-01"
    phase_id = "phase-01"
    ws_path = Path(bundle.workspace_root).resolve()
    ev_root = ws_path.parent / f"{ws_path.name}_aipc_test_evidence"
    ev_root.mkdir(parents=True, exist_ok=True)
    state = ProjectState(
        schema_version="1.0",
        project_id=pid,
        run_id="run-01",
        current_phase=phase_id,
        current_attempt=1,
        status=ProjectStatus.RUNNING,
        completed_phases=(),
        blocked_phases=(),
        active_task_id=task_id,
        last_review=None,
        updated_at=now,
    )
    task = TaskRequest(
        schema_version="1.0",
        task_id=task_id,
        project_id=pid,
        phase_id=phase_id,
        attempt=1,
        task_type="implementation",
        prompt="Implement and verify feature",
        workspace=bundle.workspace_root,
        allowed_tools=["read_file", "write_file"],
        forbidden_actions=["live_deploy"],
        timeout_seconds=120,
    )
    agent_result = AgentResult(
        schema_version="1.0",
        task_id=task_id,
        agent_id="worker-01",
        provider="google_antigravity",
        status=AgentStatus.COMPLETED,
        started_at=now,
        completed_at=now,
        summary="Completed task",
        files_changed=[],
        commands=[],
        tests=TestSummary(total=1, passed=1, failed=0, skipped=0),
        artifacts=[],
        warnings=[],
        errors=[],
        approval_requests=[],
        worker_claimed_complete=True,
        evidence=EvidenceReferences(
            git_diff_path="git_diff.patch",
            test_log_path="test.log",
            terminal_log_path="terminal.log",
            screenshots=[],
        ),
        raw_output_ref=None,
    )
    required_ids = (
        ("task", "task.json"),
        ("agent_result", "agent_result.json"),
        ("commands", "commands.json"),
        ("artifact_index", "artifact_index.json"),
        ("git_status", "git_status.txt"),
        ("git_diff", "git_diff.patch"),
        ("git_summary", "git_summary.txt"),
        ("test_log", "test.log"),
        ("terminal_log", "terminal.log"),
        ("raw_output", "raw_output.txt"),
    )
    file_payloads: dict[str, bytes] = {
        "task.json": task.model_dump_json(indent=2).encode("utf-8"),
        "agent_result.json": agent_result.model_dump_json(indent=2).encode("utf-8"),
        "commands.json": json.dumps({"source": "agent_result", "commands": []}, indent=2).encode(
            "utf-8"
        ),
        "git_status.txt": b"",
        "git_diff.patch": b"",
        "git_summary.txt": b"clean\n",
        "test.log": b"1 passed in 0.01s\n",
        "terminal.log": b"pytest -q\n",
        "raw_output.txt": b"1 passed in 0.01s\n",
    }
    artifacts_list: list[ArtifactRecord] = []
    for eid, rel_path in required_ids:
        if eid == "artifact_index":
            continue
        data = file_payloads[rel_path]
        (ev_root / rel_path).write_bytes(data)
        artifacts_list.append(
            ArtifactRecord(
                artifact_id=f"art-{eid}",
                path=rel_path,
                kind=eid,
                size=len(data),
                sha256=hashlib.sha256(data).hexdigest(),
                source="test",
            )
        )
    index_bytes = json.dumps(
        {"artifacts": [a.model_dump(mode="json") for a in artifacts_list]}, indent=2
    ).encode("utf-8")
    (ev_root / "artifact_index.json").write_bytes(index_bytes)
    artifacts_list.append(
        ArtifactRecord(
            artifact_id="art-artifact_index",
            path="artifact_index.json",
            kind="artifact_index",
            size=len(index_bytes),
            sha256=hashlib.sha256(index_bytes).hexdigest(),
            source="test",
        )
    )
    items = tuple(
        EvidenceItem(evidence_id=eid, kind=eid, status="collected", path=path, source="test")
        for eid, path in required_ids
    )
    evidence = EvidenceBundle(
        project_id=pid,
        run_id="run-01",
        phase_id=phase_id,
        attempt=1,
        task_id=task_id,
        status="COMPLETE",
        git=GitEvidence(
            status="collected",
            head=candidate_sha,
            baseline=candidate_sha,
            branch="main",
            changes=(),
        ),
        tests=TestEvidence(
            parse_status="collected",
            log_ref="test.log",
            structured=TestSummary(total=1, passed=1, failed=0, skipped=0),
            parsed=TestSummary(total=1, passed=1, failed=0, skipped=0),
            agrees=True,
        ),
        commands=(),
        items=items,
        artifacts=tuple(artifacts_list),
    )
    (ev_root / "manifest.json").write_bytes(evidence.model_dump_json(indent=2).encode("utf-8"))
    checkpoint = CheckpointMetadata(
        checkpoint_id="phase-phase-01-attempt-01",
        checkpoint_type=CheckpointType.ATTEMPT,
        project_id=pid,
        phase_id=phase_id,
        attempt=1,
        git_commit=candidate_sha,
        git_ref=f"refs/tags/aipc/{pid}/phase-phase-01/attempt-01",
        source_branch="main",
        created_at=now,
        parent_checkpoint="phase-phase-01-pre",
    )
    return ReviewContext(
        project=bundle.project_definition,
        state=state,
        task=task,
        agent_result=agent_result,
        evidence=evidence,
        evidence_root=str(ev_root),
        checkpoint=checkpoint,
        phase_stage=PhaseReviewStage.AWAITING_REVIEW,
        rules=(CriterionRule(criterion_id="AC-01", kind="tests", minimum_passed=1),),
    )


def _make_gate_decision(
    *,
    bundle: OnboardedProjectBundle,
    candidate_sha: str,
    verdict: ReviewVerdict,
    gate_outcome: str,
    review_ctx: ReviewContext | None = None,
) -> SubscriptionReviewerGateDecision:
    ctx = review_ctx or _make_review_context(bundle, candidate_sha)
    review_result = ReviewResult(
        schema_version="1.0",
        review_id="rev-01",
        task_id="task-01",
        verdict=verdict,
        confidence=0.95,
        criteria=[
            CriterionResult(
                criterion_id="AC-01",
                status=(
                    CriterionStatus.PASS if verdict == ReviewVerdict.PASS else CriterionStatus.FAIL
                ),
                evidence=["test.log"],
                notes="Checked",
            )
        ],
        failed_criteria=[] if verdict == ReviewVerdict.PASS else ["AC-01"],
        required_fixes=[] if verdict == ReviewVerdict.PASS else ["Fix failing check"],
        risks=[],
        next_phase_allowed=(verdict == ReviewVerdict.PASS),
    )
    if gate_outcome == "PASS":
        bound_review: BoundReview | None = bind_review(
            ctx,
            review_result,
            authority="codex-reviewer",
            reviewed=review_provenance(ctx),
        )
        phase_gate: PhaseGateDecision | None = PhaseGate().evaluate(bound_review, ctx)
    else:
        bound_review = None
        phase_gate = None
    session_audit = SessionIsolationAuditReceipt(
        audit_id="audit-01",
        task_id="task-01",
        candidate_commit_sha=candidate_sha,
        worker_agent_id="worker-gemini",
        worker_provider_family="google_antigravity",
        reviewer_binding_id="reviewer-codex",
        reviewer_provider_id="codex",
        reviewer_provider_family="openai_codex",
        reviewer_model_id="gpt-5-codex",
        reviewer_session_id="rev-sess-01",
        fresh_isolated_session=True,
        inherited_chat_history=False,
        read_only_sandbox_enforced=True,
        workspace_snapshot_before_sha256=_SHA256_DUMMY,
        workspace_snapshot_after_sha256=_SHA256_DUMMY,
        workspace_mutated=False,
        independence_level="cross_provider",
        reduced_independence=False,
    )
    return SubscriptionReviewerGateDecision(
        gate_outcome=gate_outcome,  # type: ignore[arg-type]
        task_id="task-01",
        candidate_commit_sha=candidate_sha,
        reviewed_commit_sha=candidate_sha if gate_outcome == "PASS" else None,
        review_result=review_result,
        bound_review=bound_review,
        phase_gate_decision=phase_gate,
        session_audit=session_audit,
        receipt_path=str(Path(bundle.workspace_root) / "review_receipt.json"),
        reasons=() if gate_outcome == "PASS" else ("Fix failing check",),
    )


def test_lazy_controller_exports() -> None:
    for name in (
        "ContainerResourceLimits",
        "MilestoneCEndToEndPipeline",
        "MultiLanguageIsolatedVerifier",
        "MultiLanguageIsolationMode",
        "MultiLanguageVerificationReceipt",
        "PRReadinessBundle",
    ):
        assert hasattr(controller_pkg, name)
        assert name in controller_pkg.__all__


def test_python_container_command_and_execution(tmp_path: Path) -> None:
    bundle, head_sha = _make_onboarded_python_project(tmp_path, with_git_file=True)
    verifier = MultiLanguageIsolatedVerifier()
    captured_argv: list[str] = []

    def fake_runner(argv, **kwargs):
        captured_argv.extend(argv)
        return subprocess.CompletedProcess(
            args=argv,
            returncode=0,
            stdout="5 passed in 0.42s\n",
            stderr="",
        )

    receipt = verifier.verify_project_candidate(
        bundle=bundle,
        candidate_sha=head_sha,
        isolation_mode="container",
        container_runtime_bin="docker",
        runner=fake_runner,
    )

    assert receipt.aggregate_checks_passed is True
    assert receipt.isolation_mode == "container"
    assert receipt.container_argv is not None
    assert isinstance(receipt.resource_limits, ContainerResourceLimits)
    assert receipt.tool_versions == {
        "isolation_mode": "container",
        "stack_type": "python_cli_or_package",
        "commands_count": "2",
    }
    assert "--rm" in captured_argv
    assert "--network" in captured_argv
    assert captured_argv[captured_argv.index("--network") + 1] == "none"
    assert "--read-only" in captured_argv
    assert "--user" in captured_argv
    assert captured_argv[captured_argv.index("--user") + 1] == "65534:65534"
    assert "--cap-drop" in captured_argv
    assert captured_argv[captured_argv.index("--cap-drop") + 1] == "ALL"
    assert "--security-opt" in captured_argv
    assert "no-new-privileges" in captured_argv
    assert "--pids-limit" in captured_argv
    assert "--memory" in captured_argv
    assert any(arg.endswith(":/workspace:ro") for arg in captured_argv)
    assert any(arg.endswith(":/workspace-out:rw") for arg in captured_argv)
    assert "/dev/null:/workspace/.git:ro" in captured_argv
    assert receipt.tests_summary == TestSummary(total=5, passed=5, failed=0, skipped=0)


def test_node_test_output_parsing_and_isolated_verification(tmp_path: Path) -> None:
    bundle, head_sha = _make_onboarded_node_project(tmp_path)
    verifier = MultiLanguageIsolatedVerifier()

    jest_summary = verifier.parse_multilang_test_output(
        "node_javascript_typescript",
        "Test Suites: 1 passed, 1 total\nTests: 4 passed, 0 failed, 4 total\n",
        exit_code=0,
    )
    assert jest_summary == TestSummary(total=4, passed=4, failed=0, skipped=0)

    mocha_summary = verifier.parse_multilang_test_output(
        "node_javascript_typescript",
        "  3 passing (12ms)\n",
        exit_code=0,
    )
    assert mocha_summary == TestSummary(total=3, passed=3, failed=0, skipped=0)

    node_tap_summary = verifier.parse_multilang_test_output(
        "node_javascript_typescript",
        "# tests 2\n# pass 2\n# fail 0\n",
        exit_code=0,
    )
    assert node_tap_summary == TestSummary(total=2, passed=2, failed=0, skipped=0)

    def fake_node_runner(argv, **kwargs):
        return subprocess.CompletedProcess(
            args=argv,
            returncode=0,
            stdout="Tests: 4 passed, 0 failed, 4 total\n",
            stderr="",
        )

    rejected = verifier.verify_project_candidate(
        bundle=bundle,
        candidate_sha=head_sha,
        isolation_mode="isolated_subprocess",
        runner=fake_node_runner,
    )
    assert rejected.passed is False
    assert rejected.failure_reason == "UNSANDBOXED_HOST_SUBPROCESS_REJECTED"

    receipt = verifier.verify_project_candidate(
        bundle=bundle,
        candidate_sha=head_sha,
        isolation_mode="container",
        container_runtime_bin="docker",
        runner=fake_node_runner,
    )
    assert receipt.aggregate_checks_passed is True
    assert receipt.stack_type == "node_javascript_typescript"
    assert receipt.tool_versions["commands_count"] == "3"
    assert receipt.tests_summary == TestSummary(total=4, passed=4, failed=0, skipped=0)


def test_static_web_and_gas_offline_mock_verification(tmp_path: Path) -> None:
    static_bundle, static_sha = _make_onboarded_static_project(tmp_path)
    gas_bundle, gas_sha = _make_onboarded_gas_project(tmp_path)
    verifier = MultiLanguageIsolatedVerifier()

    def ok_runner(argv, **kwargs):
        return subprocess.CompletedProcess(
            args=argv,
            returncode=0,
            stdout="2 passing\n",
            stderr="",
        )

    gas_rejected = verifier.verify_project_candidate(
        bundle=gas_bundle,
        candidate_sha=gas_sha,
        isolation_mode="isolated_subprocess",
        runner=ok_runner,
    )
    assert gas_rejected.passed is False
    assert gas_rejected.failure_reason == "UNSANDBOXED_HOST_SUBPROCESS_REJECTED"

    static_receipt = verifier.verify_project_candidate(
        bundle=static_bundle,
        candidate_sha=static_sha,
        isolation_mode="container",
        container_runtime_bin="docker",
        runner=ok_runner,
    )
    assert static_receipt.aggregate_checks_passed is True
    assert static_receipt.offline_mock_only is False

    gas_receipt = verifier.verify_project_candidate(
        bundle=gas_bundle,
        candidate_sha=gas_sha,
        isolation_mode="container",
        container_runtime_bin="docker",
        runner=ok_runner,
    )
    assert gas_receipt.aggregate_checks_passed is True
    assert gas_receipt.offline_mock_only is True
    assert gas_receipt.live_deployment_or_external_proof_claimed is False
    assert gas_receipt.simulation_disclaimer is not None
    assert "Offline simulation only" in gas_receipt.simulation_disclaimer

    with pytest.raises(ValidationError):
        MultiLanguageVerificationReceipt(
            receipt_id="rcpt-bad-gas",
            project_id="demo-gas",
            stack_type="google_apps_script_offline_mock",
            isolation_mode="container",
            candidate_sha=_SHA_CANDIDATE,
            command_executed=("npm", "test"),
            exit_code=0,
            passed=True,
            tests_summary=TestSummary(total=1, passed=1, failed=0, skipped=0),
            offline_mock_only=False,
            simulation_disclaimer="Offline only",
        )


def test_fail_closed_blocked_missing_runtime_mutation_and_zero_tests(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle, head_sha = _make_onboarded_python_project(tmp_path)
    verifier = MultiLanguageIsolatedVerifier()

    blocked_receipt = verifier.verify_project_candidate(
        bundle=bundle,
        candidate_sha=head_sha,
        isolation_mode="blocked",
    )
    assert blocked_receipt.passed is False
    assert blocked_receipt.failure_reason == VERIFICATION_ISOLATION_BLOCKED

    with monkeypatch.context() as runtime_unavailable:
        runtime_unavailable.setattr(
            "aipc.controller.milestone_c_pipeline.shutil.which", lambda _: None
        )
        missing_runtime_receipt = verifier.verify_project_candidate(
            bundle=bundle,
            candidate_sha=head_sha,
            isolation_mode="container",
            container_runtime_bin=None,
        )
    assert missing_runtime_receipt.passed is False
    assert missing_runtime_receipt.failure_reason == "CONTAINER_RUNTIME_OR_IMAGE_UNAVAILABLE"

    def mutating_runner(argv, **kwargs):
        (Path(bundle.workspace_root) / "mutated.txt").write_text("oops", encoding="utf-8")
        return subprocess.CompletedProcess(
            args=argv,
            returncode=0,
            stdout="1 passed in 0.1s\n",
            stderr="",
        )

    mutated_receipt = verifier.verify_project_candidate(
        bundle=bundle,
        candidate_sha=head_sha,
        isolation_mode="isolated_subprocess",
        runner=mutating_runner,
    )
    assert mutated_receipt.workspace_mutated_during_verification is True
    assert mutated_receipt.passed is False
    assert mutated_receipt.failure_reason == "WORKSPACE_MUTATED_DURING_VERIFICATION"
    (Path(bundle.workspace_root) / "mutated.txt").unlink()

    def empty_output_runner(argv, **kwargs):
        return subprocess.CompletedProcess(args=argv, returncode=0, stdout="", stderr="")

    zero_tests_receipt = verifier.verify_project_candidate(
        bundle=bundle,
        candidate_sha=head_sha,
        isolation_mode="isolated_subprocess",
        runner=empty_output_runner,
    )
    assert zero_tests_receipt.passed is False
    assert zero_tests_receipt.tests_summary.failed == 1

    with pytest.raises(ValueError, match="unsafe_host_opt_in"):
        verifier.verify_project_candidate(
            bundle=bundle,
            candidate_sha=head_sha,
            isolation_mode="isolated_subprocess",
            command_override=("python", "-m", "pytest", "--unsafe_host_opt_in"),
        )


def test_receipt_secret_scanning_rejects_leaked_token() -> None:
    fake_val = "s" + "k-" + ("b" * 24)
    with pytest.raises(SecretExposureError):
        MultiLanguageVerificationReceipt(
            receipt_id="rcpt-secret",
            project_id="demo-py",
            stack_type="python_cli_or_package",
            isolation_mode="isolated_subprocess",
            candidate_sha=_SHA_CANDIDATE,
            command_executed=("python", "-m", "pytest"),
            exit_code=0,
            passed=True,
            tests_summary=TestSummary(total=1, passed=1, failed=0, skipped=0),
            failure_reason=fake_val,
        )


def _setup_queue(tmp_path: Path, project_id: str) -> PersistentTaskQueue:
    persist_milestone_authorization_evidence(
        tmp_path / "milestone_c_approval.json",
        MilestoneAuthorizationEvidence(
            approval_id="appr-mc-c02",
            project_id=project_id,
            milestone_id="milestone-c",
            decision="APPROVED",
            authorized_workstreams=("c01", "c02", "c03"),
        ),
    )
    queue = PersistentTaskQueue(tmp_path / "queue.json")
    contract = ApprovedTaskContract(
        task_id="task-01",
        phase_id="phase-01",
        milestone_id="milestone-c",
        title="Verify project candidate",
        objective="Run isolated verification and review",
        in_scope=("src/app.py",),
        validation_commands=("python -m pytest -q",),
        approval_id="appr-mc-c02",
    )
    queue.initialize(project_id=project_id, milestone_id="milestone-c", contracts=(contract,))
    return queue


def _make_worker_binding() -> RoleModelBinding:
    return RoleModelBinding(
        binding_id="worker-gemini",
        role="worker",
        provider_id="gemini-antigravity",
        provider_family="google_antigravity",
        model_id="gemini-3-flash",
        model_tier="fast_worker",
        execution_mode="worker",
        auth_mode="antigravity_subscription",
    )


@pytest.mark.parametrize(
    "bad_boundary",
    [
        "stable_overlap",
        "stable_parent",
        "stable_child",
        "dirty",
        "missing_git",
        "wrong_head",
        "wrong_root",
    ],
)
def test_mc_audit_04_rejects_candidate_before_dispatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bad_boundary: str
) -> None:
    bundle, head = _make_onboarded_python_project(tmp_path)
    workspace = Path(bundle.workspace_root)
    candidate = _candidate_workspace(bundle)
    queue = _setup_queue(tmp_path, bundle.project_definition.project.id)
    if bad_boundary == "wrong_head":
        candidate = candidate.model_copy(update={"head": "f" * 40})
    if bad_boundary == "wrong_root":
        candidate = candidate.model_copy(update={"root": str(tmp_path)})
    if bad_boundary == "dirty":
        (workspace / "untracked.txt").write_text("dirty", encoding="utf-8")
    if bad_boundary == "missing_git":
        real_run = subprocess.run

        def broken_git(argv, **kwargs):
            if argv[:2] == ["git", "rev-parse"]:
                return subprocess.CompletedProcess(argv, 128, "", "Git inspection failed")
            return real_run(argv, **kwargs)

        monkeypatch.setattr(subprocess, "run", broken_git)

    def worker(*args, **kwargs):
        pytest.fail("worker dispatched before validating candidate boundary")

    with pytest.raises(ControllerError, match="CANDIDATE_WORKTREE"):
        MilestoneCEndToEndPipeline().run_e2e_cycle(
            bundle=bundle,
            candidate_workspace=candidate,
            task_queue=queue,
            worker_step=worker,
            worker_binding=_make_worker_binding(),
            review_callback=lambda **kwargs: _make_gate_decision(
                bundle=bundle, candidate_sha=head, verdict=ReviewVerdict.PASS, gate_outcome="PASS"
            ),
            stable_root={
                "stable_overlap": workspace,
                "stable_parent": workspace.parent,
                "stable_child": workspace / "stable",
            }.get(bad_boundary),
        )
    assert queue.load().tasks[0].status == QueuedTaskStatus.ELIGIBLE


def test_mc_audit_05_printed_pytest_summary_is_only_an_observation(tmp_path: Path) -> None:
    bundle, _ = _make_onboarded_python_project(tmp_path)
    bundle = bundle.model_copy(
        update={
            "profile": bundle.profile.model_copy(
                update={
                    "validation_pack": (
                        bundle.profile.validation_pack.model_copy(update={"lint_commands": ()})
                    )
                }
            )
        }
    )
    workspace = Path(bundle.workspace_root)
    (workspace / "tests" / "test_ok.py").unlink()
    (workspace / "spoof.py").write_text("print('1 passed')\n", encoding="utf-8")
    head = _commit_all_clean(workspace, "console output without test execution")
    receipt = MultiLanguageIsolatedVerifier().verify_project_candidate(
        bundle=bundle,
        candidate_sha=head,
        isolation_mode="isolated_subprocess",
        command_override=(sys.executable, "spoof.py"),
    )
    assert receipt.tests_summary.passed == 1
    assert receipt.passed is False
    assert receipt.failure_reason == "TEST_EXECUTION_UNAUTHENTICATED"
    assert re.fullmatch(r"Python \d+\.\d+\.\d+.*", receipt.tool_versions["python"])
    assert re.fullmatch(r"ruff \d+\.\d+\.\d+.*", receipt.tool_versions["ruff"])


@pytest.mark.parametrize("unavailable", [None, "image", "ruff"])
def test_mc_audit_06_container_versions_are_measured_and_missing_tools_block(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, unavailable: str | None
) -> None:
    bundle, head = _make_onboarded_python_project(tmp_path)
    verifier = MultiLanguageIsolatedVerifier()
    calls: list[list[str]] = []
    image_id = "sha256:" + "b" * 64

    def invoke(argv, cwd, env, runner):
        calls.append(argv)
        if argv[1:3] == ["image", "inspect"]:
            return subprocess.CompletedProcess(
                argv, 1 if unavailable == "image" else 0, image_id, ""
            )
        if argv[-1] == "--version":
            tool = argv[-2] if "-m" in argv else "python"
            versions = {"python": "Python 3.13.9", "pytest": "pytest 8.4.2", "ruff": "ruff 0.14.0"}
            return subprocess.CompletedProcess(
                argv, 127 if unavailable == tool else 0, versions[tool], ""
            )
        return subprocess.CompletedProcess(argv, 0, "1 passed", "")

    monkeypatch.setattr(verifier, "_invoke_runner", invoke)
    receipt = verifier.verify_project_candidate(
        bundle=bundle,
        candidate_sha=head,
        isolation_mode="container",
        container_runtime_bin="docker",
    )
    assert receipt.passed is False
    if unavailable is None:
        assert receipt.aggregate_checks_passed
        assert receipt.tool_versions["image_id"] == image_id
        assert receipt.tool_versions["python"] == "Python 3.13.9"
        assert receipt.tool_versions["pytest"] == "pytest 8.4.2"
        assert receipt.tool_versions["ruff"] == "ruff 0.14.0"
        assert all(image_id in argv for argv in calls if argv[1] == "run")
    else:
        assert receipt.failure_reason == (
            "CONTAINER_RUNTIME_OR_IMAGE_UNAVAILABLE"
            if unavailable == "image"
            else "REQUIRED_TOOLCHAIN_UNAVAILABLE"
        )
        assert not any("check" in argv or "-q" in argv for argv in calls)


def test_mc_audit_01_missing_c03_blocks_passing_review(tmp_path: Path) -> None:
    bundle, head_sha = _make_onboarded_python_project(tmp_path)
    queue = _setup_queue(tmp_path, bundle.project_definition.project.id)
    worker_binding = _make_worker_binding()
    review_ctx = _make_review_context(bundle, head_sha)

    def worker_step(binding: RoleModelBinding, attempt: int, repair_plan):
        return WorkerAttemptExecutionOutcome(
            candidate_sha=head_sha,
            verification_passed=True,
            review_context=review_ctx,
        )

    def ok_runner(argv, **kwargs):
        return subprocess.CompletedProcess(
            args=argv, returncode=0, stdout="3 passed in 0.2s\n", stderr=""
        )

    def pass_review(**kwargs):
        return _make_gate_decision(
            bundle=bundle,
            candidate_sha=head_sha,
            verdict=ReviewVerdict.PASS,
            gate_outcome="PASS",
        )

    pipeline = MilestoneCEndToEndPipeline()
    pr_bundle = pipeline.run_e2e_cycle(
        bundle=bundle,
        candidate_workspace=_candidate_workspace(bundle),
        task_queue=queue,
        worker_step=worker_step,
        worker_binding=worker_binding,
        review_callback=pass_review,
        isolation_mode="isolated_subprocess",
        runner=ok_runner,
    )

    assert isinstance(pr_bundle, PRReadinessBundle)
    assert pr_bundle.pr_ready is False
    assert pr_bundle.human_merge_approval_required is True
    assert pr_bundle.auto_merge_performed is False
    assert pr_bundle.next_authorized_action == "HALT_ON_BLOCKER"
    assert pr_bundle.gate_verdict == "BLOCKED"
    assert pr_bundle.review_verdict == ReviewVerdict.PASS
    assert queue.load().tasks[0].status == QueuedTaskStatus.BLOCKED


@pytest.mark.parametrize("criterion_case", ["pass", "fail", "missing"])
def test_mc_audit_01_controller_criteria_gate_real_e2e_handoff(
    tmp_path: Path, criterion_case: str
) -> None:
    from aipc.review.trusted_acceptance_harness import build_trusted_harness_manifest

    bundle, _ = _make_onboarded_python_project(tmp_path)
    # Native lint binaries require container isolation; exercise the real restricted
    # Python runner with a syntax check that stays within its execution boundary.
    bundle = bundle.model_copy(
        update={
            "profile": bundle.profile.model_copy(
                update={
                    "validation_pack": (
                        bundle.profile.validation_pack.model_copy(
                            update={"lint_commands": ("python -m ast src/feature.py",)}
                        )
                    )
                }
            )
        }
    )
    ws = Path(bundle.workspace_root)
    (ws / "src").mkdir()
    (ws / "src" / "feature.py").write_text(
        "def calculate(value):\n    return value * 2\n", encoding="utf-8"
    )
    (ws / "tests" / "trusted").mkdir()
    trusted_rel = "tests/trusted/feature_checks.py"
    expected = 9 if criterion_case == "fail" else 8
    (ws / trusted_rel).write_text(
        "def test_calculation():\n    from feature import calculate\n"
        f"    assert calculate(4) == {expected}\n",
        encoding="utf-8",
    )
    head = _commit_all_clean(ws, "candidate with operator acceptance contract")
    manifest = build_trusted_harness_manifest(
        project_id=bundle.project_definition.project.id,
        milestone_id="milestone-c",
        workspace=ws,
        protected_rel_paths=(trusted_rel,),
        required_criterion_ids=("AC-01",),
        criterion_test_nodes=(
            {} if criterion_case == "missing" else {"AC-01": f"{trusted_rel}::test_calculation"}
        ),
        trusted_manifest_dir=tmp_path / "operator-store",
    )
    queue = _setup_queue(tmp_path / "queue", bundle.project_definition.project.id)
    context = _make_review_context(bundle, head)
    result = MilestoneCEndToEndPipeline().run_e2e_cycle(
        bundle=bundle,
        candidate_workspace=_candidate_workspace(bundle),
        task_queue=queue,
        worker_step=lambda *_: WorkerAttemptExecutionOutcome(
            candidate_sha=head, verification_passed=True, review_context=context
        ),
        worker_binding=_make_worker_binding(),
        review_callback=lambda **_: _make_gate_decision(
            bundle=bundle, candidate_sha=head, verdict=ReviewVerdict.PASS, gate_outcome="PASS"
        ),
        isolation_mode="isolated_subprocess",
        trusted_manifest=manifest,
    )
    assert result.verification_receipt.aggregate_checks_passed
    assert result.review_verdict == ReviewVerdict.PASS
    assert result.verification_receipt.tool_versions["pytest"].startswith("pytest ")
    if criterion_case == "pass":
        assert result.pr_ready is True
        assert result.trusted_acceptance_evaluation_id is not None
        assert result.next_authorized_action == "HUMAN_MERGE_REVIEW"
        assert queue.load().tasks[0].status == QueuedTaskStatus.COMPLETED
    else:
        assert result.pr_ready is False
        assert result.blockers == ("TRUSTED_ACCEPTANCE_UNVERIFIED",)
        assert result.trusted_acceptance_evaluation_id is None
        assert queue.load().tasks[0].status == QueuedTaskStatus.BLOCKED


def test_e2e_pipeline_blocks_on_verification_failure_and_revise_on_review(tmp_path: Path) -> None:
    bundle, head_sha = _make_onboarded_python_project(tmp_path)
    worker_binding = _make_worker_binding()
    review_ctx = _make_review_context(bundle, head_sha)

    def worker_step(binding: RoleModelBinding, attempt: int, repair_plan):
        return WorkerAttemptExecutionOutcome(
            candidate_sha=head_sha,
            verification_passed=True,
            review_context=review_ctx,
        )

    # Case 1: verification fails -> BLOCKED
    queue_fail = _setup_queue(tmp_path / "fail_case", bundle.project_definition.project.id)

    def failing_runner(argv, **kwargs):
        return subprocess.CompletedProcess(
            args=argv, returncode=1, stdout="1 failed, 1 passed in 0.2s\n", stderr=""
        )

    pipeline = MilestoneCEndToEndPipeline()
    blocked_bundle = pipeline.run_e2e_cycle(
        bundle=bundle,
        candidate_workspace=_candidate_workspace(bundle),
        task_queue=queue_fail,
        worker_step=worker_step,
        worker_binding=worker_binding,
        review_callback=lambda **kw: _make_gate_decision(
            bundle=bundle,
            candidate_sha=head_sha,
            verdict=ReviewVerdict.PASS,
            gate_outcome="PASS",
        ),
        isolation_mode="isolated_subprocess",
        runner=failing_runner,
    )
    assert blocked_bundle.pr_ready is False
    assert blocked_bundle.gate_verdict == "BLOCKED"
    assert blocked_bundle.next_authorized_action == "HALT_ON_BLOCKER"
    assert blocked_bundle.auto_merge_performed is False
    assert queue_fail.load().tasks[0].status == QueuedTaskStatus.BLOCKED

    # Case 2: verification passes, review returns REVISE -> DISPATCH_BOUNDED_REPAIR
    queue_revise = _setup_queue(tmp_path / "revise_case", bundle.project_definition.project.id)

    def ok_runner(argv, **kwargs):
        return subprocess.CompletedProcess(
            args=argv, returncode=0, stdout="2 passed in 0.1s\n", stderr=""
        )

    revise_bundle = pipeline.run_e2e_cycle(
        bundle=bundle,
        candidate_workspace=_candidate_workspace(bundle),
        task_queue=queue_revise,
        worker_step=worker_step,
        worker_binding=worker_binding,
        review_callback=lambda **kw: _make_gate_decision(
            bundle=bundle,
            candidate_sha=head_sha,
            verdict=ReviewVerdict.REVISE,
            gate_outcome="REVISE",
        ),
        isolation_mode="isolated_subprocess",
        runner=ok_runner,
    )
    assert revise_bundle.pr_ready is False
    assert revise_bundle.gate_verdict == "REVISE"
    assert revise_bundle.next_authorized_action == "DISPATCH_BOUNDED_REPAIR"
    assert queue_revise.load().tasks[0].status == QueuedTaskStatus.REPAIR_REQUIRED


def test_python_isolated_subprocess_guard_blocks_network_and_outside_writes(
    tmp_path: Path,
) -> None:
    bundle, head_sha = _make_onboarded_python_project(tmp_path)
    no_lint_pack = bundle.profile.validation_pack.model_copy(
        update={"lint_commands": (), "build_commands": ()}
    )
    bundle = bundle.model_copy(
        update={"profile": bundle.profile.model_copy(update={"validation_pack": no_lint_pack})}
    )
    verifier = MultiLanguageIsolatedVerifier()

    net_probe = (
        "import socket\n"
        "try:\n"
        "    socket.create_connection(('127.0.0.1', 1))\n"
        "except PermissionError as exc:\n"
        "    if 'NETWORK_ISOLATION_BLOCKED' in str(exc):\n"
        "        print('1 passed')\n"
        "    else:\n"
        "        raise\n"
    )
    net_receipt = verifier.verify_project_candidate(
        bundle=bundle,
        candidate_sha=head_sha,
        isolation_mode="isolated_subprocess",
        command_override=(sys.executable, "-c", net_probe),
    )
    assert net_receipt.aggregate_checks_passed is True

    udp_probe = (
        "import socket\n"
        "sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)\n"
        "try:\n"
        "    sock.sendto(b'ping', ('127.0.0.1', 1))\n"
        "except PermissionError as exc:\n"
        "    if 'NETWORK_ISOLATION_BLOCKED' in str(exc):\n"
        "        print('1 passed')\n"
        "    else:\n"
        "        raise\n"
        "finally:\n"
        "    sock.close()\n"
    )
    udp_receipt = verifier.verify_project_candidate(
        bundle=bundle,
        candidate_sha=head_sha,
        isolation_mode="isolated_subprocess",
        command_override=(sys.executable, "-c", udp_probe),
    )
    assert udp_receipt.aggregate_checks_passed is True

    outside_file = tmp_path / "outside_escape.txt"
    write_probe = (
        "try:\n"
        f"    with open({str(outside_file)!r}, 'w', encoding='utf-8') as f:\n"
        "        f.write('escape')\n"
        "except PermissionError as exc:\n"
        "    if 'WORKSPACE_WRITE_ISOLATION_BLOCKED' in str(exc):\n"
        "        print('1 passed')\n"
        "    else:\n"
        "        raise\n"
    )
    write_receipt = verifier.verify_project_candidate(
        bundle=bundle,
        candidate_sha=head_sha,
        isolation_mode="isolated_subprocess",
        command_override=(sys.executable, "-c", write_probe),
    )
    assert write_receipt.aggregate_checks_passed is True
    assert not outside_file.exists()

    os_open_probe = (
        "import os\n"
        "try:\n"
        f"    fd = os.open({str(outside_file)!r}, os.O_WRONLY | os.O_CREAT)\n"
        "    os.close(fd)\n"
        "except PermissionError as exc:\n"
        "    if 'WORKSPACE_WRITE_ISOLATION_BLOCKED' in str(exc):\n"
        "        print('1 passed')\n"
        "    else:\n"
        "        raise\n"
    )
    os_open_receipt = verifier.verify_project_candidate(
        bundle=bundle,
        candidate_sha=head_sha,
        isolation_mode="isolated_subprocess",
        command_override=(sys.executable, "-c", os_open_probe),
    )
    assert os_open_receipt.aggregate_checks_passed is True
    assert not outside_file.exists()

    child_probe = (
        "import os, subprocess, sys\n"
        "child_code = (\n"
        "    'import socket\\n'\n"
        "    'try:\\n'\n"
        "    \"    socket.create_connection(('127.0.0.1', 1))\\n\"\n"
        "    'except PermissionError as exc:\\n'\n"
        "    \"    if 'NETWORK_ISOLATION_BLOCKED' in str(exc):\\n\"\n"
        "    \"        print('CHILD_GUARDED')\\n\"\n"
        "    '    else:\\n'\n"
        "    '        raise\\n'\n"
        ")\n"
        "proc = subprocess.run(\n"
        "    [sys.executable, '-c', child_code],\n"
        "    env={'PATH': os.environ.get('PATH', '')},\n"
        "    capture_output=True,\n"
        "    text=True,\n"
        "    check=True,\n"
        ")\n"
        "assert 'CHILD_GUARDED' in proc.stdout\n"
        "print('1 passed')\n"
    )
    child_receipt = verifier.verify_project_candidate(
        bundle=bundle,
        candidate_sha=head_sha,
        isolation_mode="isolated_subprocess",
        command_override=(sys.executable, "-c", child_probe),
    )
    assert child_receipt.aggregate_checks_passed is True

    non_py_child_probe = (
        "import os, subprocess\n"
        "shell_bin = 'cmd' if os.name == 'nt' else 'sh'\n"
        "try:\n"
        "    subprocess.run([shell_bin, '-c', 'echo hi'], check=False)\n"
        "except PermissionError as exc:\n"
        "    if 'SUBPROCESS_SANDBOX_BYPASS_BLOCKED' in str(exc):\n"
        "        print('1 passed')\n"
        "    else:\n"
        "        raise\n"
    )
    non_py_child_receipt = verifier.verify_project_candidate(
        bundle=bundle,
        candidate_sha=head_sha,
        isolation_mode="isolated_subprocess",
        command_override=(sys.executable, "-c", non_py_child_probe),
    )
    assert non_py_child_receipt.aggregate_checks_passed is True

    for bad_cmd in (
        (sys.executable, "-S", "-c", "print('1 passed')"),
        (sys.executable, "-I", "-c", "print('1 passed')"),
        (sys.executable, "-E", "-c", "print('1 passed')"),
        (sys.executable, "--isolated", "-c", "print('1 passed')"),
        ("pytest", "-q"),
    ):
        bad_receipt = verifier.verify_project_candidate(
            bundle=bundle,
            candidate_sha=head_sha,
            isolation_mode="isolated_subprocess",
            command_override=bad_cmd,
        )
        assert bad_receipt.passed is False
        assert bad_receipt.failure_reason == "ISOLATED_SUBPROCESS_COMMAND_GUARD_BYPASS_REJECTED"

    closure_probe = (
        "import sitecustomize\n"
        "assert not hasattr(sitecustomize, '_orig_open')\n"
        "assert not hasattr(sitecustomize, '_orig_os_open')\n"
        "assert not hasattr(sitecustomize, '_orig_popen_init')\n"
        "assert not hasattr(sitecustomize, '_WS_ROOT')\n"
        "print('1 passed')\n"
    )
    closure_receipt = verifier.verify_project_candidate(
        bundle=bundle,
        candidate_sha=head_sha,
        isolation_mode="isolated_subprocess",
        command_override=(sys.executable, "-c", closure_probe),
    )
    assert closure_receipt.aggregate_checks_passed is True

    os_system_probe = (
        "import os\n"
        "try:\n"
        "    os.system('echo hi')\n"
        "except PermissionError:\n"
        "    print('1 passed')\n"
    )
    os_system_receipt = verifier.verify_project_candidate(
        bundle=bundle,
        candidate_sha=head_sha,
        isolation_mode="isolated_subprocess",
        command_override=(sys.executable, "-c", os_system_probe),
    )
    assert os_system_receipt.aggregate_checks_passed is True

    ctypes_probe = (
        "try:\n    import ctypes\nexcept (ImportError, PermissionError):\n    print('1 passed')\n"
    )
    ctypes_receipt = verifier.verify_project_candidate(
        bundle=bundle,
        candidate_sha=head_sha,
        isolation_mode="isolated_subprocess",
        command_override=(sys.executable, "-c", ctypes_probe),
    )
    assert ctypes_receipt.aggregate_checks_passed is True


def test_wrong_head_or_dirty_worktree_and_lint_failure_fail_closed(tmp_path: Path) -> None:
    bundle, head_sha = _make_onboarded_python_project(tmp_path)
    verifier = MultiLanguageIsolatedVerifier()

    wrong_head = verifier.verify_project_candidate(
        bundle=bundle,
        candidate_sha="b" * 40,
        isolation_mode="isolated_subprocess",
    )
    assert wrong_head.passed is False
    assert wrong_head.failure_reason == "CANDIDATE_WORKTREE_HEAD_OR_DIRTY_MISMATCH"

    dirty_path = Path(bundle.workspace_root) / "dirty.txt"
    dirty_path.write_text("uncommitted\n", encoding="utf-8")
    dirty_receipt = verifier.verify_project_candidate(
        bundle=bundle,
        candidate_sha=head_sha,
        isolation_mode="isolated_subprocess",
    )
    assert dirty_receipt.passed is False
    assert dirty_receipt.failure_reason == "CANDIDATE_WORKTREE_HEAD_OR_DIRTY_MISMATCH"
    dirty_path.unlink()

    def lint_fail_runner(argv, **kwargs):
        if "ruff" in argv:
            return subprocess.CompletedProcess(
                args=argv, returncode=1, stdout="", stderr="lint error"
            )
        return subprocess.CompletedProcess(
            args=argv, returncode=0, stdout="2 passed in 0.1s\n", stderr=""
        )

    lint_receipt = verifier.verify_project_candidate(
        bundle=bundle,
        candidate_sha=head_sha,
        isolation_mode="isolated_subprocess",
        runner=lint_fail_runner,
    )
    assert lint_receipt.passed is False
    assert lint_receipt.failure_reason == "VALIDATION_LINT_OR_BUILD_FAILED"


def test_e2e_pipeline_rejects_mismatched_review_provenance(tmp_path: Path) -> None:
    bundle, head_sha = _make_onboarded_python_project(tmp_path)
    worker_binding = _make_worker_binding()
    review_ctx = _make_review_context(bundle, head_sha)
    pipeline = MilestoneCEndToEndPipeline()

    def worker_step(binding: RoleModelBinding, attempt: int, repair_plan):
        return WorkerAttemptExecutionOutcome(
            candidate_sha=head_sha,
            verification_passed=True,
            review_context=review_ctx,
        )

    def ok_runner(argv, **kwargs):
        return subprocess.CompletedProcess(
            args=argv, returncode=0, stdout="2 passed in 0.1s\n", stderr=""
        )

    # Case 1: wrong candidate_commit_sha
    q1 = _setup_queue(tmp_path / "q1", bundle.project_definition.project.id)
    bad_sha_decision = _make_gate_decision(
        bundle=bundle,
        candidate_sha="c" * 40,
        verdict=ReviewVerdict.PASS,
        gate_outcome="PASS",
    )
    res1 = pipeline.run_e2e_cycle(
        bundle=bundle,
        candidate_workspace=_candidate_workspace(bundle),
        task_queue=q1,
        worker_step=worker_step,
        worker_binding=worker_binding,
        review_callback=lambda **kw: bad_sha_decision,
        isolation_mode="isolated_subprocess",
        runner=ok_runner,
    )
    assert res1.pr_ready is False
    assert res1.gate_verdict == "BLOCKED"
    assert res1.blockers == ("REVIEW_PROVENANCE_MISMATCH",)

    # Case 2: wrong task_id
    q2 = _setup_queue(tmp_path / "q2", bundle.project_definition.project.id)
    valid_decision = _make_gate_decision(
        bundle=bundle,
        candidate_sha=head_sha,
        verdict=ReviewVerdict.PASS,
        gate_outcome="PASS",
    )
    bad_task_decision = valid_decision.model_copy(update={"task_id": "other-task"})
    res2 = pipeline.run_e2e_cycle(
        bundle=bundle,
        candidate_workspace=_candidate_workspace(bundle),
        task_queue=q2,
        worker_step=worker_step,
        worker_binding=worker_binding,
        review_callback=lambda **kw: bad_task_decision,
        isolation_mode="isolated_subprocess",
        runner=ok_runner,
    )
    assert res2.pr_ready is False
    assert res2.blockers == ("REVIEW_PROVENANCE_MISMATCH",)

    # Case 3: wrong bound_review.provenance.attempt
    q3 = _setup_queue(tmp_path / "q3", bundle.project_definition.project.id)
    assert valid_decision.bound_review is not None
    bad_prov = valid_decision.bound_review.provenance.model_copy(update={"attempt": 99})
    bad_bound = valid_decision.bound_review.model_copy(update={"provenance": bad_prov})
    bad_attempt_decision = valid_decision.model_copy(update={"bound_review": bad_bound})
    res3 = pipeline.run_e2e_cycle(
        bundle=bundle,
        candidate_workspace=_candidate_workspace(bundle),
        task_queue=q3,
        worker_step=worker_step,
        worker_binding=worker_binding,
        review_callback=lambda **kw: bad_attempt_decision,
        isolation_mode="isolated_subprocess",
        runner=ok_runner,
    )
    assert res3.pr_ready is False
    assert res3.blockers == ("REVIEW_PROVENANCE_MISMATCH",)


def test_e2e_pipeline_validates_reviewer_before_dispatch_and_marks_interrupted(
    tmp_path: Path,
) -> None:
    bundle, head_sha = _make_onboarded_python_project(tmp_path)
    worker_binding = _make_worker_binding()
    review_ctx = _make_review_context(bundle, head_sha)
    pipeline = MilestoneCEndToEndPipeline()

    # Missing reviewer_gate and review_callback must fail BEFORE begin_dispatch
    q_missing = _setup_queue(tmp_path / "q_missing", bundle.project_definition.project.id)
    with pytest.raises(ControllerError, match="Either reviewer_gate or review_callback"):
        pipeline.run_e2e_cycle(
            bundle=bundle,
            candidate_workspace=_candidate_workspace(bundle),
            task_queue=q_missing,
            worker_step=lambda b, a, r: WorkerAttemptExecutionOutcome(
                candidate_sha=head_sha,
                verification_passed=True,
                review_context=review_ctx,
            ),
            worker_binding=worker_binding,
        )
    assert q_missing.load().tasks[0].status == QueuedTaskStatus.ELIGIBLE

    # Exception in worker_step must transition task to INTERRUPTED
    q_worker_err = _setup_queue(tmp_path / "q_worker_err", bundle.project_definition.project.id)

    def crashing_worker(binding: RoleModelBinding, attempt: int, repair_plan):
        raise RuntimeError("worker crashed")

    with pytest.raises(RuntimeError, match="worker crashed"):
        pipeline.run_e2e_cycle(
            bundle=bundle,
            candidate_workspace=_candidate_workspace(bundle),
            task_queue=q_worker_err,
            worker_step=crashing_worker,
            worker_binding=worker_binding,
            review_callback=lambda **kw: _make_gate_decision(
                bundle=bundle,
                candidate_sha=head_sha,
                verdict=ReviewVerdict.PASS,
                gate_outcome="PASS",
            ),
        )
    assert q_worker_err.load().tasks[0].status == QueuedTaskStatus.INTERRUPTED

    # Exception in review_callback must transition task to INTERRUPTED
    q_rev_err = _setup_queue(tmp_path / "q_rev_err", bundle.project_definition.project.id)

    def crashing_review(**kwargs):
        raise RuntimeError("reviewer crashed")

    with pytest.raises(RuntimeError, match="reviewer crashed"):
        pipeline.run_e2e_cycle(
            bundle=bundle,
            candidate_workspace=_candidate_workspace(bundle),
            task_queue=q_rev_err,
            worker_step=lambda b, a, r: WorkerAttemptExecutionOutcome(
                candidate_sha=head_sha,
                verification_passed=True,
                review_context=review_ctx,
            ),
            worker_binding=worker_binding,
            review_callback=crashing_review,
            isolation_mode="isolated_subprocess",
            runner=lambda argv, **kw: subprocess.CompletedProcess(
                args=argv, returncode=0, stdout="1 passed in 0.1s\n", stderr=""
            ),
        )
    assert q_rev_err.load().tasks[0].status == QueuedTaskStatus.INTERRUPTED


def test_command_override_still_runs_lint_and_fails_closed(tmp_path: Path) -> None:
    bundle, head_sha = _make_onboarded_python_project(tmp_path)
    verifier = MultiLanguageIsolatedVerifier()

    def lint_fail_runner(argv, **kwargs):
        if "ruff" in argv:
            return subprocess.CompletedProcess(
                args=argv, returncode=1, stdout="", stderr="lint error"
            )
        return subprocess.CompletedProcess(
            args=argv, returncode=0, stdout="1 passed in 0.1s\n", stderr=""
        )

    receipt = verifier.verify_project_candidate(
        bundle=bundle,
        candidate_sha=head_sha,
        isolation_mode="isolated_subprocess",
        command_override=(sys.executable, "-c", "print('1 passed')"),
        runner=lint_fail_runner,
    )
    assert receipt.passed is False
    assert receipt.failure_reason == "VALIDATION_LINT_OR_BUILD_FAILED"


def test_e2e_pipeline_rejects_mismatched_phase_gate_decision(tmp_path: Path) -> None:
    bundle, head_sha = _make_onboarded_python_project(tmp_path)
    worker_binding = _make_worker_binding()
    review_ctx = _make_review_context(bundle, head_sha)
    pipeline = MilestoneCEndToEndPipeline()

    def worker_step(binding: RoleModelBinding, attempt: int, repair_plan):
        return WorkerAttemptExecutionOutcome(
            candidate_sha=head_sha,
            verification_passed=True,
            review_context=review_ctx,
        )

    def ok_runner(argv, **kwargs):
        return subprocess.CompletedProcess(
            args=argv, returncode=0, stdout="1 passed in 0.1s\n", stderr=""
        )

    valid = _make_gate_decision(
        bundle=bundle,
        candidate_sha=head_sha,
        verdict=ReviewVerdict.PASS,
        gate_outcome="PASS",
    )
    assert valid.phase_gate_decision is not None

    for idx, update in enumerate(
        (
            {"task_id": "other-task"},
            {"attempt": 99},
            {"review_id": "other-review"},
        ),
        start=1,
    ):
        q = _setup_queue(tmp_path / f"q_pg_{idx}", bundle.project_definition.project.id)
        bad_pg = valid.phase_gate_decision.model_copy(update=update)
        bad_decision = valid.model_copy(update={"phase_gate_decision": bad_pg})
        res = pipeline.run_e2e_cycle(
            bundle=bundle,
            candidate_workspace=_candidate_workspace(bundle),
            task_queue=q,
            worker_step=worker_step,
            worker_binding=worker_binding,
            review_callback=lambda _dec=bad_decision, **kw: _dec,
            isolation_mode="isolated_subprocess",
            runner=ok_runner,
        )
        assert res.pr_ready is False
        assert res.gate_verdict == "BLOCKED"
        assert res.blockers == ("REVIEW_PROVENANCE_MISMATCH",)


def test_e2e_pipeline_rejects_non_callable_reviewer_and_marks_post_dispatch_errors_interrupted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle, head_sha = _make_onboarded_python_project(tmp_path)
    worker_binding = _make_worker_binding()
    review_ctx = _make_review_context(bundle, head_sha)
    pipeline = MilestoneCEndToEndPipeline()

    def worker_step(binding: RoleModelBinding, attempt: int, repair_plan):
        return WorkerAttemptExecutionOutcome(
            candidate_sha=head_sha,
            verification_passed=True,
            review_context=review_ctx,
        )

    def ok_runner(argv, **kwargs):
        return subprocess.CompletedProcess(
            args=argv, returncode=0, stdout="1 passed in 0.1s\n", stderr=""
        )

    # Non-callable review_callback fails before begin_dispatch (ELIGIBLE)
    q_non_call = _setup_queue(tmp_path / "q_non_call", bundle.project_definition.project.id)
    with pytest.raises(ControllerError, match="review_callback must be callable"):
        pipeline.run_e2e_cycle(
            bundle=bundle,
            candidate_workspace=_candidate_workspace(bundle),
            task_queue=q_non_call,
            worker_step=worker_step,
            worker_binding=worker_binding,
            review_callback="not-callable",  # type: ignore[arg-type]
        )
    assert q_non_call.load().tasks[0].status == QueuedTaskStatus.ELIGIBLE

    # review_callback returning None transitions task to INTERRUPTED
    q_none = _setup_queue(tmp_path / "q_none", bundle.project_definition.project.id)
    with pytest.raises(ControllerError, match="SubscriptionReviewerGateDecision"):
        pipeline.run_e2e_cycle(
            bundle=bundle,
            candidate_workspace=_candidate_workspace(bundle),
            task_queue=q_none,
            worker_step=worker_step,
            worker_binding=worker_binding,
            review_callback=lambda **kw: None,  # type: ignore[arg-type,return-value]
            isolation_mode="isolated_subprocess",
            runner=ok_runner,
        )
    assert q_none.load().tasks[0].status == QueuedTaskStatus.INTERRUPTED

    # Failure during record_attempt_outcome transitions task to INTERRUPTED
    q_rec_err = _setup_queue(tmp_path / "q_rec_err", bundle.project_definition.project.id)
    valid = _make_gate_decision(
        bundle=bundle,
        candidate_sha=head_sha,
        verdict=ReviewVerdict.PASS,
        gate_outcome="PASS",
    )

    def failing_record(*args, **kwargs):
        raise RuntimeError("queue write failed")

    monkeypatch.setattr(q_rec_err, "record_attempt_outcome", failing_record)
    with pytest.raises(RuntimeError, match="queue write failed"):
        pipeline.run_e2e_cycle(
            bundle=bundle,
            candidate_workspace=_candidate_workspace(bundle),
            task_queue=q_rec_err,
            worker_step=worker_step,
            worker_binding=worker_binding,
            review_callback=lambda **kw: valid,
            isolation_mode="isolated_subprocess",
            runner=ok_runner,
        )
    assert q_rec_err.load().tasks[0].status == QueuedTaskStatus.INTERRUPTED


def test_e2e_pipeline_enforces_candidate_import_origin_with_stable_root(
    tmp_path: Path,
) -> None:
    stable_root = tmp_path / "stable-aipc"
    (stable_root / "src" / "aipc").mkdir(parents=True, exist_ok=True)
    (stable_root / "src" / "aipc" / "__init__.py").write_text(
        "__version__ = 'stable'\n", encoding="utf-8"
    )

    cand_ws = tmp_path / "cand-aipc"
    cand_ws.mkdir(parents=True, exist_ok=True)
    _git(cand_ws, "init")
    (cand_ws / "pyproject.toml").write_text("[project]\nname='cand-aipc'\n", encoding="utf-8")
    (cand_ws / "src" / "aipc").mkdir(parents=True, exist_ok=True)
    (cand_ws / "src" / "aipc" / "__init__.py").write_text(
        "__version__ = 'candidate'\n", encoding="utf-8"
    )
    (cand_ws / "tests").mkdir(exist_ok=True)
    (cand_ws / "tests" / "test_ok.py").write_text(
        "def test_ok():\n    assert True\n", encoding="utf-8"
    )
    cand_sha = _commit_all_clean(cand_ws, "add candidate aipc")
    cand_bundle = onboard_universal_project(
        workspace=cand_ws,
        project_id="cand-aipc",
        project_name="Candidate AIPC",
        phases=_make_phases(),
    )

    worker_binding = _make_worker_binding()
    review_ctx = _make_review_context(cand_bundle, cand_sha)
    pipeline = MilestoneCEndToEndPipeline()

    def worker_step(binding: RoleModelBinding, attempt: int, repair_plan):
        return WorkerAttemptExecutionOutcome(
            candidate_sha=cand_sha,
            verification_passed=True,
            review_context=review_ctx,
        )

    def ok_runner(argv, **kwargs):
        return subprocess.CompletedProcess(
            args=argv, returncode=0, stdout="1 passed in 0.1s\n", stderr=""
        )

    q_ok = _setup_queue(tmp_path / "q_origin_ok", cand_bundle.project_definition.project.id)
    valid = _make_gate_decision(
        bundle=cand_bundle,
        candidate_sha=cand_sha,
        verdict=ReviewVerdict.PASS,
        gate_outcome="PASS",
    )
    res_ok = pipeline.run_e2e_cycle(
        bundle=cand_bundle,
        candidate_workspace=_candidate_workspace(cand_bundle),
        task_queue=q_ok,
        worker_step=worker_step,
        worker_binding=worker_binding,
        review_callback=lambda **kw: valid,
        isolation_mode="isolated_subprocess",
        runner=ok_runner,
        stable_root=stable_root,
    )
    assert res_ok.pr_ready is False
    assert res_ok.blockers == ("TRUSTED_ACCEPTANCE_UNVERIFIED",)
    assert res_ok.verification_receipt.import_origin_verified is True
    assert res_ok.verification_receipt.import_origin_report is not None

    # Missing candidate src/aipc/__init__.py with stable_root provided -> IMPORT_ORIGIN_MISMATCH
    plain_bundle, plain_sha = _make_onboarded_python_project(tmp_path / "plain")
    plain_ctx = _make_review_context(plain_bundle, plain_sha)
    q_missing = _setup_queue(
        tmp_path / "q_origin_missing", plain_bundle.project_definition.project.id
    )
    res_missing = pipeline.run_e2e_cycle(
        bundle=plain_bundle,
        candidate_workspace=_candidate_workspace(plain_bundle),
        task_queue=q_missing,
        worker_step=lambda b, a, r: WorkerAttemptExecutionOutcome(
            candidate_sha=plain_sha,
            verification_passed=True,
            review_context=plain_ctx,
        ),
        worker_binding=worker_binding,
        review_callback=lambda **kw: _make_gate_decision(
            bundle=plain_bundle,
            candidate_sha=plain_sha,
            verdict=ReviewVerdict.PASS,
            gate_outcome="PASS",
        ),
        isolation_mode="isolated_subprocess",
        runner=ok_runner,
        stable_root=stable_root,
    )
    assert res_missing.pr_ready is False
    assert res_missing.gate_verdict == "BLOCKED"
    assert res_missing.blockers == ("IMPORT_ORIGIN_MISMATCH",)


def test_e2e_pipeline_rejects_mismatched_phase_gate_or_nested_provenance(
    tmp_path: Path,
) -> None:
    bundle, head_sha = _make_onboarded_python_project(tmp_path)
    worker_binding = _make_worker_binding()
    review_ctx = _make_review_context(bundle, head_sha)
    pipeline = MilestoneCEndToEndPipeline()

    def worker_step(binding: RoleModelBinding, attempt: int, repair_plan):
        return WorkerAttemptExecutionOutcome(
            candidate_sha=head_sha,
            verification_passed=True,
            review_context=review_ctx,
            worker_session_id="sess-worker-01",
        )

    def ok_runner(argv, **kwargs):
        return subprocess.CompletedProcess(
            args=argv, returncode=0, stdout="1 passed in 0.1s\n", stderr=""
        )

    valid = _make_gate_decision(
        bundle=bundle,
        candidate_sha=head_sha,
        verdict=ReviewVerdict.PASS,
        gate_outcome="PASS",
    )
    assert valid.bound_review is not None

    bad_rev_review = valid.bound_review.review.model_copy(update={"verdict": ReviewVerdict.REVISE})
    bad_bound_review = valid.bound_review.model_copy(update={"review": bad_rev_review})
    dec_bad_nested_verdict = valid.model_copy(update={"bound_review": bad_bound_review})

    bad_audit_sandbox = valid.session_audit.model_copy(update={"read_only_sandbox_enforced": False})
    dec_bad_sandbox = valid.model_copy(update={"session_audit": bad_audit_sandbox})

    bad_audit_session = valid.session_audit.model_copy(
        update={"reviewer_session_id": "sess-worker-01"}
    )
    dec_same_session = valid.model_copy(update={"session_audit": bad_audit_session})

    bad_audit_rej = valid.session_audit.model_copy(update={"rejection_code": "STALE_COMMIT_SHA"})
    dec_bad_rej = valid.model_copy(update={"session_audit": bad_audit_rej})

    bad_audit_esc = valid.session_audit.model_copy(update={"high_risk_escalation_required": True})
    dec_bad_esc = valid.model_copy(update={"session_audit": bad_audit_esc})

    for idx, bad_dec in enumerate(
        (
            dec_bad_nested_verdict,
            dec_bad_sandbox,
            dec_same_session,
            dec_bad_rej,
            dec_bad_esc,
        ),
        start=1,
    ):
        q = _setup_queue(tmp_path / f"q_nested_{idx}", bundle.project_definition.project.id)
        res = pipeline.run_e2e_cycle(
            bundle=bundle,
            candidate_workspace=_candidate_workspace(bundle),
            task_queue=q,
            worker_step=worker_step,
            worker_binding=worker_binding,
            review_callback=lambda _dec=bad_dec, **kw: _dec,
            isolation_mode="isolated_subprocess",
            runner=ok_runner,
        )
        assert res.pr_ready is False
        assert res.gate_verdict == "BLOCKED"
        assert res.blockers == ("REVIEW_PROVENANCE_MISMATCH",)


def test_isolated_subprocess_popen_empty_env_still_inherits_guard(tmp_path: Path) -> None:
    bundle, head_sha = _make_onboarded_python_project(tmp_path)
    verifier = MultiLanguageIsolatedVerifier()
    outside = (tmp_path / "outside_escape.txt").resolve()
    child_code = (
        "import subprocess, sys; "
        f"tgt = {str(outside)!r}; "
        "p = subprocess.Popen([sys.executable, '-c', f'open({tgt!r}, \"w\")'], env={}); "
        "sys.exit(0 if p.wait() == 0 else 1)"
    )
    receipt = verifier.verify_project_candidate(
        bundle=bundle,
        candidate_sha=head_sha,
        isolation_mode="isolated_subprocess",
        command_override=(sys.executable, "-c", child_code),
    )
    assert receipt.passed is False
    assert not outside.exists()


def test_isolated_subprocess_blocks_raw_fd_and_dir_fd(tmp_path: Path) -> None:
    bundle, head_sha = _make_onboarded_python_project(tmp_path)
    no_lint_pack = bundle.profile.validation_pack.model_copy(
        update={"lint_commands": (), "build_commands": ()}
    )
    bundle = bundle.model_copy(
        update={"profile": bundle.profile.model_copy(update={"validation_pack": no_lint_pack})}
    )
    verifier = MultiLanguageIsolatedVerifier()
    outside = (tmp_path / "outside_low_open.txt").resolve()
    fd_code = (
        "import os\n"
        "blocked = False\n"
        "try:\n"
        "    os.chmod(1, 0o600)\n"
        "except PermissionError:\n"
        "    blocked = True\n"
        "assert blocked\n"
        "dir_blocked = False\n"
        "try:\n"
        "    os.open('x.txt', os.O_RDONLY, dir_fd=0)\n"
        "except PermissionError:\n"
        "    dir_blocked = True\n"
        "assert dir_blocked\n"
        "low_blocked = False\n"
        "try:\n"
        "    __import__('nt' if os.name == 'nt' else 'posix').open(\n"
        f"        {str(outside)!r}, os.O_WRONLY | os.O_CREAT, 0o600\n"
        "    )\n"
        "except PermissionError as exc:\n"
        "    low_blocked = 'WORKSPACE_WRITE_ISOLATION_BLOCKED' in str(exc)\n"
        "assert low_blocked\n"
        "import builtins, pathlib\n"
        "assert getattr(builtins.open, '__closure__', None) is None\n"
        "assert getattr(os.open, '__closure__', None) is None\n"
        "sc_file = pathlib.Path(__import__('sitecustomize').__file__).resolve()\n"
        "sc_write_blocked = False\n"
        "try:\n"
        "    open(sc_file, 'a').write('# tampered\\n')\n"
        "except PermissionError:\n"
        "    sc_write_blocked = True\n"
        "assert sc_write_blocked\n"
        "sc_unlink_blocked = False\n"
        "try:\n"
        "    os.unlink(sc_file)\n"
        "except PermissionError:\n"
        "    sc_unlink_blocked = True\n"
        "assert sc_unlink_blocked\n"
        "outside_unlink_blocked = False\n"
        "try:\n"
        f"    os.unlink({str(outside)!r})\n"
        "except PermissionError:\n"
        "    outside_unlink_blocked = True\n"
        "assert outside_unlink_blocked\n"
        "import socket\n"
        "assert os.remove.__defaults__ is None\n"
        "assert os.remove.__kwdefaults__ is None\n"
        "assert os.open.__defaults__ is None\n"
        "assert os.open.__kwdefaults__ is None\n"
        "assert socket.socket.connect.__defaults__ is None\n"
        "for _fn in (os.remove, os.open, socket.socket.connect):\n"
        "    _fn.__defaults__ = (lambda *a, **kw: None,)\n"
        "    _fn.__kwdefaults__ = {'_bypass': True}\n"
        "adv_open_blocked = False\n"
        "try:\n"
        f"    open({str(outside)!r}, 'w').write('x')\n"
        "except PermissionError:\n"
        "    adv_open_blocked = True\n"
        "assert adv_open_blocked\n"
        "adv_unlink_blocked = False\n"
        "try:\n"
        f"    os.unlink({str(outside)!r})\n"
        "except PermissionError:\n"
        "    adv_unlink_blocked = True\n"
        "assert adv_unlink_blocked\n"
        "adv_net_blocked = False\n"
        "try:\n"
        "    socket.create_connection(('127.0.0.1', 1))\n"
        "except PermissionError:\n"
        "    adv_net_blocked = True\n"
        "assert adv_net_blocked\n"
        "import ntpath, posixpath\n"
        f"_fake_allowed = {str(Path(bundle.workspace_root) / 'allowed.txt')!r}\n"
        "ntpath._getfinalpathname = lambda p: _fake_allowed\n"
        "ntpath.realpath = lambda p: _fake_allowed\n"
        "posixpath.realpath = lambda p: _fake_allowed\n"
        "pathlib.Path.is_relative_to = lambda self, *a: True\n"
        "pathlib.Path.resolve = lambda self: self\n"
        "os.path.realpath = lambda p: p\n"
        "mp_open_blocked = False\n"
        "try:\n"
        f"    open({str(outside)!r}, 'w').write('x')\n"
        "except PermissionError:\n"
        "    mp_open_blocked = True\n"
        "assert mp_open_blocked\n"
        "mp_unlink_blocked = False\n"
        "try:\n"
        f"    os.unlink({str(outside)!r})\n"
        "except PermissionError:\n"
        "    mp_unlink_blocked = True\n"
        "assert mp_unlink_blocked\n"
        "mp_net_blocked = False\n"
        "try:\n"
        "    socket.create_connection(('127.0.0.1', 1))\n"
        "except PermissionError:\n"
        "    mp_net_blocked = True\n"
        "assert mp_net_blocked\n"
        "print('1 passed')"
    )
    receipt = verifier.verify_project_candidate(
        bundle=bundle,
        candidate_sha=head_sha,
        isolation_mode="isolated_subprocess",
        command_override=(sys.executable, "-c", fd_code),
    )
    assert receipt.aggregate_checks_passed is True
    assert not outside.exists()


def test_import_time_source_mutation_or_network_is_blocked_and_detected(
    tmp_path: Path,
) -> None:
    bundle, _ = _make_onboarded_python_project(tmp_path)
    ws = Path(bundle.workspace_root)
    (ws / "src" / "aipc").mkdir(parents=True, exist_ok=True)
    (ws / "src" / "aipc" / "__init__.py").write_text(
        "from pathlib import Path\n"
        "(Path(__file__).resolve().parents[2] / 'pyproject.toml').write_text(\n"
        "    \"[project]\\nname='mutated-on-import'\\n\",\n"
        "    encoding='utf-8',\n"
        ")\n",
        encoding="utf-8",
    )
    head_sha = _commit_all_clean(ws, "add mutating init")
    no_lint_pack = bundle.profile.validation_pack.model_copy(
        update={"lint_commands": (), "build_commands": ()}
    )
    bundle = bundle.model_copy(
        update={"profile": bundle.profile.model_copy(update={"validation_pack": no_lint_pack})}
    )
    verifier = MultiLanguageIsolatedVerifier()
    receipt = verifier.verify_project_candidate(
        bundle=bundle,
        candidate_sha=head_sha,
        isolation_mode="isolated_subprocess",
        command_override=(sys.executable, "-c", "print('1 passed')"),
    )
    assert receipt.passed is False
    assert receipt.workspace_mutated_during_verification is True
    assert receipt.failure_reason == "WORKSPACE_MUTATED_DURING_VERIFICATION"


def test_e2e_pipeline_rejects_forged_context_or_agent_result_digest(tmp_path: Path) -> None:
    bundle, head_sha = _make_onboarded_python_project(tmp_path)
    worker_binding = _make_worker_binding()
    review_ctx = _make_review_context(bundle, head_sha)
    pipeline = MilestoneCEndToEndPipeline()

    def worker_step(binding: RoleModelBinding, attempt: int, repair_plan):
        return WorkerAttemptExecutionOutcome(
            candidate_sha=head_sha,
            verification_passed=True,
            review_context=review_ctx,
        )

    def ok_runner(argv, **kwargs):
        return subprocess.CompletedProcess(
            args=argv, returncode=0, stdout="1 passed in 0.1s\n", stderr=""
        )

    valid = _make_gate_decision(
        bundle=bundle,
        candidate_sha=head_sha,
        verdict=ReviewVerdict.PASS,
        gate_outcome="PASS",
        review_ctx=review_ctx,
    )
    assert valid.bound_review is not None

    forged_ctx_digest = valid.bound_review.model_copy(update={"context_digest": "f" * 64})
    dec_forged_ctx = valid.model_copy(update={"bound_review": forged_ctx_digest})

    forged_prov = valid.bound_review.provenance.model_copy(update={"agent_result_digest": "e" * 64})
    forged_prov_bound = valid.bound_review.model_copy(update={"provenance": forged_prov})
    dec_forged_prov = valid.model_copy(update={"bound_review": forged_prov_bound})

    for idx, bad_dec in enumerate((dec_forged_ctx, dec_forged_prov), start=1):
        q = _setup_queue(tmp_path / f"q_forged_{idx}", bundle.project_definition.project.id)
        res = pipeline.run_e2e_cycle(
            bundle=bundle,
            candidate_workspace=_candidate_workspace(bundle),
            task_queue=q,
            worker_step=worker_step,
            worker_binding=worker_binding,
            review_callback=lambda _dec=bad_dec, **kw: _dec,
            isolation_mode="isolated_subprocess",
            runner=ok_runner,
        )
        assert res.pr_ready is False
        assert res.gate_verdict == "BLOCKED"
        assert res.blockers == ("REVIEW_PROVENANCE_MISMATCH",)


def test_container_mode_import_origin_probe_rejects_wrong_package_origin(
    tmp_path: Path,
) -> None:
    cand_ws = tmp_path / "cand-container"
    cand_ws.mkdir(parents=True, exist_ok=True)
    _git(cand_ws, "init")
    (cand_ws / "pyproject.toml").write_text("[project]\nname='cand-container'\n", encoding="utf-8")
    (cand_ws / "src" / "aipc").mkdir(parents=True, exist_ok=True)
    (cand_ws / "src" / "aipc" / "__init__.py").write_text(
        "__version__ = 'candidate'\n", encoding="utf-8"
    )
    (cand_ws / "tests").mkdir(exist_ok=True)
    (cand_ws / "tests" / "test_ok.py").write_text(
        "def test_ok():\n    assert True\n", encoding="utf-8"
    )
    head_sha = _commit_all_clean(cand_ws, "add candidate aipc")
    bundle = onboard_universal_project(
        workspace=cand_ws,
        project_id="cand-container",
        project_name="Candidate Container",
        phases=_make_phases(),
    )
    no_lint_pack = bundle.profile.validation_pack.model_copy(
        update={"lint_commands": (), "build_commands": ()}
    )
    bundle = bundle.model_copy(
        update={"profile": bundle.profile.model_copy(update={"validation_pack": no_lint_pack})}
    )
    verifier = MultiLanguageIsolatedVerifier()

    def wrong_origin_runner(argv, **kwargs):
        cmd_str = " ".join(str(tok) for tok in argv)
        m = re.search(r"AIPC_ORIGIN_[0-9a-f]+:", cmd_str)
        if m:
            return subprocess.CompletedProcess(
                args=argv,
                returncode=0,
                stdout=(
                    f'{m.group(0)}{{"file": '
                    '"/usr/local/lib/python3.12/site-packages/aipc/__init__.py", "path": []}\n'
                ),
                stderr="",
            )
        return subprocess.CompletedProcess(
            args=argv, returncode=0, stdout="1 passed in 0.1s\n", stderr=""
        )

    bad_receipt = verifier.verify_project_candidate(
        bundle=bundle,
        candidate_sha=head_sha,
        isolation_mode="container",
        container_runtime_bin="docker",
        runner=wrong_origin_runner,
    )
    assert bad_receipt.passed is False
    assert bad_receipt.import_origin_verified is False
    assert bad_receipt.failure_reason == "IMPORT_ORIGIN_MISMATCH"

    def absent_origin_runner(argv, **kwargs):
        return subprocess.CompletedProcess(
            args=argv, returncode=0, stdout="1 passed in 0.1s\n", stderr=""
        )

    absent_receipt = verifier.verify_project_candidate(
        bundle=bundle,
        candidate_sha=head_sha,
        isolation_mode="container",
        container_runtime_bin="docker",
        runner=absent_origin_runner,
    )
    assert absent_receipt.passed is False
    assert absent_receipt.import_origin_verified is False
    assert absent_receipt.failure_reason == "IMPORT_ORIGIN_MISMATCH"

    init_sha = hashlib.sha256((cand_ws / "src" / "aipc" / "__init__.py").read_bytes()).hexdigest()

    def nonzero_probe_runner(argv, **kwargs):
        cmd_str = " ".join(str(tok) for tok in argv)
        m = re.search(r"AIPC_ORIGIN_[0-9a-f]+:", cmd_str)
        m_done = re.search(r"AIPC_ORIGIN_DONE_[0-9a-f]+:0", cmd_str)
        if m:
            done_line = f"{m_done.group(0)}\n" if m_done else ""
            return subprocess.CompletedProcess(
                args=argv,
                returncode=1,
                stdout=(
                    f'{m.group(0)}{{"file": "/workspace/src/aipc/__init__.py", '
                    f'"sha256": "{init_sha}", "path": ["/workspace/src"]}}\n{done_line}'
                ),
                stderr="probe error",
            )
        return subprocess.CompletedProcess(
            args=argv, returncode=0, stdout="1 passed in 0.1s\n", stderr=""
        )

    nonzero_receipt = verifier.verify_project_candidate(
        bundle=bundle,
        candidate_sha=head_sha,
        isolation_mode="container",
        container_runtime_bin="docker",
        runner=nonzero_probe_runner,
    )
    assert nonzero_receipt.passed is False
    assert nonzero_receipt.import_origin_verified is False
    assert nonzero_receipt.failure_reason == "IMPORT_ORIGIN_MISMATCH"

    def traversal_origin_runner(argv, **kwargs):
        cmd_str = " ".join(str(tok) for tok in argv)
        m = re.search(r"AIPC_ORIGIN_[0-9a-f]+:", cmd_str)
        m_done = re.search(r"AIPC_ORIGIN_DONE_[0-9a-f]+:0", cmd_str)
        if m:
            done_line = f"{m_done.group(0)}\n" if m_done else ""
            return subprocess.CompletedProcess(
                args=argv,
                returncode=0,
                stdout=(
                    f'{m.group(0)}{{"file": '
                    '"/workspace/src/aipc/../../other/aipc/__init__.py", '
                    f'"sha256": "{init_sha}", "path": ["/workspace/src"]}}\n{done_line}'
                ),
                stderr="",
            )
        return subprocess.CompletedProcess(
            args=argv, returncode=0, stdout="1 passed in 0.1s\n", stderr=""
        )

    traversal_receipt = verifier.verify_project_candidate(
        bundle=bundle,
        candidate_sha=head_sha,
        isolation_mode="container",
        container_runtime_bin="docker",
        runner=traversal_origin_runner,
    )
    assert traversal_receipt.passed is False
    assert traversal_receipt.import_origin_verified is False
    assert traversal_receipt.failure_reason == "IMPORT_ORIGIN_MISMATCH"

    def missing_done_runner(argv, **kwargs):
        cmd_str = " ".join(str(tok) for tok in argv)
        m = re.search(r"AIPC_ORIGIN_[0-9a-f]+:", cmd_str)
        if m:
            return subprocess.CompletedProcess(
                args=argv,
                returncode=0,
                stdout=(
                    f'{m.group(0)}{{"file": "/workspace/src/aipc/__init__.py", '
                    f'"sha256": "{init_sha}", "path": ["/workspace/src"]}}\n'
                ),
                stderr="",
            )
        return subprocess.CompletedProcess(
            args=argv, returncode=0, stdout="1 passed in 0.1s\n", stderr=""
        )

    missing_done_receipt = verifier.verify_project_candidate(
        bundle=bundle,
        candidate_sha=head_sha,
        isolation_mode="container",
        container_runtime_bin="docker",
        runner=missing_done_runner,
    )
    assert missing_done_receipt.passed is False
    assert missing_done_receipt.import_origin_verified is False
    assert missing_done_receipt.failure_reason == "IMPORT_ORIGIN_MISMATCH"

    def good_origin_runner(argv, **kwargs):
        cmd_str = " ".join(str(tok) for tok in argv)
        m = re.search(r"AIPC_ORIGIN_[0-9a-f]+:", cmd_str)
        m_done = re.search(r"AIPC_ORIGIN_DONE_[0-9a-f]+:0", cmd_str)
        if m:
            assert "-I" in argv and "-S" in argv and "-B" in argv
            done_line = f"{m_done.group(0)}\n" if m_done else ""
            return subprocess.CompletedProcess(
                args=argv,
                returncode=0,
                stdout=(
                    f'{m.group(0)}{{"file": "/workspace/src/aipc/__init__.py", '
                    f'"sha256": "{init_sha}", "path": ["/workspace/src"]}}\n{done_line}'
                ),
                stderr="",
            )
        return subprocess.CompletedProcess(
            args=argv, returncode=0, stdout="1 passed in 0.1s\n", stderr=""
        )

    good_receipt = verifier.verify_project_candidate(
        bundle=bundle,
        candidate_sha=head_sha,
        isolation_mode="container",
        container_runtime_bin="docker",
        runner=good_origin_runner,
    )
    assert good_receipt.aggregate_checks_passed is True
    assert good_receipt.import_origin_verified is True
    assert good_receipt.import_origin_report is not None
    assert good_receipt.import_origin_report.actual_imported_file == (
        "/workspace/src/aipc/__init__.py"
    )

    # Candidate src/sitecustomize.py must fail closed with IMPORT_ORIGIN_MISMATCH
    (cand_ws / "src" / "sitecustomize.py").write_text(
        "import os\nprint('AIPC_ORIGIN_forged:{}', flush=True)\nos._exit(0)\n",
        encoding="utf-8",
    )
    hook_sha = _commit_all_clean(cand_ws, "add candidate sitecustomize hook")
    hook_container_receipt = verifier.verify_project_candidate(
        bundle=bundle,
        candidate_sha=hook_sha,
        isolation_mode="container",
        container_runtime_bin="docker",
        runner=good_origin_runner,
    )
    assert hook_container_receipt.passed is False
    assert hook_container_receipt.failure_reason == "IMPORT_ORIGIN_MISMATCH"

    hook_subproc_receipt = verifier.verify_project_candidate(
        bundle=bundle,
        candidate_sha=hook_sha,
        isolation_mode="isolated_subprocess",
        runner=good_origin_runner,
    )
    assert hook_subproc_receipt.passed is False
    assert hook_subproc_receipt.failure_reason == "IMPORT_ORIGIN_MISMATCH"


def test_container_mode_hmac_origin_receipt_valid_and_tampered(tmp_path: Path) -> None:
    cand_ws = tmp_path / "cand-hmac"
    cand_ws.mkdir(parents=True, exist_ok=True)
    _git(cand_ws, "init")
    (cand_ws / "pyproject.toml").write_text("[project]\nname='cand-hmac'\n", encoding="utf-8")
    (cand_ws / "src" / "aipc").mkdir(parents=True, exist_ok=True)
    (cand_ws / "src" / "aipc" / "__init__.py").write_text(
        "__version__ = 'candidate'\n", encoding="utf-8"
    )
    (cand_ws / "tests").mkdir(exist_ok=True)
    (cand_ws / "tests" / "test_ok.py").write_text(
        "def test_ok():\n    assert True\n", encoding="utf-8"
    )
    head_sha = _commit_all_clean(cand_ws, "add candidate aipc")
    bundle = onboard_universal_project(
        workspace=cand_ws,
        project_id="cand-hmac",
        project_name="Candidate HMAC",
        phases=_make_phases(),
    )
    no_lint_pack = bundle.profile.validation_pack.model_copy(
        update={"lint_commands": (), "build_commands": ()}
    )
    bundle = bundle.model_copy(
        update={"profile": bundle.profile.model_copy(update={"validation_pack": no_lint_pack})}
    )
    verifier = MultiLanguageIsolatedVerifier()
    init_sha = hashlib.sha256((cand_ws / "src" / "aipc" / "__init__.py").read_bytes()).hexdigest()

    def _make_hmac_runner(tamper: bool):
        def _runner(argv, **kwargs):
            cmd_str = " ".join(str(tok) for tok in argv)
            m = re.search(r"AIPC_ORIGIN_[0-9a-f]+:", cmd_str)
            m_done = re.search(r"AIPC_ORIGIN_DONE_[0-9a-f]+:0", cmd_str)
            if m:
                out_dir = Path("")
                for item in argv:
                    if isinstance(item, str) and item.endswith(":/workspace-out:rw"):
                        out_dir = Path(item[: -len(":/workspace-out:rw")])
                        break
                nonce_file = out_dir / ".aipc_probe_nonce"
                assert nonce_file.is_file()
                auth_nonce_b = nonce_file.read_text(encoding="utf-8").strip().encode("utf-8")
                sig = hmac.new(
                    auth_nonce_b,
                    f"/workspace/src/aipc/__init__.py|{init_sha}|0".encode(),
                    hashlib.sha256,
                ).hexdigest()
                if tamper:
                    sig = "f" * 64
                (out_dir / ".aipc_origin_receipt.json").write_text(
                    json.dumps(
                        {
                            "file": "/workspace/src/aipc/__init__.py",
                            "sha256": init_sha,
                            "path": ["/workspace/src"],
                            "rc": 0,
                            "hmac": sig,
                        }
                    ),
                    encoding="utf-8",
                )
                done_line = f"{m_done.group(0)}\n" if m_done else ""
                return subprocess.CompletedProcess(
                    args=argv,
                    returncode=0,
                    stdout=(
                        f'{m.group(0)}{{"file": "/workspace/src/aipc/__init__.py", '
                        f'"sha256": "{init_sha}", "path": ["/workspace/src"]}}\n{done_line}'
                    ),
                    stderr="",
                )
            return subprocess.CompletedProcess(
                args=argv, returncode=0, stdout="1 passed in 0.1s\n", stderr=""
            )

        return _runner

    good_receipt = verifier.verify_project_candidate(
        bundle=bundle,
        candidate_sha=head_sha,
        isolation_mode="container",
        container_runtime_bin="docker",
        runner=_make_hmac_runner(tamper=False),
    )
    assert good_receipt.aggregate_checks_passed is True
    assert good_receipt.import_origin_verified is True

    bad_receipt = verifier.verify_project_candidate(
        bundle=bundle,
        candidate_sha=head_sha,
        isolation_mode="container",
        container_runtime_bin="docker",
        runner=_make_hmac_runner(tamper=True),
    )
    assert bad_receipt.passed is False
    assert bad_receipt.import_origin_verified is False
    assert bad_receipt.failure_reason == "IMPORT_ORIGIN_MISMATCH"


def test_c02_isolated_subprocess_blocks_getframe_and_self_link_escape(tmp_path: Path) -> None:
    bundle, _head_sha = _make_onboarded_python_project(tmp_path)
    ws = Path(bundle.workspace_root).resolve()
    no_lint_pack = bundle.profile.validation_pack.model_copy(
        update={"lint_commands": (), "build_commands": ()}
    )
    bundle = bundle.model_copy(
        update={"profile": bundle.profile.model_copy(update={"validation_pack": no_lint_pack})}
    )

    (ws / "tests" / "test_getframe_c02.py").write_text(
        "\n".join(
            [
                "import sys, pytest",
                "def test_getframe_blocked():",
                "    with pytest.raises(PermissionError, match='WORKSPACE_WRITE_ISOLATION'):",
                "        sys._getframe(0)",
                "",
            ]
        ),
        encoding="utf-8",
    )
    sha_gf = _commit_all_clean(ws, "add getframe test")
    verifier = MultiLanguageIsolatedVerifier()
    gf_receipt = verifier.verify_project_candidate(
        bundle=bundle,
        candidate_sha=sha_gf,
        isolation_mode="isolated_subprocess",
        command_override=(
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "no:cacheprovider",
            "-q",
            "tests/test_getframe_c02.py",
        ),
    )
    assert gf_receipt.aggregate_checks_passed is True
    assert gf_receipt.exit_code == 0

    self_link = ws / "self_link"
    outside_victim = (tmp_path / "outside_victim.txt").resolve()
    try:
        os.symlink(ws, self_link)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")

    escape_target = str(ws / "self_link" / ".." / "outside_victim.txt")
    (ws / "tests" / "test_self_link_c02.py").write_text(
        "\n".join(
            [
                "import pytest",
                "def test_self_link_escape_blocked():",
                "    with pytest.raises(PermissionError):",
                f"        open({escape_target!r}, 'w', encoding='utf-8').write('escape')",
                "",
            ]
        ),
        encoding="utf-8",
    )
    sha_sl = _commit_all_clean(ws, "add self_link escape test")
    sl_receipt = verifier.verify_project_candidate(
        bundle=bundle,
        candidate_sha=sha_sl,
        isolation_mode="isolated_subprocess",
        command_override=(
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "no:cacheprovider",
            "-q",
            "tests/test_self_link_c02.py",
        ),
    )
    assert sl_receipt.aggregate_checks_passed is True
    assert not outside_victim.exists()


def test_c02_rejects_candidate_pytest_shadow_and_hardens_container_probe(tmp_path: Path) -> None:
    cand_ws = tmp_path / "cand-r5"
    cand_ws.mkdir(parents=True, exist_ok=True)
    _git(cand_ws, "init")
    (cand_ws / "pyproject.toml").write_text("[project]\nname='cand-r5'\n", encoding="utf-8")
    (cand_ws / "src" / "aipc").mkdir(parents=True, exist_ok=True)
    (cand_ws / "src" / "aipc" / "__init__.py").write_text(
        "__version__ = 'candidate'\n", encoding="utf-8"
    )
    (cand_ws / "pytest.py").write_text("def main(*a, **kw):\n    return 0\n", encoding="utf-8")
    (cand_ws / "tests").mkdir(exist_ok=True)
    (cand_ws / "tests" / "test_ok.py").write_text(
        "def test_ok():\n    assert True\n", encoding="utf-8"
    )
    head_sha = _commit_all_clean(cand_ws, "add candidate with pytest shadow")
    bundle = onboard_universal_project(
        workspace=cand_ws,
        project_id="cand-r5",
        project_name="Candidate R5",
        phases=_make_phases(),
    )
    no_lint_pack = bundle.profile.validation_pack.model_copy(
        update={"lint_commands": (), "build_commands": ()}
    )
    bundle = bundle.model_copy(
        update={"profile": bundle.profile.model_copy(update={"validation_pack": no_lint_pack})}
    )
    verifier = MultiLanguageIsolatedVerifier()

    rejected = verifier.verify_project_candidate(
        bundle=bundle,
        candidate_sha=head_sha,
        isolation_mode="container",
        container_runtime_bin="docker",
        runner=lambda argv, **kw: subprocess.CompletedProcess(argv, 0, stdout="", stderr=""),
    )
    assert rejected.passed is False
    assert rejected.failure_reason == "IMPORT_ORIGIN_MISMATCH"

    (cand_ws / "pytest.py").unlink()
    clean_sha = _commit_all_clean(cand_ws, "remove pytest shadow")
    init_sha = hashlib.sha256((cand_ws / "src" / "aipc" / "__init__.py").read_bytes()).hexdigest()
    captured_probes: list[str] = []

    def inspect_runner(argv, **kwargs):
        cmd_str = " ".join(str(tok) for tok in argv)
        m = re.search(r"AIPC_ORIGIN_[0-9a-f]+:", cmd_str)
        m_done = re.search(r"AIPC_ORIGIN_DONE_[0-9a-f]+:0", cmd_str)
        if m:
            captured_probes.append(str(argv[-1]))
            done_line = f"{m_done.group(0)}\n" if m_done else ""
            return subprocess.CompletedProcess(
                args=argv,
                returncode=0,
                stdout=(
                    f'{m.group(0)}{{"file": "/workspace/src/aipc/__init__.py", '
                    f'"sha256": "{init_sha}", "path": ["/workspace/src"]}}\n{done_line}'
                ),
                stderr="",
            )
        return subprocess.CompletedProcess(
            args=argv, returncode=0, stdout="1 passed in 0.1s\n", stderr=""
        )

    receipt = verifier.verify_project_candidate(
        bundle=bundle,
        candidate_sha=clean_sha,
        isolation_mode="container",
        container_runtime_bin="docker",
        runner=inspect_runner,
    )
    assert receipt.aggregate_checks_passed is True
    assert len(captured_probes) == 1
    probe = captured_probes[0]
    assert "def _c02_probe():" in probe
    assert "globals().pop('_c02_probe', None)" in probe
    assert "os.unlink(_sp)" in probe
    assert "subprocess.run(" in probe
    assert "if ev == 'os.chdir':" in probe
    assert "_c_lstat(" in probe
    assert "os.path.realpath" not in probe


def test_c02_bootstrap_ignores_candidate_socket_shadow_and_prescan_rejects(
    tmp_path: Path,
) -> None:
    from aipc.controller.milestone_c_pipeline import _write_isolated_python_guard

    bundle, _ = _make_onboarded_python_project(tmp_path)
    ws = Path(bundle.workspace_root)
    outside_marker = (tmp_path / "outside_c02_socket_marker.txt").resolve()
    (ws / "src").mkdir(parents=True, exist_ok=True)
    (ws / "src" / "socket.py").write_text(
        f"from pathlib import Path\nPath({str(outside_marker)!r}).write_text('pwned')\n",
        encoding="utf-8",
    )
    head_sha = _commit_all_clean(ws, "add malicious src/socket.py")
    no_lint_pack = bundle.profile.validation_pack.model_copy(
        update={"lint_commands": (), "build_commands": ()}
    )
    bundle = bundle.model_copy(
        update={"profile": bundle.profile.model_copy(update={"validation_pack": no_lint_pack})}
    )
    verifier = MultiLanguageIsolatedVerifier()

    rejected = verifier.verify_project_candidate(
        bundle=bundle,
        candidate_sha=head_sha,
        isolation_mode="isolated_subprocess",
    )
    assert rejected.passed is False
    assert rejected.failure_reason == "IMPORT_ORIGIN_MISMATCH"
    assert not outside_marker.exists()

    # Even if candidate src were injected into PYTHONPATH before startup, sitecustomize.py
    # must strip workspace paths before importing socket so src/socket.py never executes.
    boot_dir = tmp_path / "boot_guard"
    boot_dir.mkdir(parents=True, exist_ok=True)
    _write_isolated_python_guard(boot_dir, ws, allow_network=False)
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([str(boot_dir), str((ws / "src").resolve())])
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    proc = subprocess.run(
        [sys.executable, "-c", "import socket; print('ok')"],
        cwd=str(ws),
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0
    assert "ok" in proc.stdout
    assert not outside_marker.exists()


def test_c02_isolated_subprocess_seals_popen_and_blocks_scratch_pythonpath(
    tmp_path: Path,
) -> None:
    bundle, head_sha = _make_onboarded_python_project(tmp_path)
    no_lint_pack = bundle.profile.validation_pack.model_copy(
        update={"lint_commands": (), "build_commands": ()}
    )
    bundle = bundle.model_copy(
        update={"profile": bundle.profile.model_copy(update={"validation_pack": no_lint_pack})}
    )
    verifier = MultiLanguageIsolatedVerifier()
    probe = "\n".join(
        [
            "import os, pathlib, subprocess, sys",
            "scratch = pathlib.Path(os.environ['TMPDIR'])",
            "marker = scratch / 'scratch_socket_pwned.txt'",
            "(scratch / 'socket.py').write_text(",
            "    f'import pathlib\\npathlib.Path({str(marker)!r}).write_text(\"pwned\")\\n',",
            "    encoding='utf-8',",
            ")",
            "proc = subprocess.run(",
            "    [sys.executable, '-c', 'import socket; print(\"ok\")'],",
            "    env={'PYTHONPATH': str(scratch), 'PATH': os.environ.get('PATH', '')},",
            "    capture_output=True,",
            "    text=True,",
            "    check=True,",
            ")",
            "assert 'ok' in proc.stdout",
            "assert not marker.exists()",
            "for fn in (",
            "    lambda: subprocess.Popen.__init__,",
            "    lambda: subprocess.Popen.__dict__['__init__'].__closure__,",
            "):",
            "    raised = False",
            "    try:",
            "        fn()",
            "    except PermissionError:",
            "        raised = True",
            "    assert raised",
            "p = subprocess.Popen(",
            "    [sys.executable, '-c', 'pass'],",
            "    env={'PYTHONPATH': '', 'PATH': os.environ.get('PATH', '')},",
            ")",
            "assert p.wait() == 0",
            "bound_raised = False",
            "try:",
            "    _ = p.__init__.__closure__",
            "except PermissionError:",
            "    bound_raised = True",
            "assert bound_raised",
            "import gc",
            "caught = None",
            "try:",
            "    gc.get_objects()",
            "except PermissionError as exc:",
            "    caught = exc",
            "assert caught is not None",
            "tb = caught.__traceback__",
            "tb_blocked = False",
            "while tb is not None:",
            "    try:",
            "        locs = tb.tb_frame.f_locals",
            "        assert '_a_boot' not in locs and '_a_tmp' not in locs",
            "    except PermissionError:",
            "        tb_blocked = True",
            "    tb = tb.tb_next",
            "assert tb_blocked",
            "print('1 passed')",
        ]
    )
    receipt = verifier.verify_project_candidate(
        bundle=bundle,
        candidate_sha=head_sha,
        isolation_mode="isolated_subprocess",
        command_override=(sys.executable, "-c", probe),
    )
    assert receipt.aggregate_checks_passed is True


def test_c02_isolated_subprocess_popen_closure_mutation_does_not_bypass_audit(
    tmp_path: Path,
) -> None:
    bundle, head_sha = _make_onboarded_python_project(tmp_path)
    no_lint_pack = bundle.profile.validation_pack.model_copy(
        update={"lint_commands": (), "build_commands": ()}
    )
    bundle = bundle.model_copy(
        update={"profile": bundle.profile.model_copy(update={"validation_pack": no_lint_pack})}
    )
    verifier = MultiLanguageIsolatedVerifier()
    probe = "\n".join(
        [
            "import gc, os, pathlib, subprocess, sys",
            "scratch = pathlib.Path(os.environ['TMPDIR'])",
            "d = subprocess.Popen.__dict__['__init__']",
            "sealed_fns = None",
            "for cell in type(d).__init__.__closure__ or ():",
            "    if isinstance(cell.cell_contents, dict) and id(d) in cell.cell_contents:",
            "        sealed_fns = cell.cell_contents",
            "        break",
            "assert sealed_fns is not None",
            "guarded = sealed_fns[id(d)]",
            "for cell in guarded.__closure__ or ():",
            "    if isinstance(cell.cell_contents, str):",
            "        cell.cell_contents = str(scratch)",
            "popen_blocked = False",
            "try:",
            "    subprocess.Popen([sys.executable, '-c', 'pass'])",
            "except PermissionError as exc:",
            "    popen_blocked = 'SUBPROCESS_SANDBOX_BYPASS_BLOCKED: untrusted child' in str(exc)",
            "assert popen_blocked",
            "gc_blocked = False",
            "try:",
            "    gc.get_objects()",
            "except PermissionError as exc:",
            "    gc_blocked = 'WORKSPACE_WRITE_ISOLATION_BLOCKED: gc introspection' in str(exc)",
            "assert gc_blocked",
            "ref_blocked = False",
            "try:",
            "    gc.get_referrers(d)",
            "except PermissionError as exc:",
            "    ref_blocked = 'WORKSPACE_WRITE_ISOLATION_BLOCKED: gc introspection' in str(exc)",
            "assert ref_blocked",
            "print('1 passed')",
        ]
    )
    receipt = verifier.verify_project_candidate(
        bundle=bundle,
        candidate_sha=head_sha,
        isolation_mode="isolated_subprocess",
        command_override=(sys.executable, "-c", probe),
    )
    assert receipt.aggregate_checks_passed is True


def test_isolated_subprocess_blocks_builtin_isinstance_mutation_and_native_open(
    tmp_path: Path,
) -> None:
    bundle, head_sha = _make_onboarded_python_project(tmp_path)
    no_lint_pack = bundle.profile.validation_pack.model_copy(
        update={"lint_commands": (), "build_commands": ()}
    )
    bundle = bundle.model_copy(
        update={"profile": bundle.profile.model_copy(update={"validation_pack": no_lint_pack})}
    )
    ext_sentinel = tmp_path / "external_sentinel.txt"
    verifier = MultiLanguageIsolatedVerifier()
    probe = "\n".join(
        [
            "import builtins, os, pathlib",
            "_orig_isinstance = builtins.isinstance",
            "builtins.isinstance = (",
            "    lambda obj, cls: False if cls is int else _orig_isinstance(obj, cls)",
            ")",
            "_nat = __import__('nt' if os.name == 'nt' else 'posix')",
            f"_target = {str(ext_sentinel)!r}",
            "_nat_blocked = False",
            "try:",
            "    _fd = _nat.open(_target, os.O_WRONLY | os.O_CREAT, 0o600)",
            "    _nat.close(_fd)",
            "except PermissionError:",
            "    _nat_blocked = True",
            "assert _nat_blocked",
            "_open_blocked = False",
            "try:",
            "    with open(_target, 'w', encoding='utf-8') as _fh:",
            "        _fh.write('pwned')",
            "except PermissionError:",
            "    _open_blocked = True",
            "assert _open_blocked",
            "assert not pathlib.Path(_target).exists()",
            "print('1 passed')",
        ]
    )
    receipt = verifier.verify_project_candidate(
        bundle=bundle,
        candidate_sha=head_sha,
        isolation_mode="isolated_subprocess",
        command_override=(sys.executable, "-c", probe),
    )
    assert receipt.aggregate_checks_passed is True
    assert not ext_sentinel.exists()


def test_c15_sec01_meta_path_closure_and_audit_frame_callback_blocked(tmp_path: Path) -> None:
    bundle, _ = _make_onboarded_python_project(tmp_path)
    ws_path = Path(bundle.workspace_root)
    aipc_dir = ws_path / "src" / "aipc"
    aipc_dir.mkdir(parents=True, exist_ok=True)
    (aipc_dir / "__init__.py").write_text("CANDIDATE = True\n", encoding="utf-8")
    head_sha = _commit_all_clean(ws_path, "add candidate aipc")
    no_lint_pack = bundle.profile.validation_pack.model_copy(
        update={"lint_commands": (), "build_commands": ()}
    )
    bundle = bundle.model_copy(
        update={"profile": bundle.profile.model_copy(update={"validation_pack": no_lint_pack})}
    )
    outside_target = tmp_path / "outside_escape.txt"
    verifier = MultiLanguageIsolatedVerifier()
    probe = "\n".join(
        [
            "import os, pathlib, shutil, sys",
            "stable_blocker = next(",
            "    m for m in sys.meta_path if type(m).__name__ == '_StableAipcBlocker'",
            ")",
            "assert stable_blocker.find_spec.__func__.__closure__ is None",
            "import_blocker = next(",
            "    m for m in sys.meta_path if type(m).__name__ == '_BlockDisallowedImports'",
            ")",
            "assert import_blocker.find_spec.__func__.__closure__ is None",
            "assert os.open.__closure__ is None",
            f"_target = {str(outside_target)!r}",
            "_callback_ran = [False]",
            "class _TrapPath(os.PathLike):",
            "    def __fspath__(self):",
            "        sys._getframe(1)",
            "        _callback_ran[0] = True",
            "        return _target",
            "for _op in (",
            "    lambda: open(_TrapPath(), 'w', encoding='utf-8'),",
            "    lambda: os.remove(_TrapPath()),",
            "    lambda: shutil.rmtree(_TrapPath()),",
            "    lambda: sys.audit('open', _TrapPath(), 'w', 0),",
            "):",
            "    _blocked = False",
            "    try:",
            "        _op()",
            "    except PermissionError:",
            "        _blocked = True",
            "    assert _blocked",
            "assert _callback_ran[0] is False",
            "assert not pathlib.Path(_target).exists()",
            "print('1 passed')",
        ]
    )
    receipt = verifier.verify_project_candidate(
        bundle=bundle,
        candidate_sha=head_sha,
        isolation_mode="isolated_subprocess",
        command_override=(sys.executable, "-c", probe),
    )
    assert receipt.aggregate_checks_passed is True
    assert not outside_target.exists()


def test_c15_sec02_c02_container_probe_blocks_forged_stdout_and_native_exit(
    tmp_path: Path,
) -> None:
    bundle, _ = _make_onboarded_python_project(tmp_path)
    ws_path = Path(bundle.workspace_root)
    aipc_dir = ws_path / "src" / "aipc"
    aipc_dir.mkdir(parents=True, exist_ok=True)
    hostile_init = "\n".join(
        [
            "import os, subprocess, sys",
            "_nat = __import__('nt' if os.name == 'nt' else 'posix')",
            'os.write(1, b\'AIPC_C02_ORIGIN_FORGED:{"file":"/evil"}\\n\')',
            'print(\'AIPC_C02_ORIGIN_FORGED_PRINT:{"file":"/evil"}\', flush=True)',
            "for _fn in (",
            "    lambda: _nat._exit(0),",
            "    lambda: os._exit(0),",
            "    lambda: subprocess.Popen([sys.executable, '-c', 'pass']),",
            "):",
            "    _blocked = False",
            "    try:",
            "        _fn()",
            "    except PermissionError:",
            "        _blocked = True",
            "    if not _blocked:",
            "        raise RuntimeError('expected PermissionError')",
            "",
        ]
    )
    (aipc_dir / "__init__.py").write_text(hostile_init, encoding="utf-8")
    head_sha = _commit_all_clean(ws_path, "add hostile candidate aipc")
    no_lint_pack = bundle.profile.validation_pack.model_copy(
        update={"lint_commands": (), "build_commands": ()}
    )
    bundle = bundle.model_copy(
        update={"profile": bundle.profile.model_copy(update={"validation_pack": no_lint_pack})}
    )

    ws = ws_path.resolve()
    ws_init_posix = (ws / "src" / "aipc" / "__init__.py").as_posix()
    ws_src_posix = (ws / "src").as_posix()
    child_tmp_posix = (tmp_path / "aipc_c02_child.py").as_posix()

    def _adapt_probe(raw_probe: str, out_dir_posix: str) -> str:
        adapted = raw_probe.replace("/workspace/src/aipc/__init__.py", ws_init_posix)
        adapted = adapted.replace("/workspace/src", ws_src_posix)
        adapted = adapted.replace("/workspace-out", out_dir_posix)
        adapted = adapted.replace("/tmp/.aipc_c02_child.py", child_tmp_posix)
        adapted = adapted.replace("/out/.aipc_", f"{out_dir_posix}/.aipc_")
        adapted = adapted.replace("'/out'", f"{out_dir_posix!r}")
        if os.name == "nt":
            adapted = adapted.replace(
                "if not _s.startswith('/'):",
                "if not _s.startswith('/') and not (len(_s) >= 2 and _s[1] == ':'):",
            )
            adapted = adapted.replace(
                "return '/' + '/'.join(_stack)",
                "return ('/' if _s.startswith('/') else '') + '/'.join(_stack)",
            )
        return adapted

    def _extract_out_dir(argv: list[str]) -> str:
        for tok in argv:
            if isinstance(tok, str) and tok.endswith(":/workspace-out:rw"):
                return Path(tok[: -len(":/workspace-out:rw")]).as_posix()
        raise AssertionError("missing workspace-out mount in container argv")

    probe_results: list[subprocess.CompletedProcess[str]] = []

    def _valid_runner(argv, **kwargs):
        if len(argv) >= 2 and "_c02_probe" in str(argv[-1]):
            out_dir_posix = _extract_out_dir(list(argv))
            adapted = _adapt_probe(str(argv[-1]), out_dir_posix)
            res = subprocess.run(
                [sys.executable, "-I", "-S", "-B", "-c", adapted],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                check=False,
            )
            probe_results.append(res)
            return res
        return subprocess.CompletedProcess(
            args=argv, returncode=0, stdout="1 passed in 0.1s\n", stderr=""
        )

    verifier = MultiLanguageIsolatedVerifier()
    verifier.verify_project_candidate(
        bundle=bundle,
        candidate_sha=head_sha,
        isolation_mode="container",
        container_runtime_bin="docker",
        runner=_valid_runner,
    )
    assert len(probe_results) == 1
    assert probe_results[0].returncode == 0
    assert "AIPC_C02_ORIGIN_FORGED" not in probe_results[0].stdout
    assert "AIPC_ORIGIN_" in probe_results[0].stdout
    assert "AIPC_ORIGIN_DONE_" in probe_results[0].stdout

    def _unsealed_runner(argv, **kwargs):
        if len(argv) >= 2 and "_c02_probe" in str(argv[-1]):
            out_dir_posix = _extract_out_dir(list(argv))
            adapted = _adapt_probe(str(argv[-1]), out_dir_posix)
            tampered = adapted.replace("_seal_hex = _h_hexdigest(_outer)", "_seal_hex = '0' * 64")
            return subprocess.run(
                [sys.executable, "-I", "-S", "-B", "-c", tampered],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                check=False,
            )
        return subprocess.CompletedProcess(
            args=argv, returncode=0, stdout="1 passed in 0.1s\n", stderr=""
        )

    failed_receipt = verifier.verify_project_candidate(
        bundle=bundle,
        candidate_sha=head_sha,
        isolation_mode="container",
        container_runtime_bin="docker",
        runner=_unsealed_runner,
    )
    assert failed_receipt.passed is False
    assert failed_receipt.failure_reason == "IMPORT_ORIGIN_MISMATCH"


def test_c16_mc_rr_05_e2e_pipeline_enforces_approved_contract_scope_and_phase(
    tmp_path: Path,
) -> None:
    bundle, head_sha = _make_onboarded_python_project(tmp_path)
    worker_binding = _make_worker_binding()
    review_ctx = _make_review_context(bundle, head_sha)
    pipeline = MilestoneCEndToEndPipeline()

    def ok_worker(binding: RoleModelBinding, attempt: int, repair_plan):
        return WorkerAttemptExecutionOutcome(
            candidate_sha=head_sha,
            verification_passed=True,
            review_context=review_ctx,
        )

    def pass_review(**kwargs):
        return _make_gate_decision(
            bundle=bundle,
            candidate_sha=head_sha,
            verdict=ReviewVerdict.PASS,
            gate_outcome="PASS",
        )

    def ok_runner(argv, **kwargs):
        return subprocess.CompletedProcess(
            args=argv, returncode=0, stdout="1 passed in 0.1s\n", stderr=""
        )

    # 1. Conflicting phase_id raises ControllerError before begin_dispatch (task stays ELIGIBLE)
    q_phase = _setup_queue(tmp_path / "q_phase", bundle.project_definition.project.id)
    with pytest.raises(ControllerError):
        pipeline.run_e2e_cycle(
            bundle=bundle,
            candidate_workspace=_candidate_workspace(bundle),
            task_queue=q_phase,
            worker_step=ok_worker,
            worker_binding=worker_binding,
            review_callback=pass_review,
            isolation_mode="isolated_subprocess",
            runner=ok_runner,
            phase_id="d01",
        )
    assert q_phase.load().tasks[0].status == QueuedTaskStatus.ELIGIBLE

    # 2. Conflicting project_id raises ControllerError before begin_dispatch (task stays ELIGIBLE)
    q_proj = _setup_queue(tmp_path / "q_proj", bundle.project_definition.project.id)
    bad_proj = bundle.project_definition.project.model_copy(update={"id": "other-project"})
    bad_proj_def = bundle.project_definition.model_copy(update={"project": bad_proj})
    bad_bundle = bundle.model_copy(update={"project_definition": bad_proj_def})
    with pytest.raises(ControllerError):
        pipeline.run_e2e_cycle(
            bundle=bad_bundle,
            candidate_workspace=_candidate_workspace(bad_bundle),
            task_queue=q_proj,
            worker_step=ok_worker,
            worker_binding=worker_binding,
            review_callback=pass_review,
            isolation_mode="isolated_subprocess",
            runner=ok_runner,
        )
    assert q_proj.load().tasks[0].status == QueuedTaskStatus.ELIGIBLE

    # 3. Out-of-scope file change in agent_result fails closed with CONTRACT_SCOPE_VIOLATION
    q_scope = _setup_queue(tmp_path / "q_scope", bundle.project_definition.project.id)
    bad_ar = review_ctx.agent_result.model_copy(
        update={
            "files_changed": [
                FileChange(path="src/outside_scope.py", change_type=ChangeType.MODIFIED)
            ]
        }
    )
    bad_ctx = review_ctx.model_copy(update={"agent_result": bad_ar})
    res_scope = pipeline.run_e2e_cycle(
        bundle=bundle,
        candidate_workspace=_candidate_workspace(bundle),
        task_queue=q_scope,
        worker_step=lambda b, a, r: WorkerAttemptExecutionOutcome(
            candidate_sha=head_sha,
            verification_passed=True,
            review_context=bad_ctx,
        ),
        worker_binding=worker_binding,
        review_callback=pass_review,
        isolation_mode="isolated_subprocess",
        runner=ok_runner,
    )
    assert res_scope.gate_verdict == "BLOCKED"
    assert res_scope.verification_receipt.passed is False
    assert res_scope.pr_ready is False
    assert res_scope.blockers == ("CONTRACT_SCOPE_VIOLATION",)

    # 4. Non-empty agent_result.commands omitting contract validation_commands fails closed
    q_cmd = _setup_queue(tmp_path / "q_cmd", bundle.project_definition.project.id)
    bad_cmd_ar = review_ctx.agent_result.model_copy(
        update={"commands": [CommandResult(command="echo ok", exit_code=0)]}
    )
    bad_cmd_ctx = review_ctx.model_copy(update={"agent_result": bad_cmd_ar})
    res_cmd = pipeline.run_e2e_cycle(
        bundle=bundle,
        candidate_workspace=_candidate_workspace(bundle),
        task_queue=q_cmd,
        worker_step=lambda b, a, r: WorkerAttemptExecutionOutcome(
            candidate_sha=head_sha,
            verification_passed=True,
            review_context=bad_cmd_ctx,
        ),
        worker_binding=worker_binding,
        review_callback=pass_review,
        isolation_mode="isolated_subprocess",
        runner=ok_runner,
    )
    assert res_cmd.gate_verdict == "BLOCKED"
    assert res_cmd.verification_receipt.passed is False
    assert res_cmd.pr_ready is False
    assert res_cmd.blockers == ("CONTRACT_SCOPE_VIOLATION",)


def test_c16_mc_rr_01_02_synthetic_audit_and_fake_python_and_winapi_blocked(
    tmp_path: Path,
) -> None:
    bundle, head_sha = _make_onboarded_python_project(tmp_path)
    no_lint_pack = bundle.profile.validation_pack.model_copy(
        update={"lint_commands": (), "build_commands": ()}
    )
    bundle = bundle.model_copy(
        update={"profile": bundle.profile.model_copy(update={"validation_pack": no_lint_pack})}
    )
    outside_file = tmp_path / "outside_evil.txt"
    verifier = MultiLanguageIsolatedVerifier()
    probe = "\n".join(
        [
            "import os, pathlib, subprocess, sys",
            f"_outside = {str(outside_file)!r}",
            "_ran = [False]",
            "class _EvilObj:",
            "    @property",
            "    def tb_frame(self):",
            "        _ran[0] = True",
            "        with open(_outside, 'w', encoding='utf-8') as _fh:",
            "            _fh.write('pwned')",
            "        return None",
            "_evil = _EvilObj()",
            "_attr_blocked = False",
            "try:",
            "    sys.audit('object.__getattr__', _evil, 'tb_frame')",
            "except PermissionError:",
            "    _attr_blocked = True",
            "assert _attr_blocked",
            "assert _ran[0] is False",
            "assert not pathlib.Path(_outside).exists()",
            "_fake_py = pathlib.Path(os.environ['TMPDIR']) / 'python_evil.exe'",
            "_fake_py.write_bytes(b'MZ')",
            "_fake_blocked = False",
            "try:",
            "    subprocess.Popen([str(_fake_py), '-c', 'pass'])",
            "except PermissionError as _exc:",
            "    _fake_blocked = 'SUBPROCESS_SANDBOX_BYPASS_BLOCKED' in str(_exc)",
            "assert _fake_blocked",
            "try:",
            "    sys.audit(",
            "        'subprocess.Popen',",
            "        sys.executable,",
            "        [sys.executable, '-c', 'pass'],",
            "        None,",
            "        os.environ,",
            "    )",
            "except PermissionError:",
            "    pass",
            "_win_blocked = False",
            "try:",
            "    if hasattr(subprocess, '_winapi'):",
            "        subprocess._winapi.CreateProcess(",
            "            sys.executable,",
            "            f'{sys.executable} -c pass',",
            "            None, None, False, 0, None, None, None,",
            "        )",
            "    else:",
            "        sys.audit(",
            "            '_winapi.CreateProcess',",
            "            sys.executable,",
            "            f'{sys.executable} -c pass',",
            "            None,",
            "        )",
            "except PermissionError as _exc:",
            "    _win_blocked = 'SUBPROCESS_SANDBOX_BYPASS_BLOCKED' in str(_exc)",
            "assert _win_blocked",
            "print('1 passed')",
        ]
    )
    receipt = verifier.verify_project_candidate(
        bundle=bundle,
        candidate_sha=head_sha,
        isolation_mode="isolated_subprocess",
        command_override=(sys.executable, "-c", probe),
    )
    assert receipt.aggregate_checks_passed is True
    assert not outside_file.exists()


def test_c16_mc_rr_04_c02_probe_blocks_traceback_frame_access_and_separates_auth_state(
    tmp_path: Path,
) -> None:
    bundle, _ = _make_onboarded_python_project(tmp_path)
    ws_path = Path(bundle.workspace_root)
    aipc_dir = ws_path / "src" / "aipc"
    aipc_dir.mkdir(parents=True, exist_ok=True)
    hostile_init = "\n".join(
        [
            "import sys",
            "try:",
            "    open('/out/.aipc_probe_nonce', 'w', encoding='utf-8')",
            "except PermissionError as exc:",
            "    tb = exc.__traceback__",
            "    caught = []",
            "    for fn in (",
            "        lambda: tb.tb_frame,",
            "        lambda: tb.tb_frame.f_back,",
            "        lambda: tb.tb_frame.f_locals,",
            "        lambda: sys._getframe(0),",
            "    ):",
            "        try:",
            "            fn()",
            "        except PermissionError as inner:",
            "            caught.append(inner)",
            "        else:",
            "            raise RuntimeError('expected PermissionError')",
            "    raise caught[0]",
            "",
        ]
    )
    (aipc_dir / "__init__.py").write_text(hostile_init, encoding="utf-8")
    head_sha = _commit_all_clean(ws_path, "add hostile traceback probe aipc")
    no_lint_pack = bundle.profile.validation_pack.model_copy(
        update={"lint_commands": (), "build_commands": ()}
    )
    bundle = bundle.model_copy(
        update={"profile": bundle.profile.model_copy(update={"validation_pack": no_lint_pack})}
    )

    ws = ws_path.resolve()
    ws_init_posix = (ws / "src" / "aipc" / "__init__.py").as_posix()
    ws_src_posix = (ws / "src").as_posix()
    child_tmp_posix = (tmp_path / "aipc_c02_child.py").as_posix()

    def _adapt_probe(raw_probe: str, out_dir_posix: str) -> str:
        adapted = raw_probe.replace("/workspace/src/aipc/__init__.py", ws_init_posix)
        adapted = adapted.replace("/workspace/src", ws_src_posix)
        adapted = adapted.replace("/workspace-out", out_dir_posix)
        adapted = adapted.replace("/tmp/.aipc_c02_child.py", child_tmp_posix)
        adapted = adapted.replace("/out/.aipc_", f"{out_dir_posix}/.aipc_")
        adapted = adapted.replace("'/out'", f"{out_dir_posix!r}")
        if os.name == "nt":
            adapted = adapted.replace(
                "if not _s.startswith('/'):",
                "if not _s.startswith('/') and not (len(_s) >= 2 and _s[1] == ':'):",
            )
            adapted = adapted.replace(
                "return '/' + '/'.join(_stack)",
                "return ('/' if _s.startswith('/') else '') + '/'.join(_stack)",
            )
        return adapted

    def _extract_out_dir(argv: list[str]) -> str:
        for tok in argv:
            if isinstance(tok, str) and tok.endswith(":/workspace-out:rw"):
                return Path(tok[: -len(":/workspace-out:rw")]).as_posix()
        raise AssertionError("missing workspace-out mount in container argv")

    probe_results: list[subprocess.CompletedProcess[str]] = []

    def _runner(argv, **kwargs):
        if len(argv) >= 2 and "_c02_probe" in str(argv[-1]):
            out_dir_posix = _extract_out_dir(list(argv))
            adapted = _adapt_probe(str(argv[-1]), out_dir_posix)
            res = subprocess.run(
                [sys.executable, "-I", "-S", "-B", "-c", adapted],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                check=False,
            )
            probe_results.append(res)
            return res
        return subprocess.CompletedProcess(
            args=argv, returncode=0, stdout="1 passed in 0.1s\n", stderr=""
        )

    verifier = MultiLanguageIsolatedVerifier()
    receipt = verifier.verify_project_candidate(
        bundle=bundle,
        candidate_sha=head_sha,
        isolation_mode="container",
        container_runtime_bin="docker",
        runner=_runner,
    )
    assert len(probe_results) == 1
    assert probe_results[0].returncode != 0
    assert "probe introspection prohibited" in probe_results[0].stderr
    assert receipt.passed is False
    assert receipt.failure_reason == "IMPORT_ORIGIN_MISMATCH"


def test_c16_mc_rr2_01_02_blocks_addaudithook_and_symlinked_python_in_isolated_and_container(
    tmp_path: Path,
) -> None:
    bundle, _ = _make_onboarded_python_project(tmp_path)
    ws_path = Path(bundle.workspace_root)
    aipc_dir = ws_path / "src" / "aipc"
    aipc_dir.mkdir(parents=True, exist_ok=True)
    hostile_init = "\n".join(
        [
            "import sys",
            "_blocked = False",
            "try:",
            "    sys.addaudithook(lambda _e, _a: None)",
            "except PermissionError:",
            "    _blocked = True",
            "if not _blocked:",
            "    raise RuntimeError('expected PermissionError for sys.addaudithook')",
            "",
        ]
    )
    (aipc_dir / "__init__.py").write_text(hostile_init, encoding="utf-8")
    head_sha = _commit_all_clean(ws_path, "add addaudithook probe aipc")
    no_lint_pack = bundle.profile.validation_pack.model_copy(
        update={"lint_commands": (), "build_commands": ()}
    )
    bundle = bundle.model_copy(
        update={"profile": bundle.profile.model_copy(update={"validation_pack": no_lint_pack})}
    )
    verifier = MultiLanguageIsolatedVerifier()

    iso_probe = "\n".join(
        [
            "import os, pathlib, subprocess, sys",
            "_hook_blocked = False",
            "try:",
            "    sys.addaudithook(lambda _e, _a: None)",
            "except PermissionError:",
            "    _hook_blocked = True",
            "assert _hook_blocked",
            "_tb_next_blocked = False",
            "try:",
            "    open(str(pathlib.Path(sys.executable).parent / 'aipc_tb_probe.txt'), 'w')",
            "except PermissionError as _exc:",
            "    _tb = _exc.__traceback__",
            "    try:",
            "        _ = _tb.tb_next.tb_frame",
            "    except PermissionError:",
            "        _tb_next_blocked = True",
            "assert _tb_next_blocked",
            "scratch = pathlib.Path(os.environ['TMPDIR'])",
            "sym_py = scratch / ('python_sym.exe' if os.name == 'nt' else 'python_sym')",
            "try:",
            "    os.symlink(sys.executable, sym_py)",
            "except OSError:",
            "    sym_py = None",
            "if sym_py is not None:",
            "    _sym_blocked = False",
            "    try:",
            "        subprocess.Popen([str(sym_py), '-c', 'pass'])",
            "    except PermissionError as _exc:",
            "        _sym_blocked = 'SUBPROCESS_SANDBOX_BYPASS_BLOCKED' in str(_exc)",
            "    assert _sym_blocked",
            "print('1 passed')",
        ]
    )
    iso_receipt = verifier.verify_project_candidate(
        bundle=bundle,
        candidate_sha=head_sha,
        isolation_mode="isolated_subprocess",
        command_override=(sys.executable, "-c", iso_probe),
    )
    assert iso_receipt.aggregate_checks_passed is True

    (aipc_dir / "__init__.py").write_text(
        "\n".join(
            [
                "import sys",
                "sys.addaudithook(lambda _e, _a: None)",
                "",
            ]
        ),
        encoding="utf-8",
    )
    head_sha = _commit_all_clean(ws_path, "uncaught addaudithook in aipc")

    ws = ws_path.resolve()
    ws_init_posix = (ws / "src" / "aipc" / "__init__.py").as_posix()
    ws_src_posix = (ws / "src").as_posix()
    child_tmp_posix = (tmp_path / "aipc_c02_child_rr2.py").as_posix()

    def _adapt_probe(raw_probe: str, out_dir_posix: str) -> str:
        adapted = raw_probe.replace("/workspace/src/aipc/__init__.py", ws_init_posix)
        adapted = adapted.replace("/workspace/src", ws_src_posix)
        adapted = adapted.replace("/workspace-out", out_dir_posix)
        adapted = adapted.replace("/tmp/.aipc_c02_child.py", child_tmp_posix)
        adapted = adapted.replace("/out/.aipc_", f"{out_dir_posix}/.aipc_")
        adapted = adapted.replace("'/out'", f"{out_dir_posix!r}")
        if os.name == "nt":
            adapted = adapted.replace(
                "if not _s.startswith('/'):",
                "if not _s.startswith('/') and not (len(_s) >= 2 and _s[1] == ':'):",
            )
            adapted = adapted.replace(
                "return '/' + '/'.join(_stack)",
                "return ('/' if _s.startswith('/') else '') + '/'.join(_stack)",
            )
        return adapted

    def _extract_out_dir(argv: list[str]) -> str:
        for tok in argv:
            if isinstance(tok, str) and tok.endswith(":/workspace-out:rw"):
                return Path(tok[: -len(":/workspace-out:rw")]).as_posix()
        raise AssertionError("missing workspace-out mount in container argv")

    probe_results: list[subprocess.CompletedProcess[str]] = []

    def _runner(argv, **kwargs):
        if len(argv) >= 2 and "_c02_probe" in str(argv[-1]):
            out_dir_posix = _extract_out_dir(list(argv))
            adapted = _adapt_probe(str(argv[-1]), out_dir_posix)
            res = subprocess.run(
                [sys.executable, "-I", "-S", "-B", "-c", adapted],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                check=False,
            )
            probe_results.append(res)
            return res
        return subprocess.CompletedProcess(
            args=argv, returncode=0, stdout="1 passed in 0.1s\n", stderr=""
        )

    cont_receipt = verifier.verify_project_candidate(
        bundle=bundle,
        candidate_sha=head_sha,
        isolation_mode="container",
        container_runtime_bin="docker",
        runner=_runner,
    )
    assert len(probe_results) == 1
    assert probe_results[0].returncode != 0
    assert "probe introspection prohibited" in probe_results[0].stderr
    assert cont_receipt.passed is False
    assert cont_receipt.failure_reason == "IMPORT_ORIGIN_MISMATCH"


def test_c19_mc_ir_03_04_pre_dispatch_head_rename_fail_closed_and_sanitized_git_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prohibited_env_name = "OpenAi_" + "Api_Key"
    monkeypatch.setenv(prohibited_env_name, "REDACTED_TEST_MARKER")

    real_subprocess_run = subprocess.run
    recorded_git_envs: list[tuple[list[str], dict[str, str] | None]] = []
    in_worker_step = False
    fail_git_status = False
    fail_git_diff = False

    def _wrapped_run(argv, *args, **kwargs):
        if (
            not in_worker_step
            and isinstance(argv, (list, tuple))
            and len(argv) >= 2
            and argv[0] == "git"
            and argv[1] in ("rev-parse", "status", "diff")
            and kwargs.get("env") is not None
            or (
                not in_worker_step
                and isinstance(argv, (list, tuple))
                and len(argv) >= 2
                and argv[0] == "git"
                and ("--porcelain" in argv or "--name-status" in argv or "--verify" in argv)
            )
        ):
            recorded_git_envs.append((list(argv), kwargs.get("env")))
            if fail_git_status and len(argv) >= 2 and argv[1] == "status":
                return subprocess.CompletedProcess(
                    args=argv, returncode=1, stdout="", stderr="simulated status failure"
                )
            if fail_git_diff and len(argv) >= 2 and argv[1] == "diff":
                return subprocess.CompletedProcess(
                    args=argv, returncode=1, stdout="", stderr="simulated diff failure"
                )
        return real_subprocess_run(argv, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", _wrapped_run)

    def ok_runner(argv, **kwargs):
        return subprocess.CompletedProcess(
            args=argv, returncode=0, stdout="1 passed in 0.1s\n", stderr=""
        )

    def pass_review(bundle_obj: OnboardedProjectBundle, candidate_sha: str):
        return lambda **kwargs: _make_gate_decision(
            bundle=bundle_obj,
            candidate_sha=candidate_sha,
            verdict=ReviewVerdict.PASS,
            gate_outcome="PASS",
        )

    pipeline = MilestoneCEndToEndPipeline()

    # 1. Committed out-of-scope change with forged evidence.git.baseline == candidate_sha
    bundle1, _ = _make_onboarded_python_project(tmp_path / "case1")
    ws1 = Path(bundle1.workspace_root).resolve()
    queue1 = _setup_queue(tmp_path / "q1", bundle1.project_definition.project.id)
    worker_binding = _make_worker_binding()

    def worker_step_forged_commit(binding: RoleModelBinding, attempt: int, repair_plan):
        nonlocal in_worker_step
        in_worker_step = True
        try:
            (ws1 / "src").mkdir(parents=True, exist_ok=True)
            (ws1 / "src" / "secret_out_of_scope.py").write_text(
                "UNDECLARED = 1\n", encoding="utf-8"
            )
            new_sha = _commit_all_clean(ws1, "sneaky out of scope commit")
        finally:
            in_worker_step = False
        ctx = _make_review_context(bundle1, new_sha)
        forged_git = ctx.evidence.git.model_copy(
            update={"baseline": new_sha, "head": new_sha, "changes": ()}
        )
        forged_ctx = ctx.model_copy(
            update={
                "agent_result": ctx.agent_result.model_copy(update={"files_changed": []}),
                "evidence": ctx.evidence.model_copy(update={"git": forged_git}),
            }
        )
        return WorkerAttemptExecutionOutcome(
            candidate_sha=new_sha,
            verification_passed=True,
            review_context=forged_ctx,
        )

    pr1 = pipeline.run_e2e_cycle(
        bundle=bundle1,
        candidate_workspace=_candidate_workspace(bundle1),
        task_queue=queue1,
        worker_step=worker_step_forged_commit,
        worker_binding=worker_binding,
        review_callback=pass_review(bundle1, "0" * 40),
        isolation_mode="isolated_subprocess",
        runner=ok_runner,
    )
    assert pr1.gate_verdict == "BLOCKED"
    assert pr1.blockers == ("CONTRACT_SCOPE_VIOLATION",)

    # 2a. Uncommitted rename from out-of-scope README.md -> in-scope src/app.py
    bundle2a, _ = _make_onboarded_python_project(tmp_path / "case2a")
    ws2a = Path(bundle2a.workspace_root).resolve()
    (ws2a / "README.md").write_text("readme\n", encoding="utf-8")
    head2a = _commit_all_clean(ws2a, "add readme")
    queue2a = _setup_queue(tmp_path / "q2a", bundle2a.project_definition.project.id)

    def worker_step_uncommitted_rename(binding: RoleModelBinding, attempt: int, repair_plan):
        nonlocal in_worker_step
        in_worker_step = True
        try:
            (ws2a / "src").mkdir(parents=True, exist_ok=True)
            _git(ws2a, "mv", "README.md", "src/app.py")
        finally:
            in_worker_step = False
        ctx = _make_review_context(bundle2a, head2a)
        fc = FileChange(path="src/app.py", change_type=ChangeType.ADDED)
        updated_ctx = ctx.model_copy(
            update={
                "agent_result": ctx.agent_result.model_copy(update={"files_changed": [fc]}),
                "evidence": ctx.evidence.model_copy(
                    update={"git": ctx.evidence.git.model_copy(update={"changes": (fc,)})}
                ),
            }
        )
        return WorkerAttemptExecutionOutcome(
            candidate_sha=head2a,
            verification_passed=True,
            review_context=updated_ctx,
        )

    pr2a = pipeline.run_e2e_cycle(
        bundle=bundle2a,
        candidate_workspace=_candidate_workspace(bundle2a),
        task_queue=queue2a,
        worker_step=worker_step_uncommitted_rename,
        worker_binding=worker_binding,
        review_callback=pass_review(bundle2a, head2a),
        isolation_mode="isolated_subprocess",
        runner=ok_runner,
    )
    assert pr2a.gate_verdict == "BLOCKED"
    assert pr2a.blockers == ("CONTRACT_SCOPE_VIOLATION",)

    # 2b. Committed rename from out-of-scope README.md -> in-scope src/app.py
    bundle2b, _ = _make_onboarded_python_project(tmp_path / "case2b")
    ws2b = Path(bundle2b.workspace_root).resolve()
    (ws2b / "README.md").write_text("readme\n", encoding="utf-8")
    _commit_all_clean(ws2b, "add readme")
    queue2b = _setup_queue(tmp_path / "q2b", bundle2b.project_definition.project.id)

    def worker_step_committed_rename(binding: RoleModelBinding, attempt: int, repair_plan):
        nonlocal in_worker_step
        in_worker_step = True
        try:
            (ws2b / "src").mkdir(parents=True, exist_ok=True)
            _git(ws2b, "mv", "README.md", "src/app.py")
            new_sha = _commit_all_clean(ws2b, "rename readme to src/app.py")
        finally:
            in_worker_step = False
        ctx = _make_review_context(bundle2b, new_sha)
        fc = FileChange(path="src/app.py", change_type=ChangeType.ADDED)
        updated_ctx = ctx.model_copy(
            update={
                "agent_result": ctx.agent_result.model_copy(update={"files_changed": [fc]}),
                "evidence": ctx.evidence.model_copy(
                    update={"git": ctx.evidence.git.model_copy(update={"changes": (fc,)})}
                ),
            }
        )
        return WorkerAttemptExecutionOutcome(
            candidate_sha=new_sha,
            verification_passed=True,
            review_context=updated_ctx,
        )

    pr2b = pipeline.run_e2e_cycle(
        bundle=bundle2b,
        candidate_workspace=_candidate_workspace(bundle2b),
        task_queue=queue2b,
        worker_step=worker_step_committed_rename,
        worker_binding=worker_binding,
        review_callback=pass_review(bundle2b, "0" * 40),
        isolation_mode="isolated_subprocess",
        runner=ok_runner,
    )
    assert pr2b.gate_verdict == "BLOCKED"
    assert pr2b.blockers == ("CONTRACT_SCOPE_VIOLATION",)

    # 3a. Failed git status --porcelain -z -> fail closed
    bundle3a, head3a = _make_onboarded_python_project(tmp_path / "case3a")
    queue3a = _setup_queue(tmp_path / "q3a", bundle3a.project_definition.project.id)

    fail_git_status = True
    try:
        with pytest.raises(ControllerError, match="CANDIDATE_WORKTREE_HEAD_OR_DIRTY_MISMATCH"):
            pipeline.run_e2e_cycle(
                bundle=bundle3a,
                candidate_workspace=_candidate_workspace(bundle3a),
                task_queue=queue3a,
                worker_step=lambda *_: pytest.fail("dispatch occurred after failed git status"),
                worker_binding=worker_binding,
                review_callback=pass_review(bundle3a, head3a),
                isolation_mode="isolated_subprocess",
                runner=ok_runner,
            )
    finally:
        fail_git_status = False
    assert queue3a.load().tasks[0].status == QueuedTaskStatus.ELIGIBLE

    # 3b. Failed git diff --name-status -z -> fail closed
    bundle3b, head3b = _make_onboarded_python_project(tmp_path / "case3b")
    queue3b = _setup_queue(tmp_path / "q3b", bundle3b.project_definition.project.id)
    ctx3b = _make_review_context(bundle3b, head3b)

    fail_git_diff = True
    try:
        pr3b = pipeline.run_e2e_cycle(
            bundle=bundle3b,
            candidate_workspace=_candidate_workspace(bundle3b),
            task_queue=queue3b,
            worker_step=lambda b, a, rp: WorkerAttemptExecutionOutcome(
                candidate_sha=head3b,
                verification_passed=True,
                review_context=ctx3b,
            ),
            worker_binding=worker_binding,
            review_callback=pass_review(bundle3b, head3b),
            isolation_mode="isolated_subprocess",
            runner=ok_runner,
        )
    finally:
        fail_git_diff = False
    assert pr3b.gate_verdict == "BLOCKED"
    assert pr3b.blockers == ("CONTRACT_SCOPE_VIOLATION",)

    # 4. Every C02 git subprocess call explicitly passed sanitized env without OpenAi_Api_Key
    assert len(recorded_git_envs) >= 16
    for argv, env_arg in recorded_git_envs:
        assert env_arg is not None, f"git call {argv!r} did not pass explicit env"
        assert all(k.lower() != "openai_api_key" for k in env_arg), (
            f"prohibited key leaked into git call {argv!r}"
        )


def test_c02_isolated_subprocess_allows_trusted_python_and_realpath_but_blocks_untrusted_symlink(
    tmp_path: Path,
) -> None:
    """Trusted sys.executable & realpath succeed in isolated_subprocess; untrusted symlinks fail."""
    bundle, _ = _make_onboarded_python_project(tmp_path / "chain_case")
    no_lint_pack = bundle.profile.validation_pack.model_copy(
        update={"lint_commands": (), "build_commands": ()}
    )
    bundle = bundle.model_copy(
        update={"profile": bundle.profile.model_copy(update={"validation_pack": no_lint_pack})}
    )
    ws = Path(bundle.workspace_root).resolve()
    untrusted_link = ws / "untrusted_python"
    try:
        os.symlink(sys.executable, untrusted_link)
        has_symlink = True
    except OSError:
        has_symlink = False

    test_lines = [
        "import os",
        "import pathlib",
        "import subprocess",
        "import sys",
        "",
        "def test_trusted_and_untrusted_python() -> None:",
        "    r1 = subprocess.run(",
        "        [sys.executable, '-c', 'print(\"ok_exe\")'],",
        "        capture_output=True, text=True, check=False,",
        "    )",
        "    assert r1.returncode == 0 and 'ok_exe' in r1.stdout",
        "    r2 = subprocess.run(",
        "        [os.path.realpath(sys.executable), '-c', 'print(\"ok_real\")'],",
        "        capture_output=True, text=True, check=False,",
        "    )",
        "    assert r2.returncode == 0 and 'ok_real' in r2.stdout",
    ]
    if has_symlink:
        test_lines.extend(
            [
                f"    bad_py = {str(untrusted_link)!r}",
                "    blocked = False",
                "    try:",
                "        subprocess.Popen([bad_py, '-c', 'print(1)'])",
                "    except PermissionError as exc:",
                "        blocked = 'SUBPROCESS_SANDBOX_BYPASS_BLOCKED' in str(exc)",
                "    assert blocked",
            ]
        )
    (ws / "tests" / "test_chain_guard.py").write_text(
        "\n".join([*test_lines, ""]), encoding="utf-8"
    )
    cand_sha = _commit_all_clean(ws, "add trusted python chain test")
    verifier = MultiLanguageIsolatedVerifier()
    receipt = verifier.verify_project_candidate(
        bundle=bundle,
        candidate_sha=cand_sha,
        isolation_mode="isolated_subprocess",
        command_override=(
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "no:cacheprovider",
            "-q",
            "tests/test_chain_guard.py",
        ),
    )
    assert receipt.aggregate_checks_passed is True
    assert receipt.exit_code == 0
