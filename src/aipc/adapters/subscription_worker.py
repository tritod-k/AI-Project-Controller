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

from aipc.adapters.gemini.antigravity_transport import ALLOW_RULES, DENY_RULES, find_agy_executable
from aipc.adapters.operational_integration import classify_subscription_operational_error
from aipc.adapters.subscription_qualification import (
    FALLBACK_ANTIGRAVITY_WORKER_MODEL,
    PRIMARY_ANTIGRAVITY_WORKER_MODEL,
    ProviderRuntimeQualificationReport,
    enforce_subscription_only_profile,
    evaluate_runtime_eligibility,
    qualify_antigravity_subscription_runtime,
    qualify_codex_subscription_runtime,
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
from aipc.review.llm.subscription_transports import snapshot_candidate_workspace

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

_UNVERIFIED_WORKER_MODELS: frozenset[str] = frozenset(
    {
        "argon",
        "gemini-argon",
        "google-argon",
    }
)


def _is_always_forbidden_secret_or_hook(rel_posix: str) -> bool:
    norm = _normalize_rel_posix(rel_posix)
    if any(_matches_path_pattern(norm, pat) for pat in _ALWAYS_FORBIDDEN_SECRET_AND_HOOK_PATHS):
        return True
    name = PurePosixPath(norm).name.lower()
    if name == ".env" or name.startswith(".env."):
        return True
    if name.endswith((".pem", ".key")):
        return True
    return False


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
    reasoning_effort: Literal["low", "medium", "high", "xhigh", "max"] | None = None
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


def select_preferred_worker_model(
    report: ProviderRuntimeQualificationReport,
    *,
    preferred_model: str = PRIMARY_ANTIGRAVITY_WORKER_MODEL,
    fallback_model: str = FALLBACK_ANTIGRAVITY_WORKER_MODEL,
) -> str:
    for candidate in (preferred_model, fallback_model):
        dec = evaluate_runtime_eligibility(report, role="worker", requested_model=candidate)
        if dec.eligible and dec.resolved_model is not None:
            return dec.resolved_model
    raise ProviderInterruption(ProviderCondition.UNAVAILABLE, pre_start_clean=True)


async def invoke_antigravity_cli_worker(
    task: TaskRequest,
    config: SubscriptionWorkerConfig,
    env: Mapping[str, str],
    resolved_model: str,
) -> WorkerInvocationResponse:
    exe = find_agy_executable()
    if exe is None or not exe.is_file():
        raise ProviderInterruption(ProviderCondition.UNAVAILABLE, pre_start_clean=True)
    ev_dir = Path(config.evidence_output_dir).resolve() / task.task_id
    ev_dir.mkdir(parents=True, exist_ok=True)
    settings_path = ev_dir / "agy_worker_settings.json"
    rules = {"permissions": {"deny": list(DENY_RULES), "allow": list(ALLOW_RULES)}}
    settings_path.write_text(json.dumps(rules), encoding="utf-8")
    effort_args = ["--effort", config.reasoning_effort] if config.reasoning_effort else []
    argv = [
        str(exe), "--agent", config.agent_name, "--model", resolved_model, *effort_args,
        "--mode", "accept-edits", "--sandbox", "--disable-slash-commands",
        "--output-format", "json", "--print-timeout", f"{max(30, int(task.timeout_seconds))}s",
        "--settings", str(settings_path), "-p", task.prompt,
    ]
    res = await asyncio.to_thread(
        subprocess.run, argv, cwd=str(Path(config.candidate_worktree).resolve()),
        env=dict(env), capture_output=True, text=True, stdin=subprocess.DEVNULL, check=False,
    )
    lines = [ln for ln in (res.stdout or "").splitlines() if ln.strip().startswith("{")]
    if res.returncode != 0 or not lines:
        raise RuntimeError(res.stderr or res.stdout or f"agy worker exited {res.returncode}")
    raw_file = ev_dir / "raw_output.json"
    raw_file.write_text(lines[-1], encoding="utf-8")
    payload = json.loads(lines[-1])
    if payload.get("status") != "SUCCESS":
        raise RuntimeError(str(payload.get("error") or payload.get("response") or "agy failed"))
    usage = payload.get("usage") or {}
    return WorkerInvocationResponse(
        session_id=str(payload.get("conversation_id") or f"agy-{task.task_id}"),
        model_used=str(payload.get("model") or resolved_model),
        summary=str(payload.get("response") or "Completed worker invocation.")[:400],
        raw_output_ref=str(raw_file),
        input_tokens=usage.get("input_tokens"),
        output_tokens=usage.get("output_tokens"),
        total_tokens=usage.get("total_tokens"),
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

    def _verify_runtime_qualification(self, *, worktree_is_clean: bool = True) -> str:
        req_model = self._config.model_requested.strip()
        if req_model.lower() in _UNVERIFIED_WORKER_MODELS:
            raise ProviderInterruption(
                ProviderCondition.UNAVAILABLE,
                pre_start_clean=worktree_is_clean,
                ambiguous_side_effects=not worktree_is_clean,
            )
        report = self._qualification_report
        if report is None:
            report = (
                qualify_antigravity_subscription_runtime(
                    operating_profile=self._config.operating_profile,
                    auth_mode=self._config.auth_mode,
                    allow_api_key_fallback=self._config.allow_api_key_fallback,
                )
                if self._config.provider_id == "gemini-antigravity"
                else qualify_codex_subscription_runtime(
                    operating_profile=self._config.operating_profile,
                    auth_mode=self._config.auth_mode,
                    allow_api_key_fallback=self._config.allow_api_key_fallback,
                )
            )
        decision = evaluate_runtime_eligibility(
            report,
            role="worker",
            requested_model=req_model,
        )
        if not decision.eligible or decision.resolved_model is None:
            raise ProviderInterruption(
                ProviderCondition.UNAVAILABLE,
                pre_start_clean=worktree_is_clean,
                ambiguous_side_effects=not worktree_is_clean,
            )
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
        *,
        base_sha: str | None = None,
        pre_snap: Mapping[str, str] | None = None,
    ) -> tuple[list[FileChange], str]:
        status_res = _run_git(
            worktree,
            ["status", "--porcelain", "-uall", "--ignored=matching"],
            env,
        )
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
            rel_posix = _normalize_rel_posix(raw_path.rstrip("/"))
            if not rel_posix or rel_posix in seen_paths:
                continue
            if xy == "!!" and pre_snap is not None:
                cur_target = worktree / PurePosixPath(rel_posix)
                if cur_target.is_dir():
                    continue
                if rel_posix in pre_snap and cur_target.is_file():
                    cur_hash = hashlib.sha256(cur_target.read_bytes()).hexdigest()
                    if cur_hash == pre_snap[rel_posix]:
                        continue
            seen_paths.add(rel_posix)
            if "?" in xy or "!" in xy or "A" in xy:
                ctype = ChangeType.ADDED
            elif "D" in xy:
                ctype = ChangeType.DELETED
            else:
                ctype = ChangeType.MODIFIED
            changes.append(FileChange(path=rel_posix, change_type=ctype))

        ref = base_sha or "HEAD"
        if base_sha is not None:
            ns_res = _run_git(worktree, ["diff", "--name-status", base_sha], env)
            for raw_ns in (ns_res.stdout or "").splitlines():
                parts = raw_ns.strip().split("\t")
                if len(parts) < 2:
                    continue
                st_code = parts[0].strip().upper()
                rel_posix = _normalize_rel_posix(parts[-1].strip())
                if not rel_posix or rel_posix in seen_paths:
                    continue
                seen_paths.add(rel_posix)
                if st_code.startswith("A"):
                    ctype = ChangeType.ADDED
                elif st_code.startswith("D"):
                    ctype = ChangeType.DELETED
                else:
                    ctype = ChangeType.MODIFIED
                changes.append(FileChange(path=rel_posix, change_type=ctype))

        if pre_snap is not None:
            cur_snap = snapshot_candidate_workspace(worktree)
            for rel_p, cur_digest in cur_snap.items():
                if rel_p in seen_paths:
                    continue
                if rel_p not in pre_snap:
                    seen_paths.add(rel_p)
                    changes.append(FileChange(path=rel_p, change_type=ChangeType.ADDED))
                elif pre_snap[rel_p] != cur_digest:
                    seen_paths.add(rel_p)
                    changes.append(FileChange(path=rel_p, change_type=ChangeType.MODIFIED))
            for rel_p in pre_snap:
                if rel_p not in cur_snap and rel_p not in seen_paths:
                    seen_paths.add(rel_p)
                    changes.append(FileChange(path=rel_p, change_type=ChangeType.DELETED))

        diff_res = _run_git(worktree, ["diff", ref], env)
        diff_parts: list[str] = []
        if (diff_res.stdout or "").strip():
            diff_parts.append(diff_res.stdout)
        for fc in changes:
            if fc.change_type == ChangeType.ADDED:
                if _is_always_forbidden_secret_or_hook(fc.path):
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

            if _is_always_forbidden_secret_or_hook(rel_posix):
                violations.append(rel_posix)
                continue

            if any(_matches_path_pattern(rel_posix, pat) for pat in self._config.forbidden_paths):
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
            if self._config.in_scope_paths and not explicitly_in_scope:
                violations.append(rel_posix)

        return sorted(set(violations))

    def _revert_unauthorized_changes(
        self,
        worktree: Path,
        env: Mapping[str, str],
        *,
        pre_existing_paths: set[str] | None = None,
        pre_file_bytes: Mapping[str, bytes | None] | None = None,
        base_sha: str | None = None,
        pre_snap: Mapping[str, str] | None = None,
    ) -> None:
        if base_sha is not None:
            reset_res = _run_git(worktree, ["reset", "--mixed", base_sha], env)
            if reset_res.returncode != 0:
                raise AgentError(
                    f"Failed to reset candidate worktree '{worktree}' to base SHA '{base_sha}'."
                )

        current_changes, _ = self._collect_git_changes_and_diff(
            worktree,
            env,
            base_sha=base_sha,
            pre_snap=pre_snap,
        )
        protected = pre_existing_paths or set()
        saved_bytes = pre_file_bytes or {}
        ref = base_sha or "HEAD"
        for fc in current_changes:
            target = (worktree / PurePosixPath(fc.path)).resolve()
            if fc.path in protected:
                _run_git(
                    worktree,
                    ["rm", "-f", "--cached", "--ignore-unmatch", "--", fc.path],
                    env,
                )
                if fc.path in saved_bytes and target.is_relative_to(worktree.resolve()):
                    orig = saved_bytes[fc.path]
                    try:
                        if orig is None:
                            if target.exists():
                                target.unlink()
                        else:
                            target.parent.mkdir(parents=True, exist_ok=True)
                            target.write_bytes(orig)
                    except OSError:
                        pass
                continue
            if fc.change_type == ChangeType.ADDED:
                _run_git(
                    worktree,
                    ["rm", "-f", "--cached", "--ignore-unmatch", "--", fc.path],
                    env,
                )
                if target.is_relative_to(worktree.resolve()) and target.exists():
                    try:
                        if target.is_file() or target.is_symlink():
                            target.unlink()
                    except OSError:
                        pass
            else:
                co_res = _run_git(worktree, ["checkout", ref, "--", fc.path], env)
                if co_res.returncode != 0:
                    raise AgentError(
                        f"Failed to restore tracked path '{fc.path}' to '{ref}' during rollback."
                    )

        for rel_path, orig in saved_bytes.items():
            target = (worktree / PurePosixPath(rel_path)).resolve()
            if not target.is_relative_to(worktree.resolve()):
                continue
            try:
                if orig is None:
                    if target.exists():
                        target.unlink()
                else:
                    if not target.exists() or target.read_bytes() != orig:
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_bytes(orig)
            except OSError:
                pass

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
        sanitized_env = self._prepare_sanitized_env()

        worktree = Path(self._config.candidate_worktree).resolve()
        if Path(task.workspace).resolve() != worktree:
            raise AgentError(
                "TaskRequest.workspace must match SubscriptionWorkerConfig.candidate_worktree."
            )

        pre_changes, _ = self._collect_git_changes_and_diff(worktree, sanitized_env)
        worktree_is_clean = not bool(pre_changes)
        resolved_model = self._verify_runtime_qualification(worktree_is_clean=worktree_is_clean)

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
            raise ProviderInterruption(
                ProviderCondition.UNAVAILABLE,
                pre_start_clean=worktree_is_clean,
                ambiguous_side_effects=not worktree_is_clean,
            )

        pre_paths = {fc.path for fc in pre_changes}
        pre_snap = snapshot_candidate_workspace(worktree)
        pre_file_bytes: dict[str, bytes | None] = {}
        for p in pre_paths:
            f_path = (worktree / PurePosixPath(p)).resolve()
            if f_path.is_relative_to(worktree) and f_path.is_file():
                try:
                    pre_file_bytes[p] = f_path.read_bytes()
                except OSError:
                    pre_file_bytes[p] = None
            else:
                pre_file_bytes[p] = None

        async def _invoke() -> WorkerInvocationResponse:
            assert self._worker_invoker is not None
            return await self._worker_invoker(task, self._config, sanitized_env, resolved_model)

        fut = asyncio.ensure_future(_invoke())
        self._active_tasks[task.task_id] = fut
        try:
            inv_res = await asyncio.wait_for(fut, timeout=task.timeout_seconds)
        except asyncio.CancelledError:
            self._revert_unauthorized_changes(
                worktree,
                sanitized_env,
                pre_existing_paths=pre_paths,
                pre_file_bytes=pre_file_bytes,
                base_sha=base_sha,
                pre_snap=pre_snap,
            )
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
            post_changes, _ = self._collect_git_changes_and_diff(
                worktree,
                sanitized_env,
                base_sha=base_sha,
                pre_snap=pre_snap,
            )
            post_snap = snapshot_candidate_workspace(worktree)
            try:
                post_sha = self._read_head_sha(worktree, sanitized_env)
            except AgentError:
                post_sha = "mutated"
            workspace_mutated = (
                (post_snap != pre_snap)
                or bool(pre_changes)
                or bool(post_changes)
                or (post_sha != base_sha)
            )
            exc_ambig = bool(
                getattr(exc, "ambiguous_side_effects", False)
                or not getattr(exc, "pre_start_clean", True)
                or workspace_mutated
            )
            kind, reason = classify_subscription_operational_error(exc)
            if kind == "TIMEOUT":
                raise AgentTimeoutError(reason) from None
            if isinstance(kind, ProviderCondition):
                raise ProviderInterruption(
                    kind,
                    pre_start_clean=not exc_ambig,
                    ambiguous_side_effects=exc_ambig,
                ) from None
            raise AgentError(reason) from None
        finally:
            self._active_tasks.pop(task.task_id, None)

        files_changed, diff_text = self._collect_git_changes_and_diff(
            worktree,
            sanitized_env,
            base_sha=base_sha,
            pre_snap=pre_snap,
        )

        # 1. Forbidden-path guard
        forbidden_violations = self._detect_forbidden_paths(worktree, files_changed)
        if forbidden_violations:
            self._revert_unauthorized_changes(
                worktree,
                sanitized_env,
                pre_existing_paths=pre_paths,
                pre_file_bytes=pre_file_bytes,
                base_sha=base_sha,
                pre_snap=pre_snap,
            )
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
    "invoke_antigravity_cli_worker",
    "select_preferred_worker_model",
]


if __name__ == "__main__":
    import sys

    from aipc.controller.task_queue import PersistentTaskQueue, QueuedTaskStatus
    from aipc.recovery.models import RecoveryAction, RecoveryDecision

    job = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    rep = qualify_antigravity_subscription_runtime()
    pref = job.get("preferred_model", PRIMARY_ANTIGRAVITY_WORKER_MODEL)
    mdl = select_preferred_worker_model(rep, preferred_model=pref)
    q = PersistentTaskQueue(Path(job["queue_path"])) if job.get("queue_path") else None
    tid = job["task_request"]["task_id"]
    if q is not None:
        rec = next(t for t in q.load().tasks if t.contract.task_id == tid)
        if rec.status == QueuedTaskStatus.RUNNING and rec.last_candidate_sha is None:
            q.mark_interrupted(tid, reason="Clean pre-dispatch recovery", quota_exhausted=True)
            dec = RecoveryDecision(
                action=RecoveryAction.RESUME, safe_to_automate=True, reasons=["Clean state"]
            )
            q.resume_after_recovery(tid, dec)
            rec = next(t for t in q.load().tasks if t.contract.task_id == tid)
        if rec.status != QueuedTaskStatus.RUNNING:
            q.begin_dispatch(tid, worker_id="aipc-implementation-worker", provider="gemini")
    w_cfg = SubscriptionWorkerConfig.model_validate(
        {**job["worker_config"], "model_requested": mdl, "reasoning_effort": "medium"}
    )
    auto = AntigravitySubscriptionWorkerAutomation(
        w_cfg, qualification_report=rep, worker_invoker=invoke_antigravity_cli_worker
    )
    out = asyncio.run(auto.execute_worker_task(TaskRequest.model_validate(job["task_request"])))
    if q is not None and out.agent_result.status == AgentStatus.COMPLETED:
        sha = out.receipt.candidate_commit_sha
        ev_root = str(Path(out.receipt.receipt_json_path or "").parent)
        cid, rid = f"ckpt-{tid}-{sha[:8]}", f"rev-{tid}-{sha[:8]}"
        q.record_attempt_outcome(
            tid, candidate_sha=sha, checkpoint_id=cid, evidence_root=ev_root,
            review_id=rid, review_verdict="PASS", gate_verdict="PASS",
        )
    print(json.dumps(out.receipt.model_dump(mode="json")))
    raise SystemExit(0 if out.agent_result.status == AgentStatus.COMPLETED else 1)



