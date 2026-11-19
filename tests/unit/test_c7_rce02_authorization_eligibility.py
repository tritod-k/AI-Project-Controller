"""Milestone C.7 RCE02 tests: per-model eligibility & external milestone authorization."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from tests.review.conftest import context

from aipc.adapters.subscription_qualification import (
    CODEX_PINNED_SDK_VERSION,
    ProviderRuntimeQualificationReport,
    SandboxConstraintSummary,
    SurfaceCapabilitySummary,
    evaluate_runtime_eligibility,
    qualify_codex_subscription_runtime,
    to_runtime_observation,
)
from aipc.adapters.subscription_worker import (
    AntigravitySubscriptionWorkerAutomation,
    SubscriptionWorkerConfig,
    WorkerInvocationResponse,
)
from aipc.agents.role_registry import (
    ProviderRuntimeObservation,
    RoleBasedModelRegistry,
    RoleModelBinding,
    RoleSelectionRequest,
    build_default_subscription_role_registry,
)
from aipc.controller.errors import ControllerError, ProviderInterruption
from aipc.controller.milestone_authorization import (
    ApprovedMilestoneScope,
    MilestoneAuthorizationError,
    MilestoneAuthorizationTrustContext,
    compute_approval_digest,
    get_milestone_authorization_record_json_schema,
    issue_milestone_authorization_record,
    load_and_verify_milestone_authorization,
    migrate_legacy_approval_evidence,
    requires_external_milestone_trust,
)
from aipc.controller.milestone_authorization import (
    main as milestone_auth_cli_main,
)
from aipc.controller.planner_engine import (
    HumanGoalSpec,
    PlannerOrchestrationEngine,
)
from aipc.controller.task_queue import (
    ApprovedTaskContract,
    MilestoneAuthorizationEvidence,
    PersistentTaskQueue,
    QueuedTaskStatus,
    persist_milestone_authorization_evidence,
)
from aipc.domain.enums import AgentStatus, CriterionStatus, ReviewVerdict
from aipc.domain.models import AgentResult, CriterionResult, ReviewResult, TaskRequest
from aipc.project.models import AcceptanceCriterion
from aipc.recovery.models import ProviderCondition

pytest_plugins = ("tests.review.conftest",)

_GRANT_BYTES = b"COMMANDER-K.TRITOD-SIGNED-GRANT-C7-RCE02-BYTES"


def _make_agy_report(
    *,
    eligibility_state: str = "QUALIFIED",
    model_eligibility: dict[str, str] | None = None,
) -> ProviderRuntimeQualificationReport:
    is_qual = eligibility_state == "QUALIFIED"
    return ProviderRuntimeQualificationReport(
        provider_id="gemini-antigravity",
        provider_family="google_antigravity",
        operating_profile="subscription_only",
        auth_mode="antigravity_subscription",
        eligibility_state=eligibility_state,  # type: ignore[arg-type]
        available=is_qual,
        cli_executable="/usr/bin/agy",
        runtime_version="1.2.3",
        subscription_auth_present=is_qual,
        supported_model_ids=(
            ("gemini-3.8-flash-medium", "gemini-4-hydrogen-medium") if is_qual else ()
        ),
        unverified_or_unavailable_model_ids=("argon", "gemini-argon"),
        model_eligibility=model_eligibility or {},  # type: ignore[arg-type]
        account_independent_metadata={"secret_read_performed": False},
        surfaces=SurfaceCapabilitySummary(
            desktop_ui_present=False,
            cli_headless_present=is_qual,
            sdk_present=False,
            headless_worker_supported=is_qual,
            headless_reviewer_supported=is_qual,
            headless_planner_supported=is_qual,
        ),
        sandbox_constraints=SandboxConstraintSummary(
            os_kernel_sandbox_verified=False,
            workspace_isolation_mode="filesystem_only_deny_rules",
            allowed_modes=("plan", "accept-edits"),
            shell_execution="BLOCKED",
            git_execution="BLOCKED",
            network_access="BLOCKED",
        ),
        capability_evidence={
            "filesystem_read": "AVAILABLE" if is_qual else "UNVERIFIED",
            "filesystem_write": "AVAILABLE" if is_qual else "UNVERIFIED",
            "readonly_plan_mode": "AVAILABLE" if is_qual else "UNVERIFIED",
        },
        role_eligibility={
            "planner": eligibility_state,  # type: ignore[dict-item]
            "worker": eligibility_state,  # type: ignore[dict-item]
            "reviewer": eligibility_state,  # type: ignore[dict-item]
        },
    )


def _issue_external_grant(
    tmp_path: Path,
    *,
    project_id: str = "proj-aipc",
    milestone_id: str = "milestone-c7",
    commander_id: str = "K.TRITOD",
    approval_id: str = "appr-c7-rce02",
    workstreams: tuple[str, ...] = ("rce02",),
    allowed_prefixes: tuple[str, ...] = ("src/aipc/", "tests/unit/"),
    expires_at: datetime | None = None,
) -> tuple[MilestoneAuthorizationTrustContext, Path, Path]:
    trusted_root = (tmp_path / "controller_trusted").resolve()
    candidate_root = (tmp_path / "candidate_worktree").resolve()
    trusted_root.mkdir(parents=True, exist_ok=True)
    candidate_root.mkdir(parents=True, exist_ok=True)
    record_path = trusted_root / "c7_rce02_grant.json"

    record, record_sha = issue_milestone_authorization_record(
        raw_grant_bytes=_GRANT_BYTES,
        record_id="rec-c7-rce02",
        approval_id=approval_id,
        project_id=project_id,
        milestone_id=milestone_id,
        commander_id=commander_id,
        approved_scope=ApprovedMilestoneScope(
            workstreams=workstreams,
            allowed_path_prefixes=allowed_prefixes,
        ),
        output_path=record_path,
        trusted_external_root=trusted_root,
        excluded_roots=(candidate_root,),
        issued_at=datetime(2026, 11, 18, 12, 0, tzinfo=UTC),
        expires_at=expires_at,
    )
    trust_ctx = MilestoneAuthorizationTrustContext(
        record_path=record_path,
        expected_record_sha256=record_sha,
        expected_approval_digest=record.approval_digest,
        trusted_external_root=trusted_root,
        excluded_roots=(candidate_root,),
        expected_project_id=project_id,
        expected_milestone_id=milestone_id,
        expected_commander_id=commander_id,
        expected_approval_id=approval_id,
    )
    return trust_ctx, trusted_root, candidate_root


def test_per_model_eligibility_sibling_selection_and_no_upgrade() -> None:
    report = _make_agy_report(
        model_eligibility={
            "gemini-3.8-flash-medium": "RATE_LIMITED",
            "gemini-4-hydrogen-medium": "QUALIFIED",
        }
    )
    dec_limited = evaluate_runtime_eligibility(
        report, role="worker", requested_model="gemini-3.8-flash-medium"
    )
    assert not dec_limited.eligible
    assert dec_limited.eligibility_state == "UNAVAILABLE"

    dec_sibling = evaluate_runtime_eligibility(
        report, role="worker", requested_model="gemini-4-hydrogen-medium"
    )
    assert dec_sibling.eligible
    assert dec_sibling.resolved_model == "gemini-4-hydrogen-medium"

    dec_auto = evaluate_runtime_eligibility(report, role="worker", requested_model=None)
    assert dec_auto.eligible
    assert dec_auto.resolved_model == "gemini-4-hydrogen-medium"

    # Model state cannot upgrade unverified model or unqualified provider
    unverified_upgrade = _make_agy_report(model_eligibility={"argon": "QUALIFIED"})
    dec_unverified = evaluate_runtime_eligibility(
        unverified_upgrade, role="worker", requested_model="argon"
    )
    assert not dec_unverified.eligible

    blocked_provider = _make_agy_report(
        eligibility_state="BLOCKED",
        model_eligibility={"gemini-4-hydrogen-medium": "QUALIFIED"},
    )
    dec_blocked = evaluate_runtime_eligibility(
        blocked_provider, role="worker", requested_model="gemini-4-hydrogen-medium"
    )
    assert not dec_blocked.eligible

    # Absent model_eligibility preserves legacy behavior
    legacy_report = _make_agy_report()
    assert evaluate_runtime_eligibility(legacy_report, role="worker").eligible


def test_role_registry_sibling_fallback_on_rate_limit_and_quota_exhausted() -> None:
    report_rl = _make_agy_report(
        model_eligibility={
            "gemini-3.8-flash-medium": "RATE_LIMITED",
            "gemini-4-hydrogen-medium": "QUALIFIED",
        }
    )
    obs_rl = to_runtime_observation(report_rl)
    registry = build_default_subscription_role_registry((obs_rl,))
    decision = registry.resolve(
        RoleSelectionRequest(
            role="worker",
            preferred_tiers=("fast_worker", "balanced"),
            required_capabilities=("filesystem",),
        )
    )
    assert decision.outcome == "SELECTED"
    assert decision.selected_binding is not None
    assert decision.selected_binding.model_id == "gemini-4-hydrogen-medium"
    assert any(
        r.model_id == "gemini-3.8-flash-medium" and r.code == "MODEL_RATE_LIMITED"
        for r in decision.rejected_bindings
    )

    # Quota exhausted also rejects and selects sibling
    obs_qe = obs_rl.model_copy(
        update={
            "model_states": {
                "gemini-3.8-flash-medium": "QUOTA_EXHAUSTED",
                "gemini-4-hydrogen-medium": "QUALIFIED",
            }
        }
    )
    registry_qe = build_default_subscription_role_registry((obs_qe,))
    dec_qe = registry_qe.resolve(
        RoleSelectionRequest(role="worker", preferred_tiers=("fast_worker", "balanced"))
    )
    assert dec_qe.outcome == "SELECTED"
    assert dec_qe.selected_binding is not None
    assert dec_qe.selected_binding.model_id == "gemini-4-hydrogen-medium"
    assert any(r.code == "MODEL_QUOTA_EXHAUSTED" for r in dec_qe.rejected_bindings)

    # Model state cannot upgrade unverified or unsupported or unqualified runtime
    reg_custom = RoleBasedModelRegistry(
        observations=lambda: (
            ProviderRuntimeObservation(
                provider_id="gemini-antigravity",
                provider_family="google_antigravity",
                auth_mode="antigravity_subscription",
                runtime_state="QUALIFIED",
                supported_models=("gemini-4-hydrogen-medium",),
                unverified_models=("argon",),
                model_states={"argon": "QUALIFIED", "unknown-x": "QUALIFIED"},
                supported_modes=("worker",),
                capabilities={},
            ),
        )
    )
    reg_custom.register(
        RoleModelBinding(
            binding_id="w-argon",
            role="worker",
            provider_id="gemini-antigravity",
            provider_family="google_antigravity",
            model_id="argon",
            model_tier="fast_worker",
            execution_mode="worker",
            auth_mode="antigravity_subscription",
        )
    )
    reg_custom.register(
        RoleModelBinding(
            binding_id="w-unknown",
            role="worker",
            provider_id="gemini-antigravity",
            provider_family="google_antigravity",
            model_id="unknown-x",
            model_tier="fast_worker",
            execution_mode="worker",
            auth_mode="antigravity_subscription",
        )
    )
    dec_no_up = reg_custom.resolve(RoleSelectionRequest(role="worker"))
    assert dec_no_up.outcome == "NO_ELIGIBLE_BINDING"
    codes = {r.model_id: r.code for r in dec_no_up.rejected_bindings}
    assert codes["argon"] == "MODEL_UNVERIFIED"
    assert codes["unknown-x"] == "MODEL_UNSUPPORTED"


def test_dynamic_schema_issuance_migration_and_cli(tmp_path: Path) -> None:
    schema = get_milestone_authorization_record_json_schema()
    assert schema["type"] == "object"
    assert "approved_scope" in schema["properties"]
    assert "approval_digest" in schema["properties"]
    assert "commander_id" in schema["properties"]

    trust_ctx, trusted_root, candidate_root = _issue_external_grant(tmp_path)
    verified = load_and_verify_milestone_authorization(
        trust_ctx,
        phase_id="rce02",
        in_scope_paths=("src/aipc/controller/task_queue.py",),
        now=datetime(2026, 11, 18, 13, 0, tzinfo=UTC),
    )
    assert verified.project_id == "proj-aipc"
    assert verified.commander_id == "K.TRITOD"
    assert verified.approval_digest == compute_approval_digest(_GRANT_BYTES)

    # Migrate legacy MilestoneAuthorizationEvidence + raw grant bytes
    legacy_path = tmp_path / "legacy_c7.json"
    persist_milestone_authorization_evidence(
        legacy_path,
        MilestoneAuthorizationEvidence(
            approval_id="appr-c7-mig",
            project_id="proj-aipc",
            milestone_id="milestone-c7",
            authorized_by="K.TRITOD",
            authorized_workstreams=("rce02",),
            allowed_path_prefixes=("src/", "tests/"),
        ),
    )
    mig_out = trusted_root / "migrated_c7.json"
    mig_rec, mig_sha = migrate_legacy_approval_evidence(
        legacy_evidence_path=legacy_path,
        raw_grant_bytes=_GRANT_BYTES,
        output_path=mig_out,
        trusted_external_root=trusted_root,
        excluded_roots=(candidate_root,),
    )
    mig_ctx = MilestoneAuthorizationTrustContext(
        record_path=mig_out,
        expected_record_sha256=mig_sha,
        expected_approval_digest=mig_rec.approval_digest,
        trusted_external_root=trusted_root,
        excluded_roots=(candidate_root,),
        expected_project_id="proj-aipc",
        expected_milestone_id="milestone-c7",
        expected_commander_id="K.TRITOD",
    )
    assert load_and_verify_milestone_authorization(mig_ctx, phase_id="rce02").approval_id == (
        "appr-c7-mig"
    )

    # CLI issue subcommand
    raw_file = tmp_path / "grant.bin"
    raw_file.write_bytes(_GRANT_BYTES)
    cli_out = trusted_root / "cli_grant.json"
    rc = milestone_auth_cli_main(
        [
            "issue",
            "--raw-grant-file",
            str(raw_file),
            "--output",
            str(cli_out),
            "--trusted-root",
            str(trusted_root),
            "--excluded-root",
            str(candidate_root),
            "--record-id",
            "rec-cli-01",
            "--approval-id",
            "appr-cli-01",
            "--project-id",
            "proj-aipc",
            "--milestone-id",
            "milestone-c7",
            "--commander-id",
            "K.TRITOD",
            "--workstreams",
            "rce02",
            "--allowed-prefixes",
            "src/,tests/",
        ]
    )
    assert rc == 0
    assert cli_out.is_file()


def test_external_authorization_negative_boundaries(tmp_path: Path) -> None:
    exp = datetime(2026, 11, 19, 0, 0, tzinfo=UTC)
    trust_ctx, trusted_root, candidate_root = _issue_external_grant(tmp_path, expires_at=exp)

    # Absent
    missing_ctx = trust_ctx.model_copy(update={"record_path": trusted_root / "missing.json"})
    with pytest.raises(MilestoneAuthorizationError, match="not found"):
        load_and_verify_milestone_authorization(missing_ctx)

    # Expired
    with pytest.raises(MilestoneAuthorizationError, match="expired"):
        load_and_verify_milestone_authorization(
            trust_ctx, now=datetime(2026, 11, 20, 0, 0, tzinfo=UTC)
        )

    # Cross-project, cross-milestone, cross-Commander, wrong approval_digest
    for bad_update in (
        {"expected_project_id": "other-proj"},
        {"expected_milestone_id": "milestone-c6"},
        {"expected_commander_id": "OTHER.USER"},
        {"expected_approval_digest": "0" * 64},
    ):
        with pytest.raises(MilestoneAuthorizationError):
            load_and_verify_milestone_authorization(trust_ctx.model_copy(update=bad_update))

    # Cross-scope phase & path
    with pytest.raises(MilestoneAuthorizationError):
        load_and_verify_milestone_authorization(trust_ctx, phase_id="rce03")
    with pytest.raises(MilestoneAuthorizationError):
        load_and_verify_milestone_authorization(trust_ctx, in_scope_paths=("docs/README.md",))

    # Traversal and Milestone D / d01-d03
    with pytest.raises(MilestoneAuthorizationError):
        load_and_verify_milestone_authorization(trust_ctx, in_scope_paths=("../secret.txt",))
    with pytest.raises(MilestoneAuthorizationError):
        load_and_verify_milestone_authorization(trust_ctx, phase_id="d01")

    # YAML-only auth rejected
    yaml_path = trusted_root / "grant.yaml"
    yaml_path.write_text("decision: APPROVED\n", encoding="utf-8")
    with pytest.raises(MilestoneAuthorizationError, match="YAML-only"):
        load_and_verify_milestone_authorization(
            trust_ctx.model_copy(update={"record_path": yaml_path})
        )

    # Record inside excluded root rejected
    excl_file = candidate_root / "evil.json"
    excl_file.write_text(trust_ctx.record_path.read_text(encoding="utf-8"), encoding="utf-8")
    with pytest.raises(MilestoneAuthorizationError):
        load_and_verify_milestone_authorization(
            trust_ctx.model_copy(update={"record_path": excl_file})
        )

    # Symlink from trusted root into excluded root rejected
    symlink_path = trusted_root / "link_to_excluded.json"
    try:
        os.symlink(excl_file, symlink_path)
    except OSError:
        pass
    else:
        with pytest.raises(MilestoneAuthorizationError, match="symlink"):
            load_and_verify_milestone_authorization(
                trust_ctx.model_copy(update={"record_path": symlink_path})
            )

    # Tampered bytes rejected
    raw_json = json.loads(trust_ctx.record_path.read_text(encoding="utf-8"))
    raw_json["commander_id"] = "TAMPERED"
    trust_ctx.record_path.write_text(json.dumps(raw_json), encoding="utf-8")
    with pytest.raises(MilestoneAuthorizationError, match="SHA-256 mismatch"):
        load_and_verify_milestone_authorization(trust_ctx)


def test_task_queue_legacy_reload_and_external_trust_context_gate(tmp_path: Path) -> None:
    # 1. Legacy-only C7 initialization without external trust context is now rejected
    legacy_ev = tmp_path / "milestone_c7_approval.json"
    persist_milestone_authorization_evidence(
        legacy_ev,
        MilestoneAuthorizationEvidence(
            approval_id="appr-c7-leg",
            project_id="proj-aipc",
            milestone_id="milestone-c7",
            authorized_workstreams=("rce02",),
            allowed_path_prefixes=("src/", "tests/"),
        ),
    )
    contract = ApprovedTaskContract(
        task_id="rce02-task-01",
        phase_id="rce02",
        milestone_id="milestone-c7",
        title="RCE02 task",
        objective="Verify legacy rejection, reload, and external trust context.",
        in_scope=("src/aipc/controller/task_queue.py",),
        validation_commands=("pytest tests/unit/test_c7_rce02_authorization_eligibility.py",),
        approval_id="appr-c7-leg",
        approval_evidence_path=str(legacy_ev),
    )
    queue_path = tmp_path / "queue.json"
    legacy_q = PersistentTaskQueue(queue_path)
    with pytest.raises(ControllerError, match="External milestone authorization trust context"):
        legacy_q.initialize(
            project_id="proj-aipc",
            milestone_id="milestone-c7",
            contracts=(contract,),
            approval_id="appr-c7-leg",
            approval_evidence_path=legacy_ev,
        )

    # 2. Valid external trust context permits initialization and reload
    valid_ctx, _, _ = _issue_external_grant(tmp_path / "valid_q", approval_id="appr-c7-ext2")
    valid_q = PersistentTaskQueue(queue_path, milestone_trust_context=valid_ctx)
    valid_q.initialize(
        project_id="proj-aipc",
        milestone_id="milestone-c7",
        contracts=(contract,),
        approval_id="appr-c7-leg",
        approval_evidence_path=legacy_ev,
    )
    reloaded = PersistentTaskQueue(queue_path).load()
    assert reloaded.tasks[0].status == QueuedTaskStatus.ELIGIBLE

    # 3. External trust context gates begin_dispatch before RUNNING transition
    trust_ctx, _, _ = _issue_external_grant(tmp_path, approval_id="appr-c7-ext")
    ext_q = PersistentTaskQueue(queue_path, milestone_trust_context=trust_ctx)
    # Tamper external record before dispatch -> must fail closed and keep ELIGIBLE
    trust_ctx.record_path.write_text("{}", encoding="utf-8")
    with pytest.raises(ControllerError, match="External milestone"):
        ext_q.begin_dispatch("rce02-task-01", worker_id="w-1", provider="gemini-antigravity")
    assert ext_q.load().tasks[0].status == QueuedTaskStatus.ELIGIBLE

    # 4. Valid external record permits dispatch
    dispatched = valid_q.begin_dispatch(
        "rce02-task-01",
        worker_id="w-1",
        provider="gemini-antigravity",
    )
    assert dispatched.status == QueuedTaskStatus.RUNNING


def test_planner_engine_sibling_selection_and_external_grant_gate(
    tmp_path: Path,
    review_case: dict[str, Any],
) -> None:
    trust_ctx, _, _ = _issue_external_grant(
        tmp_path, project_id="example", approval_id="appr-c7-plan"
    )
    goal = HumanGoalSpec(
        project_id="example",
        project_name="Example",
        workspace=str(review_case["project"].project.workspace),
        milestone_id="milestone-c7",
        phase_id="rce02",
        phase_name="rce02-bounded-repair",
        goal_statement="Verify sibling availability and external grant gate.",
        acceptance_criteria=[
            AcceptanceCriterion(id="AC-tests", statement="Ten tests pass", required=True),
            AcceptanceCriterion(id="AC-optional", statement="Optional", required=False),
        ],
        in_scope_paths=("src/aipc/controller/task_queue.py",),
        validation_commands=("pytest",),
        approval_granted=True,
        approval_id="appr-c7-plan",
        milestone_trust_context=trust_ctx,
    )

    agy_obs = to_runtime_observation(
        _make_agy_report(
            model_eligibility={
                "gemini-3.8-flash-medium": "RATE_LIMITED",
                "gemini-4-hydrogen-medium": "QUALIFIED",
            }
        )
    )
    codex_obs = to_runtime_observation(
        qualify_codex_subscription_runtime(
            sdk_installed_override=True,
            sdk_version_override=CODEX_PINNED_SDK_VERSION,
            runtime_version_override=CODEX_PINNED_SDK_VERSION,
            auth_present_override=True,
        )
    )
    registry = build_default_subscription_role_registry((agy_obs, codex_obs))
    engine = PlannerOrchestrationEngine(role_registry=registry)
    init_state = review_case["state"].model_copy(update={"current_phase": "rce02"})

    selected_models: list[str] = []

    def _worker_fn(task_req: TaskRequest, binding: RoleModelBinding) -> AgentResult:
        selected_models.append(binding.model_id)
        return review_case["agent_result"].model_copy(
            update={
                "task_id": task_req.task_id,
                "agent_id": binding.binding_id,
                "provider": binding.provider_id,
                "metadata": {
                    **review_case["agent_result"].metadata,
                    "model_id": binding.model_id,
                    "session_id": f"sess-{binding.model_id}-{task_req.attempt}",
                },
            }
        )

    def _evidence_fn(task_req: TaskRequest, agent_res: AgentResult, attempt: int) -> Any:
        case_copy = dict(review_case)
        proj, _ = engine.synthesize_project_and_phase(goal)
        case_copy["project"] = proj
        case_copy["state"] = init_state.model_copy(
            update={
                "current_phase": "rce02",
                "current_attempt": attempt,
                "retries": init_state.retries.model_copy(update={"phase_revision": attempt - 1}),
                "active_task_id": task_req.task_id,
            }
        )
        case_copy["checkpoint"] = review_case["checkpoint"].model_copy(
            update={
                "checkpoint_id": f"phase-rce02-attempt-{attempt:02d}",
                "phase_id": "rce02",
                "attempt": attempt,
                "git_ref": f"refs/tags/aipc/example/phase-rce02/attempt-{attempt:02d}",
                "parent_checkpoint": "phase-rce02-pre",
            }
        )
        case_copy["task"] = task_req
        case_copy["agent_result"] = agent_res
        rev_ctx = context(case_copy)
        bundle = rev_ctx.evidence.model_copy(
            update={
                "phase_id": "rce02",
                "attempt": attempt,
                "task_id": task_req.task_id,
            }
        )
        (Path(case_copy["evidence_root"]) / "manifest.json").write_text(
            bundle.model_dump_json(), encoding="utf-8"
        )
        return rev_ctx.model_copy(update={"evidence": bundle})

    def _reviewer_fn(rev_ctx: Any, binding: RoleModelBinding) -> ReviewResult:
        return ReviewResult(
            schema_version="1.0",
            review_id="rev-01",
            task_id=rev_ctx.task.task_id,
            verdict=ReviewVerdict.PASS,
            confidence=0.95,
            criteria=[
                CriterionResult(
                    criterion_id="AC-tests",
                    status=CriterionStatus.PASS,
                    evidence=["test_log"],
                    notes="Passed.",
                ),
                CriterionResult(
                    criterion_id="AC-optional",
                    status=CriterionStatus.PASS,
                    evidence=["test_log"],
                    notes="Optional satisfied.",
                ),
            ],
            failed_criteria=[],
            required_fixes=[],
            risks=[],
            next_phase_allowed=True,
        )

    outcome = engine.run_closed_loop(
        goal,
        initial_state=init_state,
        worker_fn=_worker_fn,
        evidence_fn=_evidence_fn,
        reviewer_fn=_reviewer_fn,
    )
    assert outcome.final_outcome == "PASS"
    assert selected_models == ["gemini-4-hydrogen-medium"]

    # Tampered trust context rejected at HumanGoalSpec validation
    with pytest.raises(ValueError, match="milestone_trust_context"):
        HumanGoalSpec(
            project_id="example",
            project_name="Example",
            workspace=str(review_case["project"].project.workspace),
            milestone_id="milestone-c7",
            phase_id="rce02",
            phase_name="rce02-bounded-repair",
            goal_statement="Bad project",
            acceptance_criteria=[
                AcceptanceCriterion(id="AC-tests", statement="Fail", required=True)
            ],
            in_scope_paths=("src/aipc/controller/task_queue.py",),
            validation_commands=("pytest",),
            approval_granted=True,
            milestone_trust_context=trust_ctx.model_copy(
                update={"expected_project_id": "wrong-project"}
            ),
        )


def test_subscription_worker_external_grant_gate_and_rate_limit_interruption(
    tmp_path: Path,
) -> None:
    trust_ctx, _, candidate_root = _issue_external_grant(tmp_path)
    subprocess.run(["git", "init"], cwd=candidate_root, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=candidate_root,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=candidate_root,
        check=True,
        capture_output=True,
    )
    target_file = candidate_root / "src" / "aipc" / "sample.py"
    target_file.parent.mkdir(parents=True, exist_ok=True)
    target_file.write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=candidate_root, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"], cwd=candidate_root, check=True, capture_output=True
    )

    ev_dir = tmp_path / "evidence"
    invoked = False

    async def _mock_invoker(task, cfg, env, model) -> WorkerInvocationResponse:
        nonlocal invoked
        invoked = True
        return WorkerInvocationResponse(session_id="sess-1", model_used=model, summary="ok")

    task = TaskRequest(
        schema_version="1.0",
        task_id="task-rce02-01",
        project_id="proj-aipc",
        phase_id="rce02",
        attempt=1,
        task_type="implementation",
        prompt="Update sample.py",
        workspace=str(candidate_root),
        allowed_tools=["read_file", "edit_file"],
        forbidden_actions=["git_push", "shell_exec"],
        timeout_seconds=120,
    )

    # Mismatched project in task_request fails closed BEFORE worker_invoker
    bad_task = task.model_copy(update={"project_id": "wrong-proj"})
    cfg = SubscriptionWorkerConfig(
        candidate_worktree=str(candidate_root),
        evidence_output_dir=str(ev_dir),
        in_scope_paths=("src/aipc/",),
        model_requested="gemini-4-hydrogen-medium",
        require_non_empty_diff=False,
        milestone_trust_context=trust_ctx,
    )
    worker = AntigravitySubscriptionWorkerAutomation(
        cfg,
        qualification_report=_make_agy_report(),
        worker_invoker=_mock_invoker,
    )
    with pytest.raises(MilestoneAuthorizationError, match="does not match TaskRequest.project_id"):
        asyncio.run(worker.execute_worker_task(bad_task))
    assert not invoked

    # Rate-limited requested model raises ProviderInterruption(RATE_LIMITED) before dispatch
    rl_cfg = SubscriptionWorkerConfig(
        candidate_worktree=str(candidate_root),
        evidence_output_dir=str(ev_dir),
        in_scope_paths=("src/aipc/",),
        model_requested="gemini-3.8-flash-medium",
        require_non_empty_diff=False,
        milestone_trust_context=trust_ctx,
    )
    rl_worker = AntigravitySubscriptionWorkerAutomation(
        rl_cfg,
        qualification_report=_make_agy_report(
            model_eligibility={"gemini-3.8-flash-medium": "RATE_LIMITED"}
        ),
        worker_invoker=_mock_invoker,
    )
    with pytest.raises(ProviderInterruption) as exc_info:
        asyncio.run(rl_worker.execute_worker_task(task))
    assert exc_info.value.condition == ProviderCondition.RATE_LIMITED
    assert not invoked

    # Valid external grant + qualified model dispatches cleanly
    ok_outcome = asyncio.run(worker.execute_worker_task(task))
    assert invoked
    assert ok_outcome.agent_result.status == AgentStatus.COMPLETED


def _make_legacy_evidence_file(
    path: Path,
    *,
    project_id: str = "proj-aipc",
    milestone_id: str = "milestone-c7",
    approval_id: str = "appr-c7-leg",
    workstreams: tuple[str, ...] = ("rce01", "rce02", "rce03"),
) -> Path:
    persist_milestone_authorization_evidence(
        path,
        MilestoneAuthorizationEvidence(
            approval_id=approval_id,
            project_id=project_id,
            milestone_id=milestone_id,
            authorized_workstreams=workstreams,
            allowed_path_prefixes=("src/", "tests/"),
        ),
    )
    return path


def _init_git_worktree(candidate_root: Path) -> Path:
    subprocess.run(["git", "init"], cwd=candidate_root, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=candidate_root,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=candidate_root,
        check=True,
        capture_output=True,
    )
    target_file = candidate_root / "src" / "aipc" / "sample.py"
    target_file.parent.mkdir(parents=True, exist_ok=True)
    target_file.write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=candidate_root, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=candidate_root,
        check=True,
        capture_output=True,
    )
    return candidate_root


def test_remediation01_task_queue_rejects_missing_external_trust_for_c7(
    tmp_path: Path,
) -> None:
    legacy_ev = _make_legacy_evidence_file(tmp_path / "legacy_c7.json")
    for phase_id in ("rce01", "rce02", "rce03"):
        contract = ApprovedTaskContract(
            task_id=f"{phase_id}-task-01",
            phase_id=phase_id,
            milestone_id="milestone-c7",
            title=f"C7 {phase_id} task",
            objective="Verify missing external trust is rejected.",
            in_scope=("src/aipc/controller/task_queue.py",),
            validation_commands=("pytest tests/unit/test_c7_rce02_authorization_eligibility.py",),
            approval_id="appr-c7-leg",
            approval_evidence_path=str(legacy_ev),
        )
        queue = PersistentTaskQueue(tmp_path / f"queue_{phase_id}.json")
        with pytest.raises(ControllerError):
            queue.initialize(
                project_id="proj-aipc",
                milestone_id="milestone-c7",
                contracts=(contract,),
                approval_id="appr-c7-leg",
                approval_evidence_path=legacy_ev,
            )

    # Dispatch without external trust must also fail closed even on an existing queue
    valid_ctx, _, _ = _issue_external_grant(
        tmp_path / "ext_proj",
        project_id="proj-ext-c7",
        approval_id="appr-c7-ext",
        workstreams=("rce01", "rce02", "rce03"),
    )
    external_legacy_ev = _make_legacy_evidence_file(
        tmp_path / "external_legacy_c7.json",
        project_id="proj-ext-c7",
        approval_id="appr-c7-ext",
    )
    ext_contract = ApprovedTaskContract(
        task_id="rce02-task-ext",
        phase_id="rce02",
        milestone_id="milestone-c7",
        title="External C7 task",
        objective="Verify external trust required at dispatch.",
        in_scope=("src/aipc/controller/task_queue.py",),
        validation_commands=("pytest",),
        approval_id="appr-c7-ext",
        approval_evidence_path=str(external_legacy_ev),
    )
    ext_queue_path = tmp_path / "ext_queue.json"
    PersistentTaskQueue(ext_queue_path, milestone_trust_context=valid_ctx).initialize(
        project_id="proj-ext-c7",
        milestone_id="milestone-c7",
        contracts=(ext_contract,),
        approval_id="appr-c7-ext",
        approval_evidence_path=external_legacy_ev,
    )
    untrusted_q = PersistentTaskQueue(ext_queue_path)
    with pytest.raises(ControllerError):
        untrusted_q.begin_dispatch(
            "rce02-task-ext",
            worker_id="w-1",
            provider="gemini-antigravity",
        )
    assert untrusted_q.load().tasks[0].status == QueuedTaskStatus.ELIGIBLE

    # Positive external-record-governed nonlegacy project remains allowed
    dispatched = PersistentTaskQueue(
        ext_queue_path,
        milestone_trust_context=valid_ctx,
    ).begin_dispatch(
        "rce02-task-ext",
        worker_id="w-1",
        provider="gemini-antigravity",
    )
    assert dispatched.status == QueuedTaskStatus.RUNNING

    # Explicit Milestone D remains blocked
    with pytest.raises((ControllerError, ValueError)):
        d_contract = ApprovedTaskContract(
            task_id="d01-task-01",
            phase_id="d01",
            milestone_id="milestone-d",
            title="Blocked D task",
            objective="Must be blocked.",
            in_scope=("src/aipc/controller/task_queue.py",),
            validation_commands=("pytest",),
        )
        PersistentTaskQueue(tmp_path / "d_queue.json").initialize(
            project_id="proj-aipc",
            milestone_id="milestone-d",
            contracts=(d_contract,),
        )

    # Pre-C7 compatibility preserved (Milestone C.6 without milestone_trust_context)
    c6_ev = _make_legacy_evidence_file(
        tmp_path / "legacy_c6.json",
        milestone_id="milestone-c6",
        approval_id="appr-c6-leg",
        workstreams=("cisl01",),
    )
    c6_contract = ApprovedTaskContract(
        task_id="cisl01-task-01",
        phase_id="cisl01",
        milestone_id="milestone-c6",
        title="C6 compatibility task",
        objective="Preserve pre-C7 compatibility.",
        in_scope=("src/aipc/controller/task_queue.py",),
        validation_commands=("pytest",),
        approval_id="appr-c6-leg",
        approval_evidence_path=str(c6_ev),
    )
    c6_q = PersistentTaskQueue(tmp_path / "c6_queue.json")
    c6_q.initialize(
        project_id="proj-aipc",
        milestone_id="milestone-c6",
        contracts=(c6_contract,),
        approval_id="appr-c6-leg",
        approval_evidence_path=c6_ev,
    )
    c6_rec = c6_q.begin_dispatch(
        "cisl01-task-01",
        worker_id="w-1",
        provider="gemini-antigravity",
    )
    assert c6_rec.status == QueuedTaskStatus.RUNNING


def test_remediation01_planner_rejects_missing_external_trust_for_c7_and_aliases(
    tmp_path: Path,
    review_case: dict[str, Any],
) -> None:
    workspace = str(review_case["project"].project.workspace)
    legacy_ev = _make_legacy_evidence_file(
        tmp_path / "legacy_c7_plan.json",
        project_id="example",
        milestone_id="milestone-c7",
        approval_id="appr-c7-leg",
        workstreams=("rce01", "rce02", "rce03"),
    )
    criteria = [AcceptanceCriterion(id="AC-tests", statement="Tests pass", required=True)]

    for milestone_id, phase_id in (
        ("milestone-c7", "rce01"),
        ("c.7", "rce02"),
        ("milestone-c.7", "rce03"),
    ):
        with pytest.raises(ValueError):
            HumanGoalSpec(
                project_id="example",
                project_name="Example",
                workspace=workspace,
                milestone_id=milestone_id,
                phase_id=phase_id,
                phase_name=f"{phase_id}-repair",
                goal_statement="Legacy evidence without external trust must fail.",
                acceptance_criteria=criteria,
                in_scope_paths=("src/aipc/controller/task_queue.py",),
                validation_commands=("pytest",),
                approval_granted=True,
                approval_id="appr-c7-leg",
                approval_evidence_path=str(legacy_ev),
                milestone_trust_context=None,
            )

    # Bypassed goal construction must still fail at planner execution
    bypassed_goal = HumanGoalSpec.model_construct(
        project_id="example",
        project_name="Example",
        workspace=workspace,
        milestone_id="milestone-c7",
        phase_id="rce02",
        phase_name="rce02-repair",
        goal_statement="Bypassed goal without external trust.",
        acceptance_criteria=tuple(criteria),
        in_scope_paths=("src/aipc/controller/task_queue.py",),
        out_of_scope_paths=(),
        validation_commands=("pytest",),
        allowed_tools=("filesystem",),
        forbidden_actions=("network",),
        timeout_seconds=600,
        max_attempts=3,
        high_risk_mutation=False,
        expands_approved_scope=False,
        approval_granted=True,
        approval_id="appr-c7-leg",
        approval_evidence_path=str(legacy_ev),
        milestone_trust_context=None,
    )
    codex_obs = to_runtime_observation(
        qualify_codex_subscription_runtime(
            sdk_installed_override=True,
            sdk_version_override=CODEX_PINNED_SDK_VERSION,
            runtime_version_override=CODEX_PINNED_SDK_VERSION,
            auth_present_override=True,
        )
    )
    engine = PlannerOrchestrationEngine(
        role_registry=build_default_subscription_role_registry((codex_obs,))
    )
    init_state = review_case["state"].model_copy(update={"current_phase": "rce02"})
    with pytest.raises((ValueError, ControllerError)):
        engine.build_initial_plan(bypassed_goal, state=init_state)

    # Explicit Milestone D remains blocked
    with pytest.raises(ValueError):
        HumanGoalSpec(
            project_id="example",
            project_name="Example",
            workspace=workspace,
            milestone_id="milestone-d",
            phase_id="d01",
            phase_name="d01-blocked",
            goal_statement="Milestone D must remain blocked.",
            acceptance_criteria=criteria,
            in_scope_paths=("src/aipc/controller/task_queue.py",),
            validation_commands=("pytest",),
            approval_granted=True,
        )

    # Pre-C7 compatibility preserved (Milestone C.6 with legacy evidence)
    c6_ev = _make_legacy_evidence_file(
        tmp_path / "legacy_c6_plan.json",
        project_id="example",
        milestone_id="milestone-c6",
        approval_id="appr-c6-leg",
        workstreams=("cisl01",),
    )
    c6_goal = HumanGoalSpec(
        project_id="example",
        project_name="Example",
        workspace=workspace,
        milestone_id="milestone-c6",
        phase_id="cisl01",
        phase_name="cisl01-compat",
        goal_statement="Preserve C6 compatibility.",
        acceptance_criteria=criteria,
        in_scope_paths=("src/aipc/controller/task_queue.py",),
        validation_commands=("pytest",),
        approval_granted=True,
        approval_id="appr-c6-leg",
        approval_evidence_path=str(c6_ev),
    )
    assert c6_goal.milestone_id == "milestone-c6"


def test_remediation01_worker_rejects_missing_external_trust_before_invoker(
    tmp_path: Path,
) -> None:
    trust_ctx, _, candidate_root = _issue_external_grant(
        tmp_path,
        project_id="proj-ext-c7",
        approval_id="appr-c7-worker",
        workstreams=("rce01", "rce02", "rce03"),
    )
    _init_git_worktree(candidate_root)
    ev_dir = tmp_path / "evidence"
    invoked = False

    async def _mock_invoker(task, cfg, env, model) -> WorkerInvocationResponse:
        nonlocal invoked
        invoked = True
        return WorkerInvocationResponse(session_id="sess-r01", model_used=model, summary="ok")

    untrusted_cfg = SubscriptionWorkerConfig(
        candidate_worktree=str(candidate_root),
        evidence_output_dir=str(ev_dir),
        in_scope_paths=("src/aipc/",),
        model_requested="gemini-4-hydrogen-medium",
        require_non_empty_diff=False,
        milestone_trust_context=None,
    )
    untrusted_worker = AntigravitySubscriptionWorkerAutomation(
        untrusted_cfg,
        qualification_report=_make_agy_report(),
        worker_invoker=_mock_invoker,
    )

    base_task = TaskRequest(
        schema_version="1.0",
        task_id="task-r01-check",
        project_id="proj-ext-c7",
        phase_id="rce02",
        attempt=1,
        task_type="implementation",
        prompt="Update sample.py",
        workspace=str(candidate_root),
        allowed_tools=["read_file", "edit_file"],
        forbidden_actions=["git_push", "shell_exec"],
        timeout_seconds=120,
    )

    for phase_id, meta in (
        ("rce01", {}),
        ("rce02", {}),
        ("rce03", {}),
        ("c.7", {"milestone_id": "c.7"}),
        ("phase-c7", {"milestone_id": "milestone-c7"}),
        ("d01", {"milestone_id": "milestone-d"}),
    ):
        invoked = False
        bad_task = base_task.model_copy(update={"phase_id": phase_id, "metadata": meta})
        with pytest.raises((MilestoneAuthorizationError, ControllerError, ValueError)):
            asyncio.run(untrusted_worker.execute_worker_task(bad_task))
        assert not invoked

    # Positive external-record-governed nonlegacy project remains allowed
    trusted_cfg = untrusted_cfg.model_copy(update={"milestone_trust_context": trust_ctx})
    trusted_worker = AntigravitySubscriptionWorkerAutomation(
        trusted_cfg,
        qualification_report=_make_agy_report(),
        worker_invoker=_mock_invoker,
    )
    invoked = False
    ok_task = base_task.model_copy(update={"phase_id": "rce03"})
    ok_outcome = asyncio.run(trusted_worker.execute_worker_task(ok_task))
    assert invoked
    assert ok_outcome.agent_result.status == AgentStatus.COMPLETED

    # Pre-C7 compatibility preserved (C6 and Phase 24 without milestone_trust_context)
    for compat_phase in ("cisl01", "phase-24"):
        invoked = False
        compat_task = base_task.model_copy(update={"phase_id": compat_phase, "metadata": {}})
        compat_outcome = asyncio.run(untrusted_worker.execute_worker_task(compat_task))
        assert invoked
        assert compat_outcome.agent_result.status == AgentStatus.COMPLETED


@pytest.mark.parametrize(
    "phase_id",
    [
        "0",
        "00",
        "01",
        "09",
        "9",
        "10",
        "24",
        "phase00",
        "phase09",
        "phase24",
        "phase-00",
        "phase-09",
        "phase-24",
        "phase_00",
        "phase_09",
        "phase_24",
        "phase-09-review",
    ],
)
def test_pre_c7_numeric_phase_aliases_and_c7_d_future_boundaries(phase_id: str) -> None:
    assert not requires_external_milestone_trust(milestone_id="phase-24", phase_id=phase_id)
    assert requires_external_milestone_trust(milestone_id="milestone-c7", phase_id=phase_id)
    assert requires_external_milestone_trust(milestone_id="c.7", phase_id=phase_id)
    assert requires_external_milestone_trust(milestone_id="milestone-future-x", phase_id=phase_id)
    assert requires_external_milestone_trust(
        milestone_id="phase-24", phase_id=phase_id, task_id="rce02-task-01"
    )
    assert requires_external_milestone_trust(milestone_id="milestone-d", phase_id=phase_id)


@pytest.mark.parametrize(
    "bad_phase",
    [
        "25",
        "phase-25",
        "phase25",
        "99",
        "000",
        "unknown-phase",
        "d01",
        "rce01",
        "rce02",
        "rce03",
        "c.7",
    ],
)
def test_non_legacy_or_blocked_phases_still_require_external_trust(bad_phase: str) -> None:
    assert requires_external_milestone_trust(milestone_id="phase-24", phase_id=bad_phase)


def test_phase24_queue_with_bare_phase09_remains_compatible(tmp_path: Path) -> None:
    queue = PersistentTaskQueue(tmp_path / "phase24_09_queue.json")
    contract = ApprovedTaskContract(
        task_id="task-p24-09",
        phase_id="09",
        milestone_id="phase-24",
        title="Legacy Phase 24 bare phase 09 task",
        objective="Verify bare numeric phase 09 works without external trust context.",
        in_scope=("src/aipc/controller/repair_coordinator.py",),
        validation_commands=("pytest tests/unit/test_c5_rg02_triage.py -q",),
    )
    queue.initialize(
        project_id="example",
        milestone_id="phase-24",
        contracts=(contract,),
    )
    dispatched = queue.begin_dispatch(
        contract.task_id,
        worker_id="agy-flash",
        provider="gemini",
    )
    assert dispatched.status == QueuedTaskStatus.RUNNING

    with pytest.raises((ControllerError, ValueError)):
        d_contract = ApprovedTaskContract(
            task_id="d01-blocked-09",
            phase_id="09",
            milestone_id="milestone-d",
            title="Blocked D with numeric phase",
            objective="Must remain blocked.",
            in_scope=("src/aipc/controller/task_queue.py",),
            validation_commands=("pytest",),
        )
        PersistentTaskQueue(tmp_path / "d_09_queue.json").initialize(
            project_id="example",
            milestone_id="milestone-d",
            contracts=(d_contract,),
        )
