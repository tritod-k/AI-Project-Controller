"""Unit tests for Workstream C02: Multi-Language Isolated Verification & E2E Pipeline."""

from __future__ import annotations

import hashlib
import json
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
from aipc.controller.task_queue import ApprovedTaskContract, PersistentTaskQueue, QueuedTaskStatus
from aipc.domain.enums import AgentStatus, CriterionStatus, ReviewVerdict
from aipc.domain.models import (
    AgentResult,
    CriterionResult,
    EvidenceReferences,
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
from aipc.version.models import CheckpointMetadata, CheckpointType

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

    assert receipt.passed is True
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
    assert receipt.passed is True
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
    assert static_receipt.passed is True
    assert static_receipt.offline_mock_only is False

    gas_receipt = verifier.verify_project_candidate(
        bundle=gas_bundle,
        candidate_sha=gas_sha,
        isolation_mode="container",
        container_runtime_bin="docker",
        runner=ok_runner,
    )
    assert gas_receipt.passed is True
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


def test_fail_closed_blocked_missing_runtime_mutation_and_zero_tests(tmp_path: Path) -> None:
    bundle, head_sha = _make_onboarded_python_project(tmp_path)
    verifier = MultiLanguageIsolatedVerifier()

    blocked_receipt = verifier.verify_project_candidate(
        bundle=bundle,
        candidate_sha=head_sha,
        isolation_mode="blocked",
    )
    assert blocked_receipt.passed is False
    assert blocked_receipt.failure_reason == VERIFICATION_ISOLATION_BLOCKED

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
    queue = PersistentTaskQueue(tmp_path / "queue.json")
    contract = ApprovedTaskContract(
        task_id="task-01",
        phase_id="phase-01",
        milestone_id="milestone-c",
        title="Verify project candidate",
        objective="Run isolated verification and review",
        in_scope=("src/app.py",),
        validation_commands=("python -m pytest -q",),
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


def test_e2e_pipeline_stops_at_human_merge_review(tmp_path: Path) -> None:
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
        task_queue=queue,
        worker_step=worker_step,
        worker_binding=worker_binding,
        review_callback=pass_review,
        isolation_mode="isolated_subprocess",
        runner=ok_runner,
    )

    assert isinstance(pr_bundle, PRReadinessBundle)
    assert pr_bundle.pr_ready is True
    assert pr_bundle.human_merge_approval_required is True
    assert pr_bundle.auto_merge_performed is False
    assert pr_bundle.next_authorized_action == "HUMAN_MERGE_REVIEW"
    assert pr_bundle.gate_verdict == "PASS"
    assert pr_bundle.review_verdict == ReviewVerdict.PASS
    assert queue.load().tasks[0].status == QueuedTaskStatus.COMPLETED


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
    assert net_receipt.passed is True

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
    assert udp_receipt.passed is True

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
    assert write_receipt.passed is True
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
    assert os_open_receipt.passed is True
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
    assert child_receipt.passed is True

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
    assert non_py_child_receipt.passed is True

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
    assert closure_receipt.passed is True

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
    assert os_system_receipt.passed is True

    ctypes_probe = (
        "try:\n    import ctypes\nexcept (ImportError, PermissionError):\n    print('1 passed')\n"
    )
    ctypes_receipt = verifier.verify_project_candidate(
        bundle=bundle,
        candidate_sha=head_sha,
        isolation_mode="isolated_subprocess",
        command_override=(sys.executable, "-c", ctypes_probe),
    )
    assert ctypes_receipt.passed is True


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
        task_queue=q_ok,
        worker_step=worker_step,
        worker_binding=worker_binding,
        review_callback=lambda **kw: valid,
        isolation_mode="isolated_subprocess",
        runner=ok_runner,
        stable_root=stable_root,
    )
    assert res_ok.pr_ready is True
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

    for idx, bad_dec in enumerate(
        (dec_bad_nested_verdict, dec_bad_sandbox, dec_same_session),
        start=1,
    ):
        q = _setup_queue(tmp_path / f"q_nested_{idx}", bundle.project_definition.project.id)
        res = pipeline.run_e2e_cycle(
            bundle=bundle,
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
        "print('1 passed')"
    )
    receipt = verifier.verify_project_candidate(
        bundle=bundle,
        candidate_sha=head_sha,
        isolation_mode="isolated_subprocess",
        command_override=(sys.executable, "-c", fd_code),
    )
    assert receipt.passed is True
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
