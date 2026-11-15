"""Phase 24.4 Safe Self-Development Runner (Stable / Candidate Isolation).

Enforces strict separation between the running Stable AIPC Controller and Candidate AIPC
worktrees, verifies actual Python import origins in sanitized child processes
(failing closed with IMPORT_ORIGIN_MISMATCH if imports resolve to Stable AIPC),
keeps verification isolation blocked by default, and prevents automatic Stable
Controller replacement.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import stat
import subprocess
import sys
import tempfile
import uuid
from collections.abc import Callable, Mapping
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from pydantic import StrictBool, StrictInt, model_validator

from aipc.controller.errors import ControllerError
from aipc.domain.models import CanonicalRecord, NonBlank
from aipc.hardening.env_isolation import (
    assert_argv_no_credentials,
    assert_child_env_isolated,
    build_sanitized_child_env,
    prepare_isolated_child_invocation,
)
from aipc.hardening.secrets import assert_no_secrets
from aipc.version.models import Commit

IMPORT_ORIGIN_MISMATCH: str = "IMPORT_ORIGIN_MISMATCH"
VERIFICATION_ISOLATION_BLOCKED: str = "VERIFICATION_ISOLATION_BLOCKED"

VerificationIsolationMode = Literal[
    "blocked",
    "container",
    "isolated_subprocess",
]


def _scan_worktree_for_unsafe_links(workspace: Path) -> list[str]:
    """Return any symlinks, NTFS junctions, or reparse points escaping workspace."""
    violations: list[str] = []
    ws_real = Path(workspace).resolve()
    if not ws_real.exists():
        return violations
    for root, dirs, files in os.walk(ws_real, followlinks=False):
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
                        violations.append(f"escape_link:{p.name}->{resolved}")
                except Exception:
                    violations.append(f"unresolvable_link:{p.name}")
    return violations


class StableCandidateConfig(CanonicalRecord):
    """Configuration governing Stable Controller vs. Candidate AIPC worktree isolation."""

    stable_root: NonBlank
    candidate_worktree_root: NonBlank
    pinned_stable_sha: Commit
    candidate_sha: Commit
    verification_isolation: VerificationIsolationMode = "blocked"
    container_image: NonBlank = "aipc-verify:py313-v1"
    allow_auto_promote_stable: Literal[False] = False

    @model_validator(mode="after")
    def validate_disjoint_roots(self) -> StableCandidateConfig:
        s_root = Path(self.stable_root).resolve()
        c_root = Path(self.candidate_worktree_root).resolve()
        if s_root == c_root or s_root.is_relative_to(c_root) or c_root.is_relative_to(s_root):
            raise ValueError(
                f"Stable root ({s_root}) and Candidate worktree root ({c_root}) must be disjoint"
            )
        assert_no_secrets(self.model_dump(mode="json"))
        return self


class CandidateImportOriginReport(CanonicalRecord):
    """Audited result of probing actual Python import resolution for Candidate AIPC."""

    verified: StrictBool
    expected_candidate_src: NonBlank
    actual_imported_file: NonBlank
    leaked_stable_paths: tuple[str, ...] = ()
    status_code: NonBlank = "OK"


class CandidateVerificationReceipt(CanonicalRecord):
    """Audited receipt from isolated verification of Candidate AIPC."""

    candidate_sha: Commit
    isolation_mode: VerificationIsolationMode
    import_origin_verified: StrictBool
    import_origin_report: CandidateImportOriginReport | None = None
    sanitized_env_verified: StrictBool
    exit_code: StrictInt
    passed: StrictBool
    status_code: NonBlank
    summary: NonBlank


def assert_stable_process_free_of_candidate_imports(candidate_root: Path) -> None:
    """Ensure the currently running Stable Controller has not imported Candidate modules."""
    cand_resolved = Path(candidate_root).resolve()
    for mod_name, mod in list(sys.modules.items()):
        mod_file = getattr(mod, "__file__", None)
        if not mod_file:
            continue
        try:
            resolved = Path(mod_file).resolve()
        except OSError:
            continue
        if resolved.is_relative_to(cand_resolved):
            raise ControllerError(
                f"{IMPORT_ORIGIN_MISMATCH}: Stable Controller process imported module "
                f"'{mod_name}' from candidate worktree ({resolved})"
            )


def _is_stable_source_entry(entry_path: Path, stable_root: Path) -> bool:
    """Return True if entry_path points to Stable checkout/src (excluding .venv site-packages)."""
    s_root = stable_root.resolve()
    s_venv = (s_root / ".venv").resolve()
    if entry_path.is_relative_to(s_venv):
        return False
    return entry_path == s_root or entry_path.is_relative_to(s_root)


def _git_status_and_head(
    repo_root: Path,
    *,
    parent_env: Mapping[str, str] | None = None,
) -> tuple[str, str] | None:
    """Return (HEAD sha, porcelain status) if repo_root is a Git checkout, else None."""
    if not (repo_root / ".git").exists():
        return None
    env = dict(build_sanitized_child_env(parent_env))
    head_proc = subprocess.run(
        ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )
    status_proc = subprocess.run(
        ["git", "-C", str(repo_root), "status", "--porcelain", "--untracked-files=all"],
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )
    return (
        head_proc.stdout.strip() if head_proc.returncode == 0 else "",
        status_proc.stdout if status_proc.returncode == 0 else "",
    )


def _assert_no_candidate_startup_hooks(workspace_root: Path) -> None:
    """Fail closed if candidate worktree contains sitecustomize.py, usercustomize.py, or .pth."""
    ws_resolved = Path(workspace_root).resolve()
    if not ws_resolved.exists():
        return
    for item in sorted(ws_resolved.rglob("*")):
        if ".git" in item.parts:
            continue
        if item.name in ("sitecustomize.py", "usercustomize.py") or item.suffix == ".pth":
            raise ControllerError(
                f"{IMPORT_ORIGIN_MISMATCH}: candidate contains prohibited startup hook {item}"
            )


def _write_candidate_isolation_bootstrap(
    bootstrap_dir: Path,
    *,
    stable_root: Path,
    candidate_src: Path,
) -> Path:
    """Write a sitecustomize.py that prunes Stable paths and installs sandbox guards."""
    stable_str = str(Path(stable_root).resolve())
    cand_src_str = str(Path(candidate_src).resolve())
    nl = chr(10)
    script = nl.join(
        [
            "def _install_aipc_boot_guard():",
            "    import os, socket, stat, subprocess, sys, tempfile",
            "    if sys.flags.no_site or sys.flags.isolated or sys.flags.ignore_environment:",
            "        raise RuntimeError('ISOLATED_GUARD_DISABLED')",
            "    _is_win = (os.name == 'nt')",
            "    _native = __import__('nt' if _is_win else 'posix')",
            "    _c_lstat = _native.lstat",
            "    _c_getcwd = _native.getcwd",
            "    _c_readlink = getattr(_native, 'readlink', None)",
            "    _c_final = getattr(_native, '_getfinalpathname', None)",
            "    _c_fsdecode = os.fsdecode",
            "    _c_pathlike = os.PathLike",
            "    _s_islnk = stat.S_ISLNK",
            "    _reparse_flag = getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400)",
            "    _sep = '\\\\' if _is_win else '/'",
            "    _nulls = ('\\\\\\\\.\\\\nul', '/dev/null', 'nul')",
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
            "            elif len(_s) >= 2 and _s[1] == ':' and _s[0].isalpha():",
            "                if len(_s) < 3 or _s[2] != '\\\\':",
            "                    raise PermissionError(",
            "                        'WORKSPACE_WRITE_ISOLATION_BLOCKED: '",
            "                        'drive-relative path prohibited'",
            "                    )",
            "            elif _s.startswith('\\\\'):",
            "                raise PermissionError(",
            "                    'WORKSPACE_WRITE_ISOLATION_BLOCKED: '",
            "                    'drive-relative path prohibited'",
            "                )",
            "            else:",
            "                _cwd = _c_getcwd()",
            "                _s = _cwd + ('' if _cwd.endswith('\\\\') else '\\\\') + _s",
            "            if _s.startswith('\\\\\\\\'):",
            "                _parts = _s[2:].split('\\\\')",
            "                if len(_parts) >= 2:",
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
            "            if _idx == 2 and len(_p) >= 3 and _p[1] == ':':",
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
            "    def _canon_impl(_abs_lex, _hops):",
            "        if _abs_lex in _nulls:",
            "            return _abs_lex",
            "        if _is_win and _c_final is not None:",
            "            _cur = _abs_lex",
            "            _suf = []",
            "            while _cur:",
            "                try:",
            "                    _st = _c_lstat(_cur)",
            "                except OSError:",
            "                    _par = _parent_of(_cur)",
            "                    if _par == _cur:",
            "                        break",
            "                    _suf.append(_cur[len(_par):].lstrip('\\\\'))",
            "                    _cur = _par",
            "                    continue",
            "                _is_lnk = _s_islnk(_st.st_mode) or bool(",
            "                    getattr(_st, 'st_file_attributes', 0) & _reparse_flag",
            "                )",
            "                try:",
            "                    _fin = _c_final(_cur)",
            "                    _base = _lex_norm(_fin)",
            "                except OSError:",
            "                    if not _is_lnk:",
            "                        _par = _parent_of(_cur)",
            "                        if _par == _cur:",
            "                            break",
            "                        _suf.append(_cur[len(_par):].lstrip('\\\\'))",
            "                        _cur = _par",
            "                        continue",
            "                    if _hops >= 16 or _c_readlink is None:",
            "                        raise PermissionError(",
            "                            f'WORKSPACE_WRITE_ISOLATION_BLOCKED: {_cur}'",
            "                        )",
            "                    try:",
            "                        _rl = _c_readlink(_cur)",
            "                    except OSError as _exc:",
            "                        raise PermissionError(",
            "                            f'WORKSPACE_WRITE_ISOLATION_BLOCKED: {_cur}'",
            "                        ) from _exc",
            "                    _rl_s = _rl.replace('/', '\\\\')",
            "                    _is_abs = (",
            "                        _rl_s.startswith('\\\\\\\\')",
            "                        or _rl_s.startswith('\\\\??\\\\')",
            "                        or (",
            "                            len(_rl_s) >= 3",
            "                            and _rl_s[0].isalpha()",
            "                            and _rl_s[1] == ':'",
            "                            and _rl_s[2] == '\\\\'",
            "                        )",
            "                    )",
            "                    if not _is_abs:",
            "                        _par = _parent_of(_cur)",
            "                        _rl_s = (",
            "                            _par + ('' if _par.endswith('\\\\') else '\\\\') + _rl_s",
            "                        )",
            "                    _base = _canon_impl(_lex_norm(_rl_s), _hops + 1)",
            "                if _suf:",
            "                    _joined = _base + (",
            "                        '' if _base.endswith('\\\\') else '\\\\'",
            "                    ) + '\\\\'.join(reversed(_suf))",
            "                    return _lex_norm(_joined)",
            "                return _base",
            "            return _abs_lex",
            "        _segs = [x for x in _abs_lex.split('/') if x]",
            "        _res = '/'",
            "        _hops = 0",
            "        _idx = 0",
            "        while _idx < len(_segs):",
            "            _seg = _segs[_idx]",
            "            _idx += 1",
            "            if not _seg or _seg == '.':",
            "                continue",
            "            if _seg == '..':",
            "                _res = _parent_of(_res)",
            "                continue",
            "            _cand = ('/' + _seg) if _res == '/' else (_res + '/' + _seg)",
            "            try:",
            "                _st = _c_lstat(_cand)",
            "            except OSError:",
            "                _rem = _segs[_idx:]",
            "                return _cand + ('/' + '/'.join(_rem) if _rem else '')",
            "            if _s_islnk(_st.st_mode):",
            "                _hops += 1",
            "                if _hops > 32 or _c_readlink is None:",
            "                    raise PermissionError(",
            "                        f'WORKSPACE_WRITE_ISOLATION_BLOCKED: {_cand}'",
            "                    )",
            "                _tgt = _c_readlink(_cand)",
            "                if _tgt.startswith('/'):",
            "                    _res = '/'",
            "                _tparts = [x for x in _tgt.split('/') if x]",
            "                _segs = _tparts + _segs[_idx:]",
            "                _idx = 0",
            "            else:",
            "                _res = _cand",
            "        return _res",
            "    _canon_impl.__defaults__ = None",
            "    _canon_impl.__kwdefaults__ = None",
            "    def _canon_existing(_abs_lex):",
            "        return _canon_impl(_abs_lex, 0)",
            "    _canon_existing.__defaults__ = None",
            "    _canon_existing.__kwdefaults__ = None",
            "    def _in_tree(_norm_p, _root_norm, _root_prefix):",
            "        return _norm_p == _root_norm or _norm_p.startswith(_root_prefix)",
            "    _in_tree.__defaults__ = None",
            "    _in_tree.__kwdefaults__ = None",
            f"    _stable_norm = _canon_existing(_lex_norm({stable_str!r}))",
            "    _stable_prefix = (",
            "        _stable_norm if _stable_norm.endswith(_sep) else _stable_norm + _sep",
            "    )",
            "    _stable_venv_norm = _canon_existing(_lex_norm(_stable_norm + _sep + '.venv'))",
            "    _stable_venv_prefix = (",
            "        _stable_venv_norm",
            "        if _stable_venv_norm.endswith(_sep)",
            "        else _stable_venv_norm + _sep",
            "    )",
            f"    _cand_src_real = _canon_existing(_lex_norm({cand_src_str!r}))",
            "    _cand_root_norm = _parent_of(_cand_src_real)",
            "    _cand_root_prefix = (",
            "        _cand_root_norm",
            "        if _cand_root_norm.endswith(_sep)",
            "        else _cand_root_norm + _sep",
            "    )",
            "    _self_norm = _canon_existing(_lex_norm(__file__))",
            "    _BOOT_DIR = _parent_of(_self_norm)",
            "    _boot_norm = _BOOT_DIR",
            "    _boot_prefix = (",
            "        _boot_norm if _boot_norm.endswith(_sep) else _boot_norm + _sep",
            "    )",
            "    _SCRATCH_DIR = _BOOT_DIR + _sep + 'scratch'",
            "    os.makedirs(_SCRATCH_DIR, exist_ok=True)",
            "    _tmp_real = _canon_existing(_lex_norm(_SCRATCH_DIR))",
            "    _tmp_norm = _tmp_real",
            "    _tmp_prefix = _tmp_norm if _tmp_norm.endswith(_sep) else _tmp_norm + _sep",
            "    def _is_allowed_path(_np):",
            "        if _np in _nulls:",
            "            return True",
            "        if _np == _self_norm or (",
            "            _in_tree(_np, _boot_norm, _boot_prefix)",
            "            and not _in_tree(_np, _tmp_norm, _tmp_prefix)",
            "        ):",
            "            return False",
            "        return (",
            "            _in_tree(_np, _cand_root_norm, _cand_root_prefix)",
            "            or _in_tree(_np, _tmp_norm, _tmp_prefix)",
            "        )",
            "    _is_allowed_path.__defaults__ = None",
            "    _is_allowed_path.__kwdefaults__ = None",
            "    os.environ['TMPDIR'] = _tmp_real",
            "    os.environ['TEMP'] = _tmp_real",
            "    os.environ['TMP'] = _tmp_real",
            "    tempfile.tempdir = _tmp_real",
            "    _cleaned = []",
            "    for _entry in list(sys.path):",
            "        if not _entry:",
            "            continue",
            "        try:",
            "            _p = _canon_existing(_lex_norm(_entry))",
            "            if not _in_tree(_p, _stable_venv_norm, _stable_venv_prefix) and _in_tree(",
            "                _p, _stable_norm, _stable_prefix",
            "            ):",
            "                continue",
            "        except (OSError, PermissionError):",
            "            pass",
            "        _cleaned.append(_entry)",
            "    sys.path[:] = [_cand_src_real] + _cleaned",
            "    def _is_loopback_addr(_addr):",
            "        if isinstance(_addr, tuple) and len(_addr) >= 1:",
            "            return str(_addr[0]) in ('127.0.0.1', '::1', 'localhost', '')",
            "        return False",
            "    _orig_connect = socket.socket.connect",
            "    _orig_connect_ex = socket.socket.connect_ex",
            "    _orig_bind = socket.socket.bind",
            "    _orig_getaddrinfo = socket.getaddrinfo",
            "    _orig_gethostbyname = socket.gethostbyname",
            "    def _blocked_network(*_args, **_kwargs):",
            "        raise PermissionError('NETWORK_ISOLATION_BLOCKED')",
            "    _blocked_network.__defaults__ = None",
            "    _blocked_network.__kwdefaults__ = None",
            "    def _guarded_sock_connect(_self, _addr):",
            "        if not _is_loopback_addr(_addr):",
            "            raise PermissionError('NETWORK_ISOLATION_BLOCKED')",
            "        return _orig_connect(_self, _addr)",
            "    _guarded_sock_connect.__defaults__ = None",
            "    _guarded_sock_connect.__kwdefaults__ = None",
            "    def _guarded_sock_connect_ex(_self, _addr):",
            "        if not _is_loopback_addr(_addr):",
            "            raise PermissionError('NETWORK_ISOLATION_BLOCKED')",
            "        return _orig_connect_ex(_self, _addr)",
            "    _guarded_sock_connect_ex.__defaults__ = None",
            "    _guarded_sock_connect_ex.__kwdefaults__ = None",
            "    def _guarded_sock_bind(_self, _addr):",
            "        if not _is_loopback_addr(_addr):",
            "            raise PermissionError('NETWORK_ISOLATION_BLOCKED')",
            "        return _orig_bind(_self, _addr)",
            "    _guarded_sock_bind.__defaults__ = None",
            "    _guarded_sock_bind.__kwdefaults__ = None",
            "    def _guarded_getaddrinfo(_host, *_a, **_kw):",
            "        if str(_host or '') not in ('127.0.0.1', '::1', 'localhost', ''):",
            "            raise PermissionError('NETWORK_ISOLATION_BLOCKED')",
            "        return _orig_getaddrinfo(_host, *_a, **_kw)",
            "    _guarded_getaddrinfo.__defaults__ = None",
            "    _guarded_getaddrinfo.__kwdefaults__ = None",
            "    def _guarded_gethostbyname(_host):",
            "        if str(_host or '') not in ('127.0.0.1', '::1', 'localhost', ''):",
            "            raise PermissionError('NETWORK_ISOLATION_BLOCKED')",
            "        return _orig_gethostbyname(_host)",
            "    _guarded_gethostbyname.__defaults__ = None",
            "    _guarded_gethostbyname.__kwdefaults__ = None",
            "    socket.socket.connect = _guarded_sock_connect",
            "    socket.socket.connect_ex = _guarded_sock_connect_ex",
            "    socket.socket.sendto = _blocked_network",
            "    if hasattr(socket.socket, 'sendmsg'):",
            "        socket.socket.sendmsg = _blocked_network",
            "    socket.socket.bind = _guarded_sock_bind",
            "    socket.create_connection = _blocked_network",
            "    socket.getaddrinfo = _guarded_getaddrinfo",
            "    socket.gethostbyname = _guarded_gethostbyname",
            "    _WFLAGS = (",
            "        os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND",
            "    )",
            "    _orig_os_open = os.open",
            "    def _guarded_os_open(path, flags, *args, **kwargs):",
            "        if isinstance(path, int) or kwargs.get('dir_fd') is not None:",
            "            raise PermissionError('WORKSPACE_WRITE_ISOLATION_BLOCKED')",
            "        return _orig_os_open(path, flags, *args, **kwargs)",
            "    _guarded_os_open.__defaults__ = None",
            "    _guarded_os_open.__kwdefaults__ = None",
            "    os.open = _guarded_os_open",
            "    class _BlockNativeMods:",
            "        _BLOCKED = ('ctypes', '_ctypes', '_posixsubprocess', '_winapi', 'msvcrt')",
            "        def find_spec(self, fullname, path=None, target=None):",
            "            if fullname in self._BLOCKED or fullname.startswith(",
            "                ('ctypes.', '_ctypes.')",
            "            ):",
            "                raise ImportError(f'CTYPES_SANDBOX_BLOCKED: {fullname}')",
            "            return None",
            "    for _mname in ('ctypes', '_ctypes', '_posixsubprocess', '_winapi', 'msvcrt'):",
            "        sys.modules.pop(_mname, None)",
            "    sys.meta_path.insert(0, _BlockNativeMods())",
            "    _orig_popen_init = subprocess.Popen.__init__",
            "    def _guarded_popen_init(_self, *p_args, **kwargs):",
            "        if kwargs.get('shell'):",
            "            raise PermissionError('SUBPROCESS_SANDBOX_BYPASS_BLOCKED: shell=True')",
            "        raw_env = kwargs.get('env')",
            "        env_copy = dict(raw_env) if raw_env is not None else dict(os.environ)",
            "        cur_pp = env_copy.get('PYTHONPATH', '')",
            "        _bstr = str(_BOOT_DIR)",
            "        env_copy['PYTHONPATH'] = f'{_bstr}{os.pathsep}{cur_pp}' if cur_pp else _bstr",
            "        env_copy['PYTHONNOUSERSITE'] = '1'",
            "        kwargs['env'] = env_copy",
            "        return _orig_popen_init(_self, *p_args, **kwargs)",
            "    _guarded_popen_init.__defaults__ = None",
            "    _guarded_popen_init.__kwdefaults__ = None",
            "    subprocess.Popen.__init__ = _guarded_popen_init",
            "    def _aipc_boot_audit(_ev, _args):",
            "        if _ev in ('socket.connect', 'socket.bind') and len(_args) >= 2:",
            "            _addr = _args[1]",
            "            _is_lb = (",
            "                isinstance(_addr, tuple)",
            "                and len(_addr) >= 1",
            "                and str(_addr[0]) in ('127.0.0.1', '::1', 'localhost', '')",
            "            )",
            "            if not _is_lb:",
            "                raise PermissionError('NETWORK_ISOLATION_BLOCKED')",
            "        if _ev in ('socket.getaddrinfo', 'socket.gethostbyname') and _args:",
            "            if str(_args[0] or '') not in ('127.0.0.1', '::1', 'localhost', ''):",
            "                raise PermissionError('NETWORK_ISOLATION_BLOCKED')",
            "        if _ev in ('socket.sendto', 'socket.sendmsg'):",
            "            raise PermissionError('NETWORK_ISOLATION_BLOCKED')",
            "        if _ev in ('os.system', 'os.spawn', 'os.exec', 'os.posix_spawn'):",
            "            raise PermissionError(f'SUBPROCESS_SANDBOX_BYPASS_BLOCKED: {_ev}')",
            "        if _ev in ('ctypes.dlopen', 'ctypes.dlsym'):",
            "            raise PermissionError(f'CTYPES_SANDBOX_BLOCKED: {_ev}')",
            "        if _ev == 'sys._getframe':",
            "            _fr = _args[0] if _args else None",
            "            _fc = getattr(_fr, 'f_code', None) if _fr else None",
            "            _fn = getattr(_fc, 'co_filename', '') if _fc else ''",
            "            if _fn in ('<string>', '<stdin>', ''):",
            "                raise PermissionError('WORKSPACE_WRITE_ISOLATION_BLOCKED: _getframe')",
            "            try:",
            "                _fn_norm = _canon_existing(_lex_norm(_fn))",
            "                if _in_tree(_fn_norm, _cand_root_norm, _cand_root_prefix):",
            "                    raise PermissionError(",
            "                        'WORKSPACE_WRITE_ISOLATION_BLOCKED: _getframe'",
            "                    )",
            "            except OSError:",
            "                pass",
            "        if _ev == 'subprocess.Popen' and len(_args) >= 2:",
            "            _exe = _c_fsdecode(_args[0]) if _args[0] is not None else ''",
            "            _av = list(_args[1]) if isinstance(_args[1], (list, tuple)) else []",
            "            _c0 = _exe or (_c_fsdecode(_av[0]) if _av else '')",
            "            if _c0:",
            "                _c0_norm = _lex_norm(_c0)",
            "                if _sep in _c0 or '/' in _c0:",
            "                    _c0_norm = _canon_existing(_c0_norm)",
            "                _nm = _c0_norm.rsplit(_sep, 1)[-1].lower().removesuffix('.exe')",
            "            else:",
            "                _nm = ''",
            "            if not _nm.startswith('python'):",
            "                raise PermissionError('SUBPROCESS_SANDBOX_BYPASS_BLOCKED: Popen')",
            "            for _tok in _av[1:]:",
            "                _ts = str(_tok)",
            "                if _ts == '--isolated' or (",
            "                    _ts.startswith('-')",
            "                    and not _ts.startswith('--')",
            "                    and any(_ch in _ts[1:] for _ch in ('S', 'I', 'E'))",
            "                ):",
            "                    raise PermissionError(",
            "                        'SUBPROCESS_SANDBOX_BYPASS_BLOCKED: guard-disabling flag'",
            "                    )",
            "            return",
            "        _paths = ()",
            "        if _ev == 'open' and len(_args) >= 2:",
            "            _m = _args[1]",
            "            _f = _args[2] if len(_args) >= 3 else 0",
            "            _is_w = (",
            "                (isinstance(_m, str) and any(c in _m for c in ('w', 'a', 'x', '+')))",
            "                or (isinstance(_m, int) and bool(_m & _WFLAGS))",
            "                or (isinstance(_f, int) and bool(_f & _WFLAGS))",
            "            )",
            "            if _is_w:",
            "                _raw = _args[0]",
            "                if isinstance(_raw, int):",
            "                    if _raw > 2:",
            "                        raise PermissionError('WORKSPACE_WRITE_ISOLATION_BLOCKED')",
            "                    return",
            "                _paths = (_raw,)",
            "        elif _ev in (",
            "            'os.remove', 'os.unlink', 'os.mkdir', 'os.rmdir',",
            "            'os.chmod', 'os.truncate', 'os.chown', 'os.utime', 'shutil.rmtree',",
            "        ):",
            "            if _args:",
            "                _paths = (_args[0],)",
            "        elif _ev in (",
            "            'os.rename', 'os.replace', 'os.link', 'os.symlink',",
            "            'shutil.copyfile', 'shutil.move',",
            "        ):",
            "            if len(_args) >= 2:",
            "                _paths = (_args[0], _args[1])",
            "        for _raw in _paths:",
            "            if isinstance(_raw, int):",
            "                raise PermissionError('WORKSPACE_WRITE_ISOLATION_BLOCKED')",
            "            if not isinstance(_raw, (str, bytes, _c_pathlike)):",
            "                raise PermissionError('WORKSPACE_WRITE_ISOLATION_BLOCKED')",
            "            _raw_str = _c_fsdecode(_raw)",
            "            _lex = _lex_norm(_raw_str)",
            "            if _lex in _nulls:",
            "                continue",
            "            if not _is_allowed_path(_lex):",
            "                raise PermissionError(f'WORKSPACE_WRITE_ISOLATION_BLOCKED: {_lex}')",
            "            _comp = _lex",
            "            while _comp:",
            "                try:",
            "                    _st = _c_lstat(_comp)",
            "                except OSError:",
            "                    _st = None",
            "                if _st is not None:",
            "                    _is_link = _s_islnk(_st.st_mode) or bool(",
            "                        getattr(_st, 'st_file_attributes', 0) & _reparse_flag",
            "                    )",
            "                    if _is_link:",
            "                        _rc = _canon_existing(_comp)",
            "                        if not _is_allowed_path(_rc):",
            "                            raise PermissionError(",
            "                                f'WORKSPACE_WRITE_ISOLATION_BLOCKED: {_comp}'",
            "                            )",
            "                _parent = _parent_of(_comp)",
            "                if _parent == _comp:",
            "                    break",
            "                _comp = _parent",
            "            _tp = _canon_existing(_lex)",
            "            if not _is_allowed_path(_tp):",
            "                raise PermissionError(f'WORKSPACE_WRITE_ISOLATION_BLOCKED: {_tp}')",
            "    _aipc_boot_audit.__defaults__ = None",
            "    _aipc_boot_audit.__kwdefaults__ = None",
            "    sys.addaudithook(_aipc_boot_audit)",
            "    del _aipc_boot_audit",
            "_install_aipc_boot_guard()",
            "del _install_aipc_boot_guard",
            "",
        ]
    )
    target = bootstrap_dir / "sitecustomize.py"
    target.write_text(script, encoding="utf-8")
    return bootstrap_dir


def verify_candidate_import_origin(
    *,
    stable_root: Path,
    candidate_root: Path,
    python_executable: Path | str | None = None,
    use_isolation_bootstrap: bool = True,
    parent_env: Mapping[str, str] | None = None,
) -> CandidateImportOriginReport:
    """Spawn a sanitized child Python probe and verify `import aipc` resolves to Candidate src/."""
    s_root = Path(stable_root).resolve()
    c_root = Path(candidate_root).resolve()
    c_src = (c_root / "src").resolve()
    if not (c_src / "aipc" / "__init__.py").is_file():
        raise ControllerError(
            f"{IMPORT_ORIGIN_MISMATCH}: candidate src/aipc/__init__.py not found at {c_src}"
        )

    _assert_no_candidate_startup_hooks(c_root)

    link_violations = _scan_worktree_for_unsafe_links(c_root)
    if link_violations:
        raise ControllerError(
            f"Unsafe symlink/junction in candidate worktree: {'; '.join(link_violations)}"
        )

    py_exe = str(Path(python_executable).resolve()) if python_executable else sys.executable
    probe_code = (
        "import json, sys; from pathlib import Path; import aipc; "
        "print(json.dumps({'file': str(Path(aipc.__file__).resolve()), 'path': list(sys.path)}))"
    )

    with tempfile.TemporaryDirectory(prefix="aipc-cand-boot-") as tmp_boot:
        boot_path = Path(tmp_boot).resolve()
        if use_isolation_bootstrap:
            _write_candidate_isolation_bootstrap(
                boot_path,
                stable_root=s_root,
                candidate_src=c_src,
            )
            pythonpath_val = os.pathsep.join([str(boot_path), str(c_src)])
        else:
            # Deliberately unpruned mode (used in tests to verify IMPORT_ORIGIN_MISMATCH detection)
            pythonpath_val = str(s_root / "src")

        argv, sanitized_env, _report = prepare_isolated_child_invocation(
            [py_exe, "-c", probe_code],
            base_env=parent_env,
            extra_env={
                "PYTHONPATH": pythonpath_val,
                "PYTHONDONTWRITEBYTECODE": "1",
            },
        )
        assert_child_env_isolated(sanitized_env)
        proc = subprocess.run(
            list(argv),
            cwd=str(c_root),
            env=sanitized_env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
        )

    if proc.returncode != 0:
        raise ControllerError(
            f"{IMPORT_ORIGIN_MISMATCH}: candidate import probe failed (exit={proc.returncode})"
        )

    try:
        payload = json.loads(proc.stdout.strip().splitlines()[-1])
        actual_file = Path(payload["file"]).resolve()
        sys_path_entries = [str(p) for p in payload.get("path", [])]
    except Exception as exc:
        raise ControllerError(
            f"{IMPORT_ORIGIN_MISMATCH}: invalid probe output from candidate check"
        ) from exc

    leaked: list[str] = []
    for entry in sys_path_entries:
        if not entry:
            continue
        try:
            rp = Path(entry).resolve()
            if _is_stable_source_entry(rp, s_root):
                leaked.append(str(rp))
        except OSError:
            continue

    c_aipc = (c_root / "src" / "aipc").resolve()
    if (
        _is_stable_source_entry(actual_file, s_root)
        or not actual_file.is_relative_to(c_aipc)
        or leaked
    ):
        raise ControllerError(
            f"{IMPORT_ORIGIN_MISMATCH}: imported aipc from '{actual_file}' "
            f"(expected under '{c_aipc}', leaked_stable_paths={leaked})"
        )

    return CandidateImportOriginReport(
        verified=True,
        expected_candidate_src=str(c_src),
        actual_imported_file=str(actual_file),
        leaked_stable_paths=tuple(leaked),
        status_code="OK",
    )


SubprocessRunner = Callable[
    [list[str], Mapping[str, str], Path],
    subprocess.CompletedProcess[str],
]


class SafeSelfDevelopmentRunner:
    """Executes Candidate AIPC verification safely without polluting or replacing Stable AIPC."""

    def __init__(
        self,
        config: StableCandidateConfig,
        *,
        subprocess_runner: SubprocessRunner | None = None,
    ) -> None:
        self.config = StableCandidateConfig.model_validate(config)
        self._subprocess_runner = subprocess_runner

    def verify_runtime_separation(self) -> None:
        """Verify disjoint roots, absence of unsafe links, and zero candidate imports in Stable."""
        c_root = Path(self.config.candidate_worktree_root).resolve()
        assert_stable_process_free_of_candidate_imports(c_root)
        violations = _scan_worktree_for_unsafe_links(c_root)
        if violations:
            raise ControllerError(
                f"Candidate worktree failed Windows link/junction check: {'; '.join(violations)}"
            )

    def run_candidate_verification(
        self,
        *,
        pytest_targets: tuple[str, ...] = ("tests",),
        python_executable: Path | str | None = None,
        parent_env: Mapping[str, str] | None = None,
    ) -> CandidateVerificationReceipt:
        """Run isolated verification on the Candidate worktree after import-origin check."""
        self.verify_runtime_separation()

        if self.config.verification_isolation == "blocked":
            raise ControllerError(
                f"{VERIFICATION_ISOLATION_BLOCKED}: verification_isolation is 'blocked' "
                "by default; explicitly configure 'container' or 'isolated_subprocess'."
            )

        s_root = Path(self.config.stable_root).resolve()
        c_root = Path(self.config.candidate_worktree_root).resolve()
        c_src = (c_root / "src").resolve()

        if self.config.verification_isolation == "container":
            return self._run_container_verification(
                candidate_root=c_root,
                pytest_targets=pytest_targets,
                parent_env=parent_env,
            )

        pre_snap = _git_status_and_head(c_root, parent_env=parent_env)
        origin_report = verify_candidate_import_origin(
            stable_root=s_root,
            candidate_root=c_root,
            python_executable=python_executable,
            use_isolation_bootstrap=True,
            parent_env=parent_env,
        )
        post_snap = _git_status_and_head(c_root, parent_env=parent_env)
        if pre_snap != post_snap:
            raise ControllerError(
                "WORKSPACE_MUTATED_DURING_VERIFICATION: "
                "candidate worktree mutated during import-origin check"
            )

        return self._run_isolated_subprocess_verification(
            stable_root=s_root,
            candidate_root=c_root,
            candidate_src=c_src,
            origin_report=origin_report,
            pytest_targets=pytest_targets,
            python_executable=python_executable,
            parent_env=parent_env,
        )

    def promote_candidate_to_stable(self, *_args: Any, **_kwargs: Any) -> None:
        """Always forbid automatic replacement of Stable Controller during Phase 24."""
        raise ControllerError(
            "Automatic Stable Controller replacement is forbidden in Phase 24; "
            "promotion requires Human Commander merge approval."
        )

    def _run_container_verification(
        self,
        *,
        candidate_root: Path,
        pytest_targets: tuple[str, ...],
        parent_env: Mapping[str, str] | None,
    ) -> CandidateVerificationReceipt:
        candidate_src = (candidate_root / "src").resolve()
        if not (candidate_src / "aipc" / "__init__.py").is_file():
            raise ControllerError(
                f"{IMPORT_ORIGIN_MISMATCH}: candidate src/aipc/__init__.py not found at "
                f"{candidate_src}"
            )
        _assert_no_candidate_startup_hooks(candidate_root)

        probe_nonce = uuid.uuid4().hex
        done_nonce = uuid.uuid4().hex
        hmac_key = uuid.uuid4().hex + uuid.uuid4().hex
        expected_init_sha256 = hashlib.sha256(
            (candidate_src / "aipc" / "__init__.py").read_bytes()
        ).hexdigest()
        probe_script = "\n".join(
            [
                "def _aipc_container_verify():",
                "    import hashlib, hmac, json, os, pathlib, sys",
                "    _sec_key = json.loads(",
                "        pathlib.Path('/aipc_probe/secret.json').read_text(encoding='utf-8')",
                "    )['hmac_key'].encode('utf-8')",
                "    _ctrl_fd = os.open(",
                "        '/aipc_ctrl_out/receipt.json',",
                "        os.O_WRONLY | os.O_CREAT | os.O_TRUNC,",
                "        0o600,",
                "    )",
                "    globals().pop('_aipc_container_verify', None)",
                "    def _no_exit(*_a, **_kw):",
                "        raise PermissionError('os._exit prohibited')",
                "    os._exit = _no_exit",
                "    def _probe_audit(ev, args):",
                "        _pevs = (",
                "            'open', 'os.open', 'os.scandir', 'os.listdir', 'os.stat',",
                "            'os.lstat', 'os.unlink', 'os.remove', 'os.rename', 'os.replace',",
                "        )",
                "        if ev in _pevs and args:",
                "            for _arg in args[:2]:",
                "                if isinstance(_arg, (str, bytes, os.PathLike)):",
                "                    _p = os.fsdecode(_arg)",
                "                    if (",
                "                        '/aipc_probe' in _p",
                "                        or '/aipc_ctrl_out' in _p",
                "                        or _p.startswith('/proc')",
                "                    ):",
                "                        raise PermissionError('aipc_probe access prohibited')",
                "        if ev in (",
                "            'sys._getframe', 'gc.get_objects', 'gc.get_referrers',",
                "            'ctypes.dlopen', 'ctypes.dlsym',",
                "        ):",
                "            raise PermissionError('probe introspection prohibited')",
                "    sys.addaudithook(_probe_audit)",
                "    sys.path.insert(0, '/workspace/src')",
                "    _real_stdout = sys.stdout",
                "    _devnull = open(os.devnull, 'w')",
                "    sys.stdout = _devnull",
                "    try:",
                "        import aipc",
                "    except BaseException as exc:",
                "        raise SystemExit(f'IMPORT_ORIGIN_MISMATCH: {exc}')",
                "    finally:",
                "        sys.stdout = _real_stdout",
                "        _devnull.close()",
                "    _f = pathlib.Path(aipc.__file__).resolve()",
                "    _sha = hashlib.sha256(_f.read_bytes()).hexdigest()",
                "    print(",
                f"        {f'AIPC_CONTAINER_ORIGIN_{probe_nonce}:'!r}",
                "        + json.dumps({'file': str(_f), 'sha256': _sha, 'path': list(sys.path)}),",
                "        file=_real_stdout,",
                "        flush=True,",
                "    )",
                "    import site",
                "    site.main()",
                "    import pytest",
                "    try:",
                "        _rc = pytest.main(sys.argv[1:])",
                "    except BaseException as exc:",
                "        os.close(_ctrl_fd)",
                "        raise SystemExit(1) from exc",
                "    if _rc == 0:",
                "        _sig = hmac.new(",
                "            _sec_key,",
                "            f'{_f}|{_sha}|{_rc}'.encode('utf-8'),",
                "            hashlib.sha256,",
                "        ).hexdigest()",
                "        _rec = json.dumps(",
                "            {",
                "                'file': str(_f),",
                "                'sha256': _sha,",
                "                'path': list(sys.path),",
                "                'rc': int(_rc),",
                "                'hmac': _sig,",
                "            }",
                "        ).encode('utf-8')",
                "        os.write(_ctrl_fd, _rec)",
                "        os.close(_ctrl_fd)",
                "        print(",
                f"            {f'AIPC_CONTAINER_DONE_{done_nonce}:0'!r},",
                "            file=_real_stdout,",
                "            flush=True,",
                "        )",
                "    else:",
                "        os.close(_ctrl_fd)",
                "    raise SystemExit(_rc)",
                "_aipc_container_verify()",
                "",
            ]
        )

        sanitized_env = build_sanitized_child_env(parent_env)
        assert_child_env_isolated(sanitized_env)

        with tempfile.TemporaryDirectory(prefix="aipc-ctrl-probe-") as tmp_probe:
            probe_dir = Path(tmp_probe).resolve()
            ctrl_out = probe_dir / "out"
            ctrl_out.mkdir()
            (probe_dir / "secret.json").write_text(
                json.dumps({"hmac_key": hmac_key}),
                encoding="utf-8",
            )
            (probe_dir / "run_verify.py").write_text(probe_script, encoding="utf-8")
            argv = [
                "docker",
                "run",
                "--rm",
                "--network",
                "none",
                "--read-only",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "--pids-limit",
                "128",
                "--memory",
                "1g",
                "--cpus",
                "2",
                "--user",
                "65534:65534",
                "--tmpfs",
                "/tmp:rw,noexec,nosuid,size=256m",
                "-e",
                "HOME=/tmp",
                "-e",
                "PYTHONDONTWRITEBYTECODE=1",
                "-v",
                f"{candidate_root}:/workspace:ro",
                "-v",
                f"{probe_dir}:/aipc_probe:ro",
                "-v",
                f"{ctrl_out}:/aipc_ctrl_out:rw",
                "-w",
                "/workspace",
                self.config.container_image,
                "python3",
                "-I",
                "-S",
                "-B",
                "/aipc_probe/run_verify.py",
                "-q",
                "-o",
                "cache_dir=/tmp/.pytest_cache",
                "-o",
                "pythonpath=/workspace/src",
                *pytest_targets,
            ]
            assert_argv_no_credentials(argv)
            proc = self._exec(argv, sanitized_env, candidate_root)
            receipt_path = ctrl_out / "receipt.json"
            hmac_receipt_exists = receipt_path.is_file()
            hmac_receipt_raw = (
                receipt_path.read_text(encoding="utf-8") if hmac_receipt_exists else None
            )

        passed = proc.returncode == 0
        if hmac_receipt_exists:
            try:
                rec = json.loads(hmac_receipt_raw or "")
            except json.JSONDecodeError as exc:
                raise ControllerError(
                    f"{IMPORT_ORIGIN_MISMATCH}: invalid container receipt JSON"
                ) from exc
            if not isinstance(rec, dict):
                raise ControllerError(
                    f"{IMPORT_ORIGIN_MISMATCH}: container receipt must be a JSON object"
                )
            msg = f"{rec.get('file')}|{rec.get('sha256')}|{rec.get('rc')}".encode()
            expected_sig = hmac.new(
                hmac_key.encode("utf-8"),
                msg,
                hashlib.sha256,
            ).hexdigest()
            if not hmac.compare_digest(str(rec.get("hmac", "")), expected_sig) or (
                rec.get("rc") != 0
            ):
                raise ControllerError(
                    f"{IMPORT_ORIGIN_MISMATCH}: container receipt HMAC or rc mismatch"
                )
        elif self._subprocess_runner is None and passed:
            raise ControllerError(f"{IMPORT_ORIGIN_MISMATCH}: container receipt.json missing")
        origin_report: CandidateImportOriginReport | None = None
        import_origin_verified = False
        if passed:
            expected_prefix = f"AIPC_CONTAINER_ORIGIN_{probe_nonce}:"
            expected_done = f"AIPC_CONTAINER_DONE_{done_nonce}:0"
            detected_origin: str | None = None
            detected_sha256: str | None = None
            detected_sys_path: list[str] = []
            done_verified = False
            for line in (proc.stdout or "").splitlines():
                line_s = line.strip()
                if line_s == expected_done:
                    done_verified = True
                elif line_s.startswith(expected_prefix) and detected_origin is None:
                    payload = line_s[len(expected_prefix) :]
                    try:
                        parsed = json.loads(payload)
                    except json.JSONDecodeError:
                        continue
                    val = parsed.get("file") if isinstance(parsed, dict) else None
                    sha_val = parsed.get("sha256") if isinstance(parsed, dict) else None
                    if isinstance(val, str) and val:
                        detected_origin = val
                        detected_sha256 = str(sha_val) if isinstance(sha_val, str) else None
                        raw_paths = parsed.get("path", [])
                        if isinstance(raw_paths, list):
                            detected_sys_path = [str(p) for p in raw_paths]
            if detected_origin is None:
                raise ControllerError(
                    f"{IMPORT_ORIGIN_MISMATCH}: container output missing verified "
                    "aipc_import_origin observation"
                )
            if not done_verified:
                passed = False
                import_origin_verified = False
                raise ControllerError(
                    f"{IMPORT_ORIGIN_MISMATCH}: container verification did not complete "
                    "test execution"
                )
            if detected_sha256 != expected_init_sha256:
                raise ControllerError(
                    f"{IMPORT_ORIGIN_MISMATCH}: container aipc/__init__.py sha256 mismatch"
                )
            origin_posix_str = detected_origin.replace("\\", "/")
            if not origin_posix_str or ".." in PurePosixPath(origin_posix_str).parts:
                raise ControllerError(
                    f"{IMPORT_ORIGIN_MISMATCH}: container imported aipc from '{detected_origin}'"
                )
            origin_posix = PurePosixPath(origin_posix_str)
            expected_pkg = PurePosixPath("/workspace/src/aipc")
            if not (
                origin_posix == expected_pkg / "__init__.py"
                or origin_posix.is_relative_to(expected_pkg)
            ):
                raise ControllerError(
                    f"{IMPORT_ORIGIN_MISMATCH}: container imported aipc from '{detected_origin}'"
                )
            stable_str = str(Path(self.config.stable_root).resolve()).replace("\\", "/")
            if any(p.replace("\\", "/").startswith(stable_str) for p in detected_sys_path):
                raise ControllerError(
                    f"{IMPORT_ORIGIN_MISMATCH}: container sys.path leaked stable_root"
                )
            origin_report = CandidateImportOriginReport(
                verified=True,
                expected_candidate_src=str(candidate_src),
                actual_imported_file=detected_origin,
                leaked_stable_paths=(),
                status_code="OK",
            )
            import_origin_verified = True

        return CandidateVerificationReceipt(
            candidate_sha=self.config.candidate_sha,
            isolation_mode="container",
            import_origin_verified=import_origin_verified,
            import_origin_report=origin_report,
            sanitized_env_verified=True,
            exit_code=int(proc.returncode),
            passed=passed,
            status_code="PASS" if passed else "TEST_FAILURE",
            summary=(proc.stdout or proc.stderr or "container verification complete").strip()[:500],
        )

    def _run_isolated_subprocess_verification(
        self,
        *,
        stable_root: Path,
        candidate_root: Path,
        candidate_src: Path,
        origin_report: CandidateImportOriginReport,
        pytest_targets: tuple[str, ...],
        python_executable: Path | str | None,
        parent_env: Mapping[str, str] | None,
    ) -> CandidateVerificationReceipt:
        py_exe = str(Path(python_executable).resolve()) if python_executable else sys.executable
        with tempfile.TemporaryDirectory(prefix="aipc-cand-pytest-") as tmp_dir:
            tmp_path = Path(tmp_dir).resolve()
            boot_dir = tmp_path / "boot"
            boot_dir.mkdir(parents=True, exist_ok=True)
            cache_dir = boot_dir / "scratch" / "pytest_cache"
            _write_candidate_isolation_bootstrap(
                boot_dir,
                stable_root=stable_root,
                candidate_src=candidate_src,
            )
            argv = [
                py_exe,
                "-m",
                "pytest",
                "-q",
                "-o",
                f"cache_dir={cache_dir}",
                *pytest_targets,
            ]
            checked_argv, sanitized_env, _rep = prepare_isolated_child_invocation(
                argv,
                base_env=parent_env,
                extra_env={
                    "PYTHONPATH": os.pathsep.join([str(boot_dir), str(candidate_src)]),
                    "PYTHONDONTWRITEBYTECODE": "1",
                },
            )
            assert_child_env_isolated(sanitized_env)
            proc = self._exec(list(checked_argv), sanitized_env, candidate_root)

        passed = proc.returncode == 0
        raw_out = (proc.stdout or proc.stderr or "isolated verification complete").strip()
        return CandidateVerificationReceipt(
            candidate_sha=self.config.candidate_sha,
            isolation_mode="isolated_subprocess",
            import_origin_verified=True,
            import_origin_report=origin_report,
            sanitized_env_verified=True,
            exit_code=int(proc.returncode),
            passed=passed,
            status_code="PASS" if passed else "TEST_FAILURE",
            summary=raw_out[-500:],
        )

    def _exec(
        self,
        argv: list[str],
        env: Mapping[str, str],
        cwd: Path,
    ) -> subprocess.CompletedProcess[str]:
        if self._subprocess_runner is not None:
            return self._subprocess_runner(argv, env, cwd)
        return subprocess.run(
            argv,
            cwd=str(cwd),
            env=dict(env),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
        )


__all__ = [
    "CandidateImportOriginReport",
    "CandidateVerificationReceipt",
    "IMPORT_ORIGIN_MISMATCH",
    "SafeSelfDevelopmentRunner",
    "StableCandidateConfig",
    "VERIFICATION_ISOLATION_BLOCKED",
    "VerificationIsolationMode",
    "assert_stable_process_free_of_candidate_imports",
    "verify_candidate_import_origin",
]
