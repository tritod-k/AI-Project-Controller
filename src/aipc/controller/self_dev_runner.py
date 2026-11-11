"""Phase 24.4 Safe Self-Development Runner (Stable / Candidate Isolation).

Enforces strict separation between the running Stable AIPC Controller and Candidate AIPC
worktrees, verifies actual Python import origins in sanitized child processes
(failing closed with IMPORT_ORIGIN_MISMATCH if imports resolve to Stable AIPC),
keeps verification isolation blocked by default, and prevents automatic Stable
Controller replacement.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path
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


def _write_candidate_isolation_bootstrap(
    bootstrap_dir: Path,
    *,
    stable_root: Path,
    candidate_src: Path,
) -> Path:
    """Write a sitecustomize.py that prunes Stable source roots from sys.path (keeping .venv)."""
    stable_str = str(Path(stable_root).resolve())
    cand_src_str = str(Path(candidate_src).resolve())
    nl = chr(10)
    script = nl.join(
        [
            "import os, sys",
            "from pathlib import Path",
            f"_STABLE = Path({stable_str!r}).resolve()",
            "_STABLE_VENV = (_STABLE / '.venv').resolve()",
            f"_CAND_SRC = Path({cand_src_str!r}).resolve()",
            "_cleaned = []",
            "for _entry in list(sys.path):",
            "    if not _entry:",
            "        continue",
            "    try:",
            "        _p = Path(_entry).resolve()",
            "        if not _p.is_relative_to(_STABLE_VENV) and ("
            "_p == _STABLE or _p.is_relative_to(_STABLE)):",
            "            continue",
            "    except OSError:",
            "        pass",
            "    _cleaned.append(_entry)",
            "sys.path[:] = [str(_CAND_SRC)] + _cleaned",
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

    if (
        _is_stable_source_entry(actual_file, s_root)
        or not actual_file.is_relative_to(c_src)
        or leaked
    ):
        raise ControllerError(
            f"{IMPORT_ORIGIN_MISMATCH}: imported aipc from '{actual_file}' "
            f"(expected under '{c_src}', leaked_stable_paths={leaked})"
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

        origin_report = verify_candidate_import_origin(
            stable_root=s_root,
            candidate_root=c_root,
            python_executable=python_executable,
            use_isolation_bootstrap=True,
            parent_env=parent_env,
        )

        if self.config.verification_isolation == "container":
            return self._run_container_verification(
                candidate_root=c_root,
                origin_report=origin_report,
                pytest_targets=pytest_targets,
                parent_env=parent_env,
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
        origin_report: CandidateImportOriginReport,
        pytest_targets: tuple[str, ...],
        parent_env: Mapping[str, str] | None,
    ) -> CandidateVerificationReceipt:
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
            "-e",
            "PYTHONPATH=/workspace/src",
            "-v",
            f"{candidate_root}:/workspace:ro",
            "-w",
            "/workspace",
            self.config.container_image,
            "python3",
            "-m",
            "pytest",
            "-q",
            "-o",
            "cache_dir=/tmp/.pytest_cache",
            *pytest_targets,
        ]
        assert_argv_no_credentials(argv)
        sanitized_env = build_sanitized_child_env(parent_env)
        assert_child_env_isolated(sanitized_env)

        proc = self._exec(argv, sanitized_env, candidate_root)
        passed = proc.returncode == 0
        return CandidateVerificationReceipt(
            candidate_sha=self.config.candidate_sha,
            isolation_mode="container",
            import_origin_verified=True,
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
            cache_dir = tmp_path / "pytest_cache"
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
        return CandidateVerificationReceipt(
            candidate_sha=self.config.candidate_sha,
            isolation_mode="isolated_subprocess",
            import_origin_verified=True,
            import_origin_report=origin_report,
            sanitized_env_verified=True,
            exit_code=int(proc.returncode),
            passed=passed,
            status_code="PASS" if passed else "TEST_FAILURE",
            summary=(proc.stdout or proc.stderr or "isolated verification complete").strip()[:500],
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
