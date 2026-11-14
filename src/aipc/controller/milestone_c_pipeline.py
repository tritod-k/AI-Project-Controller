"""Workstream C02 — Multi-Language Isolated Verification & End-to-End Pipeline.

Provides provider-neutral isolated verification across heterogeneous project stacks
(Python, Node.js/JavaScript/TypeScript, Static Web, and Google Apps Script offline
mocks) and orchestrates the Milestone C end-to-end cycle from onboarding and task
queue dispatch through worker execution, isolated verification, and independent
subscription review, stopping strictly at the Human Merge boundary.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import Literal, Self
from uuid import uuid4

from pydantic import Field, StrictBool, ValidationError, model_validator

from aipc.agents.role_registry import RoleModelBinding
from aipc.controller.errors import ControllerError
from aipc.controller.milestone_b_orchestrator import (
    HandoffNextAuthorizedAction,
    WorkerStepCallable,
)
from aipc.controller.self_dev_runner import (
    VERIFICATION_ISOLATION_BLOCKED,
    CandidateImportOriginReport,
    verify_candidate_import_origin,
)
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
from aipc.review.context import context_fingerprint, digest, review_provenance
from aipc.review.errors import PhaseGateError, ReviewContextError
from aipc.review.gate import PhaseGate
from aipc.review.models import ReviewContext
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
    import_origin_verified: StrictBool = False
    import_origin_report: CandidateImportOriginReport | None = None

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
                raise ValueError("google_apps_script_offline_mock requires offline_mock_only=True")
            if self.simulation_disclaimer is None or not self.simulation_disclaimer.strip():
                raise ValueError(
                    "google_apps_script_offline_mock requires non-blank simulation_disclaimer"
                )
        assert_no_secrets(self.model_dump(mode="json"))
        return self


def _parse_int_match(pattern: str, text: str) -> int:
    match = re.search(pattern, text, flags=re.IGNORECASE | re.MULTILINE)
    return int(match.group(1)) if match else 0


def _validate_isolated_python_command(cmd: tuple[str, ...]) -> str | None:
    """Validate that an isolated_subprocess command invokes Python without site bypass flags."""
    if not cmd:
        return "empty command"
    exe_name = Path(cmd[0]).name.lower().removesuffix(".exe")
    if not exe_name.startswith("python"):
        return f"non-python executable prohibited in isolated_subprocess: {cmd[0]}"
    for token in cmd[1:]:
        if token == "--isolated":
            return f"isolated python flag prohibited: {token}"
        if token.startswith("-") and not token.startswith("--"):
            if any(ch in token[1:] for ch in ("S", "I", "E")):
                return f"site/env bypass flag prohibited: {token}"
    return None


def _write_isolated_python_guard(
    boot_dir: Path,
    workspace_path: Path,
    *,
    allow_network: bool,
    stable_root: Path | None = None,
) -> Path:
    """Write sitecustomize.py enforcing network, write, and import-origin isolation."""
    ws_str = str(Path(workspace_path).resolve())
    scratch_dir = Path(boot_dir) / "scratch"
    scratch_dir.mkdir(parents=True, exist_ok=True)
    tmp_str = str(scratch_dir.resolve())
    stable_str = str(Path(stable_root).resolve()) if stable_root is not None else ""
    lines = [
        "def _install_aipc_sandbox():",
        "    import builtins",
        "    import importlib.machinery",
        "    import io",
        "    import os",
        "    import shutil",
        "    import socket",
        "    import stat",
        "    import subprocess",
        "    import sys",
        "    import tempfile",
        "    from collections.abc import Mapping",
        "    from pathlib import Path",
        "",
        "    if sys.flags.no_site or sys.flags.isolated or sys.flags.ignore_environment:",
        "        raise RuntimeError('ISOLATED_GUARD_DISABLED')",
        "",
        f"    _WS_ROOT = Path({ws_str!r}).resolve()",
        "    _WS_SRC = (_WS_ROOT / 'src').resolve()",
        f"    _TMP_ROOT = Path({tmp_str!r}).resolve()",
        "    _SELF_FILE = Path(__file__).resolve()",
        "    _BOOT_DIR_PATH = _SELF_FILE.parent",
        "    _BOOT_DIR = str(_BOOT_DIR_PATH)",
        "    os.environ['TMPDIR'] = str(_TMP_ROOT)",
        "    os.environ['TEMP'] = str(_TMP_ROOT)",
        "    os.environ['TMP'] = str(_TMP_ROOT)",
        "    tempfile.tempdir = str(_TMP_ROOT)",
        f"    _ALLOW_NETWORK = {bool(allow_network)!r}",
        f"    _STABLE_STR = {stable_str!r}",
        "",
        "    if not _ALLOW_NETWORK:",
        "        def _blocked_network(*args, **kwargs):",
        "            raise PermissionError(",
        "                'NETWORK_ISOLATION_BLOCKED: external network disabled'",
        "            )",
        "        _blocked_network.__defaults__ = None",
        "        _blocked_network.__kwdefaults__ = None",
        "        socket.socket.connect = _blocked_network",
        "        socket.socket.connect_ex = _blocked_network",
        "        socket.socket.sendto = _blocked_network",
        "        socket.socket.sendmsg = _blocked_network",
        "        socket.socket.bind = _blocked_network",
        "        socket.create_connection = _blocked_network",
        "        socket.getaddrinfo = _blocked_network",
        "        socket.gethostbyname = _blocked_network",
        "",
        "    _WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND",
        "",
        "    def _guarded_os_open(path, flags, *args, **kwargs):",
        "        if kwargs.get('dir_fd') is not None:",
        "            raise PermissionError(",
        "                'WORKSPACE_WRITE_ISOLATION_BLOCKED: dir_fd prohibited'",
        "            )",
        "        import sys as _sys",
        "        _native = __import__('nt' if _sys.platform == 'win32' else 'posix')",
        "        return _native.open(path, flags, *args, **kwargs)",
        "    _guarded_os_open.__defaults__ = None",
        "    _guarded_os_open.__kwdefaults__ = None",
        "",
        "    os.open = _guarded_os_open",
        "",
        "    def _wrap_single_path_mutator(fn):",
        "        def _wrapped(path, *args, **kwargs):",
        "            if kwargs.get('dir_fd') is not None:",
        "                raise PermissionError(",
        "                    'WORKSPACE_WRITE_ISOLATION_BLOCKED: dir_fd prohibited'",
        "                )",
        "            return fn(path, *args, **kwargs)",
        "        _wrapped.__defaults__ = None",
        "        _wrapped.__kwdefaults__ = None",
        "        return _wrapped",
        "",
        "    for _fn_name in ('remove', 'unlink', 'mkdir', 'rmdir', 'chmod', 'truncate'):",
        "        setattr(os, _fn_name, _wrap_single_path_mutator(getattr(os, _fn_name)))",
        "",
        "    def _wrap_two_path_mutator(fn):",
        "        def _wrapped(src, dst, *args, **kwargs):",
        "            if (",
        "                kwargs.get('src_dir_fd') is not None",
        "                or kwargs.get('dst_dir_fd') is not None",
        "            ):",
        "                raise PermissionError(",
        "                    'WORKSPACE_WRITE_ISOLATION_BLOCKED: dir_fd prohibited'",
        "                )",
        "            return fn(src, dst, *args, **kwargs)",
        "        _wrapped.__defaults__ = None",
        "        _wrapped.__kwdefaults__ = None",
        "        return _wrapped",
        "",
        "    for _fn_name in ('rename', 'replace', 'link', 'symlink'):",
        "        setattr(os, _fn_name, _wrap_two_path_mutator(getattr(os, _fn_name)))",
        "",
        "    def _blocked_proc(*args, **kwargs):",
        "        raise PermissionError(",
        "            'SUBPROCESS_SANDBOX_BYPASS_BLOCKED: '",
        "            'direct process execution prohibited'",
        "        )",
        "    _blocked_proc.__defaults__ = None",
        "    _blocked_proc.__kwdefaults__ = None",
        "",
        "    for _name in dir(os):",
        "        if _name == 'system' or _name.startswith(('spawn', 'exec', 'popen')):",
        "            setattr(os, _name, _blocked_proc)",
        "",
        "    class _BlockDisallowedImports:",
        "        _BLOCKED = ('ctypes', '_ctypes', '_posixsubprocess', '_winapi', 'msvcrt')",
        "",
        "        def find_spec(self, fullname, path=None, target=None):",
        "            if fullname in self._BLOCKED or fullname.startswith(('ctypes.', '_ctypes.')):",
        "                raise ImportError(f'CTYPES_SANDBOX_BLOCKED: {fullname} is prohibited')",
        "            return None",
        "",
        "    for _mod_name in ('ctypes', '_ctypes', '_posixsubprocess', '_winapi', 'msvcrt'):",
        "        _loaded_mod = sys.modules.pop(_mod_name, None)",
        "        if _loaded_mod is not None and hasattr(_loaded_mod, 'fork_exec'):",
        "            _loaded_mod.fork_exec = _blocked_proc",
        "    sys.meta_path.insert(0, _BlockDisallowedImports())",
        "",
        "    _orig_popen_init = subprocess.Popen.__init__",
        "",
        "    def _guarded_popen_init(self, *p_args, **kwargs):",
        "        if kwargs.get('shell'):",
        "            raise PermissionError(",
        "                'SUBPROCESS_SANDBOX_BYPASS_BLOCKED: shell=True prohibited'",
        "            )",
        "        raw_env = kwargs.get('env')",
        "        if isinstance(raw_env, Mapping):",
        "            env_copy = dict(raw_env)",
        "            for _k in ('SYSTEMROOT', 'WINDIR', 'COMSPEC', 'PATH'):",
        "                if _k not in env_copy and _k in os.environ:",
        "                    env_copy[_k] = os.environ[_k]",
        "        else:",
        "            env_copy = dict(os.environ)",
        "        cur_pypath = env_copy.get('PYTHONPATH', '')",
        "        env_copy['PYTHONPATH'] = (",
        "            f'{_BOOT_DIR}{os.pathsep}{cur_pypath}' if cur_pypath else _BOOT_DIR",
        "        )",
        "        env_copy['PYTHONNOUSERSITE'] = '1'",
        "        kwargs['env'] = env_copy",
        "        return _orig_popen_init(self, *p_args, **kwargs)",
        "    _guarded_popen_init.__defaults__ = None",
        "    _guarded_popen_init.__kwdefaults__ = None",
        "",
        "    subprocess.Popen.__init__ = _guarded_popen_init",
        "",
        "    def _audit_hook(event, args):",
        "        if not _ALLOW_NETWORK and event in (",
        "            'socket.connect', 'socket.bind', 'socket.sendto',",
        "            'socket.sendmsg', 'socket.getaddrinfo', 'socket.gethostbyname',",
        "        ):",
        "            raise PermissionError(",
        "                'NETWORK_ISOLATION_BLOCKED: external network disabled'",
        "            )",
        "        if event in ('os.system', 'os.spawn', 'os.exec', 'os.posix_spawn'):",
        "            raise PermissionError(",
        "                f'SUBPROCESS_SANDBOX_BYPASS_BLOCKED: {event} prohibited'",
        "            )",
        "        if event in ('ctypes.dlopen', 'ctypes.dlsym'):",
        "            raise PermissionError(f'CTYPES_SANDBOX_BLOCKED: {event} prohibited')",
        "        if event == 'subprocess.Popen' and len(args) >= 2:",
        "            _exe_target, _cmd_arg = args[0], args[1]",
        "            if isinstance(_cmd_arg, (str, bytes, os.PathLike)):",
        "                _argv = [os.fsdecode(_cmd_arg)]",
        "            elif _cmd_arg is not None:",
        "                _argv = [os.fsdecode(_tok) for _tok in _cmd_arg]",
        "            else:",
        "                _argv = []",
        "            _eff_exe = _exe_target if _exe_target is not None else (",
        "                _argv[0] if _argv else None",
        "            )",
        "            if not _argv or not _eff_exe:",
        "                raise PermissionError(",
        "                    'SUBPROCESS_SANDBOX_BYPASS_BLOCKED: '",
        "                    'non-Python or unguarded child process prohibited'",
        "                )",
        "            _eff_name = Path(os.fsdecode(_eff_exe)).name.lower().removesuffix('.exe')",
        "            _blocked = not _eff_name.startswith('python')",
        "            if not _blocked and _exe_target is not None:",
        "                _a0 = Path(os.fsdecode(_argv[0])).name.lower().removesuffix('.exe')",
        "                if not _a0.startswith('python'):",
        "                    _blocked = True",
        "            if not _blocked:",
        "                for _tok in _argv[1:]:",
        "                    if _tok == '--isolated' or (",
        "                        _tok.startswith('-')",
        "                        and not _tok.startswith('--')",
        "                        and any(_ch in _tok[1:] for _ch in ('S', 'I', 'E'))",
        "                    ):",
        "                        _blocked = True",
        "                        break",
        "            if _blocked:",
        "                raise PermissionError(",
        "                    'SUBPROCESS_SANDBOX_BYPASS_BLOCKED: '",
        "                    'non-Python or unguarded child process prohibited'",
        "                )",
        "            return",
        "        _paths_to_check = ()",
        "        if event == 'open' and len(args) >= 2:",
        "            mode_arg = args[1]",
        "            flags_arg = args[2] if len(args) >= 3 else 0",
        "            is_write = (",
        "                (",
        "                    isinstance(mode_arg, str)",
        "                    and any(c in mode_arg for c in ('w', 'a', 'x', '+'))",
        "                )",
        "                or (isinstance(mode_arg, int) and bool(mode_arg & _WRITE_FLAGS))",
        "                or (isinstance(flags_arg, int) and bool(flags_arg & _WRITE_FLAGS))",
        "            )",
        "            if isinstance(args[0], int):",
        "                if is_write and args[0] > 2:",
        "                    raise PermissionError(",
        "                        'WORKSPACE_WRITE_ISOLATION_BLOCKED: '",
        "                        'raw file descriptor path prohibited'",
        "                    )",
        "                return",
        "            if is_write:",
        "                _paths_to_check = (args[0],)",
        "        elif event in (",
        "            'os.remove', 'os.unlink', 'os.mkdir', 'os.rmdir',",
        "            'os.chmod', 'os.truncate', 'os.chown', 'os.utime', 'shutil.rmtree',",
        "        ):",
        "            if args:",
        "                _paths_to_check = (args[0],)",
        "        elif event in (",
        "            'os.rename', 'os.replace', 'os.link', 'os.symlink',",
        "            'shutil.copyfile', 'shutil.move',",
        "        ):",
        "            if len(args) >= 2:",
        "                _paths_to_check = (args[0], args[1])",
        "        _nulls = (os.devnull.lower(), r'\\\\.\\nul', '/dev/null')",
        "        for _p in _paths_to_check:",
        "            if isinstance(_p, int):",
        "                raise PermissionError(",
        "                    'WORKSPACE_WRITE_ISOLATION_BLOCKED: '",
        "                    'raw file descriptor path prohibited'",
        "                )",
        "            if not isinstance(_p, (str, bytes, os.PathLike)):",
        "                raise PermissionError(",
        "                    'WORKSPACE_WRITE_ISOLATION_BLOCKED: unsupported path type'",
        "                )",
        "            _raw_path = Path(os.fsdecode(_p))",
        "            _abs_path = (",
        "                _raw_path if _raw_path.is_absolute() else (Path.cwd() / _raw_path)",
        "            )",
        "            for _comp in (_abs_path, *_abs_path.parents):",
        "                try:",
        "                    _st = _comp.lstat()",
        "                except OSError:",
        "                    continue",
        "                _is_link = stat.S_ISLNK(_st.st_mode) or bool(",
        "                    getattr(_st, 'st_file_attributes', 0)",
        "                    & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400)",
        "                )",
        "                if _is_link:",
        "                    _rc = _comp.resolve()",
        "                    _rc_ok = (",
        "                        _rc != _SELF_FILE",
        "                        and not (",
        "                            _rc.is_relative_to(_BOOT_DIR_PATH)",
        "                            and not _rc.is_relative_to(_TMP_ROOT)",
        "                        )",
        "                        and (",
        "                            str(_rc).lower() in _nulls",
        "                            or _rc.is_relative_to(_WS_ROOT)",
        "                            or _rc.is_relative_to(_TMP_ROOT)",
        "                        )",
        "                    )",
        "                    if not _rc_ok:",
        "                        raise PermissionError(",
        "                            f'WORKSPACE_WRITE_ISOLATION_BLOCKED: symlink escape {_comp}'",
        "                        )",
        "            _target = _raw_path.resolve()",
        "            _target_ok = (",
        "                _target != _SELF_FILE",
        "                and not (",
        "                    _target.is_relative_to(_BOOT_DIR_PATH)",
        "                    and not _target.is_relative_to(_TMP_ROOT)",
        "                )",
        "                and (",
        "                    str(_target).lower() in _nulls",
        "                    or _target.is_relative_to(_WS_ROOT)",
        "                    or _target.is_relative_to(_TMP_ROOT)",
        "                )",
        "            )",
        "            if not _target_ok:",
        "                raise PermissionError(f'WORKSPACE_WRITE_ISOLATION_BLOCKED: {_target}')",
        "",
        "    _audit_hook.__defaults__ = None",
        "    _audit_hook.__kwdefaults__ = None",
        "    sys.addaudithook(_audit_hook)",
        "    del _audit_hook",
        "",
        "    _STABLE = Path(_STABLE_STR).resolve() if _STABLE_STR else None",
        "    _STABLE_VENV = (_STABLE / '.venv').resolve() if _STABLE is not None else None",
        "    _HAS_WS_AIPC = (_WS_SRC / 'aipc' / '__init__.py').is_file()",
        "    if _STABLE is not None:",
        "        _cleaned = []",
        "        for _entry in list(sys.path):",
        "            if not _entry:",
        "                continue",
        "            try:",
        "                _p = Path(_entry).resolve()",
        "                if not _p.is_relative_to(_STABLE_VENV) and (",
        "                    _p == _STABLE or _p.is_relative_to(_STABLE)",
        "                ):",
        "                    continue",
        "            except OSError:",
        "                pass",
        "            _cleaned.append(_entry)",
        "        if _WS_SRC.is_dir():",
        "            sys.path[:] = [str(_WS_SRC)] + _cleaned",
        "        else:",
        "            sys.path[:] = _cleaned",
        "    elif _HAS_WS_AIPC:",
        "        sys.path[:] = [str(_WS_SRC)] + [p for p in sys.path if p != str(_WS_SRC)]",
        "",
        "    if _STABLE is not None or _HAS_WS_AIPC:",
        "        class _StableAipcBlocker:",
        "            def find_spec(self, fullname, path=None, target=None):",
        "                if fullname != 'aipc' and not fullname.startswith('aipc.'):",
        "                    return None",
        "                spec = importlib.machinery.PathFinder.find_spec(fullname, path)",
        "                if spec is not None and spec.origin:",
        "                    origin_p = Path(spec.origin).resolve()",
        "                    if _STABLE is not None and _STABLE_VENV is not None:",
        "                        if origin_p.is_relative_to(",
        "                            _STABLE",
        "                        ) and not origin_p.is_relative_to(_STABLE_VENV):",
        "                            raise ImportError(",
        "                                'IMPORT_ORIGIN_MISMATCH: '",
        "                                f'blocked {fullname} from {origin_p}'",
        "                            )",
        "                    if _HAS_WS_AIPC and not origin_p.is_relative_to(_WS_SRC):",
        "                        raise ImportError(",
        "                            f'IMPORT_ORIGIN_MISMATCH: {fullname} outside {_WS_SRC}'",
        "                        )",
        "                return spec",
        "",
        "        sys.meta_path.insert(0, _StableAipcBlocker())",
        "",
        "_install_aipc_sandbox()",
        "del _install_aipc_sandbox",
        "",
    ]
    target = Path(boot_dir) / "sitecustomize.py"
    target.write_text("\n".join(lines), encoding="utf-8")
    return target


def _verify_candidate_git_worktree(
    workspace_path: Path,
    candidate_sha: str,
    env: dict[str, str],
) -> bool:
    """Return True iff workspace_path is a clean git worktree whose HEAD == candidate_sha."""
    if not (workspace_path / ".git").exists():
        return False
    head_proc = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(workspace_path),
        env=env,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        check=False,
    )
    if head_proc.returncode != 0 or head_proc.stdout.strip() != candidate_sha:
        return False
    status_proc = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=str(workspace_path),
        env=env,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        check=False,
    )
    return status_proc.returncode == 0 and status_proc.stdout.strip() == ""


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
        stack_type: ProjectStackType | None = None,
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
        if stack_type in ("python_cli_or_package", "python_web_or_api"):
            argv.extend(["-e", "PYTHONPATH=/workspace/src"])
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
        verify_git_head: bool = True,
        stable_root: Path | None = None,
    ) -> MultiLanguageVerificationReceipt:
        """Execute isolated verification on a project candidate and return an audited receipt."""
        assert_subscription_only_mode(
            bundle.profile.operating_profile,
            component="MultiLanguageIsolatedVerifier",
            allow_api_key_fallback=bundle.profile.allow_paid_api_keys,
        )
        sanitized_env = build_sanitized_child_env()
        assert_child_env_isolated(sanitized_env)

        pack = bundle.profile.validation_pack
        for raw_cmd in (*pack.lint_commands, *pack.build_commands):
            if "unsafe_host_opt_in" in raw_cmd.lower():
                raise ValueError("unsafe_host_opt_in is prohibited in verification commands")
        extra_commands: list[tuple[str, ...]] = [
            tuple(shlex.split(cmd)) for cmd in (*pack.lint_commands, *pack.build_commands)
        ]

        if command_override is not None:
            if not command_override:
                raise ValueError("command_override must not be empty")
            for tok in command_override:
                if "unsafe_host_opt_in" in tok.lower():
                    raise ValueError("unsafe_host_opt_in is prohibited in verification commands")
            test_commands: list[tuple[str, ...]] = [tuple(command_override)]
        else:
            for raw_cmd in pack.test_commands:
                if "unsafe_host_opt_in" in raw_cmd.lower():
                    raise ValueError("unsafe_host_opt_in is prohibited in verification commands")
            test_commands = [tuple(shlex.split(cmd)) for cmd in pack.test_commands]

        commands_to_run = [*test_commands, *extra_commands]
        primary_command = commands_to_run[0]

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
        tool_versions = {
            "isolation_mode": effective_mode,
            "stack_type": stack_type,
            "commands_count": str(len(commands_to_run)),
        }

        def _fail_receipt(
            reason: str,
            *,
            mode: MultiLanguageIsolationMode = effective_mode,
            exit_code: int = 1,
        ) -> MultiLanguageVerificationReceipt:
            return MultiLanguageVerificationReceipt(
                receipt_id=receipt_id,
                project_id=project_id,
                stack_type=stack_type,
                isolation_mode=mode,
                candidate_sha=candidate_sha,
                command_executed=primary_command,
                exit_code=exit_code,
                passed=False,
                tests_summary=TestSummary(total=1, passed=0, failed=1, skipped=0),
                tool_versions=tool_versions,
                offline_mock_only=offline_mock_only,
                simulation_disclaimer=simulation_disclaimer,
                failure_reason=reason,
            )

        if effective_mode == "blocked":
            return _fail_receipt(VERIFICATION_ISOLATION_BLOCKED, mode="blocked")

        if effective_mode == "isolated_subprocess" and stack_type not in (
            "python_cli_or_package",
            "python_web_or_api",
        ):
            return _fail_receipt("UNSANDBOXED_HOST_SUBPROCESS_REJECTED")

        if effective_mode == "isolated_subprocess":
            for cmd in commands_to_run:
                if _validate_isolated_python_command(cmd) is not None:
                    return _fail_receipt("ISOLATED_SUBPROCESS_COMMAND_GUARD_BYPASS_REJECTED")

        workspace_path = Path(bundle.workspace_root).resolve()
        if verify_git_head and not _verify_candidate_git_worktree(
            workspace_path, candidate_sha, sanitized_env
        ):
            return _fail_receipt("CANDIDATE_WORKTREE_HEAD_OR_DIRTY_MISMATCH")

        initial_digests = snapshot_workspace_digests(workspace_path)

        needs_origin_check = (
            workspace_path / "src" / "aipc" / "__init__.py"
        ).is_file() or stable_root is not None
        import_origin_verified = False
        import_origin_report: CandidateImportOriginReport | None = None
        if needs_origin_check and effective_mode == "isolated_subprocess":
            first_tok = test_commands[0][0] if test_commands and test_commands[0] else ""
            py_exe = first_tok if first_tok and Path(first_tok).is_file() else sys.executable
            try:
                import_origin_report = verify_candidate_import_origin(
                    stable_root=stable_root or (workspace_path.parent / "__aipc_disjoint_stable__"),
                    candidate_root=workspace_path,
                    python_executable=py_exe,
                    use_isolation_bootstrap=True,
                    parent_env=sanitized_env,
                )
                cand_aipc_dir = (workspace_path / "src" / "aipc").resolve()
                actual_origin = Path(import_origin_report.actual_imported_file).resolve()
                if not import_origin_report.verified or not actual_origin.is_relative_to(
                    cand_aipc_dir
                ):
                    return _fail_receipt("IMPORT_ORIGIN_MISMATCH")
                import_origin_verified = True
            except ControllerError:
                return _fail_receipt("IMPORT_ORIGIN_MISMATCH")
        elif needs_origin_check and effective_mode == "container":
            if not (workspace_path / "src" / "aipc" / "__init__.py").is_file():
                return _fail_receipt("IMPORT_ORIGIN_MISMATCH")
            if stable_root is not None:
                s_res = Path(stable_root).resolve()
                if (
                    s_res == workspace_path
                    or s_res.is_relative_to(workspace_path)
                    or workspace_path.is_relative_to(s_res)
                ):
                    return _fail_receipt("IMPORT_ORIGIN_MISMATCH")

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
                return _fail_receipt("CONTAINER_RUNTIME_OR_IMAGE_UNAVAILABLE", mode="container")
            resource_limits = ContainerResourceLimits()
            with tempfile.TemporaryDirectory(prefix="aipc-c02-out-") as tmp_out:
                out_dir = Path(tmp_out)

                def _run_one(cmd: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
                    nonlocal container_argv
                    c_argv = self.build_container_command(
                        runtime_bin=container_runtime_bin,
                        image=image,
                        workspace=workspace_path,
                        out_dir=out_dir,
                        command=cmd,
                        limits=resource_limits,
                        stack_type=stack_type,
                    )
                    if container_argv is None:
                        container_argv = c_argv
                    return self._invoke_runner(list(c_argv), workspace_path, sanitized_env, runner)

                pre_digests, post_digests, test_exit, tests_summary, extra_exit = (
                    self._run_command_batches(
                        workspace_path=workspace_path,
                        stack_type=stack_type,
                        test_commands=test_commands,
                        extra_commands=extra_commands,
                        run_fn=_run_one,
                    )
                )
                if needs_origin_check:
                    probe_nonce = uuid4().hex
                    origin_prefix = f"AIPC_ORIGIN_{probe_nonce}:"
                    probe_cmd = (
                        "python3",
                        "-c",
                        (
                            "import json, sys, aipc; from pathlib import Path; "
                            f"print({origin_prefix!r} + json.dumps({{"
                            "'file': str(Path(aipc.__file__).resolve()), "
                            "'path': list(sys.path)}))"
                        ),
                    )
                    probe_proc = _run_one(probe_cmd)
                    probe_combined = "\n".join(
                        p for p in (probe_proc.stdout, probe_proc.stderr) if p
                    )
                    if (
                        probe_proc.returncode != 0
                        or "IMPORT_ORIGIN_MISMATCH" in probe_combined
                        or "AIPC_ORIGIN_MISMATCH" in probe_combined
                    ):
                        return _fail_receipt("IMPORT_ORIGIN_MISMATCH")
                    origin_payload: dict[str, object] | None = None
                    for line in reversed((probe_proc.stdout or "").splitlines()):
                        if line.startswith(origin_prefix):
                            try:
                                parsed_origin = json.loads(line[len(origin_prefix) :])
                                if isinstance(parsed_origin, dict):
                                    origin_payload = parsed_origin
                            except json.JSONDecodeError:
                                origin_payload = None
                            break
                    if origin_payload is None:
                        return _fail_receipt("IMPORT_ORIGIN_MISMATCH")
                    actual_file = str(origin_payload.get("file", ""))
                    actual_posix_str = actual_file.replace("\\", "/")
                    if not actual_posix_str or ".." in PurePosixPath(actual_posix_str).parts:
                        return _fail_receipt("IMPORT_ORIGIN_MISMATCH")
                    actual_posix = PurePosixPath(actual_posix_str)
                    expected_pkg = PurePosixPath("/workspace/src/aipc")
                    valid_origin = (
                        actual_posix == expected_pkg / "__init__.py"
                        or actual_posix.is_relative_to(expected_pkg)
                    )
                    if not valid_origin:
                        return _fail_receipt("IMPORT_ORIGIN_MISMATCH")
                    import_origin_verified = True
                    import_origin_report = CandidateImportOriginReport(
                        verified=True,
                        expected_candidate_src=str((workspace_path / "src").resolve()),
                        actual_imported_file=actual_file,
                        leaked_stable_paths=(),
                        status_code="OK",
                    )
        else:
            with tempfile.TemporaryDirectory(prefix="aipc-c02-boot-") as tmp_boot:
                _write_isolated_python_guard(
                    Path(tmp_boot),
                    workspace_path,
                    allow_network=bundle.profile.safety_policy.external_network_allowed,
                    stable_root=stable_root,
                )
                exec_env = dict(sanitized_env)
                existing_pypath = exec_env.get("PYTHONPATH", "")
                exec_env["PYTHONPATH"] = (
                    f"{tmp_boot}{os.pathsep}{existing_pypath}" if existing_pypath else tmp_boot
                )
                exec_env["PYTHONDONTWRITEBYTECODE"] = "1"

                def _run_one(cmd: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
                    exec_argv = list(assert_argv_no_credentials(cmd))
                    return self._invoke_runner(exec_argv, workspace_path, exec_env, runner)

                pre_digests, post_digests, test_exit, tests_summary, extra_exit = (
                    self._run_command_batches(
                        workspace_path=workspace_path,
                        stack_type=stack_type,
                        test_commands=test_commands,
                        extra_commands=extra_commands,
                        run_fn=_run_one,
                    )
                )

        post_git_ok = (not verify_git_head) or _verify_candidate_git_worktree(
            workspace_path, candidate_sha, sanitized_env
        )
        workspace_mutated = (
            (initial_digests != pre_digests) or (pre_digests != post_digests) or (not post_git_ok)
        )
        overall_exit_code = test_exit if test_exit != 0 else extra_exit

        if workspace_mutated:
            passed = False
            failure_reason: str | None = "WORKSPACE_MUTATED_DURING_VERIFICATION"
        elif test_exit != 0 or tests_summary.failed > 0 or tests_summary.passed == 0:
            passed = False
            failure_reason = "VERIFICATION_TESTS_FAILED"
        elif extra_exit != 0:
            passed = False
            failure_reason = "VALIDATION_LINT_OR_BUILD_FAILED"
        else:
            passed = True
            failure_reason = None

        return MultiLanguageVerificationReceipt(
            receipt_id=receipt_id,
            project_id=project_id,
            stack_type=stack_type,
            isolation_mode=effective_mode,
            candidate_sha=candidate_sha,
            command_executed=primary_command,
            container_argv=container_argv,
            resource_limits=resource_limits,
            exit_code=overall_exit_code,
            passed=passed,
            tests_summary=tests_summary,
            tool_versions=tool_versions,
            workspace_mutated_during_verification=workspace_mutated,
            offline_mock_only=offline_mock_only,
            simulation_disclaimer=simulation_disclaimer,
            failure_reason=failure_reason,
            import_origin_verified=import_origin_verified,
            import_origin_report=import_origin_report,
        )

    def _run_command_batches(
        self,
        *,
        workspace_path: Path,
        stack_type: ProjectStackType,
        test_commands: list[tuple[str, ...]],
        extra_commands: list[tuple[str, ...]],
        run_fn: Callable[[tuple[str, ...]], subprocess.CompletedProcess[str]],
    ) -> tuple[dict[str, str], dict[str, str], int, TestSummary, int]:
        pre_digests = snapshot_workspace_digests(workspace_path)
        total_passed = 0
        total_failed = 0
        total_skipped = 0
        test_exit = 0
        for cmd in test_commands:
            proc = run_fn(cmd)
            combined_output = "\n".join(part for part in (proc.stdout, proc.stderr) if part)
            cmd_summary = self.parse_multilang_test_output(
                stack_type, combined_output, int(proc.returncode)
            )
            total_passed += cmd_summary.passed
            total_failed += cmd_summary.failed
            total_skipped += cmd_summary.skipped
            if test_exit == 0 and int(proc.returncode) != 0:
                test_exit = int(proc.returncode)

        tests_summary = TestSummary(
            total=total_passed + total_failed + total_skipped,
            passed=total_passed,
            failed=total_failed,
            skipped=total_skipped,
        )

        extra_exit = 0
        for cmd in extra_commands:
            proc = run_fn(cmd)
            if int(proc.returncode) != 0:
                extra_exit = int(proc.returncode)
                break

        post_digests = snapshot_workspace_digests(workspace_path)
        return pre_digests, post_digests, test_exit, tests_summary, extra_exit

    @staticmethod
    def _invoke_runner(
        argv: list[str],
        cwd: Path,
        env: dict[str, str],
        runner: Callable[..., subprocess.CompletedProcess[str]] | None,
    ) -> subprocess.CompletedProcess[str]:
        try:
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
        except (FileNotFoundError, OSError) as exc:
            return subprocess.CompletedProcess(
                args=argv,
                returncode=127,
                stdout="",
                stderr=str(exc),
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


def _verify_gate_decision_against_context(
    gate_decision: SubscriptionReviewerGateDecision,
    review_ctx: ReviewContext,
) -> bool:
    """Recompute digests, provenance, and PhaseGate decision against review_ctx."""
    if gate_decision.bound_review is None or gate_decision.phase_gate_decision is None:
        return False
    gate_ctx = review_ctx.model_copy(
        update={"state": review_ctx.state.model_copy(update={"last_review": None})}
    )
    try:
        if gate_decision.bound_review.review_digest != digest(gate_decision.review_result):
            return False
        if gate_decision.bound_review.context_digest != context_fingerprint(gate_ctx):
            return False
        if gate_decision.bound_review.provenance != review_provenance(gate_ctx):
            return False
        evaluated = PhaseGate().evaluate(gate_decision.bound_review, gate_ctx)
    except (PhaseGateError, ReviewContextError, ValueError):
        return False
    return evaluated == gate_decision.phase_gate_decision


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
        stable_root: Path | None = None,
    ) -> PRReadinessBundle:
        """Execute one end-to-end Milestone C cycle stopping at Human Merge Review."""
        assert_subscription_only_mode(
            bundle.profile.operating_profile,
            component="MilestoneCEndToEndPipeline",
            allow_api_key_fallback=bundle.profile.allow_paid_api_keys,
        )

        if review_callback is None and reviewer_gate is None:
            raise ControllerError("Either reviewer_gate or review_callback must be provided")
        if review_callback is not None and not callable(review_callback):
            raise ControllerError("review_callback must be callable")
        if review_callback is None and (
            reviewer_gate is None or not callable(getattr(reviewer_gate, "evaluate", None))
        ):
            raise ControllerError("reviewer_gate must provide a callable evaluate method")

        queued_task = task_queue.next_eligible_task()
        if queued_task is None:
            raise ControllerError("No eligible task available in PersistentTaskQueue")

        task_id = queued_task.contract.task_id
        milestone_id = queued_task.contract.milestone_id
        resolved_phase_id = phase_id or queued_task.contract.phase_id
        attempt_num = queued_task.current_attempt + 1
        project_id = bundle.project_definition.project.id
        resolved_evidence_root = str(evidence_root or Path(bundle.workspace_root).resolve())

        task_queue.begin_dispatch(
            task_id,
            worker_id=worker_binding.binding_id,
            provider=worker_binding.provider_id.replace("_", "-"),
        )

        try:
            worker_outcome = worker_step(worker_binding, attempt_num, None)
            receipt = self.verifier.verify_project_candidate(
                bundle=bundle,
                candidate_sha=worker_outcome.candidate_sha,
                isolation_mode=isolation_mode,
                command_override=command_override,
                container_runtime_bin=container_runtime_bin,
                runner=runner,
                stable_root=stable_root,
            )

            ws_has_aipc = (
                Path(bundle.workspace_root).resolve() / "src" / "aipc" / "__init__.py"
            ).is_file()
            origin_required = ws_has_aipc or stable_root is not None
            origin_ok = (
                receipt.import_origin_verified is True
                and receipt.import_origin_report is not None
                and receipt.import_origin_report.verified is True
            )
            if not receipt.passed or (origin_required and not origin_ok):
                blocker = (
                    receipt.failure_reason
                    if not receipt.passed and receipt.failure_reason
                    else (
                        "IMPORT_ORIGIN_MISMATCH"
                        if origin_required and not origin_ok
                        else "VERIFICATION_FAILED"
                    )
                )
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
            else:
                assert reviewer_gate is not None
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

            if not isinstance(gate_decision, SubscriptionReviewerGateDecision):
                raise ControllerError("reviewer must return a SubscriptionReviewerGateDecision")

            revalidation_ok = True
            try:
                gate_decision = SubscriptionReviewerGateDecision.model_validate(
                    gate_decision.model_dump(mode="json")
                )
            except ValidationError:
                revalidation_ok = False

            review_verdict = gate_decision.review_result.verdict
            provenance_ok = (
                revalidation_ok
                and gate_decision.task_id == task_id
                and gate_decision.candidate_commit_sha == worker_outcome.candidate_sha
                and gate_decision.reviewed_commit_sha == worker_outcome.candidate_sha
                and gate_decision.review_result.task_id == task_id
                and gate_decision.bound_review is not None
                and gate_decision.bound_review.review.review_id
                == gate_decision.review_result.review_id
                and gate_decision.bound_review.review.task_id == task_id
                and gate_decision.bound_review.provenance.task_id == task_id
                and gate_decision.bound_review.provenance.git_commit == worker_outcome.candidate_sha
                and gate_decision.bound_review.provenance.project_id == project_id
                and gate_decision.bound_review.provenance.phase_id == resolved_phase_id
                and gate_decision.bound_review.provenance.attempt == attempt_num
                and gate_decision.phase_gate_decision is not None
                and gate_decision.phase_gate_decision.verdict == ReviewVerdict.PASS
                and gate_decision.phase_gate_decision.task_id == task_id
                and gate_decision.phase_gate_decision.project_id == project_id
                and gate_decision.phase_gate_decision.phase_id == resolved_phase_id
                and gate_decision.phase_gate_decision.attempt == attempt_num
                and gate_decision.phase_gate_decision.review_id
                == gate_decision.review_result.review_id
                and gate_decision.session_audit.task_id == task_id
                and gate_decision.session_audit.candidate_commit_sha == worker_outcome.candidate_sha
                and gate_decision.session_audit.rejection_code is None
                and gate_decision.session_audit.rejection_reason is None
                and not gate_decision.session_audit.high_risk_escalation_required
                and not gate_decision.session_audit.workspace_mutated
                and gate_decision.session_audit.fresh_isolated_session
                and gate_decision.bound_review.review == gate_decision.review_result
                and gate_decision.session_audit.read_only_sandbox_enforced
                and not gate_decision.session_audit.inherited_chat_history
                and gate_decision.session_audit.independence_level
                in ("cross_provider", "reduced_independence")
                and (
                    gate_decision.session_audit.independence_level != "cross_provider"
                    or gate_decision.session_audit.reviewer_provider_family
                    != worker_binding.provider_family
                )
                and gate_decision.session_audit.workspace_snapshot_before_sha256
                == gate_decision.session_audit.workspace_snapshot_after_sha256
                and gate_decision.session_audit.reviewer_binding_id != worker_binding.binding_id
                and (
                    worker_outcome.worker_session_id is None
                    or gate_decision.session_audit.reviewer_session_id
                    != worker_outcome.worker_session_id
                )
                and _verify_gate_decision_against_context(
                    gate_decision, worker_outcome.review_context
                )
            )
            if gate_decision.gate_outcome == "PASS" and review_verdict == ReviewVerdict.PASS:
                if not provenance_ok:
                    review_verdict = ReviewVerdict.BLOCKED
                    gate_verdict: Literal["PASS", "REVISE", "BLOCKED"] = "BLOCKED"
                    pr_ready = False
                    next_action: HandoffNextAuthorizedAction = "HALT_ON_BLOCKER"
                    blockers: tuple[str, ...] = ("REVIEW_PROVENANCE_MISMATCH",)
                    q_review: Literal["PASS", "REVISE", "BLOCKED"] = "BLOCKED"
                    q_gate: Literal["PASS", "REVISE", "BLOCKED"] = "BLOCKED"
                else:
                    gate_verdict = "PASS"
                    pr_ready = True
                    next_action = "HUMAN_MERGE_REVIEW"
                    blockers = ()
                    q_review = "PASS"
                    q_gate = "PASS"
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
        except Exception as exc:
            task_queue.mark_interrupted(
                task_id,
                reason=str(exc)[:200] or type(exc).__name__,
                quota_exhausted=False,
            )
            raise


__all__ = [
    "ContainerResourceLimits",
    "MilestoneCEndToEndPipeline",
    "MultiLanguageIsolatedVerifier",
    "MultiLanguageIsolationMode",
    "MultiLanguageVerificationReceipt",
    "PRReadinessBundle",
    "snapshot_workspace_digests",
]
