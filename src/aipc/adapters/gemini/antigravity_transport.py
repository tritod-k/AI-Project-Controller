"""Official Google Antigravity CLI (agy) headless transport with workspace isolation."""

import asyncio
import hashlib
import json
import os
import shlex
import shutil
import stat
import subprocess
import tempfile
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path
from typing import Literal
from uuid import uuid4

from aipc.agents.models import AgentCapabilities, RawAgentResult, RunStatus
from aipc.domain.errors import EvidencePathError
from aipc.domain.models import AgentResult, CanonicalRecord, TestSummary
from aipc.evidence.paths import authorized_root
from aipc.evidence.test_parser import PytestResultParser
from aipc.hardening.env_isolation import (
    ChildEnvironmentIsolationError,
    assert_argv_no_credentials,
    build_sanitized_child_env,
)
from aipc.hardening.errors import SecretExposureError
from aipc.hardening.secrets import assert_no_secrets
from aipc.normalization.context import NormalizationContext
from aipc.normalization.validation import validate_agent_result
from aipc.storage.json_store import _unique_object

from .config import GeminiAdapterConfig
from .doctor import (
    CONTAINER_VERIFIER_UNAVAILABLE,
    DEFAULT_CISL_VERIFIER_IMAGE,
    _resolve_docker_executable,
    antigravity_runtime_info,
    check_docker,
    find_agy_executable,
    qualify_antigravity_runtime,
)
from .errors import (
    GeminiAdapterError,
    GeminiProtocolError,
    GeminiSessionLostError,
    GeminiUnavailableError,
    provider_failure,
)
from .events import GeminiRawResult, ToolObservation, redact
from .normalizer import portable_file, terminal_status

ALLOWED_AGY_TOOLS: frozenset[str] = frozenset(
    {
        "read_file",
        "write_file",
        "edit_file",
        "view_file_outline",
        "view_code_item",
        "grep_search",
        "find_by_name",
        "list_dir",
    }
)

DENY_RULES: tuple[str, ...] = (
    "unsandboxed(*)",
    "command(*)",
    "run_command(*)",
    "bash(*)",
    "sh(*)",
    "powershell(*)",
    "pwsh(*)",
    "cmd(*)",
    "terminal(*)",
    "exec(*)",
    "fetch(*)",
    "http(*)",
    "web_search(*)",
    "search_web(*)",
    "browser(*)",
    "mcp(*)",
)

ALLOW_RULES: tuple[str, ...] = (
    "read_file(*)",
    "write_file(*)",
    "edit_file(*)",
    "grep_search(*)",
    "find_by_name(*)",
    "list_dir(*)",
)

CONFIG_GUARD_PATTERNS: tuple[str, ...] = (
    ".git/config",
    ".git/hooks/*",
    "pyproject.toml",
    "pytest.ini",
    "setup.cfg",
    "setup.py",
    "tox.ini",
    "conftest.py",
    "*.pth",
)


def _check_no_env_files(workspace: Path) -> None:
    """Fail closed if any .env file exists inside the attempt workspace."""
    for item in workspace.rglob("*"):
        if ".git" in item.parts:
            continue
        name = item.name.casefold()
        if name == ".env" or name.startswith(".env."):
            raise GeminiAdapterError("sensitive .env file present in attempt workspace")


def _git_fingerprint(workspace: Path) -> str:
    """Compute a deterministic digest of .git metadata in the workspace."""
    git_path = workspace / ".git"
    hasher = hashlib.sha256()
    if not git_path.exists():
        return "no-git"
    if git_path.is_file():
        hasher.update(b"file:")
        hasher.update(git_path.read_bytes())
        return hasher.hexdigest()
    if git_path.is_dir():
        for child in sorted(git_path.rglob("*")):
            rel = child.relative_to(git_path).as_posix()
            hasher.update(rel.encode("utf-8"))
            if child.is_file():
                hasher.update(child.read_bytes())
        return hasher.hexdigest()
    return "other"


def snapshot_build_and_hook_configs(workspace: Path) -> dict[str, str]:
    """Compute SHA-256 digests of all protected build/hook config files in the workspace."""
    digests: dict[str, str] = {}
    ws_real = workspace.resolve()
    for pattern in CONFIG_GUARD_PATTERNS:
        if "/" in pattern:
            parts = pattern.split("/")
            if "*" in parts[-1]:
                parent = workspace.joinpath(*parts[:-1])
                if parent.is_dir():
                    for child in sorted(parent.glob(parts[-1])):
                        if child.is_file():
                            try:
                                rel = child.relative_to(ws_real).as_posix()
                                digests[rel] = hashlib.sha256(child.read_bytes()).hexdigest()
                            except OSError:
                                pass
            else:
                target = workspace.joinpath(*parts)
                if target.is_file():
                    try:
                        rel = target.relative_to(ws_real).as_posix()
                        digests[rel] = hashlib.sha256(target.read_bytes()).hexdigest()
                    except OSError:
                        pass
        else:
            for child in sorted(workspace.rglob(pattern)):
                if ".git" in child.parts:
                    continue
                if child.is_file():
                    try:
                        rel = child.relative_to(ws_real).as_posix()
                        digests[rel] = hashlib.sha256(child.read_bytes()).hexdigest()
                    except OSError:
                        pass
    return digests


def check_build_config_mutations(workspace: Path, pre_snapshots: dict[str, str]) -> list[str]:
    """Return any build/hook config modifications, deletions, or creations since pre-turn."""
    post_snapshots = snapshot_build_and_hook_configs(workspace)
    violations: list[str] = []
    for path, digest_val in pre_snapshots.items():
        if path not in post_snapshots:
            violations.append(f"deleted_build_config:{path}")
        elif post_snapshots[path] != digest_val:
            violations.append(f"modified_build_config:{path}")
    for path in post_snapshots:
        if path not in pre_snapshots:
            violations.append(f"created_build_config:{path}")
    return violations


def scan_workspace_integrity(workspace: Path) -> list[str]:
    """Scan the workspace for symlinks, junctions, reparse points, or .env escapes."""
    violations: list[str] = []
    ws_real = workspace.resolve()
    for root, dirs, files in os.walk(workspace, followlinks=False):
        for name in dirs + files:
            p = Path(root) / name
            try:
                st = os.lstat(p)
            except OSError:
                continue
            is_reparse = bool(
                getattr(st, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
            )
            is_link = os.path.islink(p) or is_reparse
            if is_link:
                try:
                    resolved = p.resolve()
                    if not resolved.is_relative_to(ws_real):
                        try:
                            rel = p.relative_to(ws_real).as_posix()
                        except ValueError:
                            rel = p.name
                        violations.append(f"alias_escape:{rel}->{resolved}")
                except Exception:
                    violations.append(f"unresolvable_link:{p.name}")
            name_lower = p.name.casefold()
            if name_lower == ".env" or name_lower.startswith(".env."):
                violations.append(f"sensitive_env_file:{p.name}")
    return violations


def _hash_workspace_files(workspace: Path) -> dict[str, str]:
    """Compute SHA-256 digest of every file in the workspace excluding .git."""
    file_hashes: dict[str, str] = {}
    if not workspace.exists():
        return file_hashes
    for item in workspace.rglob("*"):
        if ".git" in item.parts:
            continue
        if item.is_file():
            try:
                rel = item.relative_to(workspace).as_posix()
                file_hashes[rel] = hashlib.sha256(item.read_bytes()).hexdigest()
            except OSError:
                pass
    return file_hashes


def _run_safe_git(
    args: list[str],
    cwd: Path,
    empty_hooks_dir: Path,
) -> subprocess.CompletedProcess[str]:
    """Run Git subprocess neutralizing hooks, fsmonitor, and external tools."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_CONFIG_SYSTEM=os.devnull,
        GIT_TERMINAL_PROMPT="0",
    )
    has_name = False
    has_email = False
    if (cwd / ".git").exists():
        check_name = subprocess.run(
            ["git", "-C", str(cwd), "config", "user.name"],
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )
        has_name = check_name.returncode == 0 and bool(check_name.stdout.strip())
        check_email = subprocess.run(
            ["git", "-C", str(cwd), "config", "user.email"],
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )
        has_email = check_email.returncode == 0 and bool(check_email.stdout.strip())

    identity_args: list[str] = []
    if not has_name:
        identity_args.extend(["-c", "user.name=AIPC Host Finalizer"])
    if not has_email:
        identity_args.extend(["-c", "user.email=aipc-host@local"])

    full_cmd = [
        "git",
        "-c",
        f"core.hooksPath={empty_hooks_dir}",
        "-c",
        "core.fsmonitor=false",
        "-c",
        "diff.external=",
        *identity_args,
        "-C",
        str(cwd),
        *args,
    ]
    return subprocess.run(
        full_cmd,
        capture_output=True,
        text=True,
        check=False,
        env=env,
        timeout=30,
    )


def _get_dirty_paths(workspace: Path, empty_hooks_dir: Path) -> set[str]:
    """List uncommitted modified or untracked paths via safe git status."""
    if not (workspace / ".git").exists():
        return set()
    res = _run_safe_git(["status", "--porcelain"], workspace, empty_hooks_dir)
    dirty = set()
    for line in (res.stdout or "").splitlines():
        if len(line) >= 4:
            path_str = line[3:].strip().strip('"')
            dirty.add(path_str)
    return dirty


def antigravity_runtime_plan(
    config: GeminiAdapterConfig,
    workspace: Path | str,
    *,
    executable: Path | str | None = None,
    settings_path: Path | str | None = None,
) -> dict[str, object]:
    """Return the deterministic subprocess execution plan for Antigravity CLI."""
    resolved_exe = Path(executable) if executable is not None else find_agy_executable()
    exe = str(resolved_exe or "agy")
    qual = qualify_antigravity_runtime(config, executable=resolved_exe)
    mode = getattr(config, "approval_mode", "accept-edits")
    settings_obj = {
        "permissions": {
            "deny": list(DENY_RULES),
            "allow": list(ALLOW_RULES),
        }
    }
    settings_arg = str(settings_path) if settings_path is not None else json.dumps(settings_obj)
    argv = [
        exe,
        "-p",
        "<PROMPT>",
        "--output-format",
        "stream-json",
        "--mode",
        str(mode),
        "--sandbox",
        "--disable-slash-commands",
        "--print-timeout",
        f"{int(config.operation_timeout * 4)}s",
        "--settings",
        settings_arg,
    ]
    if config.model:
        argv.extend(["--model", config.model])
    return {
        "argv": argv,
        "cwd": str(workspace),
        "qualified": qual.qualified,
        "qualification_reason": qual.reason,
        "runtime_version": qual.version,
        "settings": settings_obj,
        "model": config.model,
        "capabilities": {
            "filesystem": qual.qualified,
            "shell": False,
            "git": False,
            "network": False,
            "streaming_events": True,
            "resume_session": False,
        },
    }


def _classify_error_text(text: str) -> None:
    lowered = text.casefold()
    if any(
        token in lowered
        for token in (
            "unauthenticated",
            "auth_required",
            "not logged in",
            "login required",
            "401",
            "403",
        )
    ):
        provider_failure("auth_required")
    if any(
        token in lowered
        for token in ("quota_exhausted", "resource_exhausted", "usage limit", "quota exceeded")
    ):
        provider_failure("quota_exhausted")
    if any(token in lowered for token in ("rate_limited", "too many requests", "429")):
        provider_failure(429)
    if any(token in lowered for token in ("max_turn_requests", "max_tokens")):
        provider_failure("max_turn_requests")


class ContainerSnapshotVerificationReceipt(CanonicalRecord):
    """Canonical receipt for CISL01 immutable snapshot and tmpfs Docker verification."""

    passed: bool
    error_code: str | None = None
    pre_snapshot_hashes: dict[str, str]
    post_snapshot_hashes: dict[str, str]
    snapshot_immutable: bool
    host_git_diff_check_passed: bool
    host_git_diff_output: str = ""
    container_image: str
    container_runtime: str = "docker"
    staging_tmpfs_path: str = "/tmp/work"
    pytest_basetemp: str = "/tmp/pytest"
    git_mounted_in_container: bool = False
    commands_executed: tuple[str, ...] = ()
    exit_codes: tuple[int, ...] = ()
    tests_summary: TestSummary | None = None
    log_text: str = ""
    copy_overhead_ms: float = 0.0


def normalize_cisl_container_command(command: tuple[str, ...] | str) -> list[str]:
    """Normalize a Windows/host verification command for Linux tmpfs container execution."""
    if isinstance(command, str):
        raw_tokens = [
            tok.strip("\"'") for tok in shlex.split(command, posix=False) if tok.strip("\"'")
        ]
    else:
        raw_tokens = [str(tok) for tok in command]

    if not raw_tokens:
        raise ValueError("CISL container command must not be empty")

    tokens = list(raw_tokens)
    exe_posix = tokens[0].replace("\\", "/")
    exe_name = exe_posix.rsplit("/", 1)[-1].lower()

    if (
        exe_name in ("python.exe", "python3.exe", "python", "python3")
        or exe_posix.lower().endswith("/scripts/python.exe")
        or exe_posix.lower().endswith("/bin/python")
        or exe_posix.lower().endswith("/bin/python3")
    ):
        tokens[0] = "python3"
    elif (
        exe_name in ("ruff.exe", "ruff")
        or exe_posix.lower().endswith("/scripts/ruff.exe")
        or exe_posix.lower().endswith("/bin/ruff")
    ):
        tokens[:1] = ["python3", "-m", "ruff"]
    elif exe_name in ("pytest.exe", "pytest"):
        tokens[:1] = ["python3", "-m", "pytest"]

    for idx in range(1, len(tokens)):
        if "\\" in tokens[idx]:
            tokens[idx] = tokens[idx].replace("\\", "/")

    is_pytest = "pytest" in tokens or any(
        t.replace("\\", "/").rsplit("/", 1)[-1].lower() in ("pytest", "pytest.exe") for t in tokens
    )
    if is_pytest:
        if tokens[0] == "python3":
            py_flag_limit = tokens.index("-m") if "-m" in tokens else len(tokens)
            if "-B" not in tokens[:py_flag_limit]:
                tokens.insert(1, "-B")
        if "no:cacheprovider" not in tokens:
            tokens.extend(["-p", "no:cacheprovider"])
        if not any(t.startswith("--basetemp") for t in tokens):
            tokens.append("--basetemp=/tmp/pytest")

    return tokens


def _norm_rel_path(path_str: str) -> str:
    norm = path_str.replace("\\", "/").strip()
    while norm.startswith("./"):
        norm = norm[2:]
    return norm.rstrip("/")


def _path_matches_pattern(rel_path: str, pattern: str) -> bool:
    norm_path = _norm_rel_path(rel_path)
    norm_pat = _norm_rel_path(pattern)
    if not norm_pat:
        return False
    return norm_path == norm_pat or norm_path.startswith(norm_pat + "/")


def _cisl_fail_receipt(
    *,
    error_code: str,
    container_image: str,
    container_runtime: str,
    log_text: str,
    pre_snapshot_hashes: dict[str, str] | None = None,
    post_snapshot_hashes: dict[str, str] | None = None,
    snapshot_immutable: bool = True,
    host_git_diff_check_passed: bool = True,
    host_git_diff_output: str = "",
    commands_executed: tuple[str, ...] = (),
    exit_codes: tuple[int, ...] = (),
    tests_summary: TestSummary | None = None,
    copy_overhead_ms: float = 0.0,
) -> ContainerSnapshotVerificationReceipt:
    pre = pre_snapshot_hashes if pre_snapshot_hashes is not None else {}
    post = post_snapshot_hashes if post_snapshot_hashes is not None else dict(pre)
    return ContainerSnapshotVerificationReceipt(
        passed=False,
        error_code=error_code,
        pre_snapshot_hashes=pre,
        post_snapshot_hashes=post,
        snapshot_immutable=snapshot_immutable,
        host_git_diff_check_passed=host_git_diff_check_passed,
        host_git_diff_output=host_git_diff_output,
        container_image=container_image,
        container_runtime=container_runtime,
        staging_tmpfs_path="/tmp/work",
        pytest_basetemp="/tmp/pytest",
        git_mounted_in_container=False,
        commands_executed=commands_executed,
        exit_codes=exit_codes,
        tests_summary=tests_summary,
        log_text=log_text,
        copy_overhead_ms=copy_overhead_ms,
    )


def run_cisl_container_snapshot_verifier(
    workspace: Path,
    commands: Sequence[tuple[str, ...] | str],
    *,
    turn_id: str | None = None,
    state_root: Path | None = None,
    container_image: str = DEFAULT_CISL_VERIFIER_IMAGE,
    container_runtime: str = "docker",
    timeout_seconds: int = 120,
    in_scope_paths: Sequence[str] | None = None,
    out_of_scope_paths: Sequence[str] | None = None,
    forbidden_paths: Sequence[str] | None = None,
    changed_paths: Sequence[str] | None = None,
    pre_config_digests: dict[str, str] | None = None,
    allow_config_mutation: bool = False,
    docker_checker: Callable[..., dict[str, object]] | None = None,
    subprocess_runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> ContainerSnapshotVerificationReceipt:
    """Execute the 5-step CISL01 immutable snapshot & tmpfs Linux Docker verification flow."""
    ws_real = workspace.resolve()
    # Step 1 & 2: Host Controller Pre-Checks
    try:
        _check_no_env_files(ws_real)
    except GeminiAdapterError as exc:
        return _cisl_fail_receipt(
            error_code="ENV_FILE_VIOLATION",
            container_image=container_image,
            container_runtime=container_runtime,
            log_text=str(exc),
        )

    integrity_violations = scan_workspace_integrity(ws_real)
    if integrity_violations:
        return _cisl_fail_receipt(
            error_code="WORKSPACE_INTEGRITY_VIOLATION",
            container_image=container_image,
            container_runtime=container_runtime,
            log_text=f"Workspace integrity violation: {integrity_violations}",
        )

    if pre_config_digests is not None and not allow_config_mutation:
        mutations = check_build_config_mutations(ws_real, pre_config_digests)
        if mutations:
            return _cisl_fail_receipt(
                error_code="CONFIG_GUARD_VIOLATION",
                container_image=container_image,
                container_runtime=container_runtime,
                log_text=f"Build/hook config mutation detected: {mutations}",
            )

    if changed_paths is not None:
        norm_changed = [_norm_rel_path(p) for p in changed_paths if _norm_rel_path(p)]
        if forbidden_paths is not None:
            bad_forbidden = [
                cp
                for cp in norm_changed
                if any(_path_matches_pattern(cp, fp) for fp in forbidden_paths)
            ]
            if bad_forbidden:
                return _cisl_fail_receipt(
                    error_code="FORBIDDEN_PATH_VIOLATION",
                    container_image=container_image,
                    container_runtime=container_runtime,
                    log_text=f"Forbidden path modified: {bad_forbidden}",
                )
        if out_of_scope_paths is not None:
            bad_oos = [
                cp
                for cp in norm_changed
                if any(_path_matches_pattern(cp, op) for op in out_of_scope_paths)
            ]
            if bad_oos:
                return _cisl_fail_receipt(
                    error_code="OUT_OF_SCOPE_VIOLATION",
                    container_image=container_image,
                    container_runtime=container_runtime,
                    log_text=f"Out-of-scope path modified: {bad_oos}",
                )
        if in_scope_paths is not None:
            outside = [
                cp
                for cp in norm_changed
                if not any(_path_matches_pattern(cp, ip) for ip in in_scope_paths)
            ]
            if outside:
                return _cisl_fail_receipt(
                    error_code="OUT_OF_SCOPE_VIOLATION",
                    container_image=container_image,
                    container_runtime=container_runtime,
                    log_text=f"Changed path outside in_scope_paths: {outside}",
                )

    files_to_scan: list[Path] = []
    if changed_paths is not None:
        files_to_scan = [
            ws_real / _norm_rel_path(p)
            for p in changed_paths
            if (ws_real / _norm_rel_path(p)).is_file()
        ]
    elif in_scope_paths is not None:
        files_to_scan = [
            ws_real / _norm_rel_path(p)
            for p in in_scope_paths
            if (ws_real / _norm_rel_path(p)).is_file()
        ]
    else:
        skip_dirs = {".git", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache"}
        for item in ws_real.rglob("*"):
            if any(part in skip_dirs for part in item.parts):
                continue
            if item.is_file():
                files_to_scan.append(item)

    for scan_file in files_to_scan:
        try:
            content = scan_file.read_text(encoding="utf-8", errors="replace")
            assert_no_secrets(content)
        except SecretExposureError as exc:
            return _cisl_fail_receipt(
                error_code="SECRET_EXPOSURE_DETECTED",
                container_image=container_image,
                container_runtime=container_runtime,
                log_text=str(exc),
            )
        except OSError:
            pass

    host_git_diff_check_passed = True
    host_git_diff_output = ""
    git_marker = ws_real / ".git"
    if git_marker.is_file() or (git_marker.is_dir() and (git_marker / "HEAD").is_file()):
        with tempfile.TemporaryDirectory() as hooks_tmp:
            hooks_dir = (state_root / "empty_hooks") if state_root else Path(hooks_tmp)
            hooks_dir.mkdir(parents=True, exist_ok=True)
            diff_res = _run_safe_git(["diff", "--check"], ws_real, hooks_dir)
        host_git_diff_output = (diff_res.stdout or "") + (
            "\n" + diff_res.stderr if diff_res.stderr else ""
        )
        if diff_res.returncode != 0:
            return _cisl_fail_receipt(
                error_code="GIT_DIFF_CHECK_FAILED",
                container_image=container_image,
                container_runtime=container_runtime,
                host_git_diff_check_passed=False,
                host_git_diff_output=host_git_diff_output,
                log_text=host_git_diff_output.strip() or "git diff --check failed",
            )

    # Step 3: Pre-Verification SHA-256 Snapshot
    pre_snapshot_hashes = _hash_workspace_files(ws_real)

    # Step 4: Fail-Closed Docker Check & tmpfs /tmp/work Container Execution
    checker = docker_checker or check_docker
    docker_info = checker(runtime=container_runtime, image=container_image)
    if not docker_info.get("qualified"):
        return _cisl_fail_receipt(
            error_code=CONTAINER_VERIFIER_UNAVAILABLE,
            container_image=container_image,
            container_runtime=container_runtime,
            pre_snapshot_hashes=pre_snapshot_hashes,
            post_snapshot_hashes=dict(pre_snapshot_hashes),
            host_git_diff_check_passed=host_git_diff_check_passed,
            host_git_diff_output=host_git_diff_output,
            log_text=(
                f"{CONTAINER_VERIFIER_UNAVAILABLE}: "
                f"Docker daemon or verifier image '{container_image}' unavailable"
            ),
        )

    runtime_bin = str(
        docker_info.get("executable")
        or _resolve_docker_executable(container_runtime)
        or container_runtime
    )
    normalized_commands = [normalize_cisl_container_command(cmd) for cmd in commands]
    commands_executed = tuple(" ".join(cmd) for cmd in normalized_commands)
    safe_turn_id = "".join(
        c if c.isalnum() or c in "-_." else "-" for c in str(turn_id or uuid4().hex[:12])
    )
    container_name = f"aipc-cisl-verify-{safe_turn_id}"

    bootstrap_script = (
        "import json, os, pathlib, shutil, subprocess, sys, time; "
        "t0 = time.perf_counter(); "
        "shutil.copytree('/workspace', '/tmp/work', symlinks=False, dirs_exist_ok=True, "
        "ignore=shutil.ignore_patterns('.git', '.venv', '__pycache__', "
        "'.pytest_cache', '.ruff_cache')); "
        "[os.chmod(p, 0o644) for p in pathlib.Path('/tmp/work').rglob('*') if p.is_file()]; "
        "copy_ms = round((time.perf_counter() - t0) * 1000.0, 3); "
        "print(f'__AIPC_COPY_OVERHEAD_MS__={copy_ms}', flush=True); "
        f"cmds = json.loads({json.dumps(json.dumps(normalized_commands))}); "
        "codes = ["
        "int(subprocess.run(cmd, cwd='/tmp/work', check=False).returncode) "
        "for cmd in cmds]; "
        "print(f'__AIPC_EXIT_CODES__={json.dumps(codes)}', flush=True); "
        "sys.exit(next((c for c in codes if c != 0), 0))"
    )

    runner = subprocess_runner or subprocess.run
    with tempfile.TemporaryDirectory() as mask_tmp:
        git_mask_args: list[str] = []
        git_path = ws_real / ".git"
        if git_path.is_dir():
            empty_git_dir = Path(mask_tmp) / "empty_git"
            empty_git_dir.mkdir(parents=True, exist_ok=True)
            git_mask_args = ["-v", f"{empty_git_dir.resolve()}:/workspace/.git:ro"]
        elif git_path.is_file():
            empty_git_file = Path(mask_tmp) / "empty_git_pointer"
            empty_git_file.write_text("", encoding="utf-8")
            git_mask_args = ["-v", f"{empty_git_file.resolve()}:/workspace/.git:ro"]

        container_cmd = [
            runtime_bin,
            "run",
            "--rm",
            "--name",
            container_name,
            "--network",
            "none",
            "--read-only",
            "--tmpfs",
            "/tmp:rw,exec,nosuid,size=128m",
            "--user",
            "65534:65534",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--pids-limit",
            "64",
            "--memory",
            "512m",
            "-e",
            "HOME=/tmp",
            "-e",
            "TMPDIR=/tmp",
            "-e",
            "TEMP=/tmp",
            "-e",
            "TMP=/tmp",
            "-e",
            "PYTHONDONTWRITEBYTECODE=1",
            "-e",
            "VIRTUAL_ENV=/opt/aipc-venv",
            "-e",
            "PATH=/opt/aipc-venv/bin:/usr/local/bin:/usr/bin:/bin",
            "-e",
            "PYTHONPATH=/tmp/work/src",
            "-e",
            "RUFF_CACHE_DIR=/tmp/.ruff_cache",
            "-v",
            f"{ws_real}:/workspace:ro",
            *git_mask_args,
            "-w",
            "/tmp",
            container_image,
            "python3",
            "-B",
            "-c",
            bootstrap_script,
        ]

        try:
            assert_argv_no_credentials(container_cmd)
            sanitized_env = build_sanitized_child_env()
        except ChildEnvironmentIsolationError as exc:
            return _cisl_fail_receipt(
                error_code="CHILD_ENVIRONMENT_ISOLATION_BLOCKED",
                container_image=container_image,
                container_runtime=container_runtime,
                pre_snapshot_hashes=pre_snapshot_hashes,
                post_snapshot_hashes=dict(pre_snapshot_hashes),
                host_git_diff_check_passed=host_git_diff_check_passed,
                host_git_diff_output=host_git_diff_output,
                commands_executed=commands_executed,
                log_text=f"BLOCKED: {exc}",
            )

        try:
            res = runner(
                container_cmd,
                capture_output=True,
                text=True,
                check=False,
                timeout=timeout_seconds,
                env=sanitized_env,
            )
        except subprocess.TimeoutExpired:
            try:
                runner(
                    [runtime_bin, "rm", "-f", container_name],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=10,
                    env=sanitized_env,
                )
            except Exception:
                pass
            post_hashes = _hash_workspace_files(ws_real)
            return _cisl_fail_receipt(
                error_code="CONTAINER_TIMEOUT",
                container_image=container_image,
                container_runtime=container_runtime,
                pre_snapshot_hashes=pre_snapshot_hashes,
                post_snapshot_hashes=post_hashes,
                snapshot_immutable=post_hashes == pre_snapshot_hashes,
                host_git_diff_check_passed=host_git_diff_check_passed,
                host_git_diff_output=host_git_diff_output,
                commands_executed=commands_executed,
                log_text="TIMEOUT: Container verification timed out",
            )
        except OSError as exc:
            post_hashes = _hash_workspace_files(ws_real)
            return _cisl_fail_receipt(
                error_code=CONTAINER_VERIFIER_UNAVAILABLE,
                container_image=container_image,
                container_runtime=container_runtime,
                pre_snapshot_hashes=pre_snapshot_hashes,
                post_snapshot_hashes=post_hashes,
                snapshot_immutable=post_hashes == pre_snapshot_hashes,
                host_git_diff_check_passed=host_git_diff_check_passed,
                host_git_diff_output=host_git_diff_output,
                commands_executed=commands_executed,
                log_text=f"ERROR: Container execution failed: {exc}",
            )

    copy_overhead_ms = 0.0
    parsed_exit_codes: tuple[int, ...] | None = None
    clean_lines: list[str] = []
    for line in (res.stdout or "").splitlines():
        if line.startswith("__AIPC_COPY_OVERHEAD_MS__="):
            try:
                copy_overhead_ms = float(line.split("=", 1)[1].strip())
            except ValueError:
                pass
        elif line.startswith("__AIPC_EXIT_CODES__="):
            try:
                raw_codes = json.loads(line.split("=", 1)[1].strip())
                if isinstance(raw_codes, list):
                    parsed_exit_codes = tuple(int(c) for c in raw_codes)
            except (ValueError, TypeError):
                pass
        else:
            clean_lines.append(line)

    stdout_clean = "\n".join(clean_lines)
    stderr_clean = res.stderr or ""
    log_text = stdout_clean + ("\n" + stderr_clean if stderr_clean else "")

    if parsed_exit_codes is not None:
        exit_codes = parsed_exit_codes
    elif res.returncode == 0:
        exit_codes = tuple(0 for _ in normalized_commands)
    else:
        exit_codes = (int(res.returncode),)

    has_pytest = any("pytest" in cmd for cmd in normalized_commands)
    tests_summary: TestSummary | None = None
    if has_pytest:
        try:
            tests_summary = PytestResultParser().parse(log_text)
        except Exception:
            if res.returncode != 0:
                tests_summary = TestSummary(total=1, passed=0, failed=1, skipped=0)

    # Step 5: Post-Verification SHA-256 Snapshot Immutability
    post_snapshot_hashes = _hash_workspace_files(ws_real)
    cache_created = (ws_real / ".pytest_cache").exists() or any(ws_real.rglob("__pycache__"))
    snapshot_immutable = post_snapshot_hashes == pre_snapshot_hashes and not cache_created
    if not snapshot_immutable:
        return ContainerSnapshotVerificationReceipt(
            passed=False,
            error_code="SNAPSHOT_IMMUTABILITY_VIOLATION",
            pre_snapshot_hashes=pre_snapshot_hashes,
            post_snapshot_hashes=post_snapshot_hashes,
            snapshot_immutable=False,
            host_git_diff_check_passed=host_git_diff_check_passed,
            host_git_diff_output=host_git_diff_output,
            container_image=container_image,
            container_runtime=container_runtime,
            staging_tmpfs_path="/tmp/work",
            pytest_basetemp="/tmp/pytest",
            git_mounted_in_container=False,
            commands_executed=commands_executed,
            exit_codes=exit_codes,
            tests_summary=tests_summary,
            log_text=f"SNAPSHOT_IMMUTABILITY_VIOLATION: workspace mutated\n{log_text}".strip(),
            copy_overhead_ms=copy_overhead_ms,
        )

    passed = res.returncode == 0 and all(rc == 0 for rc in exit_codes)
    return ContainerSnapshotVerificationReceipt(
        passed=passed,
        error_code=None if passed else "CONTAINER_VERIFICATION_FAILED",
        pre_snapshot_hashes=pre_snapshot_hashes,
        post_snapshot_hashes=post_snapshot_hashes,
        snapshot_immutable=True,
        host_git_diff_check_passed=host_git_diff_check_passed,
        host_git_diff_output=host_git_diff_output,
        container_image=container_image,
        container_runtime=container_runtime,
        staging_tmpfs_path="/tmp/work",
        pytest_basetemp="/tmp/pytest",
        git_mounted_in_container=False,
        commands_executed=commands_executed,
        exit_codes=exit_codes,
        tests_summary=tests_summary,
        log_text=log_text,
        copy_overhead_ms=copy_overhead_ms,
    )


class AntigravityCliTransport:
    """Official Antigravity CLI (`agy`) headless transport with strict workspace isolation."""

    def __init__(
        self,
        config: GeminiAdapterConfig,
        *,
        executable: Path | str | None = None,
        runner: Callable[..., Awaitable[asyncio.subprocess.Process]] | None = None,
        verify_command: tuple[str, ...] | None = None,
        auto_commit_attempt: bool = False,
        allow_config_mutation: bool = False,
        verification_isolation: Literal["blocked", "container", "unsafe_host_opt_in"] | None = None,
        container_image: str | None = None,
        container_runtime: str | None = None,
    ) -> None:
        self.config = GeminiAdapterConfig.model_validate(config).model_copy(deep=True)
        self._explicit_executable = Path(executable) if executable is not None else None
        self.runner = runner or asyncio.create_subprocess_exec
        self.verify_command = tuple(verify_command) if verify_command is not None else None
        self.auto_commit_attempt = bool(auto_commit_attempt)
        self.allow_config_mutation = bool(allow_config_mutation)
        self.verification_isolation: Literal["blocked", "container", "unsafe_host_opt_in"] = (
            verification_isolation
            if verification_isolation is not None
            else self.config.verification_isolation
        )
        self.container_image = (
            container_image if container_image is not None else self.config.container_image
        )
        self.container_runtime = (
            container_runtime if container_runtime is not None else self.config.container_runtime
        )
        if (
            self.verification_isolation != self.config.verification_isolation
            or self.container_image != self.config.container_image
            or self.container_runtime != self.config.container_runtime
        ):
            self.config = self.config.model_copy(
                update={
                    "verification_isolation": self.verification_isolation,
                    "container_image": self.container_image,
                    "container_runtime": self.container_runtime,
                }
            )
        self.version: str | None = None
        self.sessions: dict[str, str] = {}
        self.turns: dict[tuple[str, str], dict[str, object]] = {}
        self.verification_records: dict[tuple[str, str], dict[str, object]] = {}
        self.active: tuple[str, str] | None = None

    @property
    def executable(self) -> Path | None:
        return self._explicit_executable or find_agy_executable()

    @property
    def is_qualified_filesystem(self) -> bool:
        """Dynamically check real runtime qualification (binary, version, security flags)."""
        exe = self.executable
        if exe is None:
            return False
        if self._explicit_executable is not None and self.runner != asyncio.create_subprocess_exec:
            return True
        if (
            self.verification_isolation == "unsafe_host_opt_in"
            or not self.config.security_controls_verified
        ):
            return False
        qual = qualify_antigravity_runtime(self.config, executable=exe)
        return qual.qualified

    @property
    def _aipc_qualified_antigravity_filesystem(self) -> bool:
        """Compatibility property reflecting dynamic qualification."""
        return self.is_qualified_filesystem

    @property
    def capabilities(self) -> AgentCapabilities:
        """Dynamically advertise capabilities based on real host qualification."""
        if self.is_qualified_filesystem:
            return AgentCapabilities(
                filesystem=True,
                shell=False,
                git=False,
                streaming_events=True,
                resume_session=False,
                approval_events=False,
            )
        return AgentCapabilities(
            filesystem=False,
            shell=False,
            git=False,
            streaming_events=True,
            resume_session=False,
            approval_events=False,
        )

    def _resolve_executable(self) -> Path:
        exe = self.executable
        if exe is None or not Path(exe).is_file():
            raise GeminiUnavailableError("RUNTIME_UNAVAILABLE")
        return Path(exe).resolve()

    async def readiness(self, workspace: Path | str) -> dict[str, str]:
        resolved_ws = authorized_root(Path(workspace))
        _check_no_env_files(resolved_ws)
        exe = self._resolve_executable()
        info = antigravity_runtime_info()
        if not info.get("oauth_session_present") and self._explicit_executable is None:
            raise GeminiUnavailableError("AUTH_REQUIRED")
        if self.version is None:
            from aipc.hardening.env_isolation import build_sanitized_child_env

            child_env = build_sanitized_child_env()
            try:
                proc = await self.runner(
                    str(exe),
                    "--version",
                    cwd=str(resolved_ws),
                    env=child_env,
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout_b, stderr_b = await proc.communicate()
            except OSError:
                raise GeminiUnavailableError("RUNTIME_UNAVAILABLE") from None
            if proc.returncode != 0:
                err_text = (stderr_b or b"").decode("utf-8", errors="replace")
                _classify_error_text(err_text)
                raise GeminiUnavailableError("RUNTIME_UNAVAILABLE")
            ver = (stdout_b or b"").decode("utf-8", errors="replace").strip()
            if not ver:
                raise GeminiProtocolError("empty Antigravity CLI version output")
            self.version = ver.splitlines()[-1].strip()
        return {"runtime_version": self.version}

    async def start_session(self, workspace: Path | str) -> str:
        await self.readiness(workspace)
        resolved_ws = str(authorized_root(Path(workspace)))
        session_id = "agy-" + str(uuid4())
        self.sessions[session_id] = resolved_ws
        return session_id

    async def load_session(self, session_id: str, workspace: Path | str) -> None:
        raise GeminiSessionLostError("Antigravity CLI session resume lacks a proven replay barrier")

    async def prompt(self, session_id: str, turn_id: str, text: str) -> None:
        if (
            session_id not in self.sessions
            or self.active is not None
            or (session_id, turn_id) in self.turns
        ):
            raise GeminiSessionLostError("prompt ownership differs or dispatch duplicate")
        workspace = Path(self.sessions[session_id])
        _check_no_env_files(workspace)

        empty_hooks_dir = self.config.state_root / "empty_hooks"
        empty_hooks_dir.mkdir(parents=True, exist_ok=True)
        pre_turn_dirty = _get_dirty_paths(workspace, empty_hooks_dir)
        pre_turn_configs = snapshot_build_and_hook_configs(workspace)
        pre_turn_hashes = _hash_workspace_files(workspace)
        staged_res = _run_safe_git(["diff", "--cached", "--name-only"], workspace, empty_hooks_dir)
        pre_turn_staged = set((staged_res.stdout or "").splitlines())
        pre_violations = scan_workspace_integrity(workspace)
        if pre_violations:
            raise GeminiAdapterError(
                f"Workspace integrity violation before dispatch: {pre_violations}"
            )

        pre_git = _git_fingerprint(workspace)
        exe = self._resolve_executable()

        self.config.state_root.mkdir(parents=True, exist_ok=True)
        settings_path = self.config.state_root / f"agy-settings-{turn_id}.json"
        settings_payload = {
            "permissions": {
                "deny": list(DENY_RULES),
                "allow": list(ALLOW_RULES),
            }
        }
        settings_path.write_text(json.dumps(settings_payload, allow_nan=False), encoding="utf-8")

        argv = [
            str(exe),
            "-p",
            text,
            "--output-format",
            "stream-json",
            "--mode",
            str(getattr(self.config, "approval_mode", "accept-edits")),
            "--sandbox",
            "--disable-slash-commands",
            "--print-timeout",
            f"{int(self.config.operation_timeout * 4)}s",
            "--settings",
            str(settings_path),
        ]
        if self.config.model:
            argv.extend(["--model", self.config.model])

        from aipc.hardening.env_isolation import build_sanitized_child_env

        child_env = build_sanitized_child_env()
        try:
            proc = await self.runner(
                *argv,
                cwd=str(workspace),
                env=child_env,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError:
            settings_path.unlink(missing_ok=True)
            raise GeminiUnavailableError("RUNTIME_UNAVAILABLE") from None

        self.active = (session_id, turn_id)
        state: dict[str, object] = {
            "proc": proc,
            "workspace": str(workspace),
            "pre_git": pre_git,
            "settings_path": settings_path,
            "cancelled": False,
            "pre_turn_dirty_paths": pre_turn_dirty,
            "pre_turn_config_digests": pre_turn_configs,
            "pre_turn_file_hashes": pre_turn_hashes,
            "pre_turn_staged_paths": pre_turn_staged,
        }
        state["task"] = asyncio.create_task(self._collect_process(session_id, turn_id, state))
        self.turns[(session_id, turn_id)] = state

    async def _collect_process(
        self, session_id: str, turn_id: str, state: dict[str, object]
    ) -> GeminiRawResult:
        proc: asyncio.subprocess.Process = state["proc"]  # type: ignore[assignment]
        workspace = Path(str(state["workspace"]))
        pre_git = str(state["pre_git"])
        settings_path: Path = state["settings_path"]  # type: ignore[assignment]

        try:
            stdout_lines: list[bytes] = []
            total_bytes = 0
            assert proc.stdout is not None
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break
                total_bytes += len(line)
                if total_bytes > self.config.max_event_bytes:
                    proc.kill()
                    await proc.wait()
                    raise GeminiProtocolError("Antigravity stream-json exceeded max_event_bytes")
                stdout_lines.append(line)

            stderr_b = await proc.stderr.read() if proc.stderr is not None else b""
            await proc.wait()

            if state.get("cancelled"):
                return GeminiRawResult(
                    session_id=session_id,
                    turn_id=turn_id,
                    workspace=str(workspace),
                    runtime_version=self.version or "unknown",
                    stop_reason="cancelled",
                    response="cancelled",
                    worker_status="completed",
                    model=self.config.model or "gemini-antigravity-default",
                )

            stderr_text = stderr_b.decode("utf-8", errors="replace")
            raw_res = self._parse_stream(
                session_id=session_id,
                turn_id=turn_id,
                workspace=workspace,
                pre_git=pre_git,
                returncode=proc.returncode or 0,
                stdout_lines=stdout_lines,
                stderr_text=stderr_text,
            )

            post_violations = scan_workspace_integrity(workspace)
            if post_violations:
                sec_err = f"Security violation detected in workspace: {post_violations}"
                raw_res = GeminiRawResult(
                    session_id=session_id,
                    turn_id=turn_id,
                    workspace=str(workspace),
                    runtime_version=self.version or "unknown",
                    stop_reason="end_turn",
                    response=f"{raw_res.response} [{sec_err}]",
                    worker_status="failed",
                    model=self.config.model or "gemini-antigravity-default",
                    tools=raw_res.tools,
                    usage=raw_res.usage,
                    duration_ms=raw_res.duration_ms,
                )

            if raw_res.worker_status == "completed":
                finalize_ok = await asyncio.to_thread(
                    self._post_turn_host_finalize, session_id, turn_id, workspace, state
                )
                if not finalize_ok:
                    ver_err = "Independent host verification failed or refused"
                    raw_res = GeminiRawResult(
                        session_id=session_id,
                        turn_id=turn_id,
                        workspace=str(workspace),
                        runtime_version=self.version or "unknown",
                        stop_reason="end_turn",
                        response=f"{raw_res.response} [{ver_err}]",
                        worker_status="failed",
                        model=self.config.model or "gemini-antigravity-default",
                        tools=raw_res.tools,
                        usage=raw_res.usage,
                        duration_ms=raw_res.duration_ms,
                    )
            return raw_res
        finally:
            settings_path.unlink(missing_ok=True)
            if self.active == (session_id, turn_id):
                self.active = None

    def _run_isolated_verification(
        self,
        *,
        turn_id: str,
        workspace: Path,
        command: tuple[str, ...],
        timeout_seconds: int = 60,
    ) -> tuple[bool, TestSummary, str]:
        """Run verify_command inside an isolated sandbox with sys.addaudithook security."""
        first_token = Path(command[0]).name.lower()
        is_python = "python" in first_token or first_token.startswith("py")
        if not is_python:
            return (
                False,
                TestSummary(total=1, passed=0, failed=1, skipped=0),
                "BLOCKED: Arbitrary binary verification refused (isolation cannot be established)",
            )

        sandbox_dir = self.config.state_root / f"verify-sandbox-{turn_id}"
        guard_dir = sandbox_dir / "guard"
        home_dir = sandbox_dir / "home"
        tmp_dir = sandbox_dir / "tmp"
        for d in (guard_dir, home_dir, tmp_dir):
            d.mkdir(parents=True, exist_ok=True)

        ws_real = workspace.resolve()
        sb_real = sandbox_dir.resolve()

        sitecustomize_code = f'''"""AIPC Isolated Verification Guard (sitecustomize.py)."""
import os
import sys
from pathlib import Path

WORKSPACE_PATH = Path(r"{ws_real}").resolve()
SANDBOX_PATH = Path(r"{sb_real}").resolve()
ALLOWED_READ_ROOTS = [
    WORKSPACE_PATH,
    SANDBOX_PATH,
    Path(sys.prefix).resolve(),
    Path(sys.base_prefix).resolve(),
]
sys_root = os.environ.get("SystemRoot") or os.environ.get("WINDIR")
if sys_root:
    ALLOWED_READ_ROOTS.append(Path(sys_root).resolve())
if os.name != "nt":
    for posix_root in ("/usr", "/lib", "/lib64", "/bin", "/sbin", "/etc", "/proc", "/dev"):
        p_root = Path(posix_root)
        if p_root.exists():
            ALLOWED_READ_ROOTS.append(p_root.resolve())

for p in list(sys.path):
    if p:
        try:
            rp = Path(p).resolve()
            if rp != WORKSPACE_PATH.parent and not rp.is_relative_to(WORKSPACE_PATH.parent):
                ALLOWED_READ_ROOTS.append(rp)
        except Exception:
            pass

def audit_hook(event: str, args: tuple) -> None:
    if event.startswith("socket.") or event.startswith("urllib."):
        raise PermissionError(f"Network access blocked in isolated verification sandbox: {{event}}")

    if (
        event.startswith("subprocess.")
        or event.startswith("os.system")
        or event.startswith("os.spawn")
        or event.startswith("os.exec")
        or event.startswith("os.posix_spawn")
        or event.startswith("winreg.")
        or event == "os.startfile"
    ):
        raise PermissionError(
            f"Subprocess/shell execution blocked in isolated verification sandbox: {{event}}"
        )

    if event in (
        "open",
        "os.listdir",
        "os.scandir",
        "os.mkdir",
        "os.remove",
        "os.unlink",
        "os.rmdir",
        "os.rename",
        "os.replace",
        "os.symlink",
        "os.link",
    ):
        raw_path = args[0] if args else None
        if raw_path is None or isinstance(raw_path, int):
            return
        try:
            p = Path(raw_path).resolve()
        except Exception:
            raise PermissionError(f"Unresolvable path in sandbox: {{raw_path}}")

        str_p = str(p).lower()
        if "nul" in str_p or "/dev/null" in str_p or str_p == os.devnull.lower():
            return

        is_write = False
        if event == "open":
            mode = args[1] if len(args) > 1 and args[1] is not None else "r"
            if any(m in mode for m in ("w", "a", "+", "x")):
                is_write = True
        elif event in (
            "os.mkdir",
            "os.remove",
            "os.unlink",
            "os.rmdir",
            "os.rename",
            "os.replace",
            "os.symlink",
            "os.link",
        ):
            is_write = True

        if is_write:
            in_ws = p.is_relative_to(WORKSPACE_PATH)
            in_sb = p.is_relative_to(SANDBOX_PATH)
            if not (in_ws or in_sb):
                raise PermissionError(f"Write access blocked outside workspace: {{p}}")
            if in_ws:
                rel_parts = p.relative_to(WORKSPACE_PATH).parts
                if any(part == ".git" or part.startswith(".env") for part in rel_parts):
                    raise PermissionError(
                        f"Write access blocked to protected workspace path: {{p}}"
                    )
        else:
            in_ws = p.is_relative_to(WORKSPACE_PATH)
            if in_ws:
                rel_parts = p.relative_to(WORKSPACE_PATH).parts
                if any(part.startswith(".env") for part in rel_parts):
                    raise PermissionError(f"Read access blocked to sensitive .env file: {{p}}")
                return
            if any(p.is_relative_to(root) for root in ALLOWED_READ_ROOTS):
                return
            raise PermissionError(f"Read access blocked outside workspace: {{p}}")

sys.addaudithook(audit_hook)
'''
        (guard_dir / "sitecustomize.py").write_text(sitecustomize_code, encoding="utf-8")

        env: dict[str, str] = {
            "SYSTEMROOT": os.environ.get("SYSTEMROOT", "C:\\Windows"),
            "WINDIR": os.environ.get("WINDIR", "C:\\Windows"),
            "COMSPEC": os.environ.get("COMSPEC", "C:\\Windows\\system32\\cmd.exe"),
            "PATHEXT": os.environ.get("PATHEXT", ".COM;.EXE;.BAT;.CMD"),
            "PATH": os.environ.get("PATH", ""),
            "PYTHONPATH": str(guard_dir),
            "PYTHONDONTWRITEBYTECODE": "1",
            "USERPROFILE": str(home_dir),
            "HOME": str(home_dir),
            "APPDATA": str(home_dir / "AppData" / "Roaming"),
            "LOCALAPPDATA": str(home_dir / "AppData" / "Local"),
            "TEMP": str(tmp_dir),
            "TMP": str(tmp_dir),
        }

        res = subprocess.run(
            list(command),
            cwd=str(workspace),
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_seconds,
            env=env,
        )
        shutil.rmtree(workspace / ".pytest_cache", ignore_errors=True)
        for pycache in list(workspace.rglob("__pycache__")):
            shutil.rmtree(pycache, ignore_errors=True)

        log_text = (
            "WARNING: verification_isolation='unsafe_host_opt_in' is active. "
            "Python sys.addaudithook() is best-effort defense-in-depth, "
            "not an OS-enforced security boundary. Untrusted code can bypass in-process hooks.\n"
            + (res.stdout or "")
            + ("\n" + res.stderr if res.stderr else "")
        )
        try:
            summary = PytestResultParser().parse(log_text)
        except Exception:
            summary = TestSummary(total=1, passed=0, failed=1, skipped=0)
        passed = res.returncode == 0 and summary.failed == 0 and summary.passed > 0
        return passed, summary, log_text

    def run_cisl_snapshot_verification(
        self,
        *,
        turn_id: str,
        workspace: Path,
        commands: Sequence[tuple[str, ...] | str],
        in_scope_paths: Sequence[str] | None = None,
        out_of_scope_paths: Sequence[str] | None = None,
        forbidden_paths: Sequence[str] | None = None,
        changed_paths: Sequence[str] | None = None,
        pre_config_digests: dict[str, str] | None = None,
        timeout_seconds: int | None = None,
        docker_checker: Callable[..., dict[str, object]] | None = None,
        subprocess_runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
    ) -> ContainerSnapshotVerificationReceipt:
        """Run CISL01 immutable snapshot & tmpfs Linux Docker verification."""
        return run_cisl_container_snapshot_verifier(
            workspace=workspace,
            commands=commands,
            turn_id=turn_id,
            state_root=self.config.state_root,
            container_image=self.container_image or DEFAULT_CISL_VERIFIER_IMAGE,
            container_runtime=self.container_runtime,
            timeout_seconds=(
                timeout_seconds if timeout_seconds is not None else self.config.operation_timeout
            ),
            in_scope_paths=in_scope_paths,
            out_of_scope_paths=out_of_scope_paths,
            forbidden_paths=forbidden_paths,
            changed_paths=changed_paths,
            pre_config_digests=pre_config_digests,
            allow_config_mutation=self.allow_config_mutation,
            docker_checker=docker_checker,
            subprocess_runner=subprocess_runner,
        )

    def _run_container_verification(
        self,
        *,
        turn_id: str,
        workspace: Path,
        command: tuple[str, ...],
    ) -> tuple[bool, TestSummary, str]:
        """Run verify_command inside a restricted OS container."""
        runtime_bin = _resolve_docker_executable(self.container_runtime)

        if runtime_bin is None or not self.container_image:
            return (
                False,
                TestSummary(total=1, passed=0, failed=1, skipped=0),
                "BLOCKED: Container runtime or image unavailable",
            )

        ws_real = workspace.resolve()
        normalized_cmd = list(command)
        if normalized_cmd:
            exe_token = normalized_cmd[0]
            exe_lower = exe_token.lower()
            if (
                exe_lower.endswith("python.exe")
                or exe_lower.endswith("python")
                or ("\\" in exe_token and "python" in exe_lower)
                or (":" in exe_token and "python" in exe_lower)
            ):
                normalized_cmd[0] = "python3"

        if any("pytest" in arg for arg in normalized_cmd):
            has_cache_opt = any(
                arg in ("-o", "-p") or "cache_dir" in arg or "no:cacheprovider" in arg
                for arg in normalized_cmd
            )
            if not has_cache_opt:
                normalized_cmd.extend(["-o", "cache_dir=/tmp/.pytest_cache"])

        safe_turn_id = "".join(c if c.isalnum() or c in "-_." else "-" for c in str(turn_id))
        out_dir = self.config.state_root / f"verify-out-{safe_turn_id}"
        out_dir.mkdir(parents=True, exist_ok=True)

        mount_args = [
            "-v",
            f"{ws_real}:/workspace:ro",
            "-v",
            f"{out_dir.resolve()}:/workspace-out:rw",
        ]
        git_path = workspace / ".git"
        if git_path.is_dir():
            mount_args.extend(["-v", f"{git_path.resolve()}:/workspace/.git:ro"])
        elif git_path.is_file():
            empty_git_file = self.config.state_root / "empty_git_pointer"
            empty_git_file.write_text("", encoding="utf-8")
            mount_args.extend(["-v", f"{empty_git_file.resolve()}:/workspace/.git:ro"])

        container_name = f"aipc-verify-{safe_turn_id}"

        container_cmd = [
            runtime_bin,
            "run",
            "--rm",
            "--name",
            container_name,
            "--network",
            "none",
            "--read-only",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=64m",
            "--user",
            "65534:65534",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--pids-limit",
            "64",
            "--memory",
            "512m",
            "-e",
            "HOME=/tmp",
            "-e",
            "TMPDIR=/tmp",
            "-e",
            "TEMP=/tmp",
            "-e",
            "TMP=/tmp",
            "-e",
            "PYTHONDONTWRITEBYTECODE=1",
            *mount_args,
            "-w",
            "/workspace",
            self.container_image,
            *normalized_cmd,
        ]

        from aipc.hardening.env_isolation import (
            ChildEnvironmentIsolationError,
            assert_argv_no_credentials,
            build_sanitized_child_env,
        )

        try:
            assert_argv_no_credentials(container_cmd)
            sanitized_env = build_sanitized_child_env()
        except ChildEnvironmentIsolationError as exc:
            shutil.rmtree(out_dir, ignore_errors=True)
            return (
                False,
                TestSummary(total=1, passed=0, failed=1, skipped=0),
                f"BLOCKED: {exc}",
            )

        try:
            res = subprocess.run(
                container_cmd,
                capture_output=True,
                text=True,
                check=False,
                timeout=self.config.operation_timeout,
                env=sanitized_env,
            )
        except subprocess.TimeoutExpired:
            try:
                subprocess.run(
                    [runtime_bin, "rm", "-f", container_name],
                    capture_output=True,
                    check=False,
                    timeout=10,
                    env=sanitized_env,
                )
            except Exception:
                pass
            return (
                False,
                TestSummary(total=1, passed=0, failed=1, skipped=0),
                "TIMEOUT: Container verification timed out",
            )
        except OSError as exc:
            return (
                False,
                TestSummary(total=1, passed=0, failed=1, skipped=0),
                f"ERROR: Container execution failed: {exc}",
            )
        finally:
            shutil.rmtree(out_dir, ignore_errors=True)

        shutil.rmtree(workspace / ".pytest_cache", ignore_errors=True)
        for pycache in list(workspace.rglob("__pycache__")):
            shutil.rmtree(pycache, ignore_errors=True)

        log_text = (res.stdout or "") + ("\n" + res.stderr if res.stderr else "")
        try:
            summary = PytestResultParser().parse(log_text)
        except Exception:
            summary = TestSummary(total=1, passed=0, failed=1, skipped=0)
        passed = res.returncode == 0 and summary.failed == 0 and summary.passed > 0
        return passed, summary, log_text

    def _save_failed_attempt(
        self, turn_id: str, log_text: str, workspace: Path, empty_hooks_dir: Path
    ) -> None:
        """Save failed verification log and git diff snapshot for diagnosis."""
        failed_dir = self.config.state_root / "failed_attempts"
        failed_dir.mkdir(parents=True, exist_ok=True)
        (failed_dir / f"{turn_id}.log").write_text(log_text, encoding="utf-8")
        diff_res = _run_safe_git(["diff", "HEAD"], workspace, empty_hooks_dir)
        status_res = _run_safe_git(["status", "--porcelain"], workspace, empty_hooks_dir)
        meta = {
            "turn_id": turn_id,
            "diff": diff_res.stdout,
            "status": status_res.stdout,
        }
        (failed_dir / f"{turn_id}.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

    def _commit_turn_changes(
        self,
        workspace: Path,
        turn_id: str,
        state: dict[str, object],
        empty_hooks_dir: Path,
    ) -> tuple[bool, str]:
        """Stage only files modified during the turn and commit with hooks disabled."""
        if not (workspace / ".git").exists():
            return True, ""
        shutil.rmtree(workspace / ".pytest_cache", ignore_errors=True)
        for pycache in list(workspace.rglob("__pycache__")):
            shutil.rmtree(pycache, ignore_errors=True)

        pre_hashes = state.get("pre_turn_file_hashes", {})
        assert isinstance(pre_hashes, dict)
        post_hashes = _hash_workspace_files(workspace)

        turn_modified: set[str] = set()
        all_keys = set(pre_hashes.keys()) | set(post_hashes.keys())
        for rel_path in all_keys:
            if post_hashes.get(rel_path) != pre_hashes.get(rel_path):
                turn_modified.add(rel_path)

        if not turn_modified:
            return True, ""

        for path_str in sorted(turn_modified):
            res_add = _run_safe_git(["add", "--", path_str], workspace, empty_hooks_dir)
            if res_add.returncode != 0:
                err = res_add.stderr or res_add.stdout or f"git add failed for {path_str}"
                return False, err

        res_commit = _run_safe_git(
            [
                "commit",
                "-m",
                f"AIPC Antigravity CLI turn {turn_id} changes",
                "--no-verify",
                "--only",
                "--",
                *sorted(turn_modified),
            ],
            workspace,
            empty_hooks_dir,
        )
        if res_commit.returncode != 0:
            err = res_commit.stderr or res_commit.stdout or "git commit failed"
            return False, err

        return True, ""

    def _post_turn_host_finalize(
        self, session_id: str, turn_id: str, workspace: Path, state: dict[str, object]
    ) -> bool:
        """Run independent verification and safe commit.

        Returns True on success, False on failure.
        """
        empty_hooks_dir = self.config.state_root / "empty_hooks"
        empty_hooks_dir.mkdir(parents=True, exist_ok=True)

        pre_configs = state.get("pre_turn_config_digests", {})
        assert isinstance(pre_configs, dict)
        mutations = check_build_config_mutations(workspace, pre_configs)
        if mutations and not self.allow_config_mutation:
            err_msg = (
                f"Security violation: unauthorized modification of build/hook config: {mutations}"
            )
            self._save_failed_attempt(turn_id, err_msg, workspace, empty_hooks_dir)
            return False

        if self.verify_command is not None:
            pre_verify_hashes = _hash_workspace_files(workspace)
            if self.verification_isolation == "blocked":
                log_text = (
                    "BLOCKED: Host execution of untrusted verification code is disabled by "
                    "default. Python sys.addaudithook() is not an OS security boundary. "
                    "Configure verification_isolation='container' with a restricted "
                    "container/VM/Windows Sandbox, or explicitly set "
                    "verification_isolation='unsafe_host_opt_in' for trusted local testing."
                )
                passed = False
                summary = TestSummary(total=1, passed=0, failed=1, skipped=0)
            elif self.verification_isolation == "container":
                passed, summary, log_text = self._run_container_verification(
                    turn_id=turn_id,
                    workspace=workspace,
                    command=self.verify_command,
                )
            elif self.verification_isolation == "unsafe_host_opt_in":
                passed, summary, log_text = self._run_isolated_verification(
                    turn_id=turn_id,
                    workspace=workspace,
                    command=self.verify_command,
                    timeout_seconds=self.config.operation_timeout,
                )
            else:
                log_text = (
                    f"BLOCKED: Unknown verification_isolation mode '{self.verification_isolation}'"
                )
                passed = False
                summary = TestSummary(total=1, passed=0, failed=1, skipped=0)

            post_verify_hashes = _hash_workspace_files(workspace)
            if post_verify_hashes != pre_verify_hashes:
                passed = False
                summary = TestSummary(total=1, passed=0, failed=1, skipped=0)
                err_msg = "Security violation: workspace files were mutated during verification"
                log_text = f"{err_msg}\n{log_text}" if log_text else err_msg

            verify_log_path = self.config.state_root / f"verify-{turn_id}.log"
            verify_log_path.write_text(log_text, encoding="utf-8")

            if not passed:
                self._save_failed_attempt(turn_id, log_text, workspace, empty_hooks_dir)
                return False

            log_path = workspace / ".aipc_test.log"
            log_path.write_text(log_text, encoding="utf-8")
            self.verification_records[(session_id, turn_id)] = {
                "tests": summary,
                "test_log_path": ".aipc_test.log",
                "command": " ".join(self.verify_command),
                "exit_code": 0,
            }

        if self.auto_commit_attempt:
            ok, commit_err = self._commit_turn_changes(workspace, turn_id, state, empty_hooks_dir)
            if not ok:
                self._save_failed_attempt(
                    turn_id, f"Git commit failed: {commit_err}", workspace, empty_hooks_dir
                )
                return False

        return True

    def _parse_stream(
        self,
        *,
        session_id: str,
        turn_id: str,
        workspace: Path,
        pre_git: str,
        returncode: int,
        stdout_lines: list[bytes],
        stderr_text: str,
    ) -> GeminiRawResult:
        conv_id: str | None = None
        permission_denied = False
        denial_reasons: list[str] = []
        observations: list[ToolObservation] = []
        edited_paths: set[str] = set()
        result_frame: dict[str, object] | None = None
        observed_model: str | None = self.config.model

        for line_idx, raw_line in enumerate(stdout_lines):
            line_str = raw_line.decode("utf-8", errors="replace").strip()
            if not line_str:
                continue
            try:
                frame = json.loads(line_str, object_pairs_hook=_unique_object)
            except ValueError:
                # Handle non-JSON lines emitted by CLI (e.g. jetski auto-denial banner)
                lowered_line = line_str.lower()
                if any(
                    token in lowered_line
                    for token in ("denied", "permission", "jetski", "auto-denied")
                ):
                    permission_denied = True
                    denial_reasons.append("headless_permission_auto_denied")
                    continue
                raise GeminiProtocolError(
                    f"malformed Antigravity stream JSON at line {line_idx + 1}"
                ) from None

            if not isinstance(frame, dict):
                raise GeminiProtocolError("stream-json item must be a JSON object")

            event_type = frame.get("event")
            if not isinstance(event_type, str):
                raise GeminiProtocolError("missing event type in Antigravity frame")

            if event_type in ("session_start", "init"):
                cid = frame.get("conversation_id")
                if isinstance(cid, str) and cid.strip():
                    conv_id = cid
            elif event_type in ("tool_step", "step_update"):
                step_obj = frame.get("step") or frame.get("step_update")
                if isinstance(step_obj, dict):
                    step_type = step_obj.get("step_type")
                    if step_type == "tool" or (
                        step_type is None and ("tool" in step_obj or "tool_name" in step_obj)
                    ):
                        self._process_tool_step(
                            step=step_obj,
                            workspace=workspace,
                            observations=observations,
                            edited_paths=edited_paths,
                            denial_reasons=denial_reasons,
                        )
            elif event_type in ("text_delta", "message"):
                pass
            elif event_type == "result":
                res = frame.get("result")
                if not isinstance(res, dict):
                    raise GeminiProtocolError("malformed result frame")
                cid = res.get("conversation_id")
                if not isinstance(cid, str) or not cid.strip():
                    raise GeminiProtocolError("missing conversation_id in result frame")
                if conv_id is not None and cid != conv_id:
                    raise GeminiProtocolError("result conversation_id mismatch")
                conv_id = cid
                result_frame = res
                if isinstance(res.get("model"), str) and str(res["model"]).strip():
                    observed_model = str(res["model"]).strip()
            else:
                raise GeminiProtocolError(f"unsupported Antigravity event: {event_type}")

        _check_no_env_files(workspace)
        if _git_fingerprint(workspace) != pre_git:
            raise GeminiProtocolError("Antigravity execution modified restricted .git metadata")

        if result_frame is None:
            _classify_error_text(stderr_text)
            raise GeminiProtocolError("Antigravity CLI exited without a terminal result frame")

        status_val = result_frame.get("status")
        if status_val != "SUCCESS":
            err_detail = str(
                result_frame.get("error") or result_frame.get("response") or stderr_text
            )
            _classify_error_text(err_detail)
            if returncode != 0:
                raise GeminiProtocolError("Antigravity CLI reported non-SUCCESS terminal status")

        denied_actions = result_frame.get("denied_actions")
        if isinstance(denied_actions, list) and len(denied_actions) > 0:
            permission_denied = True
            for item in denied_actions:
                if isinstance(item, dict):
                    denial_reasons.append(f"denied_action:{item.get('action', 'unknown')}")

        raw_response = result_frame.get("response")
        if not isinstance(raw_response, str):
            raise GeminiProtocolError("Antigravity result response must be a string")

        worker_status = "completed"
        response_text = raw_response
        if permission_denied or bool(denial_reasons) or status_val != "SUCCESS" or returncode != 0:
            worker_status = "failed"
            reason_summary = (
                "; ".join(denial_reasons) if denial_reasons else "tool or permission failure"
            )
            if not response_text.strip():
                response_text = f"Antigravity headless execution failed ({reason_summary})"
            else:
                response_text = f"{response_text.strip()} [{reason_summary}]"
        elif response_text.lstrip().startswith("{"):
            try:
                report = json.loads(response_text, object_pairs_hook=_unique_object)
                if set(report) == {"status", "summary"} and report["status"] in (
                    "completed",
                    "failed",
                ):
                    response_text = str(report["summary"])
                    worker_status = str(report["status"])
            except ValueError:
                pass

        if not response_text.strip():
            raise GeminiProtocolError("empty terminal response from Antigravity CLI")

        usage_obj = result_frame.get("usage")
        canonical_usage: dict[str, int] | None = None
        if isinstance(usage_obj, dict):
            try:
                in_tok = int(usage_obj.get("input_tokens", 0))
                out_tok = int(usage_obj.get("output_tokens", 0))
                cached_tok = int(
                    usage_obj.get("cache_read_tokens", usage_obj.get("cached_tokens", 0))
                )
                canonical_usage = {
                    "input_tokens": in_tok,
                    "output_tokens": out_tok,
                    "cached_tokens": cached_tok,
                }
            except (ValueError, TypeError):
                raise GeminiProtocolError("invalid Antigravity usage counters") from None

        duration_ms: int | None = None
        duration_sec = result_frame.get("duration_seconds")
        if isinstance(duration_sec, int | float) and duration_sec >= 0:
            duration_ms = int(round(float(duration_sec) * 1000))

        return GeminiRawResult(
            session_id=session_id,
            turn_id=turn_id,
            workspace=str(workspace),
            runtime_version=self.version or "unknown",
            stop_reason="end_turn",
            response=redact(response_text),
            worker_status=worker_status,  # type: ignore[arg-type]
            tools=tuple(observations),
            model=observed_model or "gemini-antigravity-default",
            usage=canonical_usage,
            duration_ms=duration_ms,
        )

    def _process_tool_step(
        self,
        *,
        step: dict[str, object],
        workspace: Path,
        observations: list[ToolObservation],
        edited_paths: set[str],
        denial_reasons: list[str],
    ) -> None:
        tool_info = step.get("tool") or step.get("tool_info")
        if not isinstance(tool_info, dict):
            name = str(step.get("tool_name") or "unknown")
            params = step.get("parameters") if isinstance(step.get("parameters"), dict) else {}
            tool_info = {"name": name, "parameters": params}

        tool_name = str(tool_info.get("name", "unknown"))
        state = str(step.get("state", "UNKNOWN"))
        step_idx = step.get("step_index", len(observations))
        tool_id = f"agy-tool-{step_idx}"

        if tool_name not in ALLOWED_AGY_TOOLS:
            if state == "DONE":
                raise GeminiProtocolError(
                    f"prohibited Antigravity tool execution detected: {tool_name}"
                )
            denial_reasons.append(f"unauthorized_tool:{tool_name}")
            existing_idx = next(
                (i for i, o in enumerate(observations) if o.tool_call_id == tool_id),
                None,
            )
            unauth_obs = ToolObservation(
                tool_call_id=tool_id, kind="execute", status="failed", paths=()
            )
            if existing_idx is not None:
                observations[existing_idx] = unauth_obs
            else:
                observations.append(unauth_obs)
            return

        kind = "read"
        if tool_name in ("write_file", "edit_file"):
            kind = "edit"

        params = tool_info.get("parameters")
        raw_path: str | None = None
        if isinstance(params, dict):
            for key in ("Path", "TargetFile", "DirectoryPath", "SearchPath", "path"):
                val = params.get(key)
                if isinstance(val, str) and val.strip():
                    raw_path = val.strip()
                    break

        paths: tuple[str, ...] = ()
        if raw_path is not None:
            try:
                rel = portable_file(raw_path, str(workspace))
                if kind == "edit" and state == "DONE":
                    if rel not in edited_paths:
                        edited_paths.add(rel)
                        paths = (rel,)
                else:
                    paths = (rel,)
            except (ValueError, EvidencePathError):
                if state == "DONE":
                    raise GeminiProtocolError(
                        "Antigravity tool accessed path outside workspace or restricted file: "
                        f"{raw_path}"
                    ) from None
                denial_reasons.append(f"denied restricted/outside path for {tool_name}")
                paths = ()

        obs_status = "completed" if state == "DONE" else "failed"
        obs = ToolObservation(
            tool_call_id=tool_id,
            kind=kind,  # type: ignore[arg-type]
            status=obs_status,  # type: ignore[arg-type]
            paths=paths,
        )
        existing_idx = next(
            (i for i, o in enumerate(observations) if o.tool_call_id == tool_id),
            None,
        )
        if existing_idx is not None:
            if state == "DONE" or obs_status == "completed":
                observations[existing_idx] = obs
        else:
            observations.append(obs)

    async def wait(self, session_id: str, turn_id: str) -> GeminiRawResult:
        state = self.turns.get((session_id, turn_id))
        if state is None:
            raise GeminiSessionLostError("unknown exact Antigravity prompt")
        task: asyncio.Task[GeminiRawResult] = state["task"]  # type: ignore[assignment]
        return await asyncio.shield(task)

    async def cancel(self, session_id: str, turn_id: str) -> None:
        if self.active != (session_id, turn_id):
            raise GeminiSessionLostError("no exact active Antigravity prompt to cancel")
        state = self.turns[(session_id, turn_id)]
        state["cancelled"] = True
        proc: asyncio.subprocess.Process = state["proc"]  # type: ignore[assignment]
        if proc.returncode is None:
            try:
                proc.terminate()
            except OSError:
                pass

    async def close(self) -> None:
        """Close transport and reset active turn."""
        self.active = None


class AntigravityNormalizer:
    """Normalizes raw Antigravity CLI output into canonical AgentResult."""

    def __init__(self, primary_workspace: Path | str | AntigravityCliTransport) -> None:
        if isinstance(primary_workspace, AntigravityCliTransport):
            primary_workspace = primary_workspace.config.primary_workspace
        self.primary = authorized_root(Path(primary_workspace))

    def normalize(
        self,
        raw_result: GeminiRawResult | RawAgentResult,
        context: NormalizationContext,
    ) -> AgentResult:
        context = NormalizationContext.model_validate(context)
        if isinstance(raw_result, RawAgentResult):
            raw_result = raw_result.payload
        if not isinstance(raw_result, GeminiRawResult):
            raise GeminiAdapterError("expected GeminiRawResult input")

        run_status = terminal_status(raw_result)
        complete = run_status == RunStatus.COMPLETED
        changes = []
        observed_files = set()
        for obs in raw_result.tools:
            if obs.kind == "edit" and obs.status == "completed":
                for path in obs.paths:
                    if path not in observed_files:
                        observed_files.add(path)
                        changes.append({"path": path, "change_type": "modified"})

        summary_text = raw_result.response.strip()
        tests_summary: TestSummary | None = None
        test_log_path: str | None = None

        test_log_file = Path(raw_result.workspace) / ".aipc_test.log"
        if test_log_file.is_file():
            try:
                log_content = test_log_file.read_text(encoding="utf-8")
                tests_summary = PytestResultParser().parse(log_content)
                test_log_path = ".aipc_test.log"
            except Exception:
                pass

        if tests_summary is None:
            lines = summary_text.splitlines()
            for line in lines:
                if "passed" in line and ("test" in line or "failed" in line):
                    try:
                        tests_summary = PytestResultParser().parse(line)
                        break
                    except Exception:
                        pass

        return validate_agent_result(
            dict(
                schema_version="1.0",
                task_id=context.task_id,
                agent_id=context.agent_id,
                provider=context.provider,
                status=run_status,
                started_at=context.started_at,
                completed_at=context.completed_at,
                summary=summary_text,
                files_changed=changes,
                commands=[],
                tests=tests_summary.model_dump() if tests_summary is not None else None,
                artifacts=[],
                warnings=[
                    "Provider tool observations are claims; inspect independent workspace evidence."
                ]
                if raw_result.tools
                else [],
                errors=["Structured worker-reported failure"]
                if run_status == RunStatus.FAILED
                else [],
                approval_requests=[],
                worker_claimed_complete=complete,
                evidence=dict(
                    git_diff_path=None,
                    test_log_path=test_log_path,
                    terminal_log_path=None,
                    screenshots=[],
                ),
                raw_output_ref=context.raw_output_ref,
                metadata={
                    "gemini": {
                        "session_id": raw_result.session_id,
                        "turn_id": raw_result.turn_id,
                        "runtime_version": raw_result.runtime_version,
                        "model": raw_result.model,
                        "usage": raw_result.usage,
                        "duration_ms": raw_result.duration_ms,
                    },
                    "antigravity": {
                        "session_id": raw_result.session_id,
                        "turn_id": raw_result.turn_id,
                        "runtime_version": raw_result.runtime_version,
                        "model": raw_result.model,
                        "stop_reason": raw_result.stop_reason,
                        "usage": raw_result.usage,
                        "duration_ms": raw_result.duration_ms,
                        "denial_reasons": list(getattr(raw_result, "denial_reasons", ())),
                    },
                },
            )
        )


__all__ = [
    "ALLOWED_AGY_TOOLS",
    "ALLOW_RULES",
    "AntigravityCliTransport",
    "AntigravityNormalizer",
    "CONTAINER_VERIFIER_UNAVAILABLE",
    "ContainerSnapshotVerificationReceipt",
    "DEFAULT_CISL_VERIFIER_IMAGE",
    "DENY_RULES",
    "antigravity_runtime_plan",
    "check_build_config_mutations",
    "normalize_cisl_container_command",
    "run_cisl_container_snapshot_verifier",
    "scan_workspace_integrity",
    "snapshot_build_and_hook_configs",
]
