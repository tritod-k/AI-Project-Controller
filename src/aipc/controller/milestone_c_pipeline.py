"""Workstream C02 — Multi-Language Isolated Verification & End-to-End Pipeline.

Provides provider-neutral isolated verification across heterogeneous project stacks
(Python, Node.js/JavaScript/TypeScript, Static Web, and Google Apps Script offline
mocks) and orchestrates the Milestone C end-to-end cycle from onboarding and task
queue dispatch through worker execution, isolated verification, and independent
subscription review, stopping strictly at the Human Merge boundary.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import shlex
import subprocess
import sys
import tempfile
from collections.abc import Callable, Sequence
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
    _assert_no_candidate_startup_hooks,
    verify_candidate_import_origin,
)
from aipc.controller.task_queue import (
    _AUTHORIZED_MILESTONE_C_IDS,
    _AUTHORIZED_MILESTONE_C_PHASES,
    _FORBIDDEN_WORKSTREAM_RE,
    PersistentTaskQueue,
    _is_forbidden_phase_token,
    _is_unapproved_future_scope,
)
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


def _validate_isolated_python_command(
    cmd: Sequence[str], workspace_path: Path | None = None
) -> str | None:
    """Validate that an isolated_subprocess command invokes Python without site bypass flags."""
    if not cmd:
        return "empty command"
    exe = cmd[0]
    if "/" in exe or "\\" in exe:
        try:
            exe_resolved = Path(exe).resolve()
        except (OSError, ValueError):
            return f"non-python executable prohibited in isolated_subprocess: {exe}"
        trusted_exes = {
            Path(sys.executable).resolve(),
            Path(getattr(sys, "_base_executable", sys.executable)).resolve(),
        }
        if workspace_path is not None:
            ws_resolved = Path(workspace_path).resolve()
            if exe_resolved == ws_resolved or exe_resolved.is_relative_to(ws_resolved):
                return f"workspace python executable prohibited in isolated_subprocess: {exe}"
        if exe_resolved not in trusted_exes:
            return f"untrusted python executable prohibited in isolated_subprocess: {exe}"
    elif exe not in ("python", "python3", "python.exe", "python3.exe"):
        return f"non-python executable prohibited in isolated_subprocess: {exe}"
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
    boot_dir_str = str(Path(boot_dir).resolve())
    stable_str = str(Path(stable_root).resolve()) if stable_root is not None else ""
    lines = [
        "def _install_aipc_sandbox():",
        "    import sys",
        f"    _cand_raw_l = {ws_str!r}.replace('\\\\', '/').rstrip('/').lower()",
        f"    _tmp_raw_l = {tmp_str!r}.replace('\\\\', '/').rstrip('/').lower()",
        f"    _boot_raw_l = {boot_dir_str!r}.replace('\\\\', '/').rstrip('/').lower()",
        "    _std_roots = tuple(",
        "        _p.replace('\\\\', '/').rstrip('/').lower()",
        "        for _p in (",
        "            sys.prefix, sys.base_prefix, sys.exec_prefix, sys.base_exec_prefix",
        "        )",
        "        if _p",
        "    )",
        "    sys.path[:] = [",
        "        _p for _p in sys.path",
        "        if _p and _p != '.'",
        "        and _p.replace('\\\\', '/').rstrip('/').lower().startswith(_std_roots)",
        "        and not _p.replace('\\\\', '/').rstrip('/').lower().startswith(",
        "            (_cand_raw_l, _tmp_raw_l, _boot_raw_l)",
        "        )",
        "    ]",
        "    import builtins",
        "    import importlib.machinery",
        "    import io",
        "    import os",
        "    import shutil",
        "    import socket",
        "    import stat",
        "    import subprocess",
        "    import tempfile",
        "    from collections.abc import Mapping",
        "",
        "    if sys.flags.no_site or sys.flags.isolated or sys.flags.ignore_environment:",
        "        raise RuntimeError('ISOLATED_GUARD_DISABLED')",
        "",
        "    _b_isinstance = builtins.isinstance",
        "    _b_len = builtins.len",
        "    _b_any = builtins.any",
        "    _b_bool = builtins.bool",
        "    _b_int = builtins.int",
        "    _b_str = builtins.str",
        "    _b_bytes = builtins.bytes",
        "    _b_tuple = builtins.tuple",
        "    _b_list = builtins.list",
        "    _b_dict = builtins.dict",
        "    _b_set = builtins.set",
        "    _b_getattr = builtins.getattr",
        "    _b_setattr = builtins.setattr",
        "    _b_hasattr = builtins.hasattr",
        "    _b_id = builtins.id",
        "    _b_object = builtins.object",
        "    _b_type = builtins.type",
        "    _b_PermissionError = builtins.PermissionError",
        "    _b_ImportError = builtins.ImportError",
        "    _b_OSError = builtins.OSError",
        "    _b_RuntimeError = builtins.RuntimeError",
        "    _b_BaseException = builtins.BaseException",
        "    _os_environ = os.environ",
        "    _PROTECTED_ENV_VARS = (",
        "        'PYTHONPATH', 'PYTHONSAFEPATH', 'PYTHONNOUSERSITE',",
        "        'PYTHONHOME', 'PYTHONSTARTUP', 'TMPDIR', 'TEMP', 'TMP',",
        "    )",
        "",
        "    _is_win = (os.name == 'nt')",
        "    _native = __import__('nt' if _is_win else 'posix')",
        "    _c_lstat = _native.lstat",
        "    _c_getcwd = _native.getcwd",
        "    _c_readlink = _b_getattr(_native, 'readlink', None)",
        "    _c_final = _b_getattr(_native, '_getfinalpathname', None)",
        "    _c_fsdecode = os.fsdecode",
        "    _c_pathlike = os.PathLike",
        "    _c_normcase = (lambda _s: _s.lower()) if _is_win else (lambda _s: _s)",
        "    _s_islnk = stat.S_ISLNK",
        "    _s_isdir = stat.S_ISDIR",
        "    _s_isreg = stat.S_ISREG",
        "    _reparse_flag = _b_getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400)",
        "    _sep = '\\\\' if _is_win else '/'",
        "    _nulls = ('\\\\\\\\.\\\\nul', '/dev/null', 'nul')",
        "",
        "    def _lex_norm(_raw_s):",
        "        if _is_win:",
        "            _s = _raw_s.replace('/', '\\\\')",
        "            _sl = _s.lower()",
        "            if _sl in ('\\\\\\\\.\\\\nul', 'nul'):",
        "                return '\\\\\\\\.\\\\nul'",
        "            if _sl.startswith('\\\\\\\\?\\\\unc\\\\'):",
        "                _s = '\\\\\\\\' + _s[8:]",
        "            elif _s.startswith('\\\\\\\\?\\\\') or _s.startswith('\\\\??\\\\'):",
        "                _s = _s[4:]",
        "            if _s.startswith('\\\\\\\\'):",
        "                pass",
        "            elif _b_len(_s) >= 2 and _s[1] == ':' and _s[0].isalpha():",
        "                if _b_len(_s) < 3 or _s[2] != '\\\\':",
        "                    raise _b_PermissionError(",
        "                        'WORKSPACE_WRITE_ISOLATION_BLOCKED: '",
        "                        'drive-relative path prohibited'",
        "                    )",
        "            elif _s.startswith('\\\\'):",
        "                raise _b_PermissionError(",
        "                    'WORKSPACE_WRITE_ISOLATION_BLOCKED: '",
        "                    'drive-relative path prohibited'",
        "                )",
        "            else:",
        "                _cwd = _c_getcwd()",
        "                _s = _cwd + ('' if _cwd.endswith('\\\\') else '\\\\') + _s",
        "            if _s.startswith('\\\\\\\\'):",
        "                _parts = _s[2:].split('\\\\')",
        "                if _b_len(_parts) >= 2:",
        "                    _root = '\\\\\\\\' + _parts[0] + '\\\\' + _parts[1]",
        "                    _rest = _parts[2:]",
        "                else:",
        "                    _root = '\\\\\\\\' + (_parts[0] if _parts else '')",
        "                    _rest = []",
        "            else:",
        "                _root = _s[:2]",
        "                _rest = _s[3:].split('\\\\')",
        "            _stack = []",
        "            for _part in _rest:",
        "                if not _part or _part == '.':",
        "                    continue",
        "                if _part == '..':",
        "                    if _stack:",
        "                        _stack.pop()",
        "                else:",
        "                    _stack.append(_part)",
        "            if _root.startswith('\\\\\\\\'):",
        "                _out = _root + ('\\\\' + '\\\\'.join(_stack) if _stack else '')",
        "            else:",
        "                _out = _root + '\\\\' + '\\\\'.join(_stack)",
        "            return _out.lower()",
        "        if _raw_s == '/dev/null':",
        "            return '/dev/null'",
        "        _s = _raw_s",
        "        if not _s.startswith('/'):",
        "            _cwd = _c_getcwd()",
        "            _s = (_cwd if _cwd == '/' else _cwd.rstrip('/')) + '/' + _s",
        "        _stack = []",
        "        for _part in _s.split('/'):",
        "            if not _part or _part == '.':",
        "                continue",
        "            if _part == '..':",
        "                if _stack:",
        "                    _stack.pop()",
        "            else:",
        "                _stack.append(_part)",
        "        return '/' + '/'.join(_stack)",
        "    _lex_norm.__defaults__ = None",
        "    _lex_norm.__kwdefaults__ = None",
        "",
        "    def _parent_of(_p):",
        "        if _is_win:",
        "            if _p.startswith('\\\\\\\\'):",
        "                _idx = _p.rfind('\\\\')",
        "                if _idx <= 1 or _p.count('\\\\') <= 3:",
        "                    return _p",
        "                return _p[:_idx]",
        "            _idx = _p.rfind('\\\\')",
        "            if _idx < 0:",
        "                return _p",
        "            if _idx == 2 and _b_len(_p) >= 3 and _p[1] == ':':",
        "                return _p[:3]",
        "            return _p[:_idx]",
        "        _idx = _p.rfind('/')",
        "        if _idx < 0:",
        "            return _p",
        "        if _idx == 0:",
        "            return '/'",
        "        return _p[:_idx]",
        "    _parent_of.__defaults__ = None",
        "    _parent_of.__kwdefaults__ = None",
        "",
        "    def _resolve_link(_lp, _hops, _req_allow):",
        "        if _hops >= 16:",
        "            raise _b_PermissionError(",
        "                f'WORKSPACE_WRITE_ISOLATION_BLOCKED: {_lp}'",
        "            )",
        "        _res_t = None",
        "        if _is_win and _c_final is not None:",
        "            try:",
        "                _res_t = _lex_norm(_c_final(_lp))",
        "            except _b_OSError:",
        "                _res_t = None",
        "        if _res_t is None:",
        "            if _c_readlink is None:",
        "                raise _b_PermissionError(",
        "                    f'WORKSPACE_WRITE_ISOLATION_BLOCKED: {_lp}'",
        "                )",
        "            try:",
        "                _rl = _c_readlink(_lp)",
        "            except _b_OSError as _exc:",
        "                raise _b_PermissionError(",
        "                    f'WORKSPACE_WRITE_ISOLATION_BLOCKED: {_lp}'",
        "                ) from _exc",
        "            _rl_s = _rl.replace('/', _sep).replace('\\\\', _sep)",
        "            if _is_win:",
        "                _is_abs = (",
        "                    _rl_s.startswith('\\\\\\\\')",
        "                    or _rl_s.startswith('\\\\??\\\\')",
        "                    or (",
        "                        _b_len(_rl_s) >= 3",
        "                        and _rl_s[0].isalpha()",
        "                        and _rl_s[1] == ':'",
        "                        and _rl_s[2] == '\\\\'",
        "                    )",
        "                )",
        "            else:",
        "                _is_abs = _rl_s.startswith('/')",
        "            if not _is_abs:",
        "                _par = _parent_of(_lp)",
        "                _rl_s = _par + ('' if _par.endswith(_sep) else _sep) + _rl_s",
        "            _res_t = _canon_impl(_rl_s, _hops + 1, _req_allow)",
        "        if _req_allow and not _is_allowed_path(_c_normcase(_lex_norm(_res_t))):",
        "            raise _b_PermissionError(",
        "                f'WORKSPACE_WRITE_ISOLATION_BLOCKED: symlink escape {_lp}'",
        "            )",
        "        return _res_t",
        "    _resolve_link.__defaults__ = None",
        "    _resolve_link.__kwdefaults__ = None",
        "",
        "    def _canon_impl(_raw_p, _hops, _req_allow=False):",
        "        _s = _c_fsdecode(_raw_p)",
        "        if '\\x00' in _s:",
        "            raise _b_PermissionError(",
        "                'WORKSPACE_WRITE_ISOLATION_BLOCKED: nul byte in path'",
        "            )",
        "        _sl_chk = _s.lower() if _is_win else _s",
        "        if _sl_chk in _nulls:",
        "            return '\\\\\\\\.\\\\nul' if _is_win else '/dev/null'",
        "        if _is_win:",
        "            _s = _s.replace('/', '\\\\')",
        "            _sl = _s.lower()",
        "            if _sl.startswith('\\\\\\\\?\\\\unc\\\\'):",
        "                _s = '\\\\\\\\' + _s[8:]",
        "            elif _s.startswith('\\\\\\\\?\\\\') or _s.startswith('\\\\??\\\\'):",
        "                _s = _s[4:]",
        "            if _s.startswith('\\\\\\\\'):",
        "                pass",
        "            elif _b_len(_s) >= 2 and _s[1] == ':' and _s[0].isalpha():",
        "                if _b_len(_s) < 3 or _s[2] != '\\\\':",
        "                    raise _b_PermissionError(",
        "                        'WORKSPACE_WRITE_ISOLATION_BLOCKED: '",
        "                        'drive-relative path prohibited'",
        "                    )",
        "            elif _s.startswith('\\\\'):",
        "                raise _b_PermissionError(",
        "                    'WORKSPACE_WRITE_ISOLATION_BLOCKED: '",
        "                    'drive-relative path prohibited'",
        "                )",
        "            else:",
        "                _cwd = _c_getcwd()",
        "                _s = _cwd + ('' if _cwd.endswith('\\\\') else '\\\\') + _s",
        "            if _s.startswith('\\\\\\\\'):",
        "                _parts = _s[2:].split('\\\\')",
        "                if _b_len(_parts) >= 2:",
        "                    _cur = '\\\\\\\\' + _parts[0] + '\\\\' + _parts[1]",
        "                    _tokens = _parts[2:]",
        "                else:",
        "                    _cur = '\\\\\\\\' + (_parts[0] if _parts else '')",
        "                    _tokens = []",
        "            else:",
        "                _cur = _s[:3]",
        "                _tokens = _s[3:].split('\\\\')",
        "        else:",
        "            _s = _s.replace('\\\\', '/')",
        "            if not _s.startswith('/'):",
        "                _cwd = _c_getcwd()",
        "                _s = _cwd + ('' if _cwd.endswith('/') else '/') + _s",
        "            _cur = '/'",
        "            _tokens = _s.split('/')",
        "        for _tok in _tokens:",
        "            if not _tok or _tok == '.':",
        "                continue",
        "            if _tok == '..':",
        "                try:",
        "                    _st = _c_lstat(_cur)",
        "                except _b_OSError:",
        "                    _st = None",
        "                if _st is not None and (",
        "                    _s_islnk(_st.st_mode)",
        "                    or _b_bool(",
        "                        _b_getattr(_st, 'st_file_attributes', 0) & _reparse_flag",
        "                    )",
        "                ):",
        "                    _cur = _resolve_link(_cur, _hops, _req_allow)",
        "                _cur = _parent_of(_cur)",
        "                continue",
        "            _cur = _cur + _tok if _cur.endswith(_sep) else _cur + _sep + _tok",
        "            try:",
        "                _st = _c_lstat(_cur)",
        "            except _b_OSError:",
        "                _st = None",
        "            if _st is not None:",
        "                _is_lnk = _s_islnk(_st.st_mode) or _b_bool(",
        "                    _b_getattr(_st, 'st_file_attributes', 0) & _reparse_flag",
        "                )",
        "                if _is_lnk:",
        "                    _cur = _resolve_link(_cur, _hops, _req_allow)",
        "                elif _is_win and _c_final is not None:",
        "                    try:",
        "                        _cur = _lex_norm(_c_final(_cur))",
        "                    except _b_OSError:",
        "                        pass",
        "        return _c_normcase(_lex_norm(_cur))",
        "    _canon_impl.__defaults__ = (False,)",
        "    _canon_impl.__kwdefaults__ = None",
        "    def _canon_existing(_raw_p, _req_allow=False):",
        "        return _canon_impl(_raw_p, 0, _req_allow)",
        "    _canon_existing.__defaults__ = (False,)",
        "    _canon_existing.__kwdefaults__ = None",
        "",
        "    def _in_tree(_norm_p, _root_norm, _root_prefix):",
        "        return _norm_p == _root_norm or _norm_p.startswith(_root_prefix)",
        "    _in_tree.__defaults__ = None",
        "    _in_tree.__kwdefaults__ = None",
        "",
        f"    _ws_norm = _canon_existing(_lex_norm({ws_str!r}))",
        "    _ws_prefix = _ws_norm if _ws_norm.endswith(_sep) else _ws_norm + _sep",
        f"    _tmp_real = _canon_existing(_lex_norm({tmp_str!r}))",
        "    _tmp_norm = _tmp_real",
        "    _tmp_prefix = _tmp_norm if _tmp_norm.endswith(_sep) else _tmp_norm + _sep",
        "    _self_norm = _canon_existing(_lex_norm(__file__))",
        "    _BOOT_DIR = _parent_of(_self_norm)",
        "    _boot_norm = _BOOT_DIR",
        "    _boot_prefix = _boot_norm if _boot_norm.endswith(_sep) else _boot_norm + _sep",
        "    _trusted_py_exe = _canon_existing(_lex_norm(sys.executable))",
        "    _trusted_base_exe = _canon_existing(",
        "        _lex_norm(_b_getattr(sys, '_base_executable', sys.executable) or sys.executable)",
        "    )",
        "    _trusted_exes = (_trusted_py_exe, _trusted_base_exe)",
        "    def _is_allowed_path(_np):",
        "        if _np in _nulls:",
        "            return True",
        "        if _np == _self_norm or (",
        "            _in_tree(_np, _boot_norm, _boot_prefix)",
        "            and not _in_tree(_np, _tmp_norm, _tmp_prefix)",
        "        ):",
        "            return False",
        "        return (",
        "            _in_tree(_np, _ws_norm, _ws_prefix)",
        "            or _in_tree(_np, _tmp_norm, _tmp_prefix)",
        "        )",
        "    _is_allowed_path.__defaults__ = None",
        "    _is_allowed_path.__kwdefaults__ = None",
        "    _os_environ['TMPDIR'] = _tmp_real",
        "    _os_environ['TEMP'] = _tmp_real",
        "    _os_environ['TMP'] = _tmp_real",
        "    tempfile.tempdir = _tmp_real",
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
        "    def _guarded_os_open(path, flags, *args, **kwargs):",
        "        if kwargs.get('dir_fd') is not None or type(path) is int or type(path) is bool:",
        "            raise PermissionError(",
        "                'WORKSPACE_WRITE_ISOLATION_BLOCKED: dir_fd prohibited'",
        "            )",
        "        import sys as _sys",
        "        _nat = __import__('nt' if _sys.platform == 'win32' else 'posix')",
        "        return _nat.open(path, flags, *args, **kwargs)",
        "    _guarded_os_open.__defaults__ = None",
        "    _guarded_os_open.__kwdefaults__ = None",
        "",
        "    os.open = _guarded_os_open",
        "",
        "    def _wrap_single_path_mutator(fn):",
        "        def _wrapped(path, *args, **kwargs):",
        "            if kwargs.get('dir_fd') is not None:",
        "                raise _b_PermissionError(",
        "                    'WORKSPACE_WRITE_ISOLATION_BLOCKED: dir_fd prohibited'",
        "                )",
        "            return fn(path, *args, **kwargs)",
        "        _wrapped.__defaults__ = None",
        "        _wrapped.__kwdefaults__ = None",
        "        return _wrapped",
        "",
        "    for _fn_name in ('remove', 'unlink', 'mkdir', 'rmdir', 'chmod', 'truncate'):",
        "        _b_setattr(os, _fn_name, _wrap_single_path_mutator(_b_getattr(os, _fn_name)))",
        "        if _b_hasattr(_native, _fn_name):",
        "            _b_setattr(",
        "                _native,",
        "                _fn_name,",
        "                _wrap_single_path_mutator(_b_getattr(_native, _fn_name)),",
        "            )",
        "",
        "    def _wrap_two_path_mutator(fn):",
        "        def _wrapped(src, dst, *args, **kwargs):",
        "            if (",
        "                kwargs.get('src_dir_fd') is not None",
        "                or kwargs.get('dst_dir_fd') is not None",
        "            ):",
        "                raise _b_PermissionError(",
        "                    'WORKSPACE_WRITE_ISOLATION_BLOCKED: dir_fd prohibited'",
        "                )",
        "            return fn(src, dst, *args, **kwargs)",
        "        _wrapped.__defaults__ = None",
        "        _wrapped.__kwdefaults__ = None",
        "        return _wrapped",
        "",
        "    for _fn_name in ('rename', 'replace', 'link', 'symlink'):",
        "        _b_setattr(os, _fn_name, _wrap_two_path_mutator(_b_getattr(os, _fn_name)))",
        "        if _b_hasattr(_native, _fn_name):",
        "            _b_setattr(",
        "                _native, _fn_name, _wrap_two_path_mutator(_b_getattr(_native, _fn_name))",
        "            )",
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
        "        if (",
        "            _name == 'system'",
        "            or _name.startswith(('spawn', 'exec', 'popen'))",
        "            or _name in ('posix_spawn', 'posix_spawnp')",
        "        ):",
        "            _b_setattr(os, _name, _blocked_proc)",
        "",
        "    class _BlockDisallowedImports:",
        "        _BLOCKED = ('ctypes', '_ctypes', '_posixsubprocess', '_winapi', 'msvcrt')",
        "",
        "        def find_spec(self, fullname, path=None, target=None):",
        "            if fullname in self._BLOCKED or fullname.startswith(('ctypes.', '_ctypes.')):",
        "                raise ImportError(",
        "                    f'CTYPES_SANDBOX_BLOCKED: {fullname} is prohibited'",
        "                )",
        "            return None",
        "",
        "    for _mod_name in ('ctypes', '_ctypes', '_posixsubprocess', '_winapi', 'msvcrt'):",
        "        sys.modules.pop(_mod_name, None)",
        "    sys.meta_path.insert(0, _BlockDisallowedImports())",
        "",
        "    _orig_popen_init = subprocess.Popen.__init__",
        "    def _setup_popen_guard(_orig_p_init, _p_boot, _p_tmp, _p_exe):",
        "        _sealed_fns = {}",
        "        _popen_active = [0]",
        "        _BARE_PY = ('python', 'python3', 'python.exe', 'python3.exe')",
        "",
        "        class _SealedMeta(_b_type):",
        "            def __getattribute__(cls, name):",
        "                raise _b_PermissionError(",
        "                    'WORKSPACE_WRITE_ISOLATION_BLOCKED: closure access prohibited'",
        "                )",
        "            def __setattr__(cls, name, value):",
        "                raise _b_PermissionError(",
        "                    'WORKSPACE_WRITE_ISOLATION_BLOCKED: closure access prohibited'",
        "                )",
        "            def __delattr__(cls, name):",
        "                raise _b_PermissionError(",
        "                    'WORKSPACE_WRITE_ISOLATION_BLOCKED: closure access prohibited'",
        "                )",
        "",
        "        class _SealedBound:",
        "            __slots__ = ()",
        "            def __init__(self, obj, fid):",
        "                _sealed_fns[_b_id(self)] = (obj, fid)",
        "            def __call__(self, *a, **kw):",
        "                _obj, _fid = _sealed_fns[_b_id(self)]",
        "                return _sealed_fns[_fid](_obj, *a, **kw)",
        "            def __getattribute__(self, name):",
        "                raise _b_PermissionError(",
        "                    'WORKSPACE_WRITE_ISOLATION_BLOCKED: closure access prohibited'",
        "                )",
        "            def __setattr__(self, name, value):",
        "                raise _b_PermissionError(",
        "                    'WORKSPACE_WRITE_ISOLATION_BLOCKED: closure access prohibited'",
        "                )",
        "            def __del__(self):",
        "                _sealed_fns.pop(_b_id(self), None)",
        "",
        "        class _SealedMethod:",
        "            __slots__ = ()",
        "            def __init__(self, fn):",
        "                _sealed_fns[_b_id(self)] = fn",
        "            def __get__(self, obj, objtype=None):",
        "                if obj is None:",
        "                    raise _b_PermissionError(",
        "                        'WORKSPACE_WRITE_ISOLATION_BLOCKED: closure access prohibited'",
        "                    )",
        "                return _SealedBound(obj, _b_id(self))",
        "            def __getattribute__(self, name):",
        "                raise _b_PermissionError(",
        "                    'WORKSPACE_WRITE_ISOLATION_BLOCKED: closure access prohibited'",
        "                )",
        "            def __setattr__(self, name, value):",
        "                raise _b_PermissionError(",
        "                    'WORKSPACE_WRITE_ISOLATION_BLOCKED: closure access prohibited'",
        "                )",
        "            def __del__(self):",
        "                _sealed_fns.pop(_b_id(self), None)",
        "",
        "        _orig_winapi = _b_getattr(subprocess, '_winapi', None)",
        "        if _orig_winapi is not None:",
        "            class _WinapiProxy(metaclass=_SealedMeta):",
        "                __slots__ = ()",
        "                def __getattribute__(self, name):",
        "                    if name == 'CreateProcess' and _popen_active[0] <= 0:",
        "                        raise _b_PermissionError(",
        "                            'SUBPROCESS_SANDBOX_BYPASS_BLOCKED: '",
        "                            'direct _winapi.CreateProcess prohibited'",
        "                        )",
        "                    return _b_getattr(_orig_winapi, name)",
        "                def __setattr__(self, name, value):",
        "                    raise _b_PermissionError(",
        "                        'SUBPROCESS_SANDBOX_BYPASS_BLOCKED: '",
        "                        'direct _winapi.CreateProcess prohibited'",
        "                    )",
        "                def __delattr__(self, name):",
        "                    raise _b_PermissionError(",
        "                        'SUBPROCESS_SANDBOX_BYPASS_BLOCKED: '",
        "                        'direct _winapi.CreateProcess prohibited'",
        "                    )",
        "            subprocess._winapi = _WinapiProxy()",
        "        if _b_hasattr(subprocess, '_use_posix_spawn'):",
        "            subprocess._use_posix_spawn = False",
        "        if _b_hasattr(subprocess, '_USE_POSIX_SPAWN'):",
        "            subprocess._USE_POSIX_SPAWN = False",
        "        _orig_posixsub = _b_getattr(subprocess, '_posixsubprocess', None)",
        "        if _orig_posixsub is not None:",
        "            _orig_fork_exec = _b_getattr(_orig_posixsub, 'fork_exec', None)",
        "            class _PosixSubprocessProxy(metaclass=_SealedMeta):",
        "                __slots__ = ()",
        "                def __getattribute__(self, name):",
        "                    if name == 'fork_exec' and _popen_active[0] <= 0:",
        "                        raise _b_PermissionError(",
        "                            'SUBPROCESS_SANDBOX_BYPASS_BLOCKED: '",
        "                            '_posixsubprocess.fork_exec'",
        "                        )",
        "                    return _b_getattr(_orig_posixsub, name)",
        "                def __setattr__(self, name, value):",
        "                    raise _b_PermissionError(",
        "                        'SUBPROCESS_SANDBOX_BYPASS_BLOCKED: _posixsubprocess immutable'",
        "                    )",
        "                def __delattr__(self, name):",
        "                    raise _b_PermissionError(",
        "                        'SUBPROCESS_SANDBOX_BYPASS_BLOCKED: _posixsubprocess immutable'",
        "                    )",
        "            subprocess._posixsubprocess = _PosixSubprocessProxy()",
        "            if _orig_fork_exec is not None:",
        "                def _guarded_fork_exec(*f_args, **f_kw):",
        "                    if _popen_active[0] <= 0:",
        "                        raise _b_PermissionError(",
        "                            'SUBPROCESS_SANDBOX_BYPASS_BLOCKED: '",
        "                            '_posixsubprocess.fork_exec'",
        "                        )",
        "                    return _orig_fork_exec(*f_args, **f_kw)",
        "                _guarded_fork_exec.__defaults__ = None",
        "                _guarded_fork_exec.__kwdefaults__ = None",
        "                subprocess._fork_exec = _guarded_fork_exec",
        "            if _b_hasattr(subprocess.Popen._execute_child, '__globals__'):",
        "                subprocess.Popen._execute_child.__globals__['_use_posix_spawn'] = False",
        "                subprocess.Popen._execute_child.__globals__['_USE_POSIX_SPAWN'] = False",
        "                if _orig_fork_exec is not None:",
        "                    subprocess.Popen._execute_child.__globals__['_fork_exec'] = (",
        "                        _guarded_fork_exec",
        "                    )",
        "",
        "        def _guarded_popen_init(self, *p_args, **kwargs):",
        "            if kwargs.get('shell') or (_b_len(p_args) > 8 and p_args[8]):",
        "                raise _b_PermissionError(",
        "                    'SUBPROCESS_SANDBOX_BYPASS_BLOCKED: shell=True prohibited'",
        "                )",
        "            _exe_kw = p_args[2] if _b_len(p_args) > 2 else kwargs.get('executable')",
        "            _allow_cmd_rewrite = (",
        "                _exe_kw is None",
        "                or (",
        "                    _b_isinstance(_exe_kw, _b_str)",
        "                    and (_exe_kw.lower() in _BARE_PY or _exe_kw == _p_exe)",
        "                )",
        "            )",
        "            if _b_isinstance(_exe_kw, _b_str) and _exe_kw.lower() in _BARE_PY:",
        "                if _b_len(p_args) > 2:",
        "                    p_args = p_args[:2] + (_p_exe,) + p_args[3:]",
        "                else:",
        "                    kwargs['executable'] = _p_exe",
        "            _cmd_arg = p_args[0] if p_args else kwargs.get('args')",
        "            if (",
        "                _allow_cmd_rewrite",
        "                and _b_isinstance(_cmd_arg, (_b_list, _b_tuple))",
        "                and _b_len(_cmd_arg) > 0",
        "                and _b_isinstance(_cmd_arg[0], _b_str)",
        "                and _cmd_arg[0].lower() in _BARE_PY",
        "            ):",
        "                _new_cmd = [_p_exe, *_cmd_arg[1:]]",
        "                if p_args:",
        "                    p_args = (_new_cmd,) + p_args[1:]",
        "                else:",
        "                    kwargs['args'] = _new_cmd",
        "            elif _allow_cmd_rewrite and _b_isinstance(_cmd_arg, _b_str):",
        "                _cs = _cmd_arg.lstrip()",
        "                _sp = _cs.split(None, 1)",
        "                if _sp and _sp[0].lower() in _BARE_PY:",
        "                    _q_exe = f'\"{_p_exe}\"' if ' ' in _p_exe else _p_exe",
        "                    _new_cmd = _q_exe + (' ' + _sp[1] if _b_len(_sp) > 1 else '')",
        "                    if p_args:",
        "                        p_args = (_new_cmd,) + p_args[1:]",
        "                    else:",
        "                        kwargs['args'] = _new_cmd",
        "            raw_env = p_args[10] if _b_len(p_args) > 10 else kwargs.get('env')",
        "            if _b_isinstance(raw_env, Mapping):",
        "                env_copy = _b_dict(raw_env)",
        "                for _k in ('SYSTEMROOT', 'WINDIR', 'COMSPEC', 'PATH'):",
        "                    if _k not in env_copy and _k in _os_environ:",
        "                        env_copy[_k] = _os_environ[_k]",
        "            else:",
        "                env_copy = _b_dict(_os_environ)",
        "            env_copy['PYTHONPATH'] = _p_boot",
        "            env_copy['PYTHONSAFEPATH'] = '1'",
        "            env_copy['PYTHONNOUSERSITE'] = '1'",
        "            env_copy['TMPDIR'] = _p_tmp",
        "            env_copy['TEMP'] = _p_tmp",
        "            env_copy['TMP'] = _p_tmp",
        "            if _b_len(p_args) > 10:",
        "                p_args = p_args[:10] + (env_copy,) + p_args[11:]",
        "            else:",
        "                kwargs['env'] = env_copy",
        "            _popen_active[0] += 1",
        "            try:",
        "                return _orig_p_init(self, *p_args, **kwargs)",
        "            finally:",
        "                _popen_active[0] -= 1",
        "        _guarded_popen_init.__defaults__ = None",
        "        _guarded_popen_init.__kwdefaults__ = None",
        "        return (_SealedMethod(_guarded_popen_init), _popen_active)",
        "",
        "    subprocess.Popen.__init__, _popen_active_cell = _setup_popen_guard(",
        "        _orig_popen_init, _b_str(_BOOT_DIR), _b_str(_tmp_real), _b_str(sys.executable)",
        "    )",
        "    del _setup_popen_guard",
        "",
        "    def _install_audit_hook(",
        "        _a_boot, _a_tmp, _a_ws, _a_stable, _a_allow_net,",
        "        _a_exec_child_code, _a_popen_active,",
        "    ):",
        "        import builtins",
        "        import os",
        "        import pathlib",
        "        import stat",
        "        import sys",
        "        import types",
        "        _b_isinstance = builtins.isinstance",
        "        _b_len = builtins.len",
        "        _b_getattr = builtins.getattr",
        "        _b_str = builtins.str",
        "        _b_bytes = builtins.bytes",
        "        _b_int = builtins.int",
        "        _b_bool = builtins.bool",
        "        _b_any = builtins.any",
        "        _b_tuple = builtins.tuple",
        "        _b_list = builtins.list",
        "        _b_dict = builtins.dict",
        "        _b_set = builtins.set",
        "        _b_type = builtins.type",
        "        _b_PermissionError = builtins.PermissionError",
        "        _b_OSError = builtins.OSError",
        "        _b_ValueError = builtins.ValueError",
        "        _FrameType = types.FrameType",
        "        _TracebackType = types.TracebackType",
        "        _GeneratorType = types.GeneratorType",
        "        _CoroutineType = types.CoroutineType",
        "        _AsyncGeneratorType = types.AsyncGeneratorType",
        "        _CodeType = types.CodeType",
        "        _fr_f_code = _FrameType.f_code.__get__",
        "        _fr_f_back = _FrameType.f_back.__get__",
        "        _co_filename = _CodeType.co_filename.__get__",
        "        _tb_frame_get = _TracebackType.tb_frame.__get__",
        "        _tb_next_get = _TracebackType.tb_next.__get__",
        "        _gi_frame_get = _GeneratorType.gi_frame.__get__",
        "        _cr_frame_get = _CoroutineType.cr_frame.__get__",
        "        _ag_frame_get = _AsyncGeneratorType.ag_frame.__get__",
        "        _FRAME_OBJ_TYPES = (",
        "            _FrameType,",
        "            _TracebackType,",
        "            _GeneratorType,",
        "            _CoroutineType,",
        "            _AsyncGeneratorType,",
        "        )",
        "        _c_getframe = sys._getframe",
        "        _PurePath = pathlib.PurePath",
        "        _purepath_fspath = pathlib.PurePath.__fspath__",
        "        _dict_get = builtins.dict.get",
        "        _is_win = (os.name == 'nt')",
        "        _native = __import__('nt' if _is_win else 'posix')",
        "        _c_lstat = _native.lstat",
        "        _c_getcwd = _native.getcwd",
        "        _c_readlink = _b_getattr(_native, 'readlink', None)",
        "        _c_final = _b_getattr(_native, '_getfinalpathname', None)",
        "        _c_fsdecode = os.fsdecode",
        "        _c_normcase = (lambda _s: _s.lower()) if _is_win else (lambda _s: _s)",
        "        _s_islnk = stat.S_ISLNK",
        "        _reparse_flag = _b_getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400)",
        "        _sep = '\\\\' if _is_win else '/'",
        "        _nulls = ('\\\\\\\\.\\\\nul', '/dev/null', 'nul')",
        "        _os_environ = os.environ",
        "        _WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND",
        "        _PROTECTED_ENV_VARS = (",
        "            'PYTHONPATH', 'PYTHONSAFEPATH', 'PYTHONNOUSERSITE',",
        "            'PYTHONHOME', 'PYTHONSTARTUP', 'TMPDIR', 'TEMP', 'TMP',",
        "        )",
        "        def _to_path_str(_p):",
        "            if _b_isinstance(_p, _b_str):",
        "                return _p",
        "            if _b_isinstance(_p, _b_bytes):",
        "                return _c_fsdecode(_p)",
        "            if _b_isinstance(_p, _PurePath):",
        "                _r = _purepath_fspath(_p)",
        "                if _b_isinstance(_r, _b_str):",
        "                    return _r",
        "            raise _b_PermissionError(",
        "                'WORKSPACE_WRITE_ISOLATION_BLOCKED: unsupported path type'",
        "            )",
        "        _to_path_str.__defaults__ = None",
        "        _to_path_str.__kwdefaults__ = None",
        "        def _in_tree(_norm_p, _root_norm, _root_prefix):",
        "            return _norm_p == _root_norm or _norm_p.startswith(_root_prefix)",
        "        _in_tree.__defaults__ = None",
        "        _in_tree.__kwdefaults__ = None",
        "        def _lex_norm(_raw_s):",
        "            if _is_win:",
        "                _s = _raw_s.replace('/', '\\\\')",
        "                _sl = _s.lower()",
        "                if _sl in ('\\\\\\\\.\\\\nul', 'nul'):",
        "                    return '\\\\\\\\.\\\\nul'",
        "                if _sl.startswith('\\\\\\\\?\\\\unc\\\\'):",
        "                    _s = '\\\\\\\\' + _s[8:]",
        "                elif _s.startswith('\\\\\\\\?\\\\') or _s.startswith('\\\\??\\\\'):",
        "                    _s = _s[4:]",
        "                if _s.startswith('\\\\\\\\'):",
        "                    pass",
        "                elif _b_len(_s) >= 2 and _s[1] == ':' and _s[0].isalpha():",
        "                    if _b_len(_s) < 3 or _s[2] != '\\\\':",
        "                        raise _b_PermissionError(",
        "                            'WORKSPACE_WRITE_ISOLATION_BLOCKED: '",
        "                            'drive-relative path prohibited'",
        "                        )",
        "                elif _s.startswith('\\\\'):",
        "                    raise _b_PermissionError(",
        "                        'WORKSPACE_WRITE_ISOLATION_BLOCKED: '",
        "                        'drive-relative path prohibited'",
        "                    )",
        "                else:",
        "                    _cwd = _c_getcwd()",
        "                    _s = _cwd + ('' if _cwd.endswith('\\\\') else '\\\\') + _s",
        "                if _s.startswith('\\\\\\\\'):",
        "                    _parts = _s[2:].split('\\\\')",
        "                    if _b_len(_parts) >= 2:",
        "                        _root = '\\\\\\\\' + _parts[0] + '\\\\' + _parts[1]",
        "                        _rest = _parts[2:]",
        "                    else:",
        "                        _root = '\\\\\\\\' + (_parts[0] if _parts else '')",
        "                        _rest = []",
        "                else:",
        "                    _root = _s[:2]",
        "                    _rest = _s[3:].split('\\\\')",
        "                _stack = []",
        "                for _part in _rest:",
        "                    if not _part or _part == '.':",
        "                        continue",
        "                    if _part == '..':",
        "                        if _stack:",
        "                            _stack.pop()",
        "                    else:",
        "                        _stack.append(_part)",
        "                if _root.startswith('\\\\\\\\'):",
        "                    _out = _root + ('\\\\' + '\\\\'.join(_stack) if _stack else '')",
        "                else:",
        "                    _out = _root + '\\\\' + '\\\\'.join(_stack)",
        "                return _out.lower()",
        "            if _raw_s == '/dev/null':",
        "                return '/dev/null'",
        "            _s = _raw_s",
        "            if not _s.startswith('/'):",
        "                _cwd = _c_getcwd()",
        "                _s = (_cwd if _cwd == '/' else _cwd.rstrip('/')) + '/' + _s",
        "            _stack = []",
        "            for _part in _s.split('/'):",
        "                if not _part or _part == '.':",
        "                    continue",
        "                if _part == '..':",
        "                    if _stack:",
        "                        _stack.pop()",
        "                else:",
        "                    _stack.append(_part)",
        "            return '/' + '/'.join(_stack)",
        "        _lex_norm.__defaults__ = None",
        "        _lex_norm.__kwdefaults__ = None",
        "        def _parent_of(_p):",
        "            if _is_win:",
        "                if _p.startswith('\\\\\\\\'):",
        "                    _idx = _p.rfind('\\\\')",
        "                    if _idx <= 1 or _p.count('\\\\') <= 3:",
        "                        return _p",
        "                    return _p[:_idx]",
        "                _idx = _p.rfind('\\\\')",
        "                if _idx < 0:",
        "                    return _p",
        "                if _idx == 2 and _b_len(_p) >= 3 and _p[1] == ':':",
        "                    return _p[:3]",
        "                return _p[:_idx]",
        "            _idx = _p.rfind('/')",
        "            if _idx < 0:",
        "                return _p",
        "            if _idx == 0:",
        "                return '/'",
        "            return _p[:_idx]",
        "        _parent_of.__defaults__ = None",
        "        _parent_of.__kwdefaults__ = None",
        "        def _resolve_link(_lp, _hops, _req_allow):",
        "            if _hops >= 16:",
        "                raise _b_PermissionError(",
        "                    f'WORKSPACE_WRITE_ISOLATION_BLOCKED: {_lp}'",
        "                )",
        "            _res_t = None",
        "            if _is_win and _c_final is not None:",
        "                try:",
        "                    _res_t = _lex_norm(_c_final(_lp))",
        "                except _b_OSError:",
        "                    _res_t = None",
        "            if _res_t is None:",
        "                if _c_readlink is None:",
        "                    raise _b_PermissionError(",
        "                        f'WORKSPACE_WRITE_ISOLATION_BLOCKED: {_lp}'",
        "                    )",
        "                try:",
        "                    _rl = _c_readlink(_lp)",
        "                except _b_OSError as _exc:",
        "                    raise _b_PermissionError(",
        "                        f'WORKSPACE_WRITE_ISOLATION_BLOCKED: {_lp}'",
        "                    ) from _exc",
        "                _rl_s = _rl.replace('/', _sep).replace('\\\\', _sep)",
        "                if _is_win:",
        "                    _is_abs = (",
        "                        _rl_s.startswith('\\\\\\\\')",
        "                        or _rl_s.startswith('\\\\??\\\\')",
        "                        or (",
        "                            _b_len(_rl_s) >= 3",
        "                            and _rl_s[0].isalpha()",
        "                            and _rl_s[1] == ':'",
        "                            and _rl_s[2] == '\\\\'",
        "                        )",
        "                    )",
        "                else:",
        "                    _is_abs = _rl_s.startswith('/')",
        "                if not _is_abs:",
        "                    _par = _parent_of(_lp)",
        "                    _rl_s = _par + ('' if _par.endswith(_sep) else _sep) + _rl_s",
        "                _res_t = _canon_impl(_rl_s, _hops + 1, _req_allow)",
        "            if _req_allow and not _is_allowed_path(_c_normcase(_lex_norm(_res_t))):",
        "                raise _b_PermissionError(",
        "                    f'WORKSPACE_WRITE_ISOLATION_BLOCKED: symlink escape {_lp}'",
        "                )",
        "            return _res_t",
        "        _resolve_link.__defaults__ = None",
        "        _resolve_link.__kwdefaults__ = None",
        "        def _canon_impl(_raw_p, _hops, _req_allow=False):",
        "            _s = _to_path_str(_raw_p)",
        "            if '\\x00' in _s:",
        "                raise _b_PermissionError(",
        "                    'WORKSPACE_WRITE_ISOLATION_BLOCKED: nul byte in path'",
        "                )",
        "            _sl_chk = _s.lower() if _is_win else _s",
        "            if _sl_chk in _nulls:",
        "                return '\\\\\\\\.\\\\nul' if _is_win else '/dev/null'",
        "            if _is_win:",
        "                _s = _s.replace('/', '\\\\')",
        "                _sl = _s.lower()",
        "                if _sl.startswith('\\\\\\\\?\\\\unc\\\\'):",
        "                    _s = '\\\\\\\\' + _s[8:]",
        "                elif _s.startswith('\\\\\\\\?\\\\') or _s.startswith('\\\\??\\\\'):",
        "                    _s = _s[4:]",
        "                if _s.startswith('\\\\\\\\'):",
        "                    pass",
        "                elif _b_len(_s) >= 2 and _s[1] == ':' and _s[0].isalpha():",
        "                    if _b_len(_s) < 3 or _s[2] != '\\\\':",
        "                        raise _b_PermissionError(",
        "                            'WORKSPACE_WRITE_ISOLATION_BLOCKED: '",
        "                            'drive-relative path prohibited'",
        "                        )",
        "                elif _s.startswith('\\\\'):",
        "                    raise _b_PermissionError(",
        "                        'WORKSPACE_WRITE_ISOLATION_BLOCKED: '",
        "                        'drive-relative path prohibited'",
        "                    )",
        "                else:",
        "                    _cwd = _c_getcwd()",
        "                    _s = _cwd + ('' if _cwd.endswith('\\\\') else '\\\\') + _s",
        "                if _s.startswith('\\\\\\\\'):",
        "                    _parts = _s[2:].split('\\\\')",
        "                    if _b_len(_parts) >= 2:",
        "                        _cur = '\\\\\\\\' + _parts[0] + '\\\\' + _parts[1]",
        "                        _tokens = _parts[2:]",
        "                    else:",
        "                        _cur = '\\\\\\\\' + (_parts[0] if _parts else '')",
        "                        _tokens = []",
        "                else:",
        "                    _cur = _s[:3]",
        "                    _tokens = _s[3:].split('\\\\')",
        "            else:",
        "                _s = _s.replace('\\\\', '/')",
        "                if not _s.startswith('/'):",
        "                    _cwd = _c_getcwd()",
        "                    _s = _cwd + ('' if _cwd.endswith('/') else '/') + _s",
        "                _cur = '/'",
        "                _tokens = _s.split('/')",
        "            for _tok in _tokens:",
        "                if not _tok or _tok == '.':",
        "                    continue",
        "                if _tok == '..':",
        "                    try:",
        "                        _st = _c_lstat(_cur)",
        "                    except _b_OSError:",
        "                        _st = None",
        "                    if _st is not None and (",
        "                        _s_islnk(_st.st_mode)",
        "                        or _b_bool(",
        "                            _b_getattr(_st, 'st_file_attributes', 0) & _reparse_flag",
        "                        )",
        "                    ):",
        "                        _cur = _resolve_link(_cur, _hops, _req_allow)",
        "                    _cur = _parent_of(_cur)",
        "                    continue",
        "                _cur = _cur + _tok if _cur.endswith(_sep) else _cur + _sep + _tok",
        "                try:",
        "                    _st = _c_lstat(_cur)",
        "                except _b_OSError:",
        "                    _st = None",
        "                if _st is not None:",
        "                    _is_lnk = _s_islnk(_st.st_mode) or _b_bool(",
        "                        _b_getattr(_st, 'st_file_attributes', 0) & _reparse_flag",
        "                    )",
        "                    if _is_lnk:",
        "                        _cur = _resolve_link(_cur, _hops, _req_allow)",
        "                    elif _is_win and _c_final is not None:",
        "                        try:",
        "                            _cur = _lex_norm(_c_final(_cur))",
        "                        except _b_OSError:",
        "                            pass",
        "            return _c_normcase(_lex_norm(_cur))",
        "        _canon_impl.__defaults__ = (False,)",
        "        _canon_impl.__kwdefaults__ = None",
        "        def _canon_existing(_raw_p, _req_allow=False):",
        "            return _canon_impl(_raw_p, 0, _req_allow)",
        "        _canon_existing.__defaults__ = (False,)",
        "        _canon_existing.__kwdefaults__ = None",
        "        _ws_norm = _canon_existing(_lex_norm(_a_ws))",
        "        _ws_prefix = _ws_norm if _ws_norm.endswith(_sep) else _ws_norm + _sep",
        "        _tmp_norm = _canon_existing(_lex_norm(_a_tmp))",
        "        _tmp_prefix = _tmp_norm if _tmp_norm.endswith(_sep) else _tmp_norm + _sep",
        "        _boot_norm = _canon_existing(_lex_norm(_a_boot))",
        "        _boot_prefix = _boot_norm if _boot_norm.endswith(_sep) else _boot_norm + _sep",
        "        _self_norm = _canon_existing(_lex_norm(_boot_norm + _sep + 'sitecustomize.py'))",
        "        _trusted_base_raw = (",
        "            _b_getattr(sys, '_base_executable', sys.executable) or sys.executable",
        "        )",
        "        _trusted_py_lex = _c_normcase(_lex_norm(sys.executable))",
        "        _trusted_base_lex = _c_normcase(_lex_norm(_trusted_base_raw))",
        "        def _collect_trusted_chain(_raw_p, _hops, _acc):",
        "            if _hops >= 16:",
        "                return",
        "            _s = _to_path_str(_raw_p)",
        "            if '\\x00' in _s:",
        "                return",
        "            _s_lex = _c_normcase(_lex_norm(_s))",
        "            if (",
        "                _in_tree(_s_lex, _ws_norm, _ws_prefix)",
        "                or _in_tree(_s_lex, _tmp_norm, _tmp_prefix)",
        "                or _in_tree(_s_lex, _boot_norm, _boot_prefix)",
        "            ):",
        "                return",
        "            _acc.add(_s_lex)",
        "            if _is_win:",
        "                _s = _s.replace('/', '\\\\')",
        "                _sl = _s.lower()",
        "                if _sl.startswith('\\\\\\\\?\\\\unc\\\\'):",
        "                    _s = '\\\\\\\\' + _s[8:]",
        "                elif _s.startswith('\\\\\\\\?\\\\') or _s.startswith('\\\\??\\\\'):",
        "                    _s = _s[4:]",
        "                if _s.startswith('\\\\\\\\'):",
        "                    pass",
        "                elif _b_len(_s) >= 2 and _s[1] == ':' and _s[0].isalpha():",
        "                    if _b_len(_s) < 3 or _s[2] != '\\\\':",
        "                        return",
        "                elif _s.startswith('\\\\'):",
        "                    return",
        "                else:",
        "                    _cwd = _c_getcwd()",
        "                    _s = _cwd + ('' if _cwd.endswith('\\\\') else '\\\\') + _s",
        "                if _s.startswith('\\\\\\\\'):",
        "                    _parts = _s[2:].split('\\\\')",
        "                    if _b_len(_parts) >= 2:",
        "                        _cur = '\\\\\\\\' + _parts[0] + '\\\\' + _parts[1]",
        "                        _tokens = _parts[2:]",
        "                    else:",
        "                        _cur = '\\\\\\\\' + (_parts[0] if _parts else '')",
        "                        _tokens = []",
        "                else:",
        "                    _cur = _s[:3]",
        "                    _tokens = _s[3:].split('\\\\')",
        "            else:",
        "                _s = _s.replace('\\\\', '/')",
        "                if not _s.startswith('/'):",
        "                    _cwd = _c_getcwd()",
        "                    _s = _cwd + ('' if _cwd.endswith('/') else '/') + _s",
        "                _cur = '/'",
        "                _tokens = _s.split('/')",
        "            _cur_norm = _c_normcase(_lex_norm(_cur))",
        "            if (",
        "                _in_tree(_cur_norm, _ws_norm, _ws_prefix)",
        "                or _in_tree(_cur_norm, _tmp_norm, _tmp_prefix)",
        "                or _in_tree(_cur_norm, _boot_norm, _boot_prefix)",
        "            ):",
        "                return",
        "            for _tok in _tokens:",
        "                if not _tok or _tok == '.':",
        "                    continue",
        "                if _tok == '..':",
        "                    try:",
        "                        _st = _c_lstat(_cur)",
        "                    except _b_OSError:",
        "                        _st = None",
        "                    if _st is not None and (",
        "                        _s_islnk(_st.st_mode)",
        "                        or _b_bool(",
        "                            _b_getattr(_st, 'st_file_attributes', 0) & _reparse_flag",
        "                        )",
        "                    ):",
        "                        try:",
        "                            _cur = _resolve_link(_cur, _hops, False)",
        "                        except _b_PermissionError:",
        "                            return",
        "                        _collect_trusted_chain(_cur, _hops + 1, _acc)",
        "                    _cur = _parent_of(_cur)",
        "                    continue",
        "                _cur = _cur + _tok if _cur.endswith(_sep) else _cur + _sep + _tok",
        "                _cur_norm = _c_normcase(_lex_norm(_cur))",
        "                if (",
        "                    _in_tree(_cur_norm, _ws_norm, _ws_prefix)",
        "                    or _in_tree(_cur_norm, _tmp_norm, _tmp_prefix)",
        "                    or _in_tree(_cur_norm, _boot_norm, _boot_prefix)",
        "                ):",
        "                    return",
        "                try:",
        "                    _st = _c_lstat(_cur)",
        "                except _b_OSError:",
        "                    _st = None",
        "                if _st is not None and (",
        "                    _s_islnk(_st.st_mode)",
        "                    or _b_bool(",
        "                        _b_getattr(_st, 'st_file_attributes', 0) & _reparse_flag",
        "                    )",
        "                ):",
        "                    _acc.add(_cur_norm)",
        "                    _tgt = None",
        "                    if _c_readlink is not None:",
        "                        try:",
        "                            _rl = _c_readlink(_cur)",
        "                            _rl_s = _rl.replace('/', _sep).replace('\\\\', _sep)",
        "                            if _is_win:",
        "                                _is_abs = (",
        "                                    _rl_s.startswith('\\\\\\\\')",
        "                                    or _rl_s.startswith('\\\\??\\\\')",
        "                                    or (",
        "                                        _b_len(_rl_s) >= 3",
        "                                        and _rl_s[0].isalpha()",
        "                                        and _rl_s[1] == ':'",
        "                                        and _rl_s[2] == '\\\\'",
        "                                    )",
        "                                )",
        "                            else:",
        "                                _is_abs = _rl_s.startswith('/')",
        "                            if not _is_abs:",
        "                                _par = _parent_of(_cur)",
        "                                _psep = '' if _par.endswith(_sep) else _sep",
        "                                _rl_s = _par + _psep + _rl_s",
        "                            _tgt = _rl_s",
        "                        except _b_OSError:",
        "                            return",
        "                    elif _is_win and _c_final is not None:",
        "                        try:",
        "                            _tgt = _lex_norm(_c_final(_cur))",
        "                        except _b_OSError:",
        "                            return",
        "                    else:",
        "                        return",
        "                    _collect_trusted_chain(_tgt, _hops + 1, _acc)",
        "                    try:",
        "                        _cur = _canon_impl(_tgt, _hops + 1, False)",
        "                    except _b_PermissionError:",
        "                        return",
        "            _acc.add(_c_normcase(_lex_norm(_cur)))",
        "        _collect_trusted_chain.__defaults__ = None",
        "        _collect_trusted_chain.__kwdefaults__ = None",
        "        def _trusted_exe_variants():",
        "            _out = _b_set()",
        "            for _seed in (sys.executable, _trusted_base_raw):",
        "                if not _seed:",
        "                    continue",
        "                _collect_trusted_chain(_seed, 0, _out)",
        "                _s_dir = _parent_of(_c_normcase(_lex_norm(_to_path_str(_seed))))",
        "                for _bn in ('python', 'python3', f'python3.{sys.version_info[1]}'):",
        "                    _cand_p = (",
        "                        _s_dir + _bn if _s_dir.endswith(_sep) else _s_dir + _sep + _bn",
        "                    )",
        "                    _collect_trusted_chain(_cand_p, 0, _out)",
        "            return _out",
        "        _trusted_chain_lexes = _trusted_exe_variants()",
        "        def _has_untrusted_component(_raw_p, _hops):",
        "            if _hops >= 16:",
        "                return True",
        "            _s = _to_path_str(_raw_p)",
        "            if '\\x00' in _s:",
        "                return True",
        "            _sl_chk = _s.lower() if _is_win else _s",
        "            if _sl_chk in _nulls:",
        "                return False",
        "            if _is_win:",
        "                _s = _s.replace('/', '\\\\')",
        "                _sl = _s.lower()",
        "                if _sl.startswith('\\\\\\\\?\\\\unc\\\\'):",
        "                    _s = '\\\\\\\\' + _s[8:]",
        "                elif _s.startswith('\\\\\\\\?\\\\') or _s.startswith('\\\\??\\\\'):",
        "                    _s = _s[4:]",
        "                if _s.startswith('\\\\\\\\'):",
        "                    pass",
        "                elif _b_len(_s) >= 2 and _s[1] == ':' and _s[0].isalpha():",
        "                    if _b_len(_s) < 3 or _s[2] != '\\\\':",
        "                        return True",
        "                elif _s.startswith('\\\\'):",
        "                    return True",
        "                else:",
        "                    _cwd = _c_getcwd()",
        "                    _s = _cwd + ('' if _cwd.endswith('\\\\') else '\\\\') + _s",
        "                if _s.startswith('\\\\\\\\'):",
        "                    _parts = _s[2:].split('\\\\')",
        "                    if _b_len(_parts) >= 2:",
        "                        _cur = '\\\\\\\\' + _parts[0] + '\\\\' + _parts[1]",
        "                        _tokens = _parts[2:]",
        "                    else:",
        "                        _cur = '\\\\\\\\' + (_parts[0] if _parts else '')",
        "                        _tokens = []",
        "                else:",
        "                    _cur = _s[:3]",
        "                    _tokens = _s[3:].split('\\\\')",
        "            else:",
        "                _s = _s.replace('\\\\', '/')",
        "                if not _s.startswith('/'):",
        "                    _cwd = _c_getcwd()",
        "                    _s = _cwd + ('' if _cwd.endswith('/') else '/') + _s",
        "                _cur = '/'",
        "                _tokens = _s.split('/')",
        "            _cur_norm = _c_normcase(_lex_norm(_cur))",
        "            if (",
        "                _in_tree(_cur_norm, _ws_norm, _ws_prefix)",
        "                or _in_tree(_cur_norm, _tmp_norm, _tmp_prefix)",
        "                or _in_tree(_cur_norm, _boot_norm, _boot_prefix)",
        "            ):",
        "                return True",
        "            for _tok in _tokens:",
        "                if not _tok or _tok == '.':",
        "                    continue",
        "                if _tok == '..':",
        "                    try:",
        "                        _st = _c_lstat(_cur)",
        "                    except _b_OSError:",
        "                        _st = None",
        "                    if _st is not None and (",
        "                        _s_islnk(_st.st_mode)",
        "                        or _b_bool(",
        "                            _b_getattr(_st, 'st_file_attributes', 0) & _reparse_flag",
        "                        )",
        "                    ):",
        "                        try:",
        "                            _cur = _resolve_link(_cur, _hops, False)",
        "                        except _b_PermissionError:",
        "                            return True",
        "                        if _has_untrusted_component(_cur, _hops + 1):",
        "                            return True",
        "                    _cur = _parent_of(_cur)",
        "                    continue",
        "                _cur = _cur + _tok if _cur.endswith(_sep) else _cur + _sep + _tok",
        "                _cur_norm = _c_normcase(_lex_norm(_cur))",
        "                if (",
        "                    _in_tree(_cur_norm, _ws_norm, _ws_prefix)",
        "                    or _in_tree(_cur_norm, _tmp_norm, _tmp_prefix)",
        "                    or _in_tree(_cur_norm, _boot_norm, _boot_prefix)",
        "                ):",
        "                    return True",
        "                try:",
        "                    _st = _c_lstat(_cur)",
        "                except _b_OSError:",
        "                    _st = None",
        "                if _st is not None and (",
        "                    _s_islnk(_st.st_mode)",
        "                    or _b_bool(",
        "                        _b_getattr(_st, 'st_file_attributes', 0) & _reparse_flag",
        "                    )",
        "                ):",
        "                    _cur_prefix = (",
        "                        _cur_norm if _cur_norm.endswith(_sep) else _cur_norm + _sep",
        "                    )",
        "                    _in_trusted_chain = False",
        "                    for _tl in _trusted_chain_lexes:",
        "                        if _in_tree(_tl, _cur_norm, _cur_prefix):",
        "                            _in_trusted_chain = True",
        "                            break",
        "                    if not _in_trusted_chain:",
        "                        return True",
        "                    try:",
        "                        _cur = _resolve_link(_cur, _hops, False)",
        "                    except _b_PermissionError:",
        "                        return True",
        "                    if (",
        "                        _in_tree(_cur, _ws_norm, _ws_prefix)",
        "                        or _in_tree(_cur, _tmp_norm, _tmp_prefix)",
        "                        or _in_tree(_cur, _boot_norm, _boot_prefix)",
        "                    ):",
        "                        return True",
        "            return False",
        "        _has_untrusted_component.__defaults__ = None",
        "        _has_untrusted_component.__kwdefaults__ = None",
        "        _trusted_py_exe = _canon_existing(_lex_norm(sys.executable))",
        "        _trusted_base_exe = _canon_existing(_lex_norm(_trusted_base_raw))",
        "        _trusted_exes = (_trusted_py_exe, _trusted_base_exe)",
        "        _trusted_all_lexes = _b_tuple(_trusted_chain_lexes) + (",
        "            _trusted_py_lex,",
        "            _trusted_base_lex,",
        "            _trusted_py_exe,",
        "            _trusted_base_exe,",
        "        )",
        "        _trusted_exes_ok = (",
        "            not _has_untrusted_component(sys.executable, 0)",
        "            and not _has_untrusted_component(_trusted_base_raw, 0)",
        "        )",
        "        def _is_trusted_py(_np, _raw_p):",
        "            if not _trusted_exes_ok or _np not in _trusted_exes:",
        "                return False",
        "            if (",
        "                _in_tree(_np, _ws_norm, _ws_prefix)",
        "                or _in_tree(_np, _tmp_norm, _tmp_prefix)",
        "                or _in_tree(_np, _boot_norm, _boot_prefix)",
        "            ):",
        "                return False",
        "            if _raw_p is None:",
        "                return False",
        "            _raw_lex = _c_normcase(_lex_norm(_to_path_str(_raw_p)))",
        "            if (",
        "                _raw_lex not in _trusted_all_lexes",
        "                or _in_tree(_raw_lex, _ws_norm, _ws_prefix)",
        "                or _in_tree(_raw_lex, _tmp_norm, _tmp_prefix)",
        "                or _in_tree(_raw_lex, _boot_norm, _boot_prefix)",
        "                or _has_untrusted_component(_raw_p, 0)",
        "            ):",
        "                return False",
        "            return True",
        "        _is_trusted_py.__defaults__ = None",
        "        _is_trusted_py.__kwdefaults__ = None",
        "        _BARE_PY = ('python', 'python3', 'python.exe', 'python3.exe')",
        "        def _is_allowed_path(_np):",
        "            if _np in _nulls:",
        "                return True",
        "            if _np == _self_norm or (",
        "                _in_tree(_np, _boot_norm, _boot_prefix)",
        "                and not _in_tree(_np, _tmp_norm, _tmp_prefix)",
        "            ):",
        "                return False",
        "            return (",
        "                _in_tree(_np, _ws_norm, _ws_prefix)",
        "                or _in_tree(_np, _tmp_norm, _tmp_prefix)",
        "            )",
        "        _is_allowed_path.__defaults__ = None",
        "        _is_allowed_path.__kwdefaults__ = None",
        "        _in_slot_read = [False]",
        "        _popen_admitted = [False]",
        "        _hook_depth = [0]",
        "        _protected_codes = _b_set()",
        "        _frame_attrs = (",
        "            'tb_frame', 'tb_next', 'f_back', 'f_locals', 'f_code',",
        "            'gi_frame', 'cr_frame', 'ag_frame',",
        "        )",
        "        def _check_audit(event, args):",
        "            if event == 'sys.addaudithook':",
        "                return 'WORKSPACE_WRITE_ISOLATION_BLOCKED: sys.addaudithook prohibited'",
        "            if (",
        "                event == 'object.__getattr__'",
        "                and not _in_slot_read[0]",
        "                and _b_len(args) >= 2",
        "                and args[1] in _frame_attrs",
        "            ):",
        "                _obj = args[0]",
        "                _obj_t = _b_type(_obj)",
        "                if _obj_t not in _FRAME_OBJ_TYPES:",
        "                    return (",
        "                        'WORKSPACE_WRITE_ISOLATION_BLOCKED: '",
        "                        'synthetic object.__getattr__ prohibited'",
        "                    )",
        "                _in_slot_read[0] = True",
        "                try:",
        "                    _attr_n = args[1]",
        "                    if _obj_t is _FrameType:",
        "                        if _attr_n in ('f_locals', 'f_code'):",
        "                            if _fr_f_code(_obj) in _protected_codes:",
        "                                return (",
        "                                    'WORKSPACE_WRITE_ISOLATION_BLOCKED: '",
        "                                    'traceback frame access prohibited'",
        "                                )",
        "                        elif _attr_n == 'f_back':",
        "                            if _fr_f_code(_obj) in _protected_codes:",
        "                                return (",
        "                                    'WORKSPACE_WRITE_ISOLATION_BLOCKED: '",
        "                                    'traceback frame access prohibited'",
        "                                )",
        "                            _bk = _fr_f_back(_obj)",
        "                            if _bk is not None and _fr_f_code(_bk) in _protected_codes:",
        "                                return (",
        "                                    'WORKSPACE_WRITE_ISOLATION_BLOCKED: '",
        "                                    'traceback frame access prohibited'",
        "                                )",
        "                    elif _obj_t is _TracebackType:",
        "                        if _attr_n == 'tb_frame':",
        "                            _fr_obj = _tb_frame_get(_obj)",
        "                            if (",
        "                                _fr_obj is not None",
        "                                and _fr_f_code(_fr_obj) in _protected_codes",
        "                            ):",
        "                                return (",
        "                                    'WORKSPACE_WRITE_ISOLATION_BLOCKED: '",
        "                                    'traceback frame access prohibited'",
        "                                )",
        "                        elif _attr_n == 'tb_next':",
        "                            _nxt = _tb_next_get(_obj)",
        "                            if _nxt is not None:",
        "                                _fr_obj = _tb_frame_get(_nxt)",
        "                                if (",
        "                                    _fr_obj is not None",
        "                                    and _fr_f_code(_fr_obj) in _protected_codes",
        "                                ):",
        "                                    return (",
        "                                        'WORKSPACE_WRITE_ISOLATION_BLOCKED: '",
        "                                        'traceback frame access prohibited'",
        "                                    )",
        "                    elif _obj_t is _GeneratorType and _attr_n == 'gi_frame':",
        "                        _fr_obj = _gi_frame_get(_obj)",
        "                        if (",
        "                            _fr_obj is not None",
        "                            and _fr_f_code(_fr_obj) in _protected_codes",
        "                        ):",
        "                            return (",
        "                                'WORKSPACE_WRITE_ISOLATION_BLOCKED: '",
        "                                'traceback frame access prohibited'",
        "                            )",
        "                    elif _obj_t is _CoroutineType and _attr_n == 'cr_frame':",
        "                        _fr_obj = _cr_frame_get(_obj)",
        "                        if (",
        "                            _fr_obj is not None",
        "                            and _fr_f_code(_fr_obj) in _protected_codes",
        "                        ):",
        "                            return (",
        "                                'WORKSPACE_WRITE_ISOLATION_BLOCKED: '",
        "                                'traceback frame access prohibited'",
        "                            )",
        "                    elif _obj_t is _AsyncGeneratorType and _attr_n == 'ag_frame':",
        "                        _fr_obj = _ag_frame_get(_obj)",
        "                        if (",
        "                            _fr_obj is not None",
        "                            and _fr_f_code(_fr_obj) in _protected_codes",
        "                        ):",
        "                            return (",
        "                                'WORKSPACE_WRITE_ISOLATION_BLOCKED: '",
        "                                'traceback frame access prohibited'",
        "                            )",
        "                finally:",
        "                    _in_slot_read[0] = False",
        "                return None",
        "            if event in ('gc.get_objects', 'gc.get_referrers', 'gc.get_referents'):",
        "                return 'WORKSPACE_WRITE_ISOLATION_BLOCKED: gc introspection'",
        "            if not _a_allow_net and event in (",
        "                'socket.connect', 'socket.bind', 'socket.sendto',",
        "                'socket.sendmsg', 'socket.getaddrinfo', 'socket.gethostbyname',",
        "            ):",
        "                return 'NETWORK_ISOLATION_BLOCKED: external network disabled'",
        "            if event in ('os.system', 'os.spawn', 'os.exec', 'os.posix_spawn'):",
        "                return f'SUBPROCESS_SANDBOX_BYPASS_BLOCKED: {event} prohibited'",
        "            if event in ('os.putenv', 'os.unsetenv') and args:",
        "                _env_k = (",
        "                    _c_fsdecode(args[0])",
        "                    if _b_isinstance(args[0], (_b_str, _b_bytes))",
        "                    else _b_str(args[0])",
        "                )",
        "                _env_chk = _env_k.upper() if _is_win else _env_k",
        "                if _env_chk in _PROTECTED_ENV_VARS:",
        "                    return (",
        "                        f'SUBPROCESS_SANDBOX_BYPASS_BLOCKED: {event} {_env_k} prohibited'",
        "                    )",
        "            if event in ('ctypes.dlopen', 'ctypes.dlsym'):",
        "                return f'CTYPES_SANDBOX_BLOCKED: {event} prohibited'",
        "            if event == 'sys._getframe':",
        "                if _in_slot_read[0]:",
        "                    return None",
        "                if _hook_depth[0] > 1:",
        "                    return 'WORKSPACE_WRITE_ISOLATION_BLOCKED: _getframe'",
        "                _fr = args[0] if args else None",
        "                if _b_type(_fr) is not _FrameType:",
        "                    return 'WORKSPACE_WRITE_ISOLATION_BLOCKED: _getframe'",
        "                _fc = _fr_f_code(_fr)",
        "                if _fc in _protected_codes:",
        "                    return 'WORKSPACE_WRITE_ISOLATION_BLOCKED: _getframe'",
        "                _fn = _co_filename(_fc)",
        "                if _fn in ('<string>', '<stdin>', ''):",
        "                    return 'WORKSPACE_WRITE_ISOLATION_BLOCKED: _getframe'",
        "                _deny_frame = False",
        "                try:",
        "                    _fn_norm = _canon_existing(_fn)",
        "                    _deny_frame = (",
        "                        (",
        "                            _in_tree(_fn_norm, _boot_norm, _boot_prefix)",
        "                            and not _in_tree(_fn_norm, _tmp_norm, _tmp_prefix)",
        "                        )",
        "                        or _in_tree(_fn_norm, _ws_norm, _ws_prefix)",
        "                    )",
        "                except _b_PermissionError as _exc:",
        "                    return _b_str(_exc)",
        "                except _b_OSError:",
        "                    _deny_frame = False",
        "                if _deny_frame:",
        "                    return 'WORKSPACE_WRITE_ISOLATION_BLOCKED: _getframe'",
        "            if event == 'subprocess.Popen' and _b_len(args) >= 2:",
        "                _exe_target, _cmd_arg = args[0], args[1]",
        "                try:",
        "                    if _b_isinstance(_cmd_arg, (_b_str, _b_bytes, _PurePath)):",
        "                        _cs = _to_path_str(_cmd_arg).lstrip()",
        "                        if _cs.startswith('\"'):",
        "                            _qe = _cs.find('\"', 1)",
        "                            _argv = (",
        "                                [_cs[1:_qe]] + _cs[_qe + 1 :].split()",
        "                                if _qe > 0",
        "                                else [_cs]",
        "                            )",
        "                        else:",
        "                            _argv = _cs.split()",
        "                    elif _cmd_arg is not None:",
        "                        _argv = [_to_path_str(_tok) for _tok in _cmd_arg]",
        "                    else:",
        "                        _argv = []",
        "                    _eff_exe = _exe_target if _exe_target is not None else (",
        "                        _argv[0] if _argv else None",
        "                    )",
        "                    if not _argv or not _eff_exe:",
        "                        return (",
        "                            'SUBPROCESS_SANDBOX_BYPASS_BLOCKED: '",
        "                            'non-Python or unguarded child process prohibited'",
        "                        )",
        "                    _eff_s = _to_path_str(_eff_exe)",
        "                    if _sep in _eff_s or '/' in _eff_s:",
        "                        _eff_norm = _canon_existing(_eff_s)",
        "                        _blocked = not _is_trusted_py(_eff_norm, _eff_s)",
        "                    else:",
        "                        _blocked = True",
        "                    if not _blocked and _exe_target is not None:",
        "                        _a0_s = _to_path_str(_argv[0])",
        "                        if _sep in _a0_s or '/' in _a0_s:",
        "                            _a0_norm = _canon_existing(_a0_s)",
        "                            if not _is_trusted_py(_a0_norm, _a0_s):",
        "                                _blocked = True",
        "                        elif _a0_s.lower() not in _BARE_PY:",
        "                            _blocked = True",
        "                except _b_PermissionError as _exc:",
        "                    return _b_str(_exc)",
        "                if not _blocked:",
        "                    for _tok in _argv[1:]:",
        "                        if _tok in ('-c', '-m') or not _tok.startswith('-'):",
        "                            break",
        "                        if _tok == '--isolated' or (",
        "                            _tok.startswith('-')",
        "                            and not _tok.startswith('--')",
        "                            and _b_any(_ch in _tok[1:] for _ch in ('S', 'I', 'E'))",
        "                        ):",
        "                            _blocked = True",
        "                            break",
        "                if _blocked:",
        "                    return (",
        "                        'SUBPROCESS_SANDBOX_BYPASS_BLOCKED: '",
        "                        'non-Python or unguarded child process prohibited'",
        "                    )",
        "                _p_env = args[3] if _b_len(args) >= 4 else None",
        "                _eff_env = _os_environ if _p_env is None else _p_env",
        "                _is_raw_dict = (_b_type(_eff_env) is _b_dict)",
        "                if (",
        "                    not (_is_raw_dict or _eff_env is _os_environ)",
        "                    or (",
        "                        _dict_get(_eff_env, 'PYTHONPATH')",
        "                        if _is_raw_dict",
        "                        else _eff_env.get('PYTHONPATH')",
        "                    ) != _a_boot",
        "                    or (",
        "                        _dict_get(_eff_env, 'TMPDIR')",
        "                        if _is_raw_dict",
        "                        else _eff_env.get('TMPDIR')",
        "                    ) != _a_tmp",
        "                ):",
        "                    return (",
        "                        'SUBPROCESS_SANDBOX_BYPASS_BLOCKED: '",
        "                        'untrusted child environment'",
        "                    )",
        "                if _a_popen_active[0] > 0:",
        "                    _in_slot_read[0] = True",
        "                    try:",
        "                        for _d in (1, 2, 3, 4):",
        "                            _caller_fr = _c_getframe(_d)",
        "                            if (",
        "                                _b_type(_caller_fr) is _FrameType",
        "                                and _fr_f_code(_caller_fr) is _a_exec_child_code",
        "                            ):",
        "                                _popen_admitted[0] = True",
        "                                break",
        "                    except (_b_ValueError, _b_PermissionError):",
        "                        pass",
        "                    finally:",
        "                        _in_slot_read[0] = False",
        "                return None",
        "            if event == '_winapi.CreateProcess':",
        "                _was_admitted = _popen_admitted[0]",
        "                _popen_admitted[0] = False",
        "                if (",
        "                    not _was_admitted",
        "                    or _a_popen_active[0] <= 0",
        "                    or _b_len(args) < 2",
        "                ):",
        "                    return (",
        "                        'SUBPROCESS_SANDBOX_BYPASS_BLOCKED: '",
        "                        '_winapi.CreateProcess prohibited'",
        "                    )",
        "                return None",
        "            _paths_to_check = ()",
        "            if event == 'open' and _b_len(args) >= 2:",
        "                mode_arg = args[1]",
        "                flags_arg = args[2] if _b_len(args) >= 3 else 0",
        "                if not (",
        "                    (mode_arg is None or _b_isinstance(mode_arg, _b_str))",
        "                    and _b_isinstance(flags_arg, _b_int)",
        "                ):",
        "                    return 'WORKSPACE_WRITE_ISOLATION_BLOCKED: invalid open mode/flags'",
        "                is_write = (",
        "                    (",
        "                        _b_isinstance(mode_arg, _b_str)",
        "                        and _b_any(c in mode_arg for c in ('w', 'a', 'x', '+'))",
        "                    )",
        "                    or _b_bool(flags_arg & _WRITE_FLAGS)",
        "                )",
        "                if _b_isinstance(args[0], _b_int):",
        "                    if is_write and args[0] > 2:",
        "                        return (",
        "                            'WORKSPACE_WRITE_ISOLATION_BLOCKED: '",
        "                            'raw file descriptor path prohibited'",
        "                        )",
        "                    return None",
        "                if is_write:",
        "                    _paths_to_check = (args[0],)",
        "            elif event in (",
        "                'os.remove', 'os.unlink', 'os.mkdir', 'os.rmdir',",
        "                'os.chmod', 'os.truncate', 'os.chown', 'os.utime', 'shutil.rmtree',",
        "            ):",
        "                if args:",
        "                    _paths_to_check = (args[0],)",
        "            elif event in (",
        "                'os.rename', 'os.replace', 'os.link', 'os.symlink',",
        "                'shutil.copyfile', 'shutil.move',",
        "            ):",
        "                if _b_len(args) >= 2:",
        "                    _paths_to_check = (args[0], args[1])",
        "            for _p in _paths_to_check:",
        "                if _b_isinstance(_p, _b_int):",
        "                    return (",
        "                        'WORKSPACE_WRITE_ISOLATION_BLOCKED: '",
        "                        'raw file descriptor path prohibited'",
        "                    )",
        "                try:",
        "                    _raw_str = _to_path_str(_p)",
        "                    _target = _canon_existing(_raw_str, True)",
        "                except _b_PermissionError as _exc:",
        "                    return _b_str(_exc)",
        "                if _target in _nulls:",
        "                    continue",
        "                if not _is_allowed_path(_target):",
        "                    return f'WORKSPACE_WRITE_ISOLATION_BLOCKED: {_target}'",
        "            return None",
        "        def _audit_hook(event, args):",
        "            _hook_depth[0] += 1",
        "            try:",
        "                _err = _check_audit(event, args)",
        "                if _err is not None:",
        "                    raise _b_PermissionError(_err)",
        "            finally:",
        "                _hook_depth[0] -= 1",
        "        _protected_codes.update((",
        "            _check_audit.__code__,",
        "            _audit_hook.__code__,",
        "            _canon_impl.__code__,",
        "            _canon_existing.__code__,",
        "            _is_allowed_path.__code__,",
        "            _is_trusted_py.__code__,",
        "            _has_untrusted_component.__code__,",
        "            _in_tree.__code__,",
        "        ))",
        "        _check_audit.__defaults__ = None",
        "        _check_audit.__kwdefaults__ = None",
        "        _audit_hook.__defaults__ = None",
        "        _audit_hook.__kwdefaults__ = None",
        "        sys.addaudithook(_audit_hook)",
        "        def _make_blocked_addaudithook(_pe):",
        "            def _blocked_addaudithook(*_a, **_kw):",
        "                raise _pe(",
        "                    'WORKSPACE_WRITE_ISOLATION_BLOCKED: sys.addaudithook prohibited'",
        "                )",
        "            _blocked_addaudithook.__defaults__ = None",
        "            _blocked_addaudithook.__kwdefaults__ = None",
        "            return _blocked_addaudithook",
        "        sys.addaudithook = _make_blocked_addaudithook(_b_PermissionError)",
        "        del _make_blocked_addaudithook",
        "        del _audit_hook",
        "",
        "    _install_audit_hook(",
        "        _b_str(_BOOT_DIR),",
        "        _b_str(_tmp_real),",
        "        _b_str(_ws_norm),",
        "        _b_str(_STABLE_STR),",
        "        _b_bool(_ALLOW_NETWORK),",
        "        subprocess.Popen._execute_child.__code__,",
        "        _popen_active_cell,",
        "    )",
        "    del _install_audit_hook",
        "    del _popen_active_cell",
        "",
        "    def _is_file_native(_fp):",
        "        try:",
        "            return _s_isreg(_c_lstat(_fp).st_mode)",
        "        except _b_OSError:",
        "            return False",
        "    def _is_dir_native(_dp):",
        "        try:",
        "            return _s_isdir(_c_lstat(_dp).st_mode)",
        "        except _b_OSError:",
        "            return False",
        f"    _ws_src_real = _canon_existing(_lex_norm({ws_str!r} + _sep + 'src'))",
        "    _ws_src_norm = _ws_src_real",
        "    _ws_src_prefix = (",
        "        _ws_src_norm if _ws_src_norm.endswith(_sep) else _ws_src_norm + _sep",
        "    )",
        "    _stable_norm = _canon_existing(_lex_norm(_STABLE_STR)) if _STABLE_STR else ''",
        "    _stable_prefix = (",
        "        _stable_norm if _stable_norm.endswith(_sep) else _stable_norm + _sep",
        "    ) if _stable_norm else ''",
        "    _stable_venv_norm = (",
        "        _canon_existing(_lex_norm(_stable_norm + _sep + '.venv')) if _stable_norm else ''",
        "    )",
        "    _stable_venv_prefix = (",
        "        _stable_venv_norm",
        "        if _stable_venv_norm.endswith(_sep)",
        "        else _stable_venv_norm + _sep",
        "    ) if _stable_venv_norm else ''",
        "    _HAS_WS_AIPC = _is_file_native(_ws_src_real + _sep + 'aipc' + _sep + '__init__.py')",
        "    if _stable_norm:",
        "        _cleaned = []",
        "        for _entry in _b_list(sys.path):",
        "            if not _entry:",
        "                continue",
        "            try:",
        "                _p = _canon_existing(_lex_norm(_entry))",
        "                if not _in_tree(",
        "                    _p, _stable_venv_norm, _stable_venv_prefix",
        "                ) and _in_tree(_p, _stable_norm, _stable_prefix):",
        "                    continue",
        "            except (_b_OSError, _b_PermissionError):",
        "                pass",
        "            _cleaned.append(_entry)",
        "        if _is_dir_native(_ws_src_real):",
        "            sys.path[:] = [_ws_src_real] + _cleaned",
        "        else:",
        "            sys.path[:] = _cleaned",
        "    elif _HAS_WS_AIPC:",
        "        sys.path[:] = [_ws_src_real] + [p for p in sys.path if p != _ws_src_real]",
        "",
        "    if _stable_norm or _HAS_WS_AIPC:",
        "        class _StableAipcBlocker:",
        "            def find_spec(self, fullname, path=None, target=None):",
        "                if fullname != 'aipc' and not fullname.startswith('aipc.'):",
        "                    return None",
        "                import importlib.machinery as _im",
        "                import os as _os",
        "                spec = _im.PathFinder.find_spec(fullname, path)",
        "                if spec is not None and spec.origin:",
        "                    origin_p = _os.path.normcase(_os.path.realpath(spec.origin))",
        f"                    stable_raw = {stable_str!r}",
        f"                    ws_raw = {ws_str!r}",
        "                    if stable_raw:",
        "                        stable_p = _os.path.normcase(_os.path.realpath(stable_raw))",
        "                        stable_pfx = (",
        "                            stable_p if stable_p.endswith(_os.sep)",
        "                            else stable_p + _os.sep",
        "                        )",
        "                        venv_p = _os.path.normcase(",
        "                            _os.path.realpath(_os.path.join(stable_p, '.venv'))",
        "                        )",
        "                        venv_pfx = (",
        "                            venv_p if venv_p.endswith(_os.sep) else venv_p + _os.sep",
        "                        )",
        "                        in_stable = (",
        "                            origin_p == stable_p or origin_p.startswith(stable_pfx)",
        "                        )",
        "                        in_venv = (",
        "                            origin_p == venv_p or origin_p.startswith(venv_pfx)",
        "                        )",
        "                        if in_stable and not in_venv:",
        "                            raise ImportError(",
        "                                'IMPORT_ORIGIN_MISMATCH: '",
        "                                f'blocked {fullname} from {origin_p}'",
        "                            )",
        "                    ws_src_p = _os.path.normcase(",
        "                        _os.path.realpath(_os.path.join(ws_raw, 'src'))",
        "                    )",
        "                    ws_src_pfx = (",
        "                        ws_src_p if ws_src_p.endswith(_os.sep)",
        "                        else ws_src_p + _os.sep",
        "                    )",
        "                    has_ws = _os.path.isfile(",
        "                        _os.path.join(ws_src_p, 'aipc', '__init__.py')",
        "                    )",
        "                    if (",
        "                        has_ws",
        "                        and origin_p != ws_src_p",
        "                        and not origin_p.startswith(ws_src_pfx)",
        "                    ):",
        "                        raise ImportError(",
        "                            f'IMPORT_ORIGIN_MISMATCH: {fullname} outside {ws_src_p}'",
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
        required_commands: tuple[tuple[str, ...], ...] = (),
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

        if required_commands:
            for rcmd in required_commands:
                if rcmd not in test_commands and rcmd not in extra_commands:
                    if any("pytest" in tok or "test" in tok for tok in rcmd):
                        test_commands.append(rcmd)
                    else:
                        extra_commands.append(rcmd)
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

        workspace_path = Path(bundle.workspace_root).resolve()

        if effective_mode == "isolated_subprocess":
            for cmd in commands_to_run:
                if (
                    _validate_isolated_python_command(cmd, workspace_path=workspace_path)
                    is not None
                ):
                    return _fail_receipt("ISOLATED_SUBPROCESS_COMMAND_GUARD_BYPASS_REJECTED")
        if verify_git_head and not _verify_candidate_git_worktree(
            workspace_path, candidate_sha, sanitized_env
        ):
            return _fail_receipt("CANDIDATE_WORKTREE_HEAD_OR_DIRTY_MISMATCH")

        try:
            _assert_no_candidate_startup_hooks(workspace_path)
        except ControllerError:
            return _fail_receipt("IMPORT_ORIGIN_MISMATCH")

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
                    done_nonce = uuid4().hex
                    auth_nonce = uuid4().hex + uuid4().hex
                    (out_dir / ".aipc_probe_nonce").write_text(auth_nonce, encoding="utf-8")
                    origin_prefix = f"AIPC_ORIGIN_{probe_nonce}:"
                    done_prefix = f"AIPC_ORIGIN_DONE_{done_nonce}:0"
                    expected_init_sha256 = hashlib.sha256(
                        (workspace_path / "src" / "aipc" / "__init__.py").read_bytes()
                    ).hexdigest()
                    child_code = "\n".join(
                        [
                            "def _install_c02_vault(_top_code):",
                            "    import builtins, hashlib, json, os, stat, sys, types",
                            "    import _json",
                            "    _b_isinstance = builtins.isinstance",
                            "    _b_int = builtins.int",
                            "    _b_str = builtins.str",
                            "    _b_bytes = builtins.bytes",
                            "    _b_len = builtins.len",
                            "    _b_getattr = builtins.getattr",
                            "    _b_type = builtins.type",
                            "    _b_hasattr = builtins.hasattr",
                            "    _b_PermissionError = builtins.PermissionError",
                            "    _b_OSError = builtins.OSError",
                            "    _b_ValueError = builtins.ValueError",
                            "    _native = __import__(",
                            "        'posix' if os.name != 'nt' else 'nt'",
                            "    )",
                            "    _c_getcwd = _native.getcwd",
                            "    _c_lstat = _native.lstat",
                            "    _c_readlink = _b_getattr(_native, 'readlink', None)",
                            "    _c_fspath = os.fspath",
                            "    _c_fsdecode = os.fsdecode",
                            "    _c_PathLike = os.PathLike",
                            "    _s_islnk = stat.S_ISLNK",
                            "    _json_encode_str = _json.encode_basestring_ascii",
                            "    _sha256_ctor = hashlib.sha256",
                            "    _nt_open = _native.open",
                            "    _nt_read = _native.read",
                            "    _nt_write = _native.write",
                            "    _nt_close = _native.close",
                            "    _nt_unlink = _native.unlink",
                            "    _os_O_RDONLY = os.O_RDONLY",
                            "    _os_O_WRONLY = os.O_WRONLY",
                            "    _os_O_CREAT = os.O_CREAT",
                            "    _os_O_TRUNC = os.O_TRUNC",
                            "    _os_dup = os.dup",
                            "    _os_dup2 = os.dup2",
                            "    _c_getframe = sys._getframe",
                            "    _FrameType = types.FrameType",
                            "    _TracebackType = types.TracebackType",
                            "    _GeneratorType = types.GeneratorType",
                            "    _CoroutineType = types.CoroutineType",
                            "    _AsyncGeneratorType = types.AsyncGeneratorType",
                            "    _FRAME_OBJ_TYPES = (",
                            "        _FrameType,",
                            "        _TracebackType,",
                            "        _GeneratorType,",
                            "        _CoroutineType,",
                            "        _AsyncGeneratorType,",
                            "    )",
                            "    _fr_f_code = _FrameType.f_code.__get__",
                            "    _fr_f_back = _FrameType.f_back.__get__",
                            "    _tb_frame_get = _TracebackType.tb_frame.__get__",
                            "    _tb_next_get = _TracebackType.tb_next.__get__",
                            "    _gi_frame_get = _GeneratorType.gi_frame.__get__",
                            "    _cr_frame_get = _CoroutineType.cr_frame.__get__",
                            "    _ag_frame_get = _AsyncGeneratorType.ag_frame.__get__",
                            "    try:",
                            "        _nt_unlink('/tmp/.aipc_c02_child.py')",
                            "    except _b_OSError:",
                            "        pass",
                            "    _od = (",
                            "        '/workspace-out'",
                            "        if os.path.isdir('/workspace-out')",
                            "        else '/out'",
                            "    )",
                            "    _ctrl_path = f'{_od}/.aipc_child_ctrl.json'",
                            "    _cfd = _nt_open(_ctrl_path, _os_O_RDONLY)",
                            "    try:",
                            "        _chunks = []",
                            "        while True:",
                            "            _ch = _nt_read(_cfd, 65536)",
                            "            if not _ch:",
                            "                break",
                            "            _chunks.append(_ch)",
                            "        _ctrl_raw = b''.join(_chunks)",
                            "    finally:",
                            "        _nt_close(_cfd)",
                            "        try:",
                            "            _nt_unlink(_ctrl_path)",
                            "        except _b_OSError:",
                            "            pass",
                            "    _ctrl_obj = json.loads(_ctrl_raw.decode('utf-8'))",
                            "    _v_prefix = _ctrl_obj['origin_prefix']",
                            "    _v_seal_path = (",
                            "        f\"{_ctrl_obj['out_dir']}/.aipc_child_seal.json\"",
                            "    )",
                            "    def _no_exit(*_a, **_kw):",
                            "        raise _b_PermissionError('os._exit prohibited')",
                            "    os._exit = _no_exit",
                            "    os.system = _no_exit",
                            "    if _b_hasattr(_native, '_exit'):",
                            "        _native._exit = _no_exit",
                            "    if _b_hasattr(_native, 'system'):",
                            "        _native.system = _no_exit",
                            "    def _norm_p(_raw_s, _hops=0):",
                            "        if _hops >= 16:",
                            "            return _raw_s",
                            "        _s = _raw_s.replace('\\\\', '/')",
                            "        if not _s.startswith('/'):",
                            "            _cwd = _c_getcwd().replace('\\\\', '/')",
                            "            _s = (_cwd if _cwd == '/' else _cwd.rstrip('/'))",
                            "            _s = _s + '/' + _raw_s.replace('\\\\', '/')",
                            "        _stack = []",
                            "        for _tok in _s.split('/'):",
                            "            if not _tok or _tok == '.':",
                            "                continue",
                            "            if _tok == '..':",
                            "                if _stack:",
                            "                    _stack.pop()",
                            "                continue",
                            "            _stack.append(_tok)",
                            "            _cur = '/' + '/'.join(_stack)",
                            "            try:",
                            "                _st = _c_lstat(_cur)",
                            "            except _b_OSError:",
                            "                _st = None",
                            "            if (",
                            "                _st is not None",
                            "                and _s_islnk(_st.st_mode)",
                            "                and _c_readlink is not None",
                            "            ):",
                            "                try:",
                            "                    _rl = _c_readlink(_cur).replace(",
                            "                        '\\\\', '/'",
                            "                    )",
                            "                    if not _rl.startswith('/'):",
                            "                        _par = '/' + '/'.join(_stack[:-1])",
                            "                        _rl = _par.rstrip('/') + '/' + _rl",
                            "                    _res = _norm_p(_rl, _hops + 1)",
                            "                    _stack = [p for p in _res.split('/') if p]",
                            "                except _b_OSError:",
                            "                    pass",
                            "        return '/' + '/'.join(_stack)",
                            "    _saved_fd1 = _os_dup(1)",
                            "    _dn_fd = _nt_open(os.devnull, _os_O_WRONLY)",
                            "    _os_dup2(_dn_fd, 1)",
                            "    _nt_close(_dn_fd)",
                            "    _in_slot_read = [False]",
                            "    _in_c02_done = [False]",
                            "    _vault_writing = [False]",
                            "    _protected_codes = set()",
                            "    _frame_attrs = (",
                            "        'tb_frame', 'tb_next', 'f_back', 'f_locals',",
                            "        'f_code', 'gi_frame', 'cr_frame', 'ag_frame',",
                            "    )",
                            "    _pevs = (",
                            "        'open', 'os.open', 'os.scandir', 'os.listdir',",
                            "        'os.stat', 'os.lstat', 'os.unlink', 'os.remove',",
                            "        'os.chmod', 'os.truncate', 'os.mkdir', 'os.rmdir',",
                            "        'os.link', 'os.symlink', 'os.rename',",
                            "        'os.replace', 'os.readlink',",
                            "    )",
                            "    _intro_evs = (",
                            "        'sys._getframe', 'gc.get_objects',",
                            "        'gc.get_referrers', 'gc.get_referents',",
                            "        'ctypes.dlopen', 'ctypes.dlsym',",
                            "    )",
                            "    def _vault_step(ev, args):",
                            "        if ev == 'sys.addaudithook':",
                            "            return 'probe introspection prohibited'",
                            "        if (",
                            "            ev == 'object.__getattr__'",
                            "            and not _in_slot_read[0]",
                            "            and _b_len(args) >= 2",
                            "            and args[1] in _frame_attrs",
                            "        ):",
                            "            return 'probe introspection prohibited'",
                            "        if ev == 'aipc.c02_done':",
                            "            if _b_len(args) != 2:",
                            "                return 'probe introspection prohibited'",
                            "            _in_c02_done[0] = True",
                            "            _in_slot_read[0] = True",
                            "            try:",
                            "                _caller_fr = _c_getframe(2)",
                            "                _is_top = (",
                            "                    _b_type(_caller_fr) is _FrameType",
                            "                    and _fr_f_code(_caller_fr) is _top_code",
                            "                )",
                            "            except _b_ValueError:",
                            "                return 'probe introspection prohibited'",
                            "            finally:",
                            "                _in_c02_done[0] = False",
                            "                _in_slot_read[0] = False",
                            "            if not _is_top:",
                            "                return 'probe introspection prohibited'",
                            "            _aipc_mod, _orig_path = args[0], args[1]",
                            "            _f = _norm_p(_b_str(_aipc_mod.__file__))",
                            "            _ffd = _nt_open(_f, _os_O_RDONLY)",
                            "            try:",
                            "                _fchunks = []",
                            "                while True:",
                            "                    _fch = _nt_read(_ffd, 65536)",
                            "                    if not _fch:",
                            "                        break",
                            "                    _fchunks.append(_fch)",
                            "                _fbytes = b''.join(_fchunks)",
                            "            finally:",
                            "                _nt_close(_ffd)",
                            "            _sha = _sha256_ctor(_fbytes).hexdigest()",
                            "            _f_j = _json_encode_str(_f)",
                            "            _sha_j = _json_encode_str(_sha)",
                            "            _p_items = ', '.join(",
                            "                _json_encode_str(_b_str(_x))",
                            "                for _x in _orig_path",
                            "            )",
                            "            _seal_json = (",
                            "                '{\"file\": ' + _f_j + ', \"sha256\": ' + _sha_j",
                            "                + ', \"path\": [' + _p_items + '], \"rc\": 0}'",
                            "            )",
                            "            _payload = (",
                            "                '{\"file\": ' + _f_j + ', \"sha256\": ' + _sha_j",
                            "                + ', \"path\": [' + _p_items + ']}'",
                            "            )",
                            "            _vault_writing[0] = True",
                            "            try:",
                            "                _sfd = _nt_open(",
                            "                    _v_seal_path,",
                            "                    _os_O_WRONLY | _os_O_CREAT | _os_O_TRUNC,",
                            "                    0o600,",
                            "                )",
                            "                try:",
                            "                    _nt_write(_sfd, _seal_json.encode('utf-8'))",
                            "                finally:",
                            "                    _nt_close(_sfd)",
                            "                _out_b = (",
                            "                    _v_prefix + _payload + '\\n'",
                            "                ).encode('utf-8')",
                            "                _nt_write(_saved_fd1, _out_b)",
                            "            finally:",
                            "                _vault_writing[0] = False",
                            "            return None",
                            "        if ev == 'os.chdir':",
                            "            return 'os.chdir prohibited'",
                            "        if (",
                            "            ev in ('subprocess.Popen', 'os.system', 'os.posix_spawn')",
                            "            or ev.startswith(('os.exec', 'os.spawn', 'os.fork'))",
                            "        ):",
                            "            return f'{ev} prohibited'",
                            "        if (",
                            "            ev == 'os.write'",
                            "            and not _vault_writing[0]",
                            "            and args",
                            "            and _b_isinstance(args[0], _b_int)",
                            "            and args[0] == _saved_fd1",
                            "        ):",
                            "            return 'probe fd blocked'",
                            "        if ev in _pevs and args:",
                            "            for _a in args[:2]:",
                            "                if _b_isinstance(_a, _b_int) and (",
                            "                    ev != 'open' or _a > 2 or _a == _saved_fd1",
                            "                ):",
                            "                    return 'probe fd blocked'",
                            "                if _b_isinstance(",
                            "                    _a, (_b_str, _b_bytes, _c_PathLike)",
                            "                ):",
                            "                    _raw = _c_fsdecode(_c_fspath(_a))",
                            "                    _rp = _norm_p(_raw)",
                            "                    if (",
                            "                        ev in ('open', 'os.open')",
                            "                        and _vault_writing[0]",
                            "                        and _rp == _v_seal_path",
                            "                    ):",
                            "                        continue",
                            "                    for _p in (_raw, _rp):",
                            "                        if (",
                            "                            '.aipc_probe_nonce' in _p",
                            "                            or '.aipc_child_ctrl.json' in _p",
                            "                            or '.aipc_child_seal.json' in _p",
                            "                            or '.aipc_origin_receipt.json' in _p",
                            "                            or '/tmp/.aipc_c02_child.py' in _p",
                            "                            or '/out/.aipc_' in _p",
                            "                            or '/workspace-out/.aipc_' in _p",
                            "                            or _p.startswith('/proc')",
                            "                        ):",
                            "                            return 'probe blocked'",
                            "        if ev == 'sys._getframe' and _in_c02_done[0]:",
                            "            return None",
                            "        if ev in _intro_evs:",
                            "            return 'probe introspection prohibited'",
                            "        return None",
                            "    def _audit(ev, args):",
                            "        _err = _vault_step(ev, args)",
                            "        if _err is not None:",
                            "            raise _b_PermissionError(_err)",
                            "    _protected_codes.update((",
                            "        _vault_step.__code__,",
                            "        _audit.__code__,",
                            "        _install_c02_vault.__code__,",
                            "        _top_code,",
                            "    ))",
                            "    sys.addaudithook(_audit)",
                            "    def _make_blocked_addaudithook(_pe):",
                            "        def _blocked_addaudithook(*_a, **_kw):",
                            "            raise _pe('probe introspection prohibited')",
                            "        _blocked_addaudithook.__defaults__ = None",
                            "        _blocked_addaudithook.__kwdefaults__ = None",
                            "        return _blocked_addaudithook",
                            "    sys.addaudithook = _make_blocked_addaudithook(_b_PermissionError)",
                            "    del _make_blocked_addaudithook",
                            "    return None",
                            "import sys",
                            "_install_c02_vault(sys._getframe(0).f_code)",
                            "del _install_c02_vault",
                            "sys.path.insert(0, '/workspace/src')",
                            "import aipc",
                            "sys.audit('aipc.c02_done', aipc, list(sys.path))",
                        ]
                    )
                    probe_code = "\n".join(
                        [
                            "def _c02_probe():",
                            "    import hashlib, hmac, json, os, secrets, subprocess, sys",
                            "    globals().pop('_c02_probe', None)",
                            "    _od = (",
                            "        '/workspace-out'",
                            "        if os.path.isdir('/workspace-out')",
                            "        else '/out'",
                            "    )",
                            "    _np = f'{_od}/.aipc_probe_nonce'",
                            "    _rec_p = f'{_od}/.aipc_origin_receipt.json'",
                            "    _ctrl_p = f'{_od}/.aipc_child_ctrl.json'",
                            "    _seal_p = f'{_od}/.aipc_child_seal.json'",
                            "    _child_script = '/tmp/.aipc_c02_child.py'",
                            "    _auth_nonce = (",
                            "        open(_np, 'rb').read().strip()",
                            "        if os.path.isfile(_np)",
                            "        else b''",
                            "    )",
                            "    for _sp in (",
                            "        '/out/.aipc_probe_nonce',",
                            "        '/workspace-out/.aipc_probe_nonce',",
                            "    ):",
                            "        if os.path.isfile(_sp):",
                            "            try:",
                            "                os.unlink(_sp)",
                            "            except OSError:",
                            "                pass",
                            "    _ctrl_key = secrets.token_hex(32)",
                            f"    _prefix = {origin_prefix!r}",
                            "    _ctrl_payload = json.dumps(",
                            "        {",
                            "            'origin_prefix': _prefix,",
                            "            'out_dir': _od,",
                            "        }",
                            "    ).encode('utf-8')",
                            "    _cfd = os.open(",
                            "        _ctrl_p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600",
                            "    )",
                            "    try:",
                            "        os.write(_cfd, _ctrl_payload)",
                            "    finally:",
                            "        os.close(_cfd)",
                            f"    _child_code = r'''\n{child_code}\n'''",
                            "    _sfd = os.open(",
                            "        _child_script, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600",
                            "    )",
                            "    try:",
                            "        os.write(_sfd, _child_code.encode('utf-8'))",
                            "    finally:",
                            "        os.close(_sfd)",
                            "    try:",
                            "        _child = subprocess.run(",
                            "            [sys.executable, '-I', '-S', '-B', _child_script],",
                            "            stdin=subprocess.DEVNULL,",
                            "            capture_output=True,",
                            "            text=True,",
                            "            close_fds=True,",
                            "        )",
                            "    finally:",
                            "        for _tmp_p in (_child_script, _ctrl_p):",
                            "            if os.path.isfile(_tmp_p):",
                            "                try:",
                            "                    os.unlink(_tmp_p)",
                            "                except OSError:",
                            "                    pass",
                            "    if _child.stderr:",
                            "        sys.stderr.write(_child.stderr)",
                            "        sys.stderr.flush()",
                            "    if _child.returncode != 0:",
                            "        raise SystemExit(_child.returncode)",
                            "    if not os.path.isfile(_seal_p):",
                            "        raise SystemExit(1)",
                            "    try:",
                            "        with open(_seal_p, 'rb') as _sf:",
                            "            _seal_raw = _sf.read()",
                            "    finally:",
                            "        try:",
                            "            os.unlink(_seal_p)",
                            "        except OSError:",
                            "            pass",
                            "    _seal_obj = json.loads(_seal_raw.decode('utf-8'))",
                            "    if not isinstance(_seal_obj, dict):",
                            "        raise SystemExit(1)",
                            "    _sf_file = str(_seal_obj.get('file', ''))",
                            "    _sf_sha = str(_seal_obj.get('sha256', ''))",
                            "    if (",
                            "        _sf_file != '/workspace/src/aipc/__init__.py'",
                            "        or not _sf_sha",
                            "        or _seal_obj.get('rc') != 0",
                            "    ):",
                            "        raise SystemExit(1)",
                            "    _kb = _ctrl_key.encode('utf-8')",
                            "    if len(_kb) > 64:",
                            "        _kb = hashlib.sha256(_kb).digest()",
                            "    _kb = _kb.ljust(64, b'\\x00')",
                            "    _ih = hashlib.sha256(bytes(_b ^ 0x36 for _b in _kb))",
                            "    _oh = hashlib.sha256(bytes(_b ^ 0x5c for _b in _kb))",
                            "    _ht = type(_ih)",
                            "    _h_copy = _ht.copy",
                            "    _h_update = _ht.update",
                            "    _h_digest = _ht.digest",
                            "    _h_hexdigest = _ht.hexdigest",
                            "    _msg = f'{_sf_file}|{_sf_sha}|0'.encode('utf-8')",
                            "    _inner = _h_copy(_ih)",
                            "    _h_update(_inner, _msg)",
                            "    _outer = _h_copy(_oh)",
                            "    _h_update(_outer, _h_digest(_inner))",
                            "    _seal_hex = _h_hexdigest(_outer)",
                            "    _exp_seal = hmac.new(",
                            "        _ctrl_key.encode('utf-8'),",
                            "        _msg,",
                            "        hashlib.sha256,",
                            "    ).hexdigest()",
                            "    if not hmac.compare_digest(_seal_hex, _exp_seal):",
                            "        raise SystemExit(1)",
                            "    _obj = None",
                            "    for _ln in (_child.stdout or '').splitlines():",
                            "        _ls = _ln.strip()",
                            "        if _ls.startswith(_prefix):",
                            "            try:",
                            "                _parsed = json.loads(_ls[len(_prefix):])",
                            "                if isinstance(_parsed, dict):",
                            "                    _obj = _parsed",
                            "                    break",
                            "            except ValueError:",
                            "                pass",
                            "    if _obj is None:",
                            "        raise SystemExit(1)",
                            "    _f = str(_obj.get('file', ''))",
                            "    _sha = str(_obj.get('sha256', ''))",
                            "    if _f != _sf_file or _sha != _sf_sha:",
                            "        raise SystemExit(1)",
                            "    _sig = (",
                            "        hmac.new(",
                            "            _auth_nonce,",
                            "            f'{_f}|{_sha}|0'.encode('utf-8'),",
                            "            hashlib.sha256,",
                            "        ).hexdigest()",
                            "        if _auth_nonce",
                            "        else ''",
                            "    )",
                            "    _rec = {",
                            "        'file': _f,",
                            "        'sha256': _sha,",
                            "        'path': _obj.get('path', []),",
                            "        'rc': 0,",
                            "        'hmac': _sig,",
                            "    }",
                            "    if os.access(_od, os.W_OK):",
                            "        _flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC",
                            "        _fd = os.open(_rec_p, _flags, 0o600)",
                            "        os.write(_fd, json.dumps(_rec).encode('utf-8'))",
                            "        os.close(_fd)",
                            "    if _child.stdout:",
                            "        sys.stdout.write(_child.stdout)",
                            "        sys.stdout.flush()",
                            f"    print({done_prefix!r}, flush=True)",
                            "_c02_probe()",
                        ]
                    )
                    probe_cmd = ("python3", "-I", "-S", "-B", "-c", probe_code)
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
                    receipt_file = out_dir / ".aipc_origin_receipt.json"
                    if receipt_file.is_file():
                        try:
                            rec = json.loads(receipt_file.read_text(encoding="utf-8"))
                        except Exception:
                            return _fail_receipt("IMPORT_ORIGIN_MISMATCH")
                        if not isinstance(rec, dict) or rec.get("rc") != 0:
                            return _fail_receipt("IMPORT_ORIGIN_MISMATCH")
                        msg = f"{rec.get('file')}|{rec.get('sha256')}|{rec.get('rc')}".encode()
                        expected_sig = hmac.new(
                            auth_nonce.encode("utf-8"),
                            msg,
                            hashlib.sha256,
                        ).hexdigest()
                        if not hmac.compare_digest(str(rec.get("hmac", "")), expected_sig):
                            return _fail_receipt("IMPORT_ORIGIN_MISMATCH")
                    elif runner is None:
                        return _fail_receipt("IMPORT_ORIGIN_MISMATCH")
                    origin_payload: dict[str, object] | None = None
                    done_verified = False
                    for line in reversed((probe_proc.stdout or "").splitlines()):
                        line_s = line.strip()
                        if line_s == done_prefix:
                            done_verified = True
                        elif line_s.startswith(origin_prefix) and origin_payload is None:
                            try:
                                parsed_origin = json.loads(line_s[len(origin_prefix) :])
                                if isinstance(parsed_origin, dict):
                                    origin_payload = parsed_origin
                            except json.JSONDecodeError:
                                origin_payload = None
                    if (
                        origin_payload is None
                        or not done_verified
                        or origin_payload.get("sha256") != expected_init_sha256
                    ):
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
                exec_env["PYTHONPATH"] = tmp_boot
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

        extra_exit = 0
        for cmd in extra_commands:
            proc = run_fn(cmd)
            if int(proc.returncode) != 0:
                extra_exit = int(proc.returncode)
                break
        tests_summary = TestSummary(
            total=total_passed + total_failed + total_skipped,
            passed=total_passed,
            failed=total_failed,
            skipped=total_skipped,
        )

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


def _normalize_rel_posix(path_str: str) -> str:
    cleaned = path_str.strip().replace("\\", "/")
    while cleaned.startswith("./"):
        cleaned = cleaned[2:]
    return cleaned.strip("/")


def _matches_path_pattern(rel_posix: str, pattern: str) -> bool:
    norm_path = _normalize_rel_posix(rel_posix)
    norm_pat = _normalize_rel_posix(pattern)
    if not norm_pat:
        return False
    return norm_path == norm_pat or norm_path.startswith(f"{norm_pat}/")


def assert_milestone_c_scope_authorized(
    milestone_id: str,
    phase_id: str,
    task_id: str,
) -> None:
    """Enforce Milestone C scope authorization on milestone, phase, and task IDs."""
    for label, val in (
        ("milestone_id", milestone_id),
        ("phase_id", phase_id),
        ("task_id", task_id),
    ):
        if _is_unapproved_future_scope(val):
            raise ControllerError(f"{label} '{val}' is outside authorized Milestone C scope")
    if milestone_id.strip().lower() not in _AUTHORIZED_MILESTONE_C_IDS:
        raise ControllerError(f"milestone_id '{milestone_id}' is not an authorized Milestone C ID")
    p_low = phase_id.strip().lower()
    if _is_forbidden_phase_token(p_low) or (
        p_low not in _AUTHORIZED_MILESTONE_C_PHASES
        and not p_low.startswith(("c01-", "c02-", "c03-"))
    ):
        raise ControllerError(f"phase_id '{phase_id}' is outside authorized Milestone C scope")
    if _FORBIDDEN_WORKSTREAM_RE.search(task_id.strip().lower()):
        raise ControllerError(f"task_id '{task_id}' is outside authorized Milestone C scope")


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

        queue_snap = task_queue.load()
        task_id = queued_task.contract.task_id
        milestone_id = queued_task.contract.milestone_id
        resolved_phase_id = phase_id or queued_task.contract.phase_id
        attempt_num = queued_task.current_attempt + 1
        project_id = bundle.project_definition.project.id
        resolved_evidence_root = str(evidence_root or Path(bundle.workspace_root).resolve())

        if project_id != queue_snap.project_id:
            raise ControllerError(
                f"project_id '{project_id}' conflicts with queue project_id "
                f"'{queue_snap.project_id}'"
            )
        if phase_id is not None and phase_id != queued_task.contract.phase_id:
            raise ControllerError(
                f"phase_id '{phase_id}' conflicts with approved contract phase_id "
                f"'{queued_task.contract.phase_id}'"
            )
        assert_milestone_c_scope_authorized(
            queued_task.contract.milestone_id,
            resolved_phase_id,
            task_id,
        )
        if Path(bundle.project_definition.project.workspace).resolve() != (
            Path(bundle.workspace_root).resolve()
        ):
            raise ControllerError(
                "project_definition.project.workspace conflicts with bundle.workspace_root"
            )
        if resolved_phase_id not in {p.id for p in bundle.project_definition.phases}:
            raise ControllerError(
                f"phase_id '{resolved_phase_id}' not found in project_definition.phases"
            )
        if command_override is not None and queued_task.contract.validation_commands:
            if " ".join(command_override) not in queued_task.contract.validation_commands:
                raise ControllerError(
                    "command_override conflicts with approved contract validation_commands"
                )

        if not queued_task.contract.in_scope:
            raise ControllerError("Task contract in_scope must not be empty")

        ws_path = Path(bundle.workspace_root).resolve()
        sanitized_git_env = build_sanitized_child_env()
        assert_child_env_isolated(sanitized_git_env)
        _pre_head_proc = subprocess.run(
            ["git", "rev-parse", "--verify", "HEAD"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
            cwd=str(ws_path),
            env=sanitized_git_env,
        )
        _pre_dispatch_sha = _pre_head_proc.stdout.strip()
        _pre_head_ok = (
            _pre_head_proc.returncode == 0
            and len(_pre_dispatch_sha) == 40
            and all(c in "0123456789abcdefABCDEF" for c in _pre_dispatch_sha)
        )

        task_queue.begin_dispatch(
            task_id,
            worker_id=worker_binding.binding_id,
            provider=worker_binding.provider_id.replace("_", "-"),
            target_paths=tuple(queued_task.contract.in_scope),
        )

        try:
            worker_outcome = worker_step(worker_binding, attempt_num, None)
            r_ctx = worker_outcome.review_context
            changed_paths = [
                *(fc.path for fc in r_ctx.agent_result.files_changed),
                *(c.path for c in r_ctx.evidence.git.changes),
            ]
            _post_head_proc = subprocess.run(
                ["git", "rev-parse", "--verify", "HEAD"],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                check=False,
                cwd=str(ws_path),
                env=sanitized_git_env,
            )
            _post_head_sha = _post_head_proc.stdout.strip()
            _cand_sha = worker_outcome.candidate_sha
            _cand_sha_valid = (
                isinstance(_cand_sha, str)
                and len(_cand_sha) == 40
                and all(c in "0123456789abcdefABCDEF" for c in _cand_sha)
            )
            _git_inspect_ok = (
                _pre_head_ok
                and _cand_sha_valid
                and _post_head_proc.returncode == 0
                and _post_head_sha.lower() == _cand_sha.lower()
            )
            actual_git_paths: list[str] = []
            _status_proc = subprocess.run(
                ["git", "status", "--porcelain", "-z"],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                check=False,
                cwd=str(ws_path),
                env=sanitized_git_env,
            )
            if _status_proc.returncode != 0:
                _git_inspect_ok = False
            else:
                _status_tokens = _status_proc.stdout.split("\0")
                if _status_tokens[-1] != "":
                    _git_inspect_ok = False
                else:
                    _idx = 0
                    while _idx < len(_status_tokens) - 1:
                        _entry = _status_tokens[_idx]
                        if len(_entry) < 4 or _entry[2] != " ":
                            _git_inspect_ok = False
                            break
                        _xy = _entry[:2]
                        _path1 = _entry[3:]
                        if not _path1:
                            _git_inspect_ok = False
                            break
                        actual_git_paths.append(_path1)
                        if _xy[0] in ("R", "C") or _xy[1] in ("R", "C"):
                            if _idx + 1 >= len(_status_tokens) - 1 or not _status_tokens[_idx + 1]:
                                _git_inspect_ok = False
                                break
                            actual_git_paths.append(_status_tokens[_idx + 1])
                            _idx += 2
                        else:
                            _idx += 1
            if _git_inspect_ok:
                _diff_proc = subprocess.run(
                    ["git", "diff", "--name-status", "-z", _pre_dispatch_sha, _post_head_sha],
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    text=True,
                    check=False,
                    cwd=str(ws_path),
                    env=sanitized_git_env,
                )
                if _diff_proc.returncode != 0:
                    _git_inspect_ok = False
                else:
                    _diff_tokens = _diff_proc.stdout.split("\0")
                    if _diff_proc.stdout and _diff_tokens[-1] != "":
                        _git_inspect_ok = False
                    else:
                        _idx = 0
                        while _idx < len(_diff_tokens) - 1:
                            _st = _diff_tokens[_idx]
                            if not _st:
                                _git_inspect_ok = False
                                break
                            if _st[0] in ("R", "C"):
                                if (
                                    _idx + 2 >= len(_diff_tokens) - 1
                                    or not _diff_tokens[_idx + 1]
                                    or not _diff_tokens[_idx + 2]
                                ):
                                    _git_inspect_ok = False
                                    break
                                actual_git_paths.append(_diff_tokens[_idx + 1])
                                actual_git_paths.append(_diff_tokens[_idx + 2])
                                _idx += 3
                            else:
                                if _idx + 1 >= len(_diff_tokens) - 1 or not _diff_tokens[_idx + 1]:
                                    _git_inspect_ok = False
                                    break
                                actual_git_paths.append(_diff_tokens[_idx + 1])
                                _idx += 2
            _declared_norm = {p.replace("\\", "/") for p in changed_paths}
            _all_git_declared = _git_inspect_ok and all(
                p.replace("\\", "/") in _declared_norm for p in actual_git_paths
            )
            scope_paths_ok = _all_git_declared and all(
                any(_matches_path_pattern(p, pat) for pat in queued_task.contract.in_scope)
                and not any(
                    _matches_path_pattern(p, pat) for pat in queued_task.contract.out_of_scope
                )
                for p in (*changed_paths, *actual_git_paths)
            )
            req_cmds = tuple(
                tuple(shlex.split(c)) for c in queued_task.contract.validation_commands if c.strip()
            )
            recorded_cmds = {
                tuple(shlex.split(cmd.command))
                for cmd in r_ctx.agent_result.commands
                if cmd.command.strip()
            }
            commands_ok = (
                not req_cmds
                or not r_ctx.agent_result.commands
                or (
                    all(cmd.exit_code == 0 for cmd in r_ctx.agent_result.commands)
                    and all(rc in recorded_cmds for rc in req_cmds)
                )
            )
            contract_scope_ok = (
                r_ctx.project.project.id == project_id
                and r_ctx.task.task_id == task_id
                and r_ctx.task.phase_id == resolved_phase_id
                and Path(r_ctx.task.workspace).resolve() == Path(bundle.workspace_root).resolve()
                and scope_paths_ok
                and commands_ok
            )
            if not contract_scope_ok:
                is_gas = bundle.profile.stack_type == "google_apps_script_offline_mock"
                blocked_receipt = MultiLanguageVerificationReceipt(
                    receipt_id=f"rcpt-{uuid4().hex[:12]}",
                    project_id=project_id,
                    stack_type=bundle.profile.stack_type,
                    isolation_mode=isolation_mode or "blocked",
                    candidate_sha=worker_outcome.candidate_sha,
                    command_executed=command_override or ("contract-scope-check",),
                    exit_code=1,
                    passed=False,
                    tests_summary=TestSummary(total=0, passed=0, failed=0, skipped=0),
                    offline_mock_only=is_gas,
                    simulation_disclaimer=_DEFAULT_GAS_DISCLAIMER if is_gas else None,
                    failure_reason="CONTRACT_SCOPE_VIOLATION",
                )
                task_queue.record_attempt_outcome(
                    task_id,
                    candidate_sha=worker_outcome.candidate_sha,
                    checkpoint_id=f"chk-{task_id}-scope-fail",
                    evidence_root=resolved_evidence_root,
                    review_id=f"rev-skip-{task_id}",
                    review_verdict="BLOCKED",
                    gate_verdict="BLOCKED",
                    failure_reason="CONTRACT_SCOPE_VIOLATION",
                )
                return PRReadinessBundle(
                    bundle_id=f"prb-{uuid4().hex[:12]}",
                    project_id=project_id,
                    milestone_id=milestone_id,
                    phase_id=resolved_phase_id,
                    task_id=task_id,
                    candidate_sha=worker_outcome.candidate_sha,
                    stack_type=bundle.profile.stack_type,
                    verification_receipt=blocked_receipt,
                    review_verdict=ReviewVerdict.BLOCKED,
                    gate_verdict="BLOCKED",
                    pr_ready=False,
                    next_authorized_action="HALT_ON_BLOCKER",
                    blockers=("CONTRACT_SCOPE_VIOLATION",),
                )

            receipt = self.verifier.verify_project_candidate(
                bundle=bundle,
                candidate_sha=worker_outcome.candidate_sha,
                isolation_mode=isolation_mode,
                command_override=command_override,
                container_runtime_bin=container_runtime_bin,
                runner=runner,
                stable_root=stable_root,
                required_commands=req_cmds,
            )
            if (
                req_cmds
                and not r_ctx.agent_result.commands
                and int(receipt.tool_versions.get("commands_count", "0")) < len(req_cmds)
            ):
                task_queue.record_attempt_outcome(
                    task_id,
                    candidate_sha=worker_outcome.candidate_sha,
                    checkpoint_id=f"chk-{task_id}-verify-fail",
                    evidence_root=resolved_evidence_root,
                    review_id=f"rev-skip-{task_id}",
                    review_verdict="BLOCKED",
                    gate_verdict="BLOCKED",
                    failure_reason="CONTRACT_SCOPE_VIOLATION",
                )
                return PRReadinessBundle(
                    bundle_id=f"prb-{uuid4().hex[:12]}",
                    project_id=project_id,
                    milestone_id=milestone_id,
                    phase_id=resolved_phase_id,
                    task_id=task_id,
                    candidate_sha=worker_outcome.candidate_sha,
                    stack_type=bundle.profile.stack_type,
                    verification_receipt=receipt.model_copy(
                        update={"passed": False, "failure_reason": "CONTRACT_SCOPE_VIOLATION"}
                    ),
                    review_verdict=ReviewVerdict.BLOCKED,
                    gate_verdict="BLOCKED",
                    pr_ready=False,
                    next_authorized_action="HALT_ON_BLOCKER",
                    blockers=("CONTRACT_SCOPE_VIOLATION",),
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
