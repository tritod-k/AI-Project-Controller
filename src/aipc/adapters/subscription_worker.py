"""Workstream B01 — Antigravity / Gemini Subscription Worker Automation.

Automates bounded implementation and verification execution through qualified
Antigravity/Gemini (and Codex) subscription runtimes inside isolated candidate
git worktrees. Captures prompt digest/excerpt, requested vs actual model,
session ID, changed files, unified git diffs, verification command receipts,
and exact base/candidate commit SHAs. Enforces timeout, cancellation, no-op
detection, and forbidden-path guards before returning canonical AgentResult
and retained verification evidence.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import shlex
import subprocess
import time
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, Literal, Self

from pydantic import StrictInt, model_validator

from aipc.adapters.operational_integration import classify_subscription_operational_error
from aipc.adapters.subscription_qualification import (
    ProviderRuntimeQualificationReport,
    enforce_subscription_only_profile,
    evaluate_runtime_eligibility,
)
from aipc.controller.errors import ProviderInterruption
from aipc.domain.enums import AgentStatus, ChangeType
from aipc.domain.errors import AgentError, AgentTimeoutError
from aipc.domain.models import (
    AgentResult,
    CanonicalRecord,
    CommandResult,
    EvidenceReferences,
    FileChange,
    NonBlank,
    NonNegativeInt,
    TaskRequest,
    TestSummary,
)
from aipc.hardening.env_isolation import (
    SubscriptionBoundaryError,
    assert_child_env_isolated,
    build_sanitized_child_env,
)
from aipc.hardening.secrets import assert_no_secrets
from aipc.normalization.validation import validate_agent_result
from aipc.recovery.models import ProviderCondition

_NL = chr(10)
_BS = chr(92)

DEFAULT_FORBIDDEN_WORKER_PATHS: tuple[str, ...] = (
    "MASTER_PLAN.md",
    "AGENTS.md",
    "SKILL.md",
    "schemas/",
    ".github/workflows/",
    "tools/sanitized_exec.py",
    ".env",
    ".env.local",
    ".git/config",
    ".git/hooks/",
)

_ALWAYS_FORBIDDEN_SECRET_AND_HOOK_PATHS: tuple[str, ...] = (
    ".env",
    ".env.local",
    ".git/config",
    ".git/hooks/",
)

_PROHIBITED_WORKER_AUTH_MODES: frozenset[str] = frozenset(
    {
        "openai_responses_api",
        "responses_api",
        "gemini_api_key",
        "google_api_key",
        "vertex_ai",
        "anthropic_api_key",
        "api_key",
        "paid_api",
    }
)

_HEX_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_PYTEST_PASSED_RE = re.compile(r"(\d+)\s+passed")
_PYTEST_FAILED_RE = re.compile(r"(\d+)\s+failed")
_PYTEST_SKIPPED_RE = re.compile(r"(\d+)\s+skipped")
_PYTEST_ERROR_RE = re.compile(r"(\d+)\s+errors?")


def _normalize_rel_posix(path_str: str) -> str:
    cleaned = path_str.strip().replace(_BS, "/")
    while cleaned.startswith("./"):
        cleaned = cleaned[2:]
    return cleaned.strip("/")


def _matches_path_pattern(rel_posix: str, pattern: str) -> bool:
    norm_path = _normalize_rel_posix(rel_posix)
    norm_pat = _normalize_rel_posix(pattern)
    if not norm_pat:
        return False
    if pattern.endswith(("/", _BS)):
        return norm_path == norm_pat or norm_path.startswith(f"{norm_pat}/")
    return norm_path == norm_pat or norm_path.startswith(f"{norm_pat}/")


def _parse_pytest_output(output_text: str, exit_code: int) -> TestSummary | None:
    passed_m = _PYTEST_PASSED_RE.search(output_text)
    failed_m = _PYTEST_FAILED_RE.search(output_text)
    skipped_m = _PYTEST_SKIPPED_RE.search(output_text)
    error_m = _PYTEST_ERROR_RE.search(output_text)
    if not any((passed_m, failed_m, skipped_m, error_m)):
        if exit_code == 0:
            return TestSummary(total=1, passed=1, failed=0, skipped=0)
        return TestSummary(total=1, passed=0, failed=1, skipped=0)
    passed = int(passed_m.group(1)) if passed_m else 0
    failed = (int(failed_m.group(1)) if failed_m else 0) + (int(error_m.group(1)) if error_m else 0)
    skipped = int(skipped_m.group(1)) if skipped_m else 0
    total = passed + failed + skipped
    return TestSummary(total=total, passed=passed, failed=failed, skipped=skipped)


class WorkerCommandReceipt(CanonicalRecord):
    """Sanitized command execution receipt recorded inside the candidate worktree."""

    command: NonBlank
    exit_code: StrictInt
    stdout: str = ""
    stderr: str = ""
    duration_ms: NonNegativeInt = 0
    sanitized_env: bool = True

    @model_validator(mode="after")
    def validate_receipt_no_secrets(self) -> Self:
        assert_no_secrets(self.model_dump(mode="json"))
        return self


class WorkerInvocationResponse(CanonicalRecord):
    """Normalized response returned from a subscription worker CLI invocation."""

    session_id: NonBlank
    model_used: NonBlank
    summary: str = "Completed subscription worker invocation."
    raw_output_ref: str | None = None
    input_tokens: StrictInt | None = None
    output_tokens: StrictInt | None = None
    total_tokens: StrictInt | None = None


class WorkerAutomationReceipt(CanonicalRecord):
    """Immutable, secret-free audit receipt for a Milestone B01 worker execution."""

    receipt_id: NonBlank
    task_id: NonBlank
    operating_profile: str = "subscription_only"
    provider_id: Literal["gemini-antigravity", "codex"] = "gemini-antigravity"
    auth_mode: Literal["antigravity_subscription", "codex_subscription"] = (
        "antigravity_subscription"
    )
    prompt_sha256: NonBlank
    prompt_excerpt: NonBlank
    model_requested: NonBlank
    model_used: NonBlank
    session_id: NonBlank
    candidate_worktree: NonBlank
    base_commit_sha: NonBlank
    candidate_commit_sha: NonBlank
    files_changed: tuple[FileChange, ...] = ()
    git_diff_text: str = ""
    git_diff_path: str | None = None
    test_log_path: str | None = None
    receipt_json_path: str | None = None
    command_receipts: tuple[WorkerCommandReceipt, ...] = ()
    tests_summary: TestSummary | None = None
    noop_detected: bool = False
    forbidden_paths_violated: tuple[str, ...] = ()
    sanitized_child_env: bool = True

    @model_validator(mode="after")
    def validate_worker_receipt_invariants(self) -> Self:
        enforce_subscription_only_profile(
            self.operating_profile,
            auth_mode=self.auth_mode,
            allow_api_key_fallback=False,
        )
        if not _HEX_SHA_RE.fullmatch(self.base_commit_sha):
            raise ValueError("base_commit_sha must be a 40-character lowercase hex git SHA")
        if not _HEX_SHA_RE.fullmatch(self.candidate_commit_sha):
            raise ValueError("candidate_commit_sha must be a 40-character lowercase hex git SHA")
        if not self.sanitized_child_env:
            raise ValueError("WorkerAutomationReceipt requires sanitized_child_env=True")
        assert_no_secrets(self.model_dump(mode="json"))
        return self


class SubscriptionWorkerConfig(CanonicalRecord):
    """Configuration contract for B01 subscription worker automation."""

    operating_profile: str = "subscription_only"
    provider_id: Literal["gemini-antigravity", "codex"] = "gemini-antigravity"
    auth_mode: str = "antigravity_subscription"
    model_requested: NonBlank = "gemini-3.8-flash-medium"
    agent_name: NonBlank = "aipc-implementation-worker"
    candidate_worktree: NonBlank
    stable_runner_root: str | None = None
    evidence_output_dir: NonBlank
    in_scope_paths: tuple[str, ...] = ()
    out_of_scope_paths: tuple[str, ...] = ()
    forbidden_paths: tuple[str, ...] = DEFAULT_FORBIDDEN_WORKER_PATHS
    validation_commands: tuple[str, ...] = ()
    require_non_empty_diff: bool = True
    auto_commit_candidate: bool = True
    commit_message: str | None = None
    allow_api_key_fallback: bool = False

    @model_validator(mode="after")
    def validate_subscription_worker_config(self) -> Self:
        enforce_subscription_only_profile(
            self.operating_profile,
            auth_mode=self.auth_mode,
            allow_api_key_fallback=self.allow_api_key_fallback,
        )
        norm_auth = self.auth_mode.strip().lower()
        if norm_auth in _PROHIBITED_WORKER_AUTH_MODES or "api_key" in norm_auth:
            raise SubscriptionBoundaryError(
                f"Prohibited paid API authentication mode '{self.auth_mode}' in worker config."
            )
        if norm_auth not in ("antigravity_subscription", "codex_subscription"):
            raise ValueError(f"Unsupported subscription worker auth_mode '{self.auth_mode}'.")
        cand = Path(self.candidate_worktree).resolve()
        if self.stable_runner_root is not None:
            stable = Path(self.stable_runner_root).resolve()
            if cand == stable or cand.is_relative_to(stable) or stable.is_relative_to(cand):
                raise ValueError(
                    "candidate_worktree must be strictly isolated from stable_runner_root."
                )
        assert_no_secrets(self.model_dump(mode="json"))
        return self


class AutomatedWorkerOutcome(CanonicalRecord):
    """Combined canonical AgentResult and retained B01 WorkerAutomationReceipt."""

    agent_result: AgentResult
    receipt: WorkerAutomationReceipt


WorkerInvokerCallable = Callable[
    [TaskRequest, SubscriptionWorkerConfig, Mapping[str, str], str],
    Awaitable[WorkerInvocationResponse],
]

ValidationCommandCallable = Callable[
    [str, Path, Mapping[str, str]],
    WorkerCommandReceipt,
]


def _run_git(
    worktree: Path,
    args: list[str],
    env: Mapping[str, str],
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-c", "core.fsmonitor=false", *args],
        cwd=str(worktree),
        env=dict(env),
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        check=False,
    )


def _default_validation_command_runner(
    command: str,
    worktree: Path,
    env: Mapping[str, str],
) -> WorkerCommandReceipt:
    t0 = time.monotonic()
    argv = shlex.split(command, posix=False)
    cleaned_argv = [
        token[1:-1]
        if len(token) >= 2 and token[0] == token[-1] and token[0] in ("'", '"')
        else token
        for token in argv
    ]
    res = subprocess.run(
        cleaned_argv,
        cwd=str(worktree),
        env=dict(env),
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        check=False,
    )
    elapsed_ms = max(0, int((time.monotonic() - t0) * 1000))
    return WorkerCommandReceipt(
        command=command,
        exit_code=int(res.returncode),
        stdout=res.stdout or "",
        stderr=res.stderr or "",
        duration_ms=elapsed_ms,
        sanitized_env=True,
    )


class AntigravitySubscriptionWorkerAutomation:
    """Automates bounded worker execution in isolated candidate worktrees."""

    def __init__(
        self,
        config: SubscriptionWorkerConfig,
        *,
        qualification_report: ProviderRuntimeQualificationReport | None = None,
        worker_invoker: WorkerInvokerCallable | None = None,
        command_runner: ValidationCommandCallable | None = None,
        base_env: Mapping[str, str] | None = None,
    ) -> None:
        self._config = SubscriptionWorkerConfig.model_validate(config)
        self._qualification_report = qualification_report
        self._worker_invoker = worker_invoker
        self._command_runner = command_runner or _default_validation_command_runner
        self._base_env = base_env
        self._active_tasks: dict[str, asyncio.Task[Any]] = {}
        self._cancelled_task_ids: set[str] = set()

    @property
    def config(self) -> SubscriptionWorkerConfig:
        return self._config

    def _prepare_sanitized_env(self) -> dict[str, str]:
        env = build_sanitized_child_env(self._base_env)
        assert_child_env_isolated(env)
        return env

    def _verify_runtime_qualification(self) -> str:
        if self._qualification_report is None:
            return self._config.model_requested
        decision = evaluate_runtime_eligibility(
            self._qualification_report,
            role="worker",
            requested_model=self._config.model_requested,
        )
        if not decision.eligible or decision.resolved_model is None:
            raise ProviderInterruption(ProviderCondition.UNAVAILABLE)
        return decision.resolved_model

    async def cancel(self, task_id: str) -> bool:
        """Cancel an in-flight or queued worker task by task_id."""
        self._cancelled_task_ids.add(task_id)
        running = self._active_tasks.get(task_id)
        if running is not None and not running.done():
            running.cancel()
            try:
                await running
            except (asyncio.CancelledError, BaseException):
                pass
            return True
        return False

    def _read_head_sha(self, worktree: Path, env: Mapping[str, str]) -> str:
        res = _run_git(worktree, ["rev-parse", "HEAD"], env)
        sha = (res.stdout or "").strip().lower()
        if res.returncode != 0 or not _HEX_SHA_RE.fullmatch(sha):
            raise AgentError(
                f"Candidate worktree '{worktree}' does not have a valid 40-hex git HEAD SHA."
            )
        return sha

    def _collect_git_changes_and_diff(
        self,
        worktree: Path,
        env: Mapping[str, str],
    ) -> tuple[list[FileChange], str]:
        status_res = _run_git(worktree, ["status", "--porcelain", "-uall"], env)
        changes: list[FileChange] = []
        seen_paths: set[str] = set()
        for raw_line in (status_res.stdout or "").splitlines():
            if len(raw_line) < 4:
                continue
            xy = raw_line[:2]
            raw_path = raw_line[3:].strip()
            if " -> " in raw_path:
                raw_path = raw_path.split(" -> ", 1)[1].strip()
            if raw_path.startswith('"') and raw_path.endswith('"'):
                raw_path = raw_path[1:-1]
            rel_posix = _normalize_rel_posix(raw_path)
            if not rel_posix or rel_posix in seen_paths:
                continue
            seen_paths.add(rel_posix)
            if "?" in xy or "A" in xy:
                ctype = ChangeType.ADDED
            elif "D" in xy:
                ctype = ChangeType.DELETED
            else:
                ctype = ChangeType.MODIFIED
            changes.append(FileChange(path=rel_posix, change_type=ctype))

        diff_res = _run_git(worktree, ["diff", "HEAD"], env)
        diff_parts: list[str] = []
        if (diff_res.stdout or "").strip():
            diff_parts.append(diff_res.stdout)
        for fc in changes:
            if fc.change_type == ChangeType.ADDED:
                if any(
                    _matches_path_pattern(fc.path, pat)
                    for pat in _ALWAYS_FORBIDDEN_SECRET_AND_HOOK_PATHS
                ):
                    continue
                target_file = worktree / PurePosixPath(fc.path)
                if target_file.is_file():
                    try:
                        content = target_file.read_text(encoding="utf-8")
                    except OSError:
                        content = ""
                    plus_lines = _NL.join(f"+{line}" for line in content.splitlines())
                    diff_parts.append(f"--- /dev/null{_NL}+++ b/{fc.path}{_NL}{plus_lines}{_NL}")
        return changes, _NL.join(diff_parts)

    def _detect_forbidden_paths(
        self,
        worktree: Path,
        files_changed: list[FileChange],
    ) -> list[str]:
        violations: list[str] = []
        resolved_wt = worktree.resolve()
        resolved_stable = (
            Path(self._config.stable_runner_root).resolve()
            if self._config.stable_runner_root is not None
            else None
        )

        for fc in files_changed:
            rel_posix = _normalize_rel_posix(fc.path)
            candidate_path = (resolved_wt / PurePosixPath(rel_posix)).resolve()

            if not candidate_path.is_relative_to(resolved_wt):
                violations.append(rel_posix)
                continue
            if resolved_stable is not None and (
                candidate_path == resolved_stable or candidate_path.is_relative_to(resolved_stable)
            ):
                violations.append(rel_posix)
                continue

            if any(
                _matches_path_pattern(rel_posix, pat)
                for pat in _ALWAYS_FORBIDDEN_SECRET_AND_HOOK_PATHS
            ):
                violations.append(rel_posix)
                continue

            if any(
                _matches_path_pattern(rel_posix, pat) for pat in self._config.out_of_scope_paths
            ):
                violations.append(rel_posix)
                continue

            explicitly_in_scope = any(
                _matches_path_pattern(rel_posix, pat) for pat in self._config.in_scope_paths
            )
            if not explicitly_in_scope and any(
                _matches_path_pattern(rel_posix, pat) for pat in self._config.forbidden_paths
            ):
                violations.append(rel_posix)
                continue

            if self._config.in_scope_paths and not explicitly_in_scope:
                violations.append(rel_posix)

        return sorted(set(violations))

    def _revert_unauthorized_changes(self, worktree: Path, env: Mapping[str, str]) -> None:
        _run_git(worktree, ["checkout", "--", "."], env)
        _run_git(worktree, ["clean", "-fd"], env)

    def _write_retained_evidence(
        self,
        *,
        task_id: str,
        diff_text: str,
        command_receipts: list[WorkerCommandReceipt],
        receipt: WorkerAutomationReceipt,
    ) -> tuple[str, str | None, str]:
        out_dir = Path(self._config.evidence_output_dir).resolve() / task_id
        out_dir.mkdir(parents=True, exist_ok=True)

        diff_file = out_dir / "git.diff"
        diff_file.write_text(diff_text, encoding="utf-8")
        git_diff_path = str(diff_file)

        test_log_path: str | None = None
        if command_receipts:
            test_log_file = out_dir / "test_output.log"
            log_lines: list[str] = []
            for cr in command_receipts:
                log_lines.append(f"$ {cr.command} (exit_code={cr.exit_code})")
                if cr.stdout:
                    log_lines.append(cr.stdout)
                if cr.stderr:
                    log_lines.append(cr.stderr)
            test_log_file.write_text(_NL.join(log_lines) + _NL, encoding="utf-8")
            test_log_path = str(test_log_file)

        receipt_file = out_dir / "worker_receipt.json"
        updated_receipt = receipt.model_copy(
            update={
                "git_diff_path": git_diff_path,
                "test_log_path": test_log_path,
                "receipt_json_path": str(receipt_file),
            }
        )
        receipt_file.write_text(
            json.dumps(updated_receipt.model_dump(mode="json"), indent=2, sort_keys=True),
            encoding="utf-8",
        )
        return git_diff_path, test_log_path, str(receipt_file)

    async def execute_worker_task(self, task: TaskRequest) -> AutomatedWorkerOutcome:
        """Execute a bounded implementation task in the candidate worktree."""
        task = TaskRequest.model_validate(task)
        resolved_model = self._verify_runtime_qualification()
        sanitized_env = self._prepare_sanitized_env()

        worktree = Path(self._config.candidate_worktree).resolve()
        if Path(task.workspace).resolve() != worktree:
            raise AgentError(
                "TaskRequest.workspace must match SubscriptionWorkerConfig.candidate_worktree."
            )

        base_sha = self._read_head_sha(worktree, sanitized_env)
        started_at = datetime.now(UTC)
        prompt_sha256 = hashlib.sha256(task.prompt.encode("utf-8")).hexdigest()
        prompt_excerpt = task.prompt.strip()[:200]
        provider_label: Literal["google", "openai"] = (
            "google" if self._config.provider_id == "gemini-antigravity" else "openai"
        )

        if task.task_id in self._cancelled_task_ids:
            return self._build_outcome(
                task=task,
                provider_label=provider_label,
                status=AgentStatus.CANCELLED,
                summary="Subscription worker task cancelled prior to execution.",
                started_at=started_at,
                completed_at=datetime.now(UTC),
                prompt_sha256=prompt_sha256,
                prompt_excerpt=prompt_excerpt,
                model_used=resolved_model,
                session_id=f"cancelled-{task.task_id}",
                base_sha=base_sha,
                candidate_sha=base_sha,
                files_changed=[],
                diff_text="",
                command_receipts=[],
                tests_summary=None,
                noop_detected=False,
                forbidden_paths=[],
                errors=["CANCELLED"],
                error_code="CANCELLED",
            )

        if self._worker_invoker is None:
            raise ProviderInterruption(ProviderCondition.UNAVAILABLE)

        async def _invoke() -> WorkerInvocationResponse:
            return await self._worker_invoker(task, self._config, sanitized_env, resolved_model)

        fut = asyncio.ensure_future(_invoke())
        self._active_tasks[task.task_id] = fut
        try:
            inv_res = await asyncio.wait_for(fut, timeout=task.timeout_seconds)
        except asyncio.CancelledError:
            self._revert_unauthorized_changes(worktree, sanitized_env)
            return self._build_outcome(
                task=task,
                provider_label=provider_label,
                status=AgentStatus.CANCELLED,
                summary="Subscription worker execution cancelled in flight.",
                started_at=started_at,
                completed_at=datetime.now(UTC),
                prompt_sha256=prompt_sha256,
                prompt_excerpt=prompt_excerpt,
                model_used=resolved_model,
                session_id=f"cancelled-{task.task_id}",
                base_sha=base_sha,
                candidate_sha=base_sha,
                files_changed=[],
                diff_text="",
                command_receipts=[],
                tests_summary=None,
                noop_detected=False,
                forbidden_paths=[],
                errors=["CANCELLED"],
                error_code="CANCELLED",
            )
        except Exception as exc:
            kind, reason = classify_subscription_operational_error(exc)
            if kind == "TIMEOUT":
                raise AgentTimeoutError(reason) from None
            if isinstance(kind, ProviderCondition):
                raise ProviderInterruption(kind) from None
            raise AgentError(reason) from None
        finally:
            self._active_tasks.pop(task.task_id, None)

        files_changed, diff_text = self._collect_git_changes_and_diff(worktree, sanitized_env)

        # 1. Forbidden-path guard
        forbidden_violations = self._detect_forbidden_paths(worktree, files_changed)
        if forbidden_violations:
            self._revert_unauthorized_changes(worktree, sanitized_env)
            err_msg = "FORBIDDEN_PATH_VIOLATION: worker modified unauthorized paths: " + ", ".join(
                forbidden_violations
            )
            return self._build_outcome(
                task=task,
                provider_label=provider_label,
                status=AgentStatus.FAILED,
                summary=err_msg,
                started_at=started_at,
                completed_at=datetime.now(UTC),
                prompt_sha256=prompt_sha256,
                prompt_excerpt=prompt_excerpt,
                model_used=inv_res.model_used,
                session_id=inv_res.session_id,
                base_sha=base_sha,
                candidate_sha=base_sha,
                files_changed=files_changed,
                diff_text="",
                command_receipts=[],
                tests_summary=None,
                noop_detected=False,
                forbidden_paths=forbidden_violations,
                errors=[err_msg],
                error_code="FORBIDDEN_PATH_VIOLATION",
            )

        # 2. No-op detection guard
        if self._config.require_non_empty_diff and (not files_changed or not diff_text.strip()):
            err_msg = "NOOP_EXECUTION_DETECTED: worker completed without modifying candidate files."
            return self._build_outcome(
                task=task,
                provider_label=provider_label,
                status=AgentStatus.FAILED,
                summary=err_msg,
                started_at=started_at,
                completed_at=datetime.now(UTC),
                prompt_sha256=prompt_sha256,
                prompt_excerpt=prompt_excerpt,
                model_used=inv_res.model_used,
                session_id=inv_res.session_id,
                base_sha=base_sha,
                candidate_sha=base_sha,
                files_changed=[],
                diff_text="",
                command_receipts=[],
                tests_summary=None,
                noop_detected=True,
                forbidden_paths=[],
                errors=[err_msg],
                error_code="NOOP_EXECUTION_DETECTED",
            )

        # 3. Execute validation commands in isolated candidate worktree
        command_receipts: list[WorkerCommandReceipt] = []
        agg_passed = 0
        agg_failed = 0
        agg_skipped = 0
        validation_failed_cmds: list[str] = []

        for cmd in self._config.validation_commands:
            c_receipt = self._command_runner(cmd, worktree, sanitized_env)
            command_receipts.append(c_receipt)
            parsed = _parse_pytest_output(
                f"{c_receipt.stdout}{_NL}{c_receipt.stderr}",
                c_receipt.exit_code,
            )
            if parsed is not None:
                agg_passed += parsed.passed
                agg_failed += parsed.failed
                agg_skipped += parsed.skipped
            if c_receipt.exit_code != 0:
                validation_failed_cmds.append(f"{cmd} (exit {c_receipt.exit_code})")

        tests_summary: TestSummary | None = None
        if command_receipts:
            total_tests = agg_passed + agg_failed + agg_skipped
            tests_summary = TestSummary(
                total=total_tests,
                passed=agg_passed,
                failed=agg_failed,
                skipped=agg_skipped,
            )

        if validation_failed_cmds:
            err_msg = "VALIDATION_COMMAND_FAILED: " + "; ".join(validation_failed_cmds)
            return self._build_outcome(
                task=task,
                provider_label=provider_label,
                status=AgentStatus.FAILED,
                summary=err_msg,
                started_at=started_at,
                completed_at=datetime.now(UTC),
                prompt_sha256=prompt_sha256,
                prompt_excerpt=prompt_excerpt,
                model_used=inv_res.model_used,
                session_id=inv_res.session_id,
                base_sha=base_sha,
                candidate_sha=base_sha,
                files_changed=files_changed,
                diff_text=diff_text,
                command_receipts=command_receipts,
                tests_summary=tests_summary,
                noop_detected=False,
                forbidden_paths=[],
                errors=[err_msg],
                error_code="VALIDATION_COMMAND_FAILED",
            )

        # 4. Commit authorized changes to capture exact candidate commit SHA
        candidate_sha = base_sha
        if self._config.auto_commit_candidate and files_changed:
            add_paths = [fc.path for fc in files_changed]
            add_res = _run_git(worktree, ["add", "--", *add_paths], sanitized_env)
            if add_res.returncode != 0:
                raise AgentError(f"Failed staging candidate files: {add_res.stderr}")
            commit_msg = (
                self._config.commit_message
                or f"feat({task.phase_id}): automated worker implementation for {task.task_id}"
            )
            commit_res = _run_git(worktree, ["commit", "-m", commit_msg], sanitized_env)
            if commit_res.returncode != 0:
                raise AgentError(f"Failed committing candidate changes: {commit_res.stderr}")
            candidate_sha = self._read_head_sha(worktree, sanitized_env)

        return self._build_outcome(
            task=task,
            provider_label=provider_label,
            status=AgentStatus.COMPLETED,
            summary=inv_res.summary,
            started_at=started_at,
            completed_at=datetime.now(UTC),
            prompt_sha256=prompt_sha256,
            prompt_excerpt=prompt_excerpt,
            model_used=inv_res.model_used,
            session_id=inv_res.session_id,
            base_sha=base_sha,
            candidate_sha=candidate_sha,
            files_changed=files_changed,
            diff_text=diff_text,
            command_receipts=command_receipts,
            tests_summary=tests_summary,
            noop_detected=False,
            forbidden_paths=[],
            errors=[],
            error_code=None,
            raw_output_ref=inv_res.raw_output_ref,
        )

    def _build_outcome(
        self,
        *,
        task: TaskRequest,
        provider_label: Literal["google", "openai"],
        status: AgentStatus,
        summary: str,
        started_at: datetime,
        completed_at: datetime,
        prompt_sha256: str,
        prompt_excerpt: str,
        model_used: str,
        session_id: str,
        base_sha: str,
        candidate_sha: str,
        files_changed: list[FileChange],
        diff_text: str,
        command_receipts: list[WorkerCommandReceipt],
        tests_summary: TestSummary | None,
        noop_detected: bool,
        forbidden_paths: list[str],
        errors: list[str],
        error_code: str | None,
        raw_output_ref: str | None = None,
    ) -> AutomatedWorkerOutcome:
        initial_receipt = WorkerAutomationReceipt(
            receipt_id=f"wrec-{task.task_id}-{candidate_sha[:8]}",
            task_id=task.task_id,
            operating_profile=self._config.operating_profile,
            provider_id=self._config.provider_id,
            auth_mode=self._config.auth_mode,  # type: ignore[arg-type]
            prompt_sha256=prompt_sha256,
            prompt_excerpt=prompt_excerpt,
            model_requested=self._config.model_requested,
            model_used=model_used,
            session_id=session_id,
            candidate_worktree=str(Path(self._config.candidate_worktree).resolve()),
            base_commit_sha=base_sha,
            candidate_commit_sha=candidate_sha,
            files_changed=tuple(files_changed),
            git_diff_text=diff_text,
            command_receipts=tuple(command_receipts),
            tests_summary=tests_summary,
            noop_detected=noop_detected,
            forbidden_paths_violated=tuple(forbidden_paths),
            sanitized_child_env=True,
        )
        git_diff_path, test_log_path, receipt_json_path = self._write_retained_evidence(
            task_id=task.task_id,
            diff_text=diff_text,
            command_receipts=command_receipts,
            receipt=initial_receipt,
        )
        final_receipt = initial_receipt.model_copy(
            update={
                "git_diff_path": git_diff_path,
                "test_log_path": test_log_path,
                "receipt_json_path": receipt_json_path,
            }
        )

        meta: dict[str, Any] = {
            "operating_profile": self._config.operating_profile,
            "provider_id": self._config.provider_id,
            "auth_mode": self._config.auth_mode,
            "model_requested": self._config.model_requested,
            "model_used": model_used,
            "session_id": session_id,
            "prompt_sha256": prompt_sha256,
            "base_commit_sha": base_sha,
            "candidate_commit_sha": candidate_sha,
            "receipt_json_path": receipt_json_path,
            "noop_detected": noop_detected,
            "forbidden_paths_violated": list(forbidden_paths),
            "sanitized_child_env": True,
        }
        if error_code is not None:
            meta["error_code"] = error_code

        canonical_result = validate_agent_result(
            AgentResult(
                schema_version="1.0",
                task_id=task.task_id,
                agent_id=self._config.agent_name,
                provider=provider_label,
                status=status,
                started_at=started_at,
                completed_at=completed_at,
                summary=summary,
                files_changed=files_changed,
                commands=[
                    CommandResult(command=cr.command, exit_code=cr.exit_code)
                    for cr in command_receipts
                ],
                tests=tests_summary,
                artifacts=[receipt_json_path],
                warnings=[],
                errors=errors,
                approval_requests=[],
                worker_claimed_complete=status == AgentStatus.COMPLETED,
                evidence=EvidenceReferences(
                    git_diff_path=git_diff_path,
                    test_log_path=test_log_path,
                    terminal_log_path=None,
                    screenshots=[],
                ),
                raw_output_ref=raw_output_ref,
                metadata=meta,
            )
        )
        return AutomatedWorkerOutcome(
            agent_result=canonical_result,
            receipt=final_receipt,
        )


__all__ = [
    "AntigravitySubscriptionWorkerAutomation",
    "AutomatedWorkerOutcome",
    "DEFAULT_FORBIDDEN_WORKER_PATHS",
    "SubscriptionWorkerConfig",
    "ValidationCommandCallable",
    "WorkerAutomationReceipt",
    "WorkerCommandReceipt",
    "WorkerInvocationResponse",
    "WorkerInvokerCallable",
]
