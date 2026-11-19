"""Deterministic unit and boundary tests for C.7 RCE01 Subscription Reviewer CLI."""

from __future__ import annotations

import asyncio
import hashlib
import json
import subprocess
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import jsonschema
import pytest
import yaml
from tests.llm.support import proposal
from tests.review.conftest import context

from aipc.adapters.gemini.doctor import AntigravityQualificationResult
from aipc.adapters.subscription_qualification import qualify_antigravity_subscription_runtime
from aipc.agents.role_registry import (
    ProviderRuntimeObservation,
    RoleBasedModelRegistry,
    RoleModelBinding,
    RoleSelectionRequest,
)
from aipc.agents.routing_models import CapabilityStatus
from aipc.controller import (
    ApprovedTaskContract,
    MilestoneAuthorizationEvidence,
    persist_milestone_authorization_evidence,
)
from aipc.domain.models import TestSummary as DomainTestSummary
from aipc.project.models import ProjectDefinition
from aipc.review.context import checked_context, digest
from aipc.review.governance_policy import PolicyTrustContext
from aipc.review.governance_triage import UntrustedFindingDescriptor
from aipc.review.llm import (
    LIVE_REVIEW_UNVERIFIED,
    AntigravitySubscriptionReviewerTransport,
    CodexSubscriptionReviewerTransport,
    LLMReviewerConfig,
    LLMReviewError,
    LLMReviewRequest,
    SubscriptionReviewerConfig,
)
from aipc.review.llm.models import ReviewRequestManifest
from aipc.review.llm.request import sha
from aipc.review.models import CriterionRule
from aipc.review.subscription_reviewer_gate import (
    EXIT_MALFORMED_JOB,
    EXIT_PASS,
    EXIT_PROVIDER_UNAVAILABLE,
    EXIT_REVISE_OR_BLOCKED,
    C7ReviewerCliReceipt,
    C7ReviewJob,
    SubscriptionReviewerGateConfig,
    SubscriptionReviewerGateError,
    _report_to_observation,
    _write_json_exclusive_immutable,
    build_cli_subscription_reviewer_transport_factory,
    get_c7_review_job_json_schema,
    main,
    qualify_subscription_reviewer_runtimes,
    run_subscription_reviewer_job,
)

pytest_plugins = ("tests.review.conftest",)

_C7_BASELINE_SHA = "f98846e8639b3ab846afe49454a6f79f6b236c36"
_STALE_SHA = "b" * 40


def _truthful_transport_factory(
    runner: Any,
) -> Callable[[RoleModelBinding, SubscriptionReviewerConfig], Any]:
    """Select the exact transport matching binding.provider_family and assert auth mapping."""

    def _factory(binding: RoleModelBinding, sub_cfg: SubscriptionReviewerConfig) -> Any:
        if binding.provider_family == "openai_codex":
            assert sub_cfg.auth_mode == "codex_subscription"
            assert sub_cfg.llm_config.provider == "openai"
            return CodexSubscriptionReviewerTransport(sub_cfg, runner=runner)
        if binding.provider_family == "google_antigravity":
            assert sub_cfg.auth_mode == "antigravity_subscription"
            assert sub_cfg.llm_config.provider == "gemini"
            return AntigravitySubscriptionReviewerTransport(sub_cfg, runner=runner)
        raise AssertionError(f"Unexpected reviewer provider_family: {binding.provider_family}")

    return _factory


def _init_git_worktree(ws: Path, content: str = "x = 1\n") -> str:
    ws.mkdir(parents=True, exist_ok=True)
    if not (ws / ".git").exists():
        for cmd in (
            ["git", "init"],
            ["git", "config", "user.email", "test@example.com"],
            ["git", "config", "user.name", "AIPC Test"],
        ):
            subprocess.run(cmd, cwd=str(ws), check=True, capture_output=True, text=True)
    (ws / "candidate.py").write_text(content, encoding="utf-8")
    (ws / ".gitignore").write_text("ignored_marker.tmp\n", encoding="utf-8")
    (ws / "ignored_marker.tmp").write_text("clean-ignored\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=str(ws), check=True, capture_output=True, text=True)
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "candidate commit"],
        cwd=str(ws),
        check=True,
        capture_output=True,
        text=True,
    )
    res = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(ws),
        check=True,
        capture_output=True,
        text=True,
    )
    return res.stdout.strip().lower()


def _policy_payload(owner: str = "K.TRITOD") -> dict[str, Any]:
    return {
        "policy_version": "1.0",
        "policy_id": "aipc-c7-review-governance",
        "operating_profile": "subscription_only",
        "owner": owner,
        "source_authority": "operator_owned_external_policy",
        "review": {
            "preserve_raw_review": True,
            "preserve_canonical_verdict": True,
            "require_exact_candidate_sha": True,
            "default_independence": "prefer_cross_provider",
            "require_objective_evidence_for_blockers": True,
            "unsupported_or_unverifiable_critical_finding": "NEEDS_TRIAGE_HUMAN",
            "advisory": {"auto_dispatch": False, "action": "AUDIT_BACKLOG_ONLY"},
            "release_blocker": {
                "allow_release": False,
                "auto_dispatch_only_if_authorized_and_within_budget": True,
            },
            "needs_triage": {
                "allow_release_if_credible_unresolved": False,
                "action": "HUMAN_REVIEW_REQUIRED",
            },
        },
        "repair_budget": {
            "default_auto_code_repairs_per_task": 2,
            "total_auto_code_repairs_per_milestone": 8,
            "budget_exhausted_action": "HALT_ON_RETRY_EXHAUSTION",
            "preserve_unresolved_blockers": True,
            "counter_scope": "approved_milestone_and_root_task_lineage",
            "persist_across_restarts": True,
            "persist_across_provider_fallback": True,
            "reject_counter_reset_by_task_splitting": True,
            "extension": {
                "human_commander_approval_required": True,
                "requires_explicit_task_and_scope": True,
                "requires_max_additional_attempts": True,
                "auditable_and_expiring": True,
            },
        },
        "change_scope": {
            "cannot_expand_milestone_from_review": True,
            "prohibit_speculative_security_hardening": True,
            "no_second_phase_gate": True,
            "no_auto_merge": True,
            "no_auto_deploy": True,
            "no_milestone_d": True,
        },
    }


def _probe(*, codex_ok: bool = True, agy_ok: bool = True) -> tuple[ProviderRuntimeObservation, ...]:
    return (
        ProviderRuntimeObservation(
            provider_id="codex",
            provider_family="openai_codex",
            auth_mode="codex_subscription",
            runtime_state="QUALIFIED" if codex_ok else "UNAVAILABLE",
            supported_models=("gpt-5.3-codex",) if codex_ok else (),
            supported_modes=("plan", "reviewer") if codex_ok else (),
            capabilities=(
                {
                    "filesystem": CapabilityStatus.AVAILABLE,
                    "read_only_review": CapabilityStatus.AVAILABLE,
                    "readonly_review": CapabilityStatus.AVAILABLE,
                }
                if codex_ok
                else {}
            ),
        ),
        ProviderRuntimeObservation(
            provider_id="gemini-antigravity",
            provider_family="google_antigravity",
            auth_mode="antigravity_subscription",
            runtime_state="QUALIFIED" if agy_ok else "UNAVAILABLE",
            supported_models=("gemini-3.8-flash-medium",) if agy_ok else (),
            supported_modes=("plan", "reviewer") if agy_ok else (),
            capabilities=(
                {
                    "filesystem": CapabilityStatus.AVAILABLE,
                    "read_only_review": CapabilityStatus.AVAILABLE,
                    "readonly_review": CapabilityStatus.AVAILABLE,
                }
                if agy_ok
                else {}
            ),
        ),
    )


def _build_job_bundle(
    review_case: dict[str, Any],
    tmp_path: Path,
    *,
    test_log: str = "10 passed in 0.1s\n",
    tests_failed: int = 0,
    rules: tuple[CriterionRule, ...] = (),
    finding_descriptors: tuple[UntrustedFindingDescriptor, ...] | None = None,
    expected_sha_override: str | None = None,
    cli_receipt_path: Path | None = None,
    evidence_output_dir: Path | None = None,
) -> tuple[Path, C7ReviewJob, Path, Path]:
    case = dict(review_case)
    original_ws = Path(review_case["task"].workspace)
    stable_root = original_ws.parent / "stable_checkout"
    stable_root.mkdir(parents=True, exist_ok=True)
    ws = tmp_path / "workspace"
    ev_root = tmp_path / "evidence"
    ev_root.mkdir(parents=True, exist_ok=True)

    data = case["project"].model_dump(mode="json")
    data["project"]["workspace"] = str(ws)
    data["phases"][0]["id"] = "rce01"
    criteria = list(data["phases"][0]["acceptance_criteria"])
    if not any(c.get("id") == "AC-design" for c in criteria):
        criteria.append(
            {
                "id": "AC-design",
                "statement": "Core logic is independent of providers.",
                "required": True,
            }
        )
    data["phases"][0]["acceptance_criteria"] = criteria
    case["project"] = ProjectDefinition.model_validate(data)
    case["task"] = case["task"].model_copy(
        update={
            "task_id": "task-rce01-review",
            "phase_id": "rce01",
            "workspace": str(ws),
        }
    )
    case["state"] = case["state"].model_copy(
        update={"current_phase": "rce01", "active_task_id": "task-rce01-review"}
    )
    case["evidence_root"] = str(ev_root)
    real_sha = _init_git_worktree(ws)
    case["checkpoint"] = case["checkpoint"].model_copy(
        update={
            "phase_id": "rce01",
            "checkpoint_id": "phase-rce01-attempt-01",
            "parent_checkpoint": "phase-rce01-pre",
            "git_ref": "refs/tags/aipc/example/phase-rce01/attempt-01",
            "git_commit": real_sha,
        }
    )
    res_data = case["agent_result"].model_dump(mode="json")
    res_data["task_id"] = "task-rce01-review"
    res_data["provider"] = "gemini"
    res_data["agent_id"] = "worker-agy-fast"
    res_data["tests"] = DomainTestSummary(
        total=10,
        passed=10 - tests_failed,
        failed=tests_failed,
        skipped=0,
    ).model_dump(mode="json")
    res_data["metadata"] = {
        "provider_family": "google_antigravity",
        "session_id": "worker-sess-agy-001",
    }
    case["agent_result"] = type(case["agent_result"]).model_validate(res_data)
    ctx = context(
        case,
        log=test_log,
        artifacts=(("design", b"Core imports a neutral transport protocol."),),
    )
    aligned_evidence = ctx.evidence.model_copy(update={"phase_id": "rce01"})
    (ev_root / "manifest.json").write_text(
        aligned_evidence.model_dump_json(),
        encoding="utf-8",
    )
    ctx = ctx.model_copy(update={"evidence": aligned_evidence})
    if rules:
        ctx = ctx.model_copy(update={"rules": rules})
    ctx, _ = checked_context(ctx)

    ext_root = tmp_path / "external_runtime"
    policy_path = ext_root / "policy" / "c5_review_policy.yaml"
    policy_path.parent.mkdir(parents=True, exist_ok=True)
    raw_policy = yaml.safe_dump(_policy_payload(), sort_keys=False).encode("utf-8")
    policy_path.write_bytes(raw_policy)
    policy_sha = hashlib.sha256(raw_policy).hexdigest()

    approval_path = ext_root / "approvals" / "c7_approval.json"
    evidence = MilestoneAuthorizationEvidence(
        approval_id="ktritod-c7-20261118",
        project_id=ctx.project.project.id,
        milestone_id="milestone-c7",
        decision="APPROVED",
        authorized_by="K.TRITOD",
        baseline_commit_sha=_C7_BASELINE_SHA,
        authorized_workstreams=("rce01", "rce02", "rce03"),
        allowed_path_prefixes=("src/aipc/review/", "tests/unit/"),
    )
    persist_milestone_authorization_evidence(approval_path, evidence)

    trust_ctx = PolicyTrustContext(
        policy_path=policy_path,
        expected_sha256=policy_sha,
        trusted_external_root=ext_root,
        excluded_roots=(ws.resolve(), stable_root.resolve()),
        approval_path=approval_path,
        expected_approval_id="ktritod-c7-20261118",
        expected_owner="K.TRITOD",
        expected_project_id=ctx.project.project.id,
        expected_milestone_id="milestone-c7",
    )
    contract = ApprovedTaskContract(
        task_id=ctx.task.task_id,
        phase_id="rce01",
        milestone_id="milestone-c7",
        title="Implement RCE01 CLI",
        objective="Execute independent subscription review CLI",
        in_scope=("src/aipc/review/subscription_reviewer_gate.py",),
        validation_commands=("pytest tests/unit/test_c7_rce01_reviewer_cli.py -q",),
        approval_id="ktritod-c7-20261118",
        approval_evidence_path=str(approval_path),
    )

    out_dir = evidence_output_dir or (tmp_path / "cli_out" / "gate_evidence")
    receipt_file = cli_receipt_path or (tmp_path / "cli_out" / "rce01_receipt.json")
    job = C7ReviewJob(
        job_id="job-rce01-001",
        review_context=ctx,
        gate_config=SubscriptionReviewerGateConfig(
            candidate_worktree=str(ws),
            evidence_output_dir=str(out_dir),
            independence_policy="prefer_cross_provider",
        ),
        expected_candidate_sha=expected_sha_override or real_sha,
        worker_agent_id="worker-agy-fast",
        worker_provider_family="google_antigravity",
        worker_session_id="worker-sess-agy-001",
        planner_provider_family="openai_codex",
        planner_session_id="planner-sess-001",
        trust_context=trust_ctx,
        task_contract=contract,
        finding_descriptors=finding_descriptors,
        cli_receipt_path=str(receipt_file),
        candidate_root=str(ws),
        stable_root=str(stable_root),
    )
    job_path = tmp_path / "job.json"
    job_path.write_text(json.dumps(job.model_dump(mode="json"), indent=2), encoding="utf-8")
    return job_path, job, ws, stable_root


def test_rce01_job_schema_validates_against_jsonschema(
    review_case: dict[str, Any],
    tmp_path: Path,
) -> None:
    """Generated C7ReviewJob JSON Schema is valid Draft 2020-12 and enforces strict fields."""
    _, job, _, _ = _build_job_bundle(review_case, tmp_path)
    schema = get_c7_review_job_json_schema()
    jsonschema.Draft202012Validator.check_schema(schema)
    payload = job.model_dump(mode="json")
    jsonschema.validate(instance=payload, schema=schema)

    payload_with_extra = dict(payload, unexpected_field="disallowed")
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=payload_with_extra, schema=schema)


def test_rce01_cli_pass_with_triage_and_atomic_sidecar(
    review_case: dict[str, Any],
    tmp_path: Path,
) -> None:
    """CLI PASS delegates to B02/PhaseGate/triage and writes atomic receipt + .sha256 sidecar."""
    job_path, job, _, _ = _build_job_bundle(review_case, tmp_path)

    async def pass_runner(
        req: LLMReviewRequest,
        _env: Mapping[str, str],
        _session_id: str,
    ) -> dict[str, Any]:
        return proposal(req, status="PASS")

    code, receipt = run_subscription_reviewer_job(
        job_path,
        runtime_probe=lambda: _probe(codex_ok=True, agy_ok=True),
        transport_factory=_truthful_transport_factory(pass_runner),
    )
    assert code == EXIT_PASS
    assert isinstance(receipt, C7ReviewerCliReceipt)
    assert receipt.gate_outcome == "PASS"
    assert receipt.phase_gate_decision is not None
    assert receipt.phase_gate_decision.verdict == "PASS"
    assert receipt.phase_gate_decision.next_phase_allowed is True
    assert receipt.session_isolation_audit is not None
    assert receipt.session_isolation_audit.independence_level == "cross_provider"
    assert receipt.pre_tree_sha256 == receipt.post_tree_sha256
    assert receipt.governance_triage is not None
    assert receipt.governance_triage.outcome == "AUDIT_BACKLOG_ONLY"

    receipt_file = Path(job.cli_receipt_path)
    sidecar = Path(f"{receipt_file}.sha256")
    assert receipt_file.is_file()
    assert sidecar.is_file()
    expected_digest = hashlib.sha256(receipt_file.read_bytes()).hexdigest()
    assert sidecar.read_text(encoding="utf-8").strip().startswith(expected_digest)


def test_rce01_cli_revise_integrates_governance_triage(
    review_case: dict[str, Any],
    tmp_path: Path,
) -> None:
    """CLI REVISE returns EXIT_REVISE_OR_BLOCKED and runs objective governance triage."""
    rule = CriterionRule(criterion_id="AC-tests", kind="tests", minimum_passed=10)
    descriptor = UntrustedFindingDescriptor(
        finding_id="f-tests-01",
        criterion_id="AC-tests",
        evidence_refs=("test_log",),
        original_text="Test suite reported 1 failure.",
        target_paths=("src/aipc/review/subscription_reviewer_gate.py",),
        confidence=1.0,
    )
    job_path, _, _, _ = _build_job_bundle(
        review_case,
        tmp_path,
        test_log="9 passed, 1 failed in 0.1s\n",
        tests_failed=1,
        rules=(rule,),
        finding_descriptors=(descriptor,),
    )

    async def revise_runner(
        req: LLMReviewRequest,
        _env: Mapping[str, str],
        _session_id: str,
    ) -> dict[str, Any]:
        return proposal(req, status="FAIL", verdict="REVISE")

    code, receipt = run_subscription_reviewer_job(
        job_path,
        runtime_probe=lambda: _probe(codex_ok=True, agy_ok=True),
        transport_factory=_truthful_transport_factory(revise_runner),
    )
    assert code == EXIT_REVISE_OR_BLOCKED
    assert receipt is not None
    assert receipt.gate_outcome == "REVISE"
    assert receipt.governance_triage is not None
    assert receipt.governance_triage.outcome == "AUTHORIZE_BOUNDED_REPAIR"
    assert len(receipt.governance_triage.release_blockers) == 1

    low_conf_descriptor = UntrustedFindingDescriptor(
        finding_id="f-tests-02",
        criterion_id="AC-tests",
        evidence_refs=("test_log",),
        original_text="Test suite reported 1 failure.",
        target_paths=("src/aipc/review/subscription_reviewer_gate.py",),
        confidence=0.0,
    )
    low_job_path, _, _, _ = _build_job_bundle(
        review_case,
        tmp_path / "low_conf",
        test_log="9 passed, 1 failed in 0.1s\n",
        tests_failed=1,
        rules=(rule,),
        finding_descriptors=(low_conf_descriptor,),
    )
    low_code, low_receipt = run_subscription_reviewer_job(
        low_job_path,
        runtime_probe=lambda: _probe(codex_ok=True, agy_ok=True),
        transport_factory=_truthful_transport_factory(revise_runner),
    )
    assert low_code == EXIT_REVISE_OR_BLOCKED
    assert low_receipt is not None
    assert low_receipt.gate_outcome == "REVISE"
    assert low_receipt.governance_triage is not None
    assert low_receipt.governance_triage.outcome == "HOLD_FOR_HUMAN_TRIAGE"


def test_rce01_phase_gate_veto_cannot_be_bypassed_on_llm_pass(
    review_case: dict[str, Any],
    tmp_path: Path,
) -> None:
    """Existing PhaseGate + DeterministicReviewer veto overrides LLM PASS."""
    job_path, _, _, _ = _build_job_bundle(
        review_case,
        tmp_path,
        test_log="9 passed, 1 failed in 0.1s\n",
        tests_failed=1,
    )

    async def pass_runner(
        req: LLMReviewRequest,
        _env: Mapping[str, str],
        _session_id: str,
    ) -> dict[str, Any]:
        return proposal(req, status="PASS")

    code, receipt = run_subscription_reviewer_job(
        job_path,
        runtime_probe=lambda: _probe(codex_ok=True, agy_ok=True),
        transport_factory=_truthful_transport_factory(pass_runner),
    )
    assert code == EXIT_REVISE_OR_BLOCKED
    assert receipt is not None
    assert receipt.gate_outcome == "REVISE"
    assert receipt.phase_gate_decision is not None
    assert receipt.phase_gate_decision.verdict == "REVISE"
    assert receipt.phase_gate_decision.next_phase_allowed is False


def test_rce01_rejects_malformed_json_unknown_fields_and_missing_trust(
    review_case: dict[str, Any],
    tmp_path: Path,
) -> None:
    """Malformed JSON, unknown fields, and missing/tampered trust policy fail closed."""
    bad_json = tmp_path / "bad.json"
    bad_json.write_text("{not-json", encoding="utf-8")
    code_bad, rec_bad = run_subscription_reviewer_job(bad_json)
    assert code_bad == EXIT_MALFORMED_JOB
    assert rec_bad is None

    job_path, job, _, _ = _build_job_bundle(review_case, tmp_path)
    raw = json.loads(job_path.read_text(encoding="utf-8"))
    raw["unknown_key"] = "forbidden"
    extra_path = tmp_path / "extra.json"
    extra_path.write_text(json.dumps(raw), encoding="utf-8")
    code_extra, rec_extra = run_subscription_reviewer_job(extra_path)
    assert code_extra == EXIT_MALFORMED_JOB
    assert rec_extra is None

    mismatched = job.model_dump(mode="json")
    mismatched["task_contract"]["phase_id"] = "rce02"
    with pytest.raises(ValueError, match="phase_id"):
        C7ReviewJob.model_validate(mismatched)

    # Missing external policy file -> EXIT_MALFORMED_JOB with fail-closed receipt
    job.trust_context.policy_path.unlink()
    code_trust, rec_trust = run_subscription_reviewer_job(job_path)
    assert code_trust == EXIT_MALFORMED_JOB
    assert rec_trust is not None
    assert rec_trust.gate_outcome == "MALFORMED_JOB"
    assert Path(job.cli_receipt_path).is_file()


def test_rce01_rejects_dirty_worktree_stale_sha_and_ignored_file_mutation(
    review_case: dict[str, Any],
    tmp_path: Path,
) -> None:
    """Dirty worktree, stale SHA, and ignored-file mutation fail closed."""
    # 1. Stale expected SHA
    job_path_sha, _, ws_sha, _ = _build_job_bundle(
        review_case,
        tmp_path / "stale",
        expected_sha_override=_STALE_SHA,
    )
    code_sha, rec_sha = run_subscription_reviewer_job(
        job_path_sha,
        runtime_probe=lambda: _probe(codex_ok=True, agy_ok=True),
    )
    assert code_sha == EXIT_REVISE_OR_BLOCKED
    assert rec_sha is not None
    assert rec_sha.gate_outcome == "BLOCKED"
    assert rec_sha.session_isolation_audit is not None
    assert rec_sha.session_isolation_audit.rejection_code == "STALE_COMMIT_SHA"
    assert ws_sha.exists()

    # 2. Dirty worktree before review
    job_path_dirty, _, ws_dirty, _ = _build_job_bundle(review_case, tmp_path / "dirty")
    (ws_dirty / "candidate.py").write_text("x = 2\n", encoding="utf-8")
    code_dirty, rec_dirty = run_subscription_reviewer_job(
        job_path_dirty,
        runtime_probe=lambda: _probe(codex_ok=True, agy_ok=True),
    )
    assert code_dirty == EXIT_REVISE_OR_BLOCKED
    assert rec_dirty is not None
    assert rec_dirty.gate_outcome == "BLOCKED"
    assert rec_dirty.session_isolation_audit is not None
    assert rec_dirty.session_isolation_audit.rejection_code == "STALE_COMMIT_SHA"

    # 3. Ignored file mutation during transport
    job_path_mut, _, ws_mut, _ = _build_job_bundle(review_case, tmp_path / "mut")

    async def mutating_ignored_runner(
        req: LLMReviewRequest,
        _env: Mapping[str, str],
        _session_id: str,
    ) -> dict[str, Any]:
        (ws_mut / "ignored_marker.tmp").write_text("mutated-ignored\n", encoding="utf-8")
        return proposal(req, status="PASS")

    code_mut, rec_mut = run_subscription_reviewer_job(
        job_path_mut,
        runtime_probe=lambda: _probe(codex_ok=True, agy_ok=True),
        transport_factory=_truthful_transport_factory(mutating_ignored_runner),
    )
    assert code_mut == EXIT_REVISE_OR_BLOCKED
    assert rec_mut is not None
    assert rec_mut.gate_outcome == "BLOCKED"
    assert rec_mut.pre_tree_sha256 != rec_mut.post_tree_sha256
    assert rec_mut.session_isolation_audit is not None
    assert rec_mut.session_isolation_audit.rejection_code == "REVIEWER_MUTATED_CANDIDATE_WORKSPACE"


def test_rce01_rejects_output_crossover_and_receipt_collision(
    review_case: dict[str, Any],
    tmp_path: Path,
) -> None:
    """Output paths inside Candidate/Stable and existing receipt collisions are rejected."""
    ws_probe = tmp_path / "cross_cand" / "workspace"
    stable_probe = Path(review_case["task"].workspace).parent / "stable_checkout"

    # 1. Receipt inside Candidate worktree
    job_cand, _, _, _ = _build_job_bundle(
        review_case,
        tmp_path / "cross_cand",
        cli_receipt_path=ws_probe / "leak_receipt.json",
    )
    code_cand, rec_cand = run_subscription_reviewer_job(job_cand)
    assert code_cand == EXIT_MALFORMED_JOB
    assert rec_cand is None
    assert not (ws_probe / "leak_receipt.json").exists()

    # 2. Evidence dir inside Stable checkout
    job_stable, _, _, _ = _build_job_bundle(
        review_case,
        tmp_path / "cross_stable",
        evidence_output_dir=stable_probe / "leak_evidence",
    )
    code_stable, rec_stable = run_subscription_reviewer_job(job_stable)
    assert code_stable == EXIT_MALFORMED_JOB
    assert rec_stable is None

    # 3. Existing receipt collision (no overwrite)
    job_col, job_obj, _, _ = _build_job_bundle(review_case, tmp_path / "collision")
    existing = Path(job_obj.cli_receipt_path)
    existing.parent.mkdir(parents=True, exist_ok=True)
    existing.write_text('{"sentinel": "preserve"}\n', encoding="utf-8")
    code_col, rec_col = run_subscription_reviewer_job(job_col)
    assert code_col == EXIT_MALFORMED_JOB
    assert rec_col is None
    assert existing.read_text(encoding="utf-8") == '{"sentinel": "preserve"}\n'


def test_rce01_provider_unavailable_and_no_fake_qualification(
    review_case: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Provider quota/unavailability returns EXIT_PROVIDER_UNAVAILABLE and never fakes QUALIFIED."""
    job_path, job, _, _ = _build_job_bundle(review_case, tmp_path)

    async def quota_runner(
        _req: LLMReviewRequest,
        _env: Mapping[str, str],
        _session_id: str,
    ) -> dict[str, Any]:
        raise LLMReviewError("rate_limit")

    code, receipt = run_subscription_reviewer_job(
        job_path,
        runtime_probe=lambda: _probe(codex_ok=True, agy_ok=True),
        transport_factory=_truthful_transport_factory(quota_runner),
    )
    assert code == EXIT_PROVIDER_UNAVAILABLE
    assert receipt is not None
    assert receipt.gate_outcome == "WAITING_PROVIDER"
    assert Path(job.cli_receipt_path).is_file()

    # Production probe must not fabricate QUALIFIED when runtimes or auth are absent
    monkeypatch.setattr(
        "aipc.adapters.subscription_qualification._probe_codex_sdk_and_bundled_binary",
        lambda: (False, None, None),
    )
    monkeypatch.setattr(
        "aipc.adapters.subscription_qualification._probe_codex_auth_metadata_present",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(
        "aipc.adapters.subscription_qualification.find_agy_executable",
        lambda: None,
    )
    monkeypatch.setattr(
        "aipc.adapters.subscription_qualification._probe_antigravity_oauth_dir_present",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(
        "aipc.adapters.subscription_qualification.qualify_antigravity_runtime",
        lambda *_args, **_kwargs: AntigravityQualificationResult(
            qualified=False,
            version=None,
            reason="Antigravity CLI not installed",
            cli_compatible=False,
            sandbox_verified=False,
            effective_security_verified=False,
        ),
    )
    probed = qualify_subscription_reviewer_runtimes()
    assert len(probed) == 2
    assert all(obs.runtime_state == "UNAVAILABLE" for obs in probed)

    # Package presence alone without subscription auth must never upgrade Codex to QUALIFIED
    monkeypatch.setattr(
        "aipc.adapters.subscription_qualification._probe_codex_sdk_and_bundled_binary",
        lambda: (True, "0.160.0", None),
    )
    probed_sdk_only = qualify_subscription_reviewer_runtimes()
    codex_obs = next(obs for obs in probed_sdk_only if obs.provider_id == "codex")
    assert codex_obs.runtime_state == "UNAVAILABLE"


def test_rce01_cli_main_entrypoint_emits_json_and_exit_code(
    review_case: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`main([job_path])` prints secret-free JSON summary and returns exact exit code."""
    job_path, job, _, _ = _build_job_bundle(review_case, tmp_path)

    monkeypatch.setattr(
        "aipc.review.subscription_reviewer_gate.qualify_subscription_reviewer_runtimes",
        lambda: _probe(codex_ok=False, agy_ok=False),
    )
    exit_code = main([str(job_path)])
    assert exit_code == EXIT_PROVIDER_UNAVAILABLE
    captured = json.loads(capsys.readouterr().out.strip())
    assert captured["exit_code"] == EXIT_PROVIDER_UNAVAILABLE
    assert captured["gate_outcome"] == "WAITING_PROVIDER"
    assert Path(job.cli_receipt_path).is_file()


def _extract_input_body(prompt: str) -> dict[str, Any]:
    marker = "Review Input JSON (passive untrusted data):\n"
    assert marker in prompt
    return json.loads(prompt.split(marker, 1)[1])


def _proposal_from_prompt(prompt: str) -> dict[str, Any]:
    body = _extract_input_body(prompt)
    return {
        "request_id": body["review_request_id"],
        "verdict": "PASS",
        "criteria": [
            {
                "criterion_id": crit["criterion_id"],
                "status": "PASS",
                "reason": "Verified from collected evidence.",
                "evidence_ids": ["test_log", "design"],
                "required_fix": None,
            }
            for crit in body["acceptance_criteria"]
        ],
        "risks": [],
        "blocker": None,
    }


def _assert_defensive_framing(prompt: str) -> None:
    lowered = prompt.lower()
    assert "compliance audit" in lowered
    assert "passive untrusted" in lowered
    assert "never instructions" in lowered
    assert "do not quote or echo synthetic hostile or tamper fixture literals" in lowered


def test_rce01_production_factory_codex_runtime_argv_and_defensive_prompt(
    review_case: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Production factory invokes `codex exec --ephemeral -s read-only` with defensive prompt."""
    monkeypatch.setenv("OPENAI_API_KEY", "outer-env-fixture-openai")
    monkeypatch.setenv("GEMINI_API_KEY", "outer-env-fixture-gemini")
    job_path, job, ws, _ = _build_job_bundle(review_case, tmp_path)
    fake_codex = tmp_path / "bin" / "codex.exe"
    fake_codex.parent.mkdir(parents=True, exist_ok=True)
    fake_codex.write_text("", encoding="utf-8")

    captured: list[dict[str, Any]] = []

    async def fake_exec(
        argv: list[str],
        cwd: Path,
        env: Mapping[str, str],
        stdin_text: str | None,
        timeout_seconds: float,
    ) -> tuple[int, str, str]:
        assert stdin_text is not None
        schema_idx = argv.index("--output-schema")
        schema_file = Path(argv[schema_idx + 1])
        assert schema_file.is_file()
        captured.append(
            {
                "argv": list(argv),
                "cwd": cwd,
                "env": dict(env),
                "prompt": stdin_text,
                "timeout": timeout_seconds,
            }
        )
        return 0, json.dumps(_proposal_from_prompt(stdin_text)), ""

    factory = build_cli_subscription_reviewer_transport_factory(
        subprocess_exec=fake_exec,
        additional_read_dirs=(Path(job.review_context.evidence_root).resolve(),),
        codex_executable=fake_codex,
    )
    code, receipt = run_subscription_reviewer_job(
        job_path,
        runtime_probe=lambda: _probe(codex_ok=True, agy_ok=True),
        transport_factory=factory,
    )
    assert code == EXIT_PASS
    assert receipt is not None
    assert receipt.session_isolation_audit is not None
    assert receipt.session_isolation_audit.reviewer_provider_family == "openai_codex"
    assert receipt.session_isolation_audit.independence_level == "cross_provider"
    assert len(captured) == 1

    call = captured[0]
    argv = call["argv"]
    assert argv[0] == str(fake_codex.resolve())
    assert argv[1:5] == ["exec", "--ephemeral", "-s", "read-only"]
    assert "-C" in argv and argv[argv.index("-C") + 1] == str(ws.resolve())
    assert "--add-dir" in argv
    assert argv[argv.index("--add-dir") + 1] == str(
        Path(job.review_context.evidence_root).resolve()
    )
    assert "-m" in argv and argv[argv.index("-m") + 1] == "gpt-5.3-codex"
    assert "--output-schema" in argv
    assert argv[-1] == "-"
    assert "--ignore-user-config" not in argv
    assert "resume" not in argv
    assert "OPENAI_API_KEY" not in call["env"]
    assert "GEMINI_API_KEY" not in call["env"]
    _assert_defensive_framing(call["prompt"])


def test_rce01_production_factory_agy_runtime_argv_and_defensive_prompt(
    review_case: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Production factory invokes AGY `--mode plan --sandbox` with defensive prompt."""
    monkeypatch.setenv("OPENAI_API_KEY", "outer-env-fixture-openai")
    monkeypatch.setenv("GEMINI_API_KEY", "outer-env-fixture-gemini")
    job_path, _, ws, _ = _build_job_bundle(review_case, tmp_path)
    fake_agy = tmp_path / "bin" / "agy.exe"
    fake_agy.parent.mkdir(parents=True, exist_ok=True)
    fake_agy.write_text("", encoding="utf-8")

    captured: list[dict[str, Any]] = []

    async def fake_exec(
        argv: list[str],
        cwd: Path,
        env: Mapping[str, str],
        stdin_text: str | None,
        timeout_seconds: float,
    ) -> tuple[int, str, str]:
        assert stdin_text is None
        prompt = argv[argv.index("-p") + 1]
        captured.append(
            {
                "argv": list(argv),
                "cwd": cwd,
                "env": dict(env),
                "prompt": prompt,
                "timeout": timeout_seconds,
            }
        )
        frame = {
            "event": "result",
            "result": {
                "conversation_id": "agy-fresh-rev-001",
                "response": json.dumps(_proposal_from_prompt(prompt)),
            },
        }
        return 0, json.dumps(frame) + "\n", ""

    factory = build_cli_subscription_reviewer_transport_factory(
        subprocess_exec=fake_exec,
        agy_executable=fake_agy,
    )
    code, receipt = run_subscription_reviewer_job(
        job_path,
        runtime_probe=lambda: _probe(codex_ok=False, agy_ok=True),
        transport_factory=factory,
    )
    assert code == EXIT_PASS
    assert receipt is not None
    assert receipt.session_isolation_audit is not None
    assert receipt.session_isolation_audit.reviewer_provider_family == "google_antigravity"
    assert receipt.session_isolation_audit.independence_level == "reduced_independence"
    assert receipt.session_isolation_audit.reduced_independence is True
    assert len(captured) == 1

    call = captured[0]
    argv = call["argv"]
    assert call["cwd"] == ws.resolve()
    assert argv[0] == str(fake_agy.resolve())
    assert "--output-format" in argv and argv[argv.index("--output-format") + 1] == "stream-json"
    assert "--mode" in argv and argv[argv.index("--mode") + 1] == "plan"
    assert "--sandbox" in argv
    assert "--disable-slash-commands" in argv
    assert "--model" in argv and argv[argv.index("--model") + 1] == "gemini-3.8-flash-medium"
    assert "--resume" not in argv and "--continue" not in argv
    settings_obj = json.loads(argv[argv.index("--settings") + 1])
    assert "allow" in settings_obj["permissions"] and "deny" in settings_obj["permissions"]
    assert "OPENAI_API_KEY" not in call["env"]
    assert "GEMINI_API_KEY" not in call["env"]
    _assert_defensive_framing(call["prompt"])


def test_rce01_unknown_provider_family_fails_closed_without_agy_fallback(
    review_case: dict[str, Any],
    tmp_path: Path,
) -> None:
    """Unknown reviewer provider_family never silently maps to AGY and fails closed."""
    job_path, job, _, _ = _build_job_bundle(review_case, tmp_path)
    unknown_binding = RoleModelBinding(
        binding_id="reviewer-unknown-provider",
        role="reviewer",
        provider_id="unknown-sub",
        provider_family="unknown_vendor",
        model_id="unknown-model-v1",
        model_tier="review_strict",
        execution_mode="reviewer",
        auth_mode="codex_subscription",
        required_capabilities=("read_only_review",),
        priority=99,
    )
    unknown_obs = ProviderRuntimeObservation(
        provider_id="unknown-sub",
        provider_family="unknown_vendor",
        auth_mode="codex_subscription",
        runtime_state="QUALIFIED",
        supported_models=("unknown-model-v1",),
        supported_modes=("reviewer",),
        capabilities={
            "filesystem": CapabilityStatus.AVAILABLE,
            "read_only_review": CapabilityStatus.AVAILABLE,
        },
    )
    updated_job = job.model_copy(update={"role_bindings": (unknown_binding,)})
    job_path.write_text(json.dumps(updated_job.model_dump(mode="json"), indent=2), encoding="utf-8")

    code, receipt = run_subscription_reviewer_job(job_path, runtime_probe=lambda: (unknown_obs,))
    assert code == EXIT_REVISE_OR_BLOCKED
    assert receipt is not None
    assert receipt.gate_outcome == "BLOCKED"
    assert receipt.session_isolation_audit is not None
    assert receipt.session_isolation_audit.rejection_code == "REVIEWER_UNAVAILABLE"


def test_rce01_production_factory_codex_nonzero_rate_limit_fails_closed(
    review_case: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Production Codex CLI nonzero rate-limit result fails closed with immutable receipt."""
    monkeypatch.setenv("OPENAI_API_KEY", "outer-env-fixture-openai")
    monkeypatch.setenv("GEMINI_API_KEY", "outer-env-fixture-gemini")
    job_path, job, ws, _ = _build_job_bundle(review_case, tmp_path)
    fake_codex = tmp_path / "bin" / "codex.exe"
    fake_codex.parent.mkdir(parents=True, exist_ok=True)
    fake_codex.write_text("", encoding="utf-8")

    captured: list[dict[str, Any]] = []

    async def fake_exec(
        argv: list[str],
        cwd: Path,
        env: Mapping[str, str],
        stdin_text: str | None,
        timeout_seconds: float,
    ) -> tuple[int, str, str]:
        captured.append(
            {
                "argv": list(argv),
                "cwd": cwd,
                "env": dict(env),
                "prompt": stdin_text,
                "timeout": timeout_seconds,
            }
        )
        return 1, "", "rate limit exceeded"

    factory = build_cli_subscription_reviewer_transport_factory(
        subprocess_exec=fake_exec,
        additional_read_dirs=(Path(job.review_context.evidence_root).resolve(),),
        codex_executable=fake_codex,
    )
    code, receipt = run_subscription_reviewer_job(
        job_path,
        runtime_probe=lambda: _probe(codex_ok=True, agy_ok=True),
        transport_factory=factory,
    )

    assert len(captured) == 1
    call = captured[0]
    argv = call["argv"]
    assert call["cwd"] == ws.resolve()
    assert argv[0] == str(fake_codex.resolve())
    assert argv[1:5] == ["exec", "--ephemeral", "-s", "read-only"]
    assert "resume" not in argv
    assert "OPENAI_API_KEY" not in call["env"]
    assert "GEMINI_API_KEY" not in call["env"]

    assert code == EXIT_PROVIDER_UNAVAILABLE
    assert isinstance(receipt, C7ReviewerCliReceipt)
    assert receipt.exit_code == EXIT_PROVIDER_UNAVAILABLE
    assert receipt.gate_outcome == "WAITING_PROVIDER"
    assert receipt.reviewed_candidate_sha is None
    assert receipt.phase_gate_decision is None
    assert receipt.pre_tree_sha256 == receipt.post_tree_sha256
    assert receipt.canonical_review is not None
    assert receipt.canonical_review.verdict == "ERROR"
    assert receipt.canonical_review.next_phase_allowed is False
    assert receipt.canonical_review.metadata.get("live_review_status") == LIVE_REVIEW_UNVERIFIED
    assert receipt.session_isolation_audit is not None
    assert receipt.session_isolation_audit.reviewer_provider_family == "openai_codex"
    assert receipt.session_isolation_audit.fresh_isolated_session is True
    assert receipt.session_isolation_audit.read_only_sandbox_enforced is True
    assert receipt.session_isolation_audit.rejection_code == "REVIEWER_QUOTA_OR_UNAVAILABLE"

    receipt_file = Path(job.cli_receipt_path)
    sidecar = Path(f"{receipt_file}.sha256")
    assert receipt_file.is_file()
    assert sidecar.is_file()
    expected_digest = hashlib.sha256(receipt_file.read_bytes()).hexdigest()
    assert sidecar.read_text(encoding="utf-8").strip().startswith(expected_digest)
    persisted = C7ReviewerCliReceipt.model_validate_json(receipt_file.read_text(encoding="utf-8"))
    assert persisted == receipt


def test_remediation01_agy_nonzero_exit_with_valid_pass_payload_fails_closed(
    review_case: dict[str, Any],
    tmp_path: Path,
) -> None:
    """Nonzero AGY exit fails closed even when stdout stream-json holds a schema-valid PASS."""
    job_path, _, _, _ = _build_job_bundle(review_case, tmp_path)
    fake_agy = tmp_path / "bin" / "agy.exe"
    fake_agy.parent.mkdir(parents=True, exist_ok=True)
    fake_agy.write_text("", encoding="utf-8")
    calls = 0

    async def fake_exec(
        argv: list[str],
        cwd: Path,
        env: Mapping[str, str],
        stdin_text: str | None,
        timeout_seconds: float,
    ) -> tuple[int, str, str]:
        nonlocal calls
        calls += 1
        del cwd, env, stdin_text, timeout_seconds
        prompt = argv[argv.index("-p") + 1]
        frame = {
            "event": "result",
            "result": {
                "conversation_id": "agy-nonzero-pass-001",
                "response": json.dumps(_proposal_from_prompt(prompt)),
            },
        }
        return 7, json.dumps(frame) + "\n", "nonfatal diagnostic warning: cache miss"

    factory = build_cli_subscription_reviewer_transport_factory(
        subprocess_exec=fake_exec,
        agy_executable=fake_agy,
    )
    code, receipt = run_subscription_reviewer_job(
        job_path,
        runtime_probe=lambda: _probe(codex_ok=False, agy_ok=True),
        transport_factory=factory,
    )

    assert calls == 1
    assert code != EXIT_PASS
    assert code == EXIT_PROVIDER_UNAVAILABLE
    assert isinstance(receipt, C7ReviewerCliReceipt)
    assert receipt.gate_outcome != "PASS"
    assert receipt.gate_outcome == "WAITING_PROVIDER"
    assert receipt.reviewed_candidate_sha is None
    assert receipt.phase_gate_decision is None
    assert receipt.canonical_review is not None
    assert receipt.canonical_review.verdict == "ERROR"
    assert receipt.canonical_review.next_phase_allowed is False
    assert receipt.canonical_review.metadata.get("live_review_status") == LIVE_REVIEW_UNVERIFIED
    assert receipt.session_isolation_audit is not None
    assert receipt.session_isolation_audit.rejection_code == "REVIEWER_QUOTA_OR_UNAVAILABLE"


@pytest.mark.parametrize(
    ("stderr_text", "expected_err"),
    [
        ("429 resource_exhausted rate_limit", "rate_limit"),
        ("401 unauthenticated login required", "auth"),
        ("nonfatal diagnostic warning", "service"),
    ],
)
def test_remediation01_agy_nonzero_exit_preserves_auth_and_quota_classification(
    tmp_path: Path,
    stderr_text: str,
    expected_err: str,
) -> None:
    """Nonzero AGY exit raises LLMReviewError and preserves auth/quota classification."""
    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    fake_agy = tmp_path / "bin" / "agy.exe"
    fake_agy.parent.mkdir(parents=True, exist_ok=True)
    fake_agy.write_text("", encoding="utf-8")
    dummy_sha = "a" * 40
    dummy_digest = "a" * 64
    calls = 0

    async def fake_exec(
        argv: list[str],
        cwd: Path,
        env: Mapping[str, str],
        stdin_text: str | None,
        timeout_seconds: float,
    ) -> tuple[int, str, str]:
        nonlocal calls
        calls += 1
        del cwd, env, stdin_text, timeout_seconds
        prompt = argv[argv.index("-p") + 1]
        frame = {
            "event": "result",
            "result": {
                "conversation_id": "agy-err-001",
                "response": json.dumps(_proposal_from_prompt(prompt)),
            },
        }
        return 7, json.dumps(frame) + "\n", stderr_text

    binding = RoleModelBinding(
        binding_id="reviewer-agy-strict",
        role="reviewer",
        provider_id="gemini-antigravity",
        provider_family="google_antigravity",
        model_id="gemini-3.8-flash-medium",
        model_tier="review_strict",
        execution_mode="reviewer",
        auth_mode="antigravity_subscription",
        required_capabilities=("read_only_review",),
        priority=95,
        fallback_order=0,
    )
    llm_cfg = LLMReviewerConfig(
        reviewer_id=binding.binding_id,
        provider="gemini",
        model=binding.model_id,
    )
    sub_cfg = SubscriptionReviewerConfig(
        llm_config=llm_cfg,
        auth_mode="antigravity_subscription",
        independence_policy="allow_isolated_same_provider_fallback",
        expected_candidate_sha=dummy_sha,
        candidate_workspace=str(ws),
    )
    instructions = "Review candidate."
    input_json = json.dumps(
        {
            "review_request_id": dummy_digest,
            "acceptance_criteria": [{"criterion_id": "AC-tests"}],
        }
    )
    req = LLMReviewRequest(
        request_id=dummy_digest,
        config=llm_cfg,
        instructions=instructions,
        input_json=input_json,
        manifest=ReviewRequestManifest(
            project_id="example",
            run_id="run-1",
            phase_id="rce01",
            attempt=1,
            task_id="task-rce01-review",
            worker_agent_id="worker-agy-fast",
            worker_provider="gemini",
            worker_model="gemini-3.8-flash-medium",
            input_digest=sha(input_json.encode()),
            instructions_digest=sha(instructions.encode()),
            config_digest=digest(llm_cfg),
            context_digest=dummy_digest,
            task_digest=dummy_digest,
            agent_result_digest=dummy_digest,
            evidence_digest=dummy_digest,
            git_commit=dummy_sha,
            items=(),
        ),
    )
    factory = build_cli_subscription_reviewer_transport_factory(
        subprocess_exec=fake_exec,
        agy_executable=fake_agy,
    )
    transport = factory(binding, sub_cfg)
    with pytest.raises(LLMReviewError, match=expected_err):
        asyncio.run(transport.review(req))
    assert calls == 1


def test_remediation01_report_to_observation_preserves_model_eligibility_for_selection(
    tmp_path: Path,
) -> None:
    """_report_to_observation retains model_eligibility so registry skips restricted models."""
    home = tmp_path / "home"
    (home / ".gemini" / "antigravity").mkdir(parents=True)
    fake_agy = tmp_path / "agy.exe"
    fake_agy.write_text("binary", encoding="utf-8")

    base_report = qualify_antigravity_subscription_runtime(
        executable=fake_agy,
        home_dir=home,
        qualification_override=AntigravityQualificationResult(
            qualified=True,
            version="1.5.8",
            reason="qualified",
            cli_compatible=True,
            effective_security_verified=True,
        ),
        oauth_present_override=True,
    )
    report = base_report.model_copy(
        update={
            "supported_model_ids": ("argon", "gemini-4-hydrogen-medium"),
            "unverified_or_unavailable_model_ids": (),
            "model_eligibility": {
                "argon": "RATE_LIMITED",
                "gemini-4-hydrogen-medium": "QUALIFIED",
            },
        }
    )

    obs = _report_to_observation(report)
    assert obs.model_states == {
        "argon": "RATE_LIMITED",
        "gemini-4-hydrogen-medium": "QUALIFIED",
    }

    registry = RoleBasedModelRegistry(observations=lambda: (obs,))
    registry.register(
        RoleModelBinding(
            binding_id="reviewer-agy-argon",
            role="reviewer",
            provider_id="gemini-antigravity",
            provider_family="google_antigravity",
            model_id="argon",
            model_tier="review_strict",
            execution_mode="reviewer",
            auth_mode="antigravity_subscription",
            required_capabilities=("read_only_review",),
            priority=99,
            fallback_order=0,
        )
    )
    registry.register(
        RoleModelBinding(
            binding_id="reviewer-agy-hydrogen",
            role="reviewer",
            provider_id="gemini-antigravity",
            provider_family="google_antigravity",
            model_id="gemini-4-hydrogen-medium",
            model_tier="review_strict",
            execution_mode="reviewer",
            auth_mode="antigravity_subscription",
            required_capabilities=("read_only_review",),
            priority=95,
            fallback_order=1,
        )
    )

    decision = registry.resolve(
        RoleSelectionRequest(
            role="reviewer",
            preferred_tiers=("review_strict",),
            required_capabilities=("read_only_review",),
        )
    )
    assert decision.outcome == "SELECTED"
    assert decision.selected_binding is not None
    assert decision.selected_binding.binding_id == "reviewer-agy-hydrogen"
    assert decision.selected_binding.model_id == "gemini-4-hydrogen-medium"
    assert any(
        rej.binding_id == "reviewer-agy-argon"
        and rej.model_id == "argon"
        and rej.code == "MODEL_RATE_LIMITED"
        for rej in decision.rejected_bindings
    )


@pytest.mark.parametrize("use_dot_segment", [False, True])
def test_rce01_rejects_aliased_cli_and_gate_receipt_paths_before_reviewer(
    review_case: dict[str, Any],
    tmp_path: Path,
    use_dot_segment: bool,
) -> None:
    """Identical or lexically equivalent receipt paths fail closed before reviewer invocation."""
    out_dir = tmp_path / "aliased_out"
    job_path, job, _, _ = _build_job_bundle(
        review_case,
        tmp_path,
        evidence_output_dir=out_dir,
    )
    task_id = job.review_context.task.task_id
    attempt = job.review_context.task.attempt
    gate_name = f"b02_reviewer_gate_{task_id}_att_{attempt:02d}.json"
    aliased_cli_str = f"{out_dir}/./{gate_name}" if use_dot_segment else str(out_dir / gate_name)

    raw_job = json.loads(job_path.read_text(encoding="utf-8"))
    raw_job["cli_receipt_path"] = aliased_cli_str
    job_path.write_text(json.dumps(raw_job, indent=2), encoding="utf-8")

    gate_receipt = out_dir / gate_name
    assert not out_dir.exists()
    assert not gate_receipt.exists()

    calls = 0

    async def should_not_run(
        req: LLMReviewRequest,
        _env: Mapping[str, str],
        _session_id: str,
    ) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return proposal(req, status="PASS")

    code, receipt = run_subscription_reviewer_job(
        job_path,
        runtime_probe=lambda: _probe(codex_ok=True, agy_ok=True),
        transport_factory=_truthful_transport_factory(should_not_run),
    )
    assert code == EXIT_MALFORMED_JOB
    assert receipt is None
    assert calls == 0
    assert not gate_receipt.exists()
    assert not Path(f"{gate_receipt}.sha256").exists()


@pytest.mark.parametrize("competing_target", ["gate", "cli"])
def test_rce01_exclusive_creation_fails_closed_on_concurrent_competing_writer(
    review_case: dict[str, Any],
    tmp_path: Path,
    competing_target: str,
) -> None:
    """Concurrent O_EXCL race fails closed and leaves competing writer bytes 100% unchanged."""
    job_path, job, _, _ = _build_job_bundle(review_case, tmp_path / competing_target)
    task_id = job.review_context.task.task_id
    attempt = job.review_context.task.attempt
    gate_receipt = (
        Path(job.gate_config.evidence_output_dir)
        / f"b02_reviewer_gate_{task_id}_att_{attempt:02d}.json"
    )
    cli_receipt = Path(job.cli_receipt_path)
    sentinel = f'{{"competing": "{competing_target}_sentinel"}}\n'

    async def racing_runner(
        req: LLMReviewRequest,
        _env: Mapping[str, str],
        _session_id: str,
    ) -> dict[str, Any]:
        target = gate_receipt if competing_target == "gate" else cli_receipt
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(sentinel, encoding="utf-8", newline="\n")
        return proposal(req, status="PASS")

    code, receipt = run_subscription_reviewer_job(
        job_path,
        runtime_probe=lambda: _probe(codex_ok=True, agy_ok=True),
        transport_factory=_truthful_transport_factory(racing_runner),
    )
    assert code == EXIT_MALFORMED_JOB
    assert receipt is None
    if competing_target == "gate":
        assert gate_receipt.read_text(encoding="utf-8") == sentinel
        assert not cli_receipt.exists()
    else:
        assert cli_receipt.read_text(encoding="utf-8") == sentinel
        assert not Path(f"{cli_receipt}.sha256").exists()

    direct_target = tmp_path / f"direct_{competing_target}.json"
    direct_target.write_text(sentinel, encoding="utf-8", newline="\n")
    with pytest.raises(SubscriptionReviewerGateError, match="Refusing to overwrite"):
        _write_json_exclusive_immutable(direct_target, {"overwrite": "forbidden"})
    assert direct_target.read_text(encoding="utf-8") == sentinel
