"""FINAL-04 (ARCH-GIT-01..02) Private Git quiescence verifier and exclusive lock guard."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import os
import re
import stat
import subprocess
import sys
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Literal

from aipc.domain.errors import StateError
from aipc.domain.models import CanonicalRecord
from aipc.hardening.errors import SecretExposureError
from aipc.hardening.secrets import assert_no_secrets
from aipc.storage.json_store import load_json

_REQUIRED_GITIGNORE_EXCLUSIONS: tuple[str, ...] = (
    "stable_c/",
    "worktrees/",
    "logs/",
    "*.lock",
    "*.key",
    ".env*",
)
_NON_QUIESCENT_TASK_STATUSES: frozenset[str] = frozenset(
    {
        "RUNNING",
        "DISPATCHING",
        "IN_PROGRESS",
        "CLAIMING",
        "RECONCILIATION_REQUIRED",
    }
)
_SECRET_SCAN_SUFFIXES: frozenset[str] = frozenset({".json", ".yaml", ".yml", ".md"})
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")


class WorkspaceGitQuiescenceError(StateError):
    """Fail-closed error for invalid workspace paths or lock acquisition failure."""


class WorkspaceQuiescenceReport(CanonicalRecord):
    """Structured report for FINAL-04 Private Git quiescence verification."""

    schema_version: Literal["1.0"] = "1.0"
    workspace_root: str
    lock_path: str
    status: Literal["PASS", "FAIL"]
    passed: bool
    is_git_repo: bool
    checks: dict[str, bool]
    violations: tuple[str, ...]


def _validate_clean_path(path: Path, *, label: str) -> Path:
    if "\x00" in str(path):
        raise WorkspaceGitQuiescenceError(f"Null byte in {label}: {path}")
    if ".." in path.parts:
        raise WorkspaceGitQuiescenceError(f"Parent traversal '..' forbidden in {label}: {path}")
    lexical = path if path.is_absolute() else Path.cwd() / path
    if lexical.parts:
        current = Path(lexical.parts[0])
        for part in lexical.parts[1:]:
            current = current / part
            try:
                st = os.lstat(current)
            except FileNotFoundError:
                continue
            except OSError as exc:
                raise WorkspaceGitQuiescenceError(
                    f"Cannot lstat {label} ({current}): {exc}"
                ) from exc
            if stat.S_ISLNK(st.st_mode):
                raise WorkspaceGitQuiescenceError(f"Symlink forbidden in {label}: {current}")
    return lexical


@contextlib.contextmanager
def acquire_controller_lock(lock_path: Path) -> Iterator[None]:
    """Acquire an exclusive non-blocking controller lock and release it in ``finally``."""
    clean_lock = _validate_clean_path(lock_path, label="controller_lock")
    clean_lock.parent.mkdir(parents=True, exist_ok=True)
    if clean_lock.exists():
        st = os.lstat(clean_lock)
        if stat.S_ISLNK(st.st_mode) or stat.S_ISDIR(st.st_mode):
            raise WorkspaceGitQuiescenceError(f"Invalid controller lock target: {clean_lock}")

    fh = open(clean_lock, "a+b")
    locked = False
    try:
        if sys.platform == "win32":
            import msvcrt

            fh.seek(0)
            try:
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise WorkspaceGitQuiescenceError(
                    f"Cannot acquire exclusive controller lock: {clean_lock}"
                ) from exc
            locked = True
        else:
            import fcntl

            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise WorkspaceGitQuiescenceError(
                    f"Cannot acquire exclusive controller lock: {clean_lock}"
                ) from exc
            locked = True
        yield
    finally:
        try:
            if locked:
                if sys.platform == "win32":
                    import msvcrt

                    with contextlib.suppress(OSError):
                        fh.seek(0)
                        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    with contextlib.suppress(OSError):
                        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        finally:
            fh.close()


def _walk_workspace_files(workspace_root: Path) -> tuple[list[Path], list[str]]:
    """Walk workspace files without following symlinks, skipping ``.git``."""
    files: list[Path] = []
    violations: list[str] = []

    def _visit(directory: Path) -> None:
        try:
            entries = sorted(directory.iterdir(), key=lambda p: p.name)
        except OSError as exc:
            violations.append(f"Cannot list directory {directory}: {exc}")
            return
        for entry in entries:
            if entry.name == ".git":
                continue
            try:
                st = os.lstat(entry)
            except OSError as exc:
                violations.append(f"Cannot lstat {entry}: {exc}")
                continue
            if stat.S_ISLNK(st.st_mode):
                rel = entry.relative_to(workspace_root).as_posix()
                violations.append(f"Symlink forbidden in workspace: {rel}")
                continue
            if stat.S_ISDIR(st.st_mode):
                _visit(entry)
            elif stat.S_ISREG(st.st_mode):
                files.append(entry)

    _visit(workspace_root)
    return files, violations


def _check_git_hygiene(workspace_root: Path) -> list[str]:
    violations: list[str] = []
    gitattributes = workspace_root / ".gitattributes"
    try:
        attr_st = os.lstat(gitattributes)
    except FileNotFoundError:
        violations.append("Missing .gitattributes in workspace root")
        attr_st = None
    except OSError as exc:
        violations.append(f"Cannot inspect .gitattributes: {exc}")
        attr_st = None

    if attr_st is not None:
        if stat.S_ISLNK(attr_st.st_mode) or not stat.S_ISREG(attr_st.st_mode):
            violations.append(".gitattributes must be a regular non-symlink file")
        else:
            try:
                attr_text = gitattributes.read_text(encoding="utf-8")
            except (OSError, UnicodeError) as exc:
                violations.append(f"Cannot read .gitattributes: {exc}")
            else:
                has_eol_lf = any(
                    "eol=lf" in line
                    for line in attr_text.splitlines()
                    if line.strip() and not line.lstrip().startswith("#")
                )
                if not has_eol_lf:
                    violations.append(".gitattributes missing required 'eol=lf' directive")

    gitignore = workspace_root / ".gitignore"
    try:
        ign_st = os.lstat(gitignore)
    except FileNotFoundError:
        violations.append("Missing .gitignore in workspace root")
        ign_st = None
    except OSError as exc:
        violations.append(f"Cannot inspect .gitignore: {exc}")
        ign_st = None

    if ign_st is not None:
        if stat.S_ISLNK(ign_st.st_mode) or not stat.S_ISREG(ign_st.st_mode):
            violations.append(".gitignore must be a regular non-symlink file")
        else:
            try:
                ign_text = gitignore.read_text(encoding="utf-8")
            except (OSError, UnicodeError) as exc:
                violations.append(f"Cannot read .gitignore: {exc}")
            else:
                active_lines = {
                    line.strip().lstrip("/")
                    for line in ign_text.splitlines()
                    if line.strip() and not line.lstrip().startswith("#")
                }
                for req in _REQUIRED_GITIGNORE_EXCLUSIONS:
                    if req not in active_lines:
                        violations.append(f".gitignore missing required exclusion: {req}")
    return violations


def _check_queue_quiescence(workspace_root: Path, projects_subdir: str) -> list[str]:
    violations: list[str] = []
    projects_dir = workspace_root / projects_subdir
    if not projects_dir.exists():
        return violations
    if not projects_dir.is_dir():
        return [f"Projects path is not a directory: {projects_subdir}"]

    for project_dir in sorted(projects_dir.iterdir(), key=lambda p: p.name):
        if not project_dir.is_dir():
            continue
        state_dir = project_dir / "runtime" / "state"
        queue_path = state_dir / "canonical_queue.json"
        try:
            st = os.lstat(queue_path)
        except FileNotFoundError:
            st = None
        except OSError as exc:
            violations.append(f"Cannot inspect queue for {project_dir.name}: {exc}")
            st = None

        if st is not None:
            if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
                violations.append(f"Invalid queue file type for {project_dir.name}")
            else:
                try:
                    queue_data = load_json(queue_path)
                except StateError as exc:
                    violations.append(f"Invalid canonical_queue.json in {project_dir.name}: {exc}")
                else:
                    if not isinstance(queue_data, dict):
                        violations.append(
                            f"Malformed canonical_queue.json in {project_dir.name}: "
                            "root must be a JSON object"
                        )
                    else:
                        if "tasks" in queue_data:
                            raw_tasks = queue_data["tasks"]
                            collection_name = "tasks"
                        elif "items" in queue_data:
                            raw_tasks = queue_data["items"]
                            collection_name = "items"
                        else:
                            raw_tasks = None
                            collection_name = "tasks"
                            violations.append(
                                f"Malformed canonical_queue.json in {project_dir.name}: "
                                "missing 'tasks' list"
                            )

                        if raw_tasks is not None:
                            if not isinstance(raw_tasks, list):
                                violations.append(
                                    f"Malformed canonical_queue.json in {project_dir.name}: "
                                    f"'{collection_name}' must be a list"
                                )
                            else:
                                for idx, task in enumerate(raw_tasks):
                                    if not isinstance(task, dict):
                                        violations.append(
                                            f"Malformed canonical_queue.json in "
                                            f"{project_dir.name}: "
                                            f"task entry at index {idx} must be a JSON object"
                                        )
                                        continue
                                    status = str(task.get("status") or "").strip().upper()
                                    if status in _NON_QUIESCENT_TASK_STATUSES:
                                        contract = task.get("contract")
                                        nested_tid = (
                                            contract.get("task_id")
                                            if isinstance(contract, dict)
                                            else None
                                        )
                                        task_id = str(
                                            nested_tid
                                            or task.get("task_id")
                                            or task.get("id")
                                            or f"index-{idx}"
                                        )
                                        violations.append(
                                            f"Non-quiescent task {task_id} in {project_dir.name}: "
                                            f"status={status}"
                                        )

        ledger_path = state_dir / "repair_budget_ledger.json"
        try:
            l_st = os.lstat(ledger_path)
        except FileNotFoundError:
            continue
        except OSError as exc:
            violations.append(
                f"Cannot inspect repair_budget_ledger.json for {project_dir.name}: {exc}"
            )
            continue

        if stat.S_ISLNK(l_st.st_mode) or not stat.S_ISREG(l_st.st_mode):
            violations.append(f"Invalid repair_budget_ledger.json file type for {project_dir.name}")
            continue

        try:
            ledger_data = load_json(ledger_path)
        except StateError as exc:
            violations.append(f"Invalid repair_budget_ledger.json in {project_dir.name}: {exc}")
            continue

        if not isinstance(ledger_data, dict):
            violations.append(
                f"Malformed repair_budget_ledger.json in {project_dir.name}: "
                "root must be a JSON object"
            )
            continue

        if "reservations" in ledger_data:
            reservations = ledger_data["reservations"]
            if not isinstance(reservations, dict):
                violations.append(
                    f"Malformed repair_budget_ledger.json in {project_dir.name}: "
                    "'reservations' must be a JSON object"
                )
                continue
            for res_id, res_entry in reservations.items():
                if not isinstance(res_entry, dict):
                    violations.append(
                        f"Malformed reservation {res_id} in repair_budget_ledger.json "
                        f"for {project_dir.name}"
                    )
                    continue
                res_status = str(res_entry.get("status") or "").strip().upper()
                if res_status == "RESERVED":
                    violations.append(
                        f"Non-quiescent RESERVED budget entry {res_id} in {project_dir.name}"
                    )
    return violations


def _check_audit_sha256_sidecars(workspace_root: Path, projects_subdir: str) -> list[str]:
    violations: list[str] = []
    projects_dir = workspace_root / projects_subdir
    if not projects_dir.exists() or not projects_dir.is_dir():
        return violations

    for project_dir in sorted(projects_dir.iterdir(), key=lambda p: p.name):
        audit_dir = project_dir / "runtime" / "audit"
        if not audit_dir.exists() or not audit_dir.is_dir():
            continue
        for sidecar in sorted(audit_dir.rglob("*.sha256")):
            rel = sidecar.relative_to(workspace_root).as_posix()
            try:
                sc_st = os.lstat(sidecar)
            except OSError as exc:
                violations.append(f"Cannot lstat sidecar {rel}: {exc}")
                continue
            if stat.S_ISLNK(sc_st.st_mode) or not stat.S_ISREG(sc_st.st_mode):
                violations.append(f"Sidecar is not a regular file: {rel}")
                continue

            try:
                raw_bytes = sidecar.read_bytes()
            except OSError as exc:
                violations.append(f"Cannot read sidecar {rel}: {exc}")
                continue
            if b"\r" in raw_bytes:
                violations.append(f"CRLF/CR line ending forbidden in sidecar: {rel}")
                continue

            try:
                text = raw_bytes.decode("utf-8").strip()
            except UnicodeError:
                violations.append(f"Non-UTF8 sidecar: {rel}")
                continue

            parts = text.split()
            if not parts or _SHA256_RE.fullmatch(parts[0]) is None:
                violations.append(f"Malformed sha256 digest in sidecar: {rel}")
                continue
            expected_digest = parts[0].lower()

            target_path = sidecar.with_name(sidecar.name[: -len(".sha256")])
            if len(parts) >= 2 and Path(parts[-1]).name != target_path.name:
                violations.append(f"Sidecar target filename mismatch in {rel}")
                continue

            try:
                tgt_st = os.lstat(target_path)
            except FileNotFoundError:
                violations.append(f"Missing target file for sidecar {rel}")
                continue
            except OSError as exc:
                violations.append(f"Cannot inspect target for sidecar {rel}: {exc}")
                continue
            if stat.S_ISLNK(tgt_st.st_mode) or not stat.S_ISREG(tgt_st.st_mode):
                violations.append(f"Target for sidecar {rel} is not a regular file")
                continue

            actual_digest = hashlib.sha256(target_path.read_bytes()).hexdigest().lower()
            if actual_digest != expected_digest:
                violations.append(f"SHA256 mismatch for {rel}")
    return violations


def _is_forbidden_git_path(rel_posix: str) -> bool:
    norm = rel_posix.strip("/")
    parts = [p for p in norm.split("/") if p]
    if not parts:
        return False
    if "stable_c" in parts:
        return True
    if norm.startswith("runtime/logs/") or "/runtime/logs/" in f"/{norm}":
        return True
    if norm.startswith("runtime/worktrees/") or "/runtime/worktrees/" in f"/{norm}":
        return True
    filename = parts[-1]
    if filename.endswith(".lock") or filename.endswith(".key") or filename.startswith(".env"):
        return True
    return False


def _run_git_nul_list(workspace_root: Path, args: list[str]) -> tuple[bool, list[str]]:
    try:
        proc = subprocess.run(
            ["git", "-C", str(workspace_root), *args],
            capture_output=True,
            check=False,
        )
    except OSError:
        return False, []
    if proc.returncode != 0:
        return False, []
    entries = [
        item.decode("utf-8", errors="replace") for item in proc.stdout.split(b"\x00") if item
    ]
    return True, entries


def _check_git_index_clean(workspace_root: Path) -> tuple[bool, list[str]]:
    git_entry = workspace_root / ".git"
    if not git_entry.exists():
        return False, []

    ok_tracked, tracked = _run_git_nul_list(workspace_root, ["ls-files", "-z"])
    ok_staged, staged = _run_git_nul_list(workspace_root, ["diff", "--cached", "--name-only", "-z"])
    if not ok_tracked or not ok_staged:
        return True, ["Git index inspection failed; cannot verify tracked/staged paths"]

    violations: list[str] = []
    seen: set[str] = set()
    for rel_path in [*tracked, *staged]:
        if rel_path in seen:
            continue
        seen.add(rel_path)
        if _is_forbidden_git_path(rel_path):
            violations.append(f"Forbidden path tracked or staged in Git: {rel_path}")
    return True, violations


def verify_workspace_git_quiescence(
    workspace_root: Path | str,
    *,
    lock_path: Path | str | None = None,
    projects_subdir: str = "projects",
) -> WorkspaceQuiescenceReport:
    """Verify FINAL-04 workspace quiescence under an exclusive ``controller.lock``."""
    if (
        not isinstance(projects_subdir, str)
        or not projects_subdir
        or "/" in projects_subdir
        or "\\" in projects_subdir
        or ".." in projects_subdir
        or "\x00" in projects_subdir
    ):
        raise WorkspaceGitQuiescenceError(f"Invalid projects_subdir: {projects_subdir!r}")

    clean_ws = _validate_clean_path(Path(workspace_root), label="workspace_root")
    if not clean_ws.exists() or not clean_ws.is_dir():
        raise WorkspaceGitQuiescenceError(
            f"workspace_root must be an existing directory: {clean_ws}"
        )

    resolved_lock = (
        _validate_clean_path(Path(lock_path), label="lock_path")
        if lock_path is not None
        else clean_ws / "controller.lock"
    )

    with acquire_controller_lock(resolved_lock):
        all_files, walk_violations = _walk_workspace_files(clean_ws)

        hygiene_violations = _check_git_hygiene(clean_ws)

        manifest_violations = [
            f"Forbidden checkpoint_manifest.json found: {f.relative_to(clean_ws).as_posix()}"
            for f in all_files
            if f.name == "checkpoint_manifest.json"
        ]

        queue_violations = _check_queue_quiescence(clean_ws, projects_subdir)
        sidecar_violations = _check_audit_sha256_sidecars(clean_ws, projects_subdir)

        secret_violations: list[str] = []
        for candidate_file in all_files:
            if candidate_file.suffix.lower() not in _SECRET_SCAN_SUFFIXES:
                continue
            rel = candidate_file.relative_to(clean_ws).as_posix()
            try:
                content = candidate_file.read_text(encoding="utf-8")
                assert_no_secrets(content)
            except SecretExposureError:
                secret_violations.append(f"Secret detected in workspace file: {rel}")
            except (OSError, UnicodeError):
                secret_violations.append(f"Cannot read workspace text file as UTF-8: {rel}")

        is_git_repo, git_index_violations = _check_git_index_clean(clean_ws)

    checks = {
        "git_hygiene": not hygiene_violations,
        "no_checkpoint_manifest": not manifest_violations,
        "queue_quiescence": not queue_violations,
        "audit_sha256_integrity": not sidecar_violations,
        "no_secrets": not secret_violations,
        "git_index_clean": not git_index_violations,
        "no_workspace_symlinks": not walk_violations,
    }
    all_violations = (
        *walk_violations,
        *hygiene_violations,
        *manifest_violations,
        *queue_violations,
        *sidecar_violations,
        *secret_violations,
        *git_index_violations,
    )
    passed = all(checks.values()) and not all_violations
    return WorkspaceQuiescenceReport(
        workspace_root=str(clean_ws),
        lock_path=str(resolved_lock),
        status="PASS" if passed else "FAIL",
        passed=passed,
        is_git_repo=is_git_repo,
        checks=checks,
        violations=all_violations,
    )


def _build_cli_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m aipc.project.workspace_git_checkpoint",
        description="Verify FINAL-04 Private Git workspace quiescence under controller.lock.",
    )
    parser.add_argument("--workspace-root", type=Path, required=True)
    parser.add_argument("--lock-path", type=Path, default=None)
    parser.add_argument("--projects-subdir", type=str, default="projects")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entrypoint for FINAL-04 workspace quiescence verification."""
    parser = _build_cli_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    report = verify_workspace_git_quiescence(
        args.workspace_root,
        lock_path=args.lock_path,
        projects_subdir=args.projects_subdir,
    )
    print(report.model_dump_json(indent=2))
    return 0 if report.passed else 1


if __name__ == "__main__":
    sys.exit(main())
