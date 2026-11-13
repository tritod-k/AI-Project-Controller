"""Workstream C02 — Multi-Language Isolated Verification & End-to-End Pipeline.

Provides provider-neutral isolated verification across heterogeneous project stacks
(Python, Node.js/JavaScript/TypeScript, Static Web, and Google Apps Script offline
mocks) and orchestrates the Milestone C end-to-end cycle from onboarding and task
queue dispatch through worker execution, isolated verification, and independent
subscription review, stopping strictly at the Human Merge boundary.
"""

from __future__ import annotations

import hashlib
import re
import shlex
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Literal, Self
from uuid import uuid4

from pydantic import Field, StrictBool, model_validator

from aipc.agents.role_registry import RoleModelBinding
from aipc.controller.errors import ControllerError
from aipc.controller.milestone_b_orchestrator import (
    HandoffNextAuthorizedAction,
    WorkerStepCallable,
)
from aipc.controller.self_dev_runner import VERIFICATION_ISOLATION_BLOCKED
from aipc.controller.task_queue import PersistentTaskQueue
from aipc.domain.enums import ReviewVerdict
from aipc.domain.models import CanonicalRecord, NonBlank, PositiveInt, TestSummary
from aipc.hardening.env_isolation import (
    assert_argv_no_credentials,
    assert_child_env_isolated,
    assert_subscription_only_mode,
    build_sanitized_child_env,
)
from aipc.hardening.secrets import assert_no_secrets
from aipc.project.universal_profiles import OnboardedProjectBundle, ProjectStackType
from aipc.review.subscription_reviewer_gate import (
    IndependentSubscriptionReviewerGate,
    SubscriptionReviewerGateDecision,
)
from aipc.version.models import Commit

MultiLanguageIsolationMode = Literal["blocked", "container", "isolated_subprocess"]

_DEFAULT_GAS_DISCLAIMER = (
    "Offline simulation only: offline GAS mocks do not confirm live TradingView, "
    "Google Sheets, OKX, or Google Apps Script deployment safety."
)


def snapshot_workspace_digests(workspace: Path) -> dict[str, str]:
    """Compute SHA-256 digests of all files in workspace excluding .git."""
    resolved = Path(workspace).resolve()
    if not resolved.exists() or not resolved.is_dir():
        return {}
    digests: dict[str, str] = {}
    for item in sorted(resolved.rglob("*")):
        if ".git" in item.parts or not item.is_file():
            continue
        try:
            rel = item.relative_to(resolved).as_posix()
            digests[rel] = hashlib.sha256(item.read_bytes()).hexdigest()
        except OSError:
            continue
    return digests


class ContainerResourceLimits(CanonicalRecord):
    """Fail-closed container isolation and resource limits for verification runs."""

    network_mode: Literal["none"] = "none"
    read_only_root_fs: Literal[True] = True
    non_root_user: NonBlank = "65534:65534"
    cap_drop: tuple[NonBlank, ...] = ("ALL",)
    no_new_privileges: Literal[True] = True
    pids_limit: PositiveInt = 64
    memory_limit: NonBlank = "512m"
    tmpfs_spec: NonBlank = "/tmp:rw,noexec,nosuid,size=64m"


class MultiLanguageVerificationReceipt(CanonicalRecord):
    """Audited verification receipt for a multi-language project candidate."""

    receipt_id: NonBlank
    project_id: NonBlank
    stack_type: ProjectStackType
    isolation_mode: MultiLanguageIsolationMode
    candidate_sha: Commit
    command_executed: tuple[NonBlank, ...]
    container_argv: tuple[NonBlank, ...] | None = None
    resource_limits: ContainerResourceLimits | None = None
    exit_code: int
    passed: StrictBool
    tests_summary: TestSummary
    tool_versions: dict[str, str] = Field(default_factory=dict)
    workspace_mutated_during_verification: StrictBool = False
    offline_mock_only: StrictBool = False
    live_deployment_or_external_proof_claimed: Literal[False] = False
    simulation_disclaimer: NonBlank | None = None
    failure_reason: str | None = None
    sanitized_env: Literal[True] = True

    @model_validator(mode="after")
    def validate_receipt_invariants(self) -> Self:
        if self.passed and (
            self.isolation_mode == "blocked"
            or self.workspace_mutated_during_verification
            or self.exit_code != 0
            or self.tests_summary.failed > 0
            or self.tests_summary.passed == 0
        ):
            raise ValueError(
                "passed cannot be True when isolation_mode is blocked, workspace mutated, "
                "exit_code != 0, failed > 0, or passed == 0"
            )
        if self.stack_type == "google_apps_script_offline_mock":
            if not self.offline_mock_only:
                raise ValueError(
                    "google_apps_script_offline_mock requires offline_mock_only=True"
                )
            if self.simulation_disclaimer is None or not self.simulation_disclaimer.strip():
                raise ValueError(
                    "google_apps_script_offline_mock requires non-blank simulation_disclaimer"
                )
        assert_no_secrets(self.model_dump(mode="json"))
        return self


def _parse_int_match(pattern: str, text: str) -> int:
    match = re.search(pattern, text, flags=re.IGNORECASE | re.MULTILINE)
    return int(match.group(1)) if match else 0


class MultiLanguageIsolatedVerifier:
    """Executes or fails closed on isolated multi-language candidate verification."""

    @staticmethod
    def build_container_command(
        *,
        runtime_bin: str,
        image: str,
        workspace: Path,
        out_dir: Path,
        command: tuple[str, ...],
        limits: ContainerResourceLimits | None = None,
    ) -> tuple[str, ...]:
        """Construct a hardened container invocation with read-only mount and no network."""
        if not runtime_bin or not runtime_bin.strip():
            raise ValueError("runtime_bin must be non-blank")
        if not image or not image.strip():
            raise ValueError("image must be non-blank")
        if not command:
            raise ValueError("command must not be empty")

        effective_limits = limits or ContainerResourceLimits()
        ws_resolved = Path(workspace).resolve()
        out_resolved = Path(out_dir).resolve()

        argv: list[str] = [
            runtime_bin.strip(),
            "run",
            "--rm",
            "--network",
            effective_limits.network_mode,
        ]
        if effective_limits.read_only_root_fs:
            argv.append("--read-only")
        argv.extend(["--tmpfs", effective_limits.tmpfs_spec])
        argv.extend(["--user", effective_limits.non_root_user])
        for cap in effective_limits.cap_drop:
            argv.extend(["--cap-drop", cap])
        if effective_limits.no_new_privileges:
            argv.extend(["--security-opt", "no-new-privileges"])
        argv.extend(["--pids-limit", str(effective_limits.pids_limit)])
        argv.extend(["--memory", effective_limits.memory_limit])
        argv.extend(["-v", f"{ws_resolved}:/workspace:ro"])
        argv.extend(["-v", f"{out_resolved}:/workspace-out:rw"])
        if (ws_resolved / ".git").is_file():
            argv.extend(["-v", "/dev/null:/workspace/.git:ro"])
        argv.extend(["-w", "/workspace", "-e", "HOME=/tmp", "-e", "PYTHONDONTWRITEBYTECODE=1"])
        argv.append(image.strip())
        argv.extend(command)
        return assert_argv_no_credentials(argv)

    @staticmethod
    def parse_multilang_test_output(
        stack_type: ProjectStackType,
        output: str,
        exit_code: int,
    ) -> TestSummary:
        """Parse pytest, Jest/Vitest, node --test, or Mocha test summaries, failing closed."""
        text = output or ""
        passed = 0
        failed = 0
        skipped = 0

        if stack_type in ("python_cli_or_package", "python_web_or_api"):
            passed = _parse_int_match(r"\b(\d+)\s+passed\b", text)
            failed = _parse_int_match(r"\b(\d+)\s+failed\b", text)
            failed += _parse_int_match(r"\b(\d+)\s+errors?\b", text)
            skipped = _parse_int_match(r"\b(\d+)\s+skipped\b", text)
        else:
            # Jest / Vitest summary line: e.g. "Tests: 4 passed, 0 failed, 4 total"
            tests_line_match = re.search(r"Tests:\s*([^\r\n]+)", text, flags=re.IGNORECASE)
            if tests_line_match:
                line = tests_line_match.group(1)
                passed = _parse_int_match(r"\b(\d+)\s+passed\b", line)
                failed = _parse_int_match(r"\b(\d+)\s+failed\b", line)
                skipped = _parse_int_match(r"\b(\d+)\s+skipped\b", line)
            else:
                # node --test / TAP ("pass X", "fail Y", "skipped Z")
                # or Mocha ("X passing", "Y failing")
                passed = _parse_int_match(r"(?:^|\s)(?:pass|passing)\s+(\d+)\b", text)
                if passed == 0:
                    passed = _parse_int_match(r"\b(\d+)\s+passing\b", text)
                if passed == 0:
                    passed = _parse_int_match(r"\b(\d+)\s+passed\b", text)

                failed = _parse_int_match(r"(?:^|\s)(?:fail|failing)\s+(\d+)\b", text)
                if failed == 0:
                    failed = _parse_int_match(r"\b(\d+)\s+failing\b", text)
                if failed == 0:
                    failed = _parse_int_match(r"\b(\d+)\s+failed\b", text)

                skipped = _parse_int_match(r"(?:^|\s)skipped\s+(\d+)\b", text)
                if skipped == 0:
                    skipped = _parse_int_match(r"\b(\d+)\s+(?:skipped|pending)\b", text)

        if passed == 0 and failed == 0 and skipped == 0:
            return TestSummary(total=1, passed=0, failed=1, skipped=0)

        if exit_code != 0 and failed == 0:
            failed = 1

        total = passed + failed + skipped
        return TestSummary(total=total, passed=passed, failed=failed, skipped=skipped)

    def verify_project_candidate(
        self,
        *,
        bundle: OnboardedProjectBundle,
        candidate_sha: str,
        isolation_mode: MultiLanguageIsolationMode | None = None,
        command_override: tuple[str, ...] | None = None,
        container_runtime_bin: str | None = None,
        runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
    ) -> MultiLanguageVerificationReceipt:
        """Execute isolated verification on a project candidate and return an audited receipt."""
        assert_subscription_only_mode(
            bundle.profile.operating_profile,
            component="MultiLanguageIsolatedVerifier",
            allow_api_key_fallback=bundle.profile.allow_paid_api_keys,
        )
        sanitized_env = build_sanitized_child_env()
        assert_child_env_isolated(sanitized_env)

        if command_override is not None:
            if not command_override:
                raise ValueError("command_override must not be empty")
            for token in command_override:
                if "unsafe_host_opt_in" in token.lower():
                    raise ValueError("unsafe_host_opt_in is prohibited in verification commands")
            command = tuple(command_override)
        else:
            raw_cmd = bundle.profile.validation_pack.test_commands[0]
            if "unsafe_host_opt_in" in raw_cmd.lower():
                raise ValueError("unsafe_host_opt_in is prohibited in verification commands")
            command = tuple(shlex.split(raw_cmd))

        effective_mode: MultiLanguageIsolationMode = (
            isolation_mode
            if isolation_mode is not None
            else bundle.profile.validation_pack.default_verification_isolation
        )
        stack_type = bundle.profile.stack_type
        offline_mock_only = (
            bundle.profile.validation_pack.offline_mock_only
            or stack_type == "google_apps_script_offline_mock"
        )
        simulation_disclaimer = bundle.profile.safety_policy.simulation_disclaimer
        if stack_type == "google_apps_script_offline_mock" and (
            simulation_disclaimer is None or not simulation_disclaimer.strip()
        ):
            simulation_disclaimer = _DEFAULT_GAS_DISCLAIMER

        receipt_id = f"rcpt-{uuid4().hex[:12]}"
        project_id = bundle.project_definition.project.id

        if effective_mode == "blocked":
            return MultiLanguageVerificationReceipt(
                receipt_id=receipt_id,
                project_id=project_id,
                stack_type=stack_type,
                isolation_mode="blocked",
                candidate_sha=candidate_sha,
                command_executed=command,
                exit_code=1,
                passed=False,
                tests_summary=TestSummary(total=1, passed=0, failed=1, skipped=0),
                offline_mock_only=offline_mock_only,
                simulation_disclaimer=simulation_disclaimer,
                failure_reason=VERIFICATION_ISOLATION_BLOCKED,
            )

        workspace_path = Path(bundle.workspace_root).resolve()
        container_argv: tuple[str, ...] | None = None
        resource_limits: ContainerResourceLimits | None = None

        if effective_mode == "container":
            image = bundle.profile.validation_pack.recommended_container_image
            if (
                container_runtime_bin is None
                or not container_runtime_bin.strip()
                or image is None
                or not image.strip()
            ):
                return MultiLanguageVerificationReceipt(
                    receipt_id=receipt_id,
                    project_id=project_id,
                    stack_type=stack_type,
                    isolation_mode="container",
                    candidate_sha=candidate_sha,
                    command_executed=command,
                    exit_code=1,
                    passed=False,
                    tests_summary=TestSummary(total=1, passed=0, failed=1, skipped=0),
                    offline_mock_only=offline_mock_only,
                    simulation_disclaimer=simulation_disclaimer,
                    failure_reason="CONTAINER_RUNTIME_OR_IMAGE_UNAVAILABLE",
                )
            resource_limits = ContainerResourceLimits()
            with tempfile.TemporaryDirectory(prefix="aipc-c02-out-") as tmp_out:
                container_argv = self.build_container_command(
                    runtime_bin=container_runtime_bin,
                    image=image,
                    workspace=workspace_path,
                    out_dir=Path(tmp_out),
                    command=command,
                    limits=resource_limits,
                )
                exec_argv = list(container_argv)
                pre_digests = snapshot_workspace_digests(workspace_path)
                proc = self._invoke_runner(exec_argv, workspace_path, sanitized_env, runner)
                post_digests = snapshot_workspace_digests(workspace_path)
        else:
            exec_argv = list(assert_argv_no_credentials(command))
            pre_digests = snapshot_workspace_digests(workspace_path)
            proc = self._invoke_runner(exec_argv, workspace_path, sanitized_env, runner)
            post_digests = snapshot_workspace_digests(workspace_path)

        workspace_mutated = pre_digests != post_digests
        combined_output = "\n".join(part for part in (proc.stdout, proc.stderr) if part)
        tests_summary = self.parse_multilang_test_output(
            stack_type, combined_output, int(proc.returncode)
        )

        if workspace_mutated:
            passed = False
            failure_reason: str | None = "WORKSPACE_MUTATED_DURING_VERIFICATION"
        elif proc.returncode != 0 or tests_summary.failed > 0 or tests_summary.passed == 0:
            passed = False
            failure_reason = "VERIFICATION_TESTS_FAILED"
        else:
            passed = True
            failure_reason = None

        return MultiLanguageVerificationReceipt(
            receipt_id=receipt_id,
            project_id=project_id,
            stack_type=stack_type,
            isolation_mode=effective_mode,
            candidate_sha=candidate_sha,
            command_executed=command,
            container_argv=container_argv,
            resource_limits=resource_limits,
            exit_code=int(proc.returncode),
            passed=passed,
            tests_summary=tests_summary,
            workspace_mutated_during_verification=workspace_mutated,
            offline_mock_only=offline_mock_only,
            simulation_disclaimer=simulation_disclaimer,
            failure_reason=failure_reason,
        )

    @staticmethod
    def _invoke_runner(
        argv: list[str],
        cwd: Path,
        env: dict[str, str],
        runner: Callable[..., subprocess.CompletedProcess[str]] | None,
    ) -> subprocess.CompletedProcess[str]:
        if runner is not None:
            return runner(
                argv,
                cwd=str(cwd),
                env=env,
                capture_output=True,
                text=True,
                stdin=subprocess.DEVNULL,
                check=False,
            )
        return subprocess.run(
            argv,
            cwd=str(cwd),
            env=env,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            check=False,
        )


class PRReadinessBundle(CanonicalRecord):
    """Final Milestone C PR readiness package stopping at the Human Merge boundary."""

    bundle_id: NonBlank
    project_id: NonBlank
    milestone_id: NonBlank
    phase_id: NonBlank
    task_id: NonBlank
    candidate_sha: Commit
    stack_type: ProjectStackType
    verification_receipt: MultiLanguageVerificationReceipt
    review_verdict: ReviewVerdict
    gate_verdict: Literal["PASS", "REVISE", "BLOCKED"]
    pr_ready: StrictBool
    human_merge_approval_required: Literal[True] = True
    auto_merge_performed: Literal[False] = False
    next_authorized_action: HandoffNextAuthorizedAction
    blockers: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_pr_readiness_invariants(self) -> Self:
        if self.pr_ready:
            if not self.verification_receipt.passed:
                raise ValueError(
                    "pr_ready cannot be True when verification_receipt.passed is False"
                )
            if self.review_verdict != ReviewVerdict.PASS:
                raise ValueError("pr_ready cannot be True when review_verdict != PASS")
            if self.gate_verdict != "PASS":
                raise ValueError("pr_ready cannot be True when gate_verdict != 'PASS'")
            if self.next_authorized_action != "HUMAN_MERGE_REVIEW":
                raise ValueError(
                    "pr_ready=True requires next_authorized_action == 'HUMAN_MERGE_REVIEW'"
                )
        assert_no_secrets(self.model_dump(mode="json"))
        return self


class MilestoneCEndToEndPipeline:
    """Orchestrates Milestone C onboarding, queue dispatch, verification, and review."""

    def __init__(
        self,
        *,
        verifier: MultiLanguageIsolatedVerifier | None = None,
    ) -> None:
        self.verifier = verifier or MultiLanguageIsolatedVerifier()

    def run_e2e_cycle(
        self,
        *,
        bundle: OnboardedProjectBundle,
        task_queue: PersistentTaskQueue,
        worker_step: WorkerStepCallable,
        worker_binding: RoleModelBinding,
        reviewer_gate: IndependentSubscriptionReviewerGate | None = None,
        review_callback: Callable[..., SubscriptionReviewerGateDecision] | None = None,
        reviewer_binding: RoleModelBinding | None = None,
        isolation_mode: MultiLanguageIsolationMode | None = None,
        container_runtime_bin: str | None = None,
        command_override: tuple[str, ...] | None = None,
        runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
        phase_id: str | None = None,
        evidence_root: Path | None = None,
    ) -> PRReadinessBundle:
        """Execute one end-to-end Milestone C cycle stopping at Human Merge Review."""
        assert_subscription_only_mode(
            bundle.profile.operating_profile,
            component="MilestoneCEndToEndPipeline",
            allow_api_key_fallback=bundle.profile.allow_paid_api_keys,
        )

        queued_task = task_queue.next_eligible_task()
        if queued_task is None:
            raise ControllerError("No eligible task available in PersistentTaskQueue")

        task_id = queued_task.contract.task_id
        milestone_id = queued_task.contract.milestone_id
        resolved_phase_id = phase_id or queued_task.contract.phase_id
        project_id = bundle.project_definition.project.id
        resolved_evidence_root = str(evidence_root or Path(bundle.workspace_root).resolve())

        task_queue.begin_dispatch(
            task_id,
            worker_id=worker_binding.binding_id,
            provider=worker_binding.provider_id.replace("_", "-"),
        )

        worker_outcome = worker_step(worker_binding, queued_task.current_attempt + 1, None)
        receipt = self.verifier.verify_project_candidate(
            bundle=bundle,
            candidate_sha=worker_outcome.candidate_sha,
            isolation_mode=isolation_mode,
            command_override=command_override,
            container_runtime_bin=container_runtime_bin,
            runner=runner,
        )

        if not receipt.passed:
            blocker = receipt.failure_reason or "VERIFICATION_FAILED"
            task_queue.record_attempt_outcome(
                task_id,
                candidate_sha=worker_outcome.candidate_sha,
                checkpoint_id=f"chk-{task_id}-verify-fail",
                evidence_root=resolved_evidence_root,
                review_id=f"rev-skip-{task_id}",
                review_verdict="BLOCKED",
                gate_verdict="BLOCKED",
                failure_reason=blocker,
            )
            return PRReadinessBundle(
                bundle_id=f"prb-{uuid4().hex[:12]}",
                project_id=project_id,
                milestone_id=milestone_id,
                phase_id=resolved_phase_id,
                task_id=task_id,
                candidate_sha=worker_outcome.candidate_sha,
                stack_type=bundle.profile.stack_type,
                verification_receipt=receipt,
                review_verdict=ReviewVerdict.BLOCKED,
                gate_verdict="BLOCKED",
                pr_ready=False,
                next_authorized_action="HALT_ON_BLOCKER",
                blockers=(blocker,),
            )

        if review_callback is not None:
            gate_decision = review_callback(
                worker_outcome=worker_outcome,
                verification_receipt=receipt,
                bundle=bundle,
            )
        elif reviewer_gate is not None:
            gate_decision = reviewer_gate.evaluate(
                worker_outcome.review_context,
                expected_candidate_sha=worker_outcome.candidate_sha,
                worker_agent_id=worker_binding.binding_id,
                worker_provider_family=worker_binding.provider_family,
                worker_session_id=worker_outcome.worker_session_id,
                explicit_binding_id=(
                    reviewer_binding.binding_id if reviewer_binding is not None else None
                ),
            )
        else:
            raise ControllerError("Either reviewer_gate or review_callback must be provided")

        review_verdict = gate_decision.review_result.verdict
        if gate_decision.gate_outcome == "PASS" and review_verdict == ReviewVerdict.PASS:
            gate_verdict: Literal["PASS", "REVISE", "BLOCKED"] = "PASS"
            pr_ready = True
            next_action: HandoffNextAuthorizedAction = "HUMAN_MERGE_REVIEW"
            blockers: tuple[str, ...] = ()
            q_review: Literal["PASS", "REVISE", "BLOCKED"] = "PASS"
            q_gate: Literal["PASS", "REVISE", "BLOCKED"] = "PASS"
        elif gate_decision.gate_outcome == "REVISE" or review_verdict == ReviewVerdict.REVISE:
            gate_verdict = "REVISE"
            pr_ready = False
            next_action = "DISPATCH_BOUNDED_REPAIR"
            blockers = tuple(gate_decision.reasons) or ("REVIEW_REVISE_REQUIRED",)
            q_review = "REVISE"
            q_gate = "REVISE"
        else:
            gate_verdict = "BLOCKED"
            pr_ready = False
            next_action = "HALT_ON_BLOCKER"
            blockers = tuple(gate_decision.reasons) or (
                f"REVIEW_GATE_{gate_decision.gate_outcome}",
            )
            q_review = "BLOCKED"
            q_gate = "BLOCKED"

        task_queue.record_attempt_outcome(
            task_id,
            candidate_sha=worker_outcome.candidate_sha,
            checkpoint_id=f"chk-{task_id}-attempt",
            evidence_root=resolved_evidence_root,
            review_id=gate_decision.review_result.review_id,
            review_verdict=q_review,
            gate_verdict=q_gate,
            failure_reason=blockers[0] if blockers else None,
        )

        return PRReadinessBundle(
            bundle_id=f"prb-{uuid4().hex[:12]}",
            project_id=project_id,
            milestone_id=milestone_id,
            phase_id=resolved_phase_id,
            task_id=task_id,
            candidate_sha=worker_outcome.candidate_sha,
            stack_type=bundle.profile.stack_type,
            verification_receipt=receipt,
            review_verdict=review_verdict,
            gate_verdict=gate_verdict,
            pr_ready=pr_ready,
            next_authorized_action=next_action,
            blockers=blockers,
        )


__all__ = [
    "ContainerResourceLimits",
    "MilestoneCEndToEndPipeline",
    "MultiLanguageIsolatedVerifier",
    "MultiLanguageIsolationMode",
    "MultiLanguageVerificationReceipt",
    "PRReadinessBundle",
    "snapshot_workspace_digests",
]
