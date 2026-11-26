"""Milestone D D03: FINAL-05 isolation, deterministic closeout, and sealed archive checks.

Provides:
- ``IsolationPolicyError`` and ``IsolationLevelDisclosure`` (Levels A, B, and C Windows host
  shared-profile disclosure for ``agy.exe`` and ``codex.cmd``).
- ``build_minimal_docker_verify_argv`` enforcing ``--network none``, read-only root, and
  fail-closed rejection of mounts overlapping ``state/``, ``audit/approvals/``,
  ``repair_budget_ledger.json``, ``canonical_queue.json``, ``*.key``, ``.aipc_operator``,
  or ``forbidden_mount_roots``.
- ``export_release_closeout_zip`` and ``verify_and_restore_closeout_zip`` for deterministic,
  LF-safe, SHA-256-verified closeout archives.
- ``verify_sealed_archive_untouched`` performing read-only ``os.scandir`` + ``os.lstat``
  verification against a ``scandir_lstat_content_sha256`` baseline manifest.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import stat
import tempfile
import zipfile
from collections.abc import Sequence
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Literal, NamedTuple

from pydantic import StrictBool

from aipc.domain.models import CanonicalRecord, NonBlank, NonNegativeInt
from aipc.hardening.env_isolation import ChildEnvironmentIsolationError, assert_argv_no_credentials
from aipc.hardening.secrets import assert_no_secrets

_FIXED_ZIP_DATE_TIME: tuple[int, int, int, int, int, int] = (2026, 1, 1, 0, 0, 0)
_HASH_MODE_SCANDIR_LSTAT = "scandir_lstat_content_sha256"
_FORBIDDEN_MOUNT_FILENAMES: frozenset[str] = frozenset(
    {
        "repair_budget_ledger.json",
        "canonical_queue.json",
        ".aipc_operator",
    }
)
_SHA256_CHUNK_BYTES = 65536
_DOCKER_REGISTRY_RE = (
    r"(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]*[a-zA-Z0-9])?"
    r"(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9-]*[a-zA-Z0-9])?)*(?::[0-9]{1,5})?/)"
)
_DOCKER_REPO_SEG_RE = r"[a-z0-9]+(?:(?:[._]|__|[-]+)[a-z0-9]+)*"
_DOCKER_NAME_RE = rf"(?:{_DOCKER_REGISTRY_RE})?{_DOCKER_REPO_SEG_RE}(?:/{_DOCKER_REPO_SEG_RE})*"
_DOCKER_TAG_RE = r"(?::[A-Za-z0-9_][A-Za-z0-9._-]{0,127})"
_DOCKER_DIGEST_RE = r"(?:@sha256:[0-9a-f]{64})"
_DOCKER_IMAGE_REF_RE = re.compile(rf"^{_DOCKER_NAME_RE}{_DOCKER_TAG_RE}?{_DOCKER_DIGEST_RE}?$")


def _validate_docker_image_ref(image: str) -> str:
    if not isinstance(image, str) or not image:
        raise IsolationPolicyError("image must be a non-empty string")
    if image.startswith("-"):
        raise IsolationPolicyError(f"image cannot start with '-': {image!r}")
    if any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in image):
        raise IsolationPolicyError(f"image cannot contain whitespace or control chars: {image!r}")
    if _DOCKER_IMAGE_REF_RE.fullmatch(image) is None:
        raise IsolationPolicyError(f"Invalid Docker image reference: {image!r}")
    name_part = image.split("@", 1)[0]
    last_slash = name_part.rfind("/")
    if ":" in name_part[last_slash + 1 :]:
        name_part = name_part[: name_part.rfind(":")]
    slash_idx = name_part.find("/")
    if slash_idx != -1:
        first_seg = name_part[:slash_idx]
        if "." in first_seg or ":" in first_seg or first_seg == "localhost":
            repo_name = name_part[slash_idx + 1 :]
        else:
            repo_name = name_part
    else:
        repo_name = name_part
    if len(repo_name) > 255:
        raise IsolationPolicyError(
            f"Docker image repository name exceeds 255 characters ({len(repo_name)}): {image!r}"
        )
    return image


class IsolationPolicyError(ValueError):
    """Fail-closed error for FINAL-05 isolation, closeout archive, or sealed-archive violations."""


class IsolationLevelDisclosure(CanonicalRecord):
    """Explicit 3-level isolation boundary disclosure (FINAL-05)."""

    schema_version: Literal["1.0"] = "1.0"
    level_a_path_isolation: NonBlank = (
        "Level A (Path/Process Isolation): Controller-enforced disjoint project runtime, "
        "worktree, and external trust roots with lexical/lstat symlink rejection and "
        "sanitized child-process environments."
    )
    level_b_minimal_docker_mounts: NonBlank = (
        "Level B (Minimal Docker Mounts): Read-only candidate worktree and optional "
        "read-only trusted harness mounted under --network none --read-only --cap-drop ALL, "
        "excluding state/, audit/approvals/, ledgers, queues, *.key, and .aipc_operator."
    )
    level_c_host_os_disclosure: NonBlank = (
        "Level C (Windows Host OS / Shared User-Profile Boundary): Host-invoked "
        "subscription CLIs (agy.exe and codex.cmd) execute under the shared Windows OS "
        "user profile without kernel/container namespace isolation; only containerized "
        "verification steps run inside Level B Docker boundaries."
    )


class CloseoutExportReceipt(CanonicalRecord):
    """Deterministic closeout archive export receipt."""

    schema_version: Literal["1.0"] = "1.0"
    zip_path: NonBlank
    sha256_path: NonBlank
    zip_sha256: NonBlank
    file_count: NonNegativeInt
    included_files: tuple[NonBlank, ...] = ()


class CloseoutRestoreReceipt(CanonicalRecord):
    """Verified closeout archive restore receipt."""

    schema_version: Literal["1.0"] = "1.0"
    zip_path: NonBlank
    restore_target_dir: NonBlank
    zip_sha256: NonBlank
    restored_file_count: NonNegativeInt
    restored_files: tuple[NonBlank, ...] = ()


class SealedArchiveVerificationReport(CanonicalRecord):
    """Read-only sealed archive integrity verification report."""

    schema_version: Literal["1.0"] = "1.0"
    hash_mode: Literal["scandir_lstat_content_sha256"] = _HASH_MODE_SCANDIR_LSTAT
    passed: StrictBool
    expected_digest: NonBlank
    observed_digest: NonBlank
    expected_file_count: NonNegativeInt
    observed_file_count: NonNegativeInt
    archive_root: NonBlank
    baseline_manifest_path: NonBlank


def _assert_not_symlink_lstat(path: Path, *, label: str) -> os.stat_result | None:
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return None
    except NotADirectoryError as exc:
        raise IsolationPolicyError(f"Non-directory ancestor for {label}: {path}") from exc
    except OSError as exc:
        raise IsolationPolicyError(f"Cannot lstat {label} ({path}): {exc}") from exc
    is_reparse = bool(
        getattr(st, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )
    if stat.S_ISLNK(st.st_mode) or is_reparse:
        raise IsolationPolicyError(f"Symlink/reparse path is forbidden for {label}: {path}")
    return st


def _validate_clean_path(raw: Path | str, *, label: str) -> Path:
    if not isinstance(raw, Path | str) or not str(raw).strip():
        raise IsolationPolicyError(f"{label} must be a non-empty path")
    raw_str = str(raw)
    if "\x00" in raw_str:
        raise IsolationPolicyError(f"Null byte in {label} path")
    path = Path(raw_str)
    if ".." in path.parts:
        raise IsolationPolicyError(f"Parent traversal '..' is forbidden in {label}: {path}")
    lexical = path if path.is_absolute() else Path.cwd() / path
    if lexical.parts:
        current = Path(lexical.parts[0])
        _assert_not_symlink_lstat(current, label=label)
        for part in lexical.parts[1:]:
            current = current / part
            _assert_not_symlink_lstat(current, label=label)
    return lexical


def _paths_overlap(path_a: Path, path_b: Path) -> bool:
    return path_a == path_b or path_a.is_relative_to(path_b) or path_b.is_relative_to(path_a)


def _is_forbidden_mount_component(path: Path) -> str | None:
    lower_parts = [p.lower() for p in path.parts]
    for idx, part in enumerate(lower_parts):
        if part == "state":
            return "state/"
        if part == "audit" and idx + 1 < len(lower_parts) and lower_parts[idx + 1] == "approvals":
            return "audit/approvals/"
        if part == ".aipc_operator":
            return ".aipc_operator"
        if part in _FORBIDDEN_MOUNT_FILENAMES:
            return part
        if part.endswith(".key"):
            return "*.key"
    return None


def _scan_mount_for_forbidden_descendants(mount_dir: Path, *, label: str) -> None:
    """Reject mount directories that contain forbidden state, approvals, queues, or keys."""
    stack: list[Path] = [mount_dir]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as it:
                entries = sorted(it, key=lambda e: e.name)
        except OSError as exc:
            raise IsolationPolicyError(f"Cannot scan {label} ({current}): {exc}") from exc
        for entry in entries:
            entry_path = Path(entry.path)
            st = _assert_not_symlink_lstat(entry_path, label=f"{label}_entry")
            if st is None:
                continue
            name_lower = entry.name.lower()
            if name_lower in _FORBIDDEN_MOUNT_FILENAMES or name_lower.endswith(".key"):
                raise IsolationPolicyError(
                    f"{label} overlaps forbidden artifact '{entry.name}' at {entry_path}"
                )
            if stat.S_ISDIR(st.st_mode):
                if name_lower == "state":
                    raise IsolationPolicyError(
                        f"{label} overlaps forbidden directory 'state/' at {entry_path}"
                    )
                if name_lower == "approvals" and current.name.lower() == "audit":
                    raise IsolationPolicyError(
                        f"{label} overlaps forbidden directory 'audit/approvals/' at {entry_path}"
                    )
                stack.append(entry_path)


def _validate_docker_mount(
    mount_raw: Path | str,
    *,
    label: str,
    forbidden_roots: Sequence[Path],
) -> Path:
    lexical = _validate_clean_path(mount_raw, label=label)
    component_hit = _is_forbidden_mount_component(lexical)
    if component_hit is not None:
        raise IsolationPolicyError(f"{label} ({lexical}) overlaps forbidden path '{component_hit}'")

    st = _assert_not_symlink_lstat(lexical, label=label)
    if st is None or not stat.S_ISDIR(st.st_mode):
        raise IsolationPolicyError(f"{label} must be an existing directory: {lexical}")

    resolved = lexical.resolve()
    resolved_hit = _is_forbidden_mount_component(resolved)
    if resolved_hit is not None:
        raise IsolationPolicyError(
            f"{label} ({resolved}) resolves into forbidden path '{resolved_hit}'"
        )

    for idx, forb_raw in enumerate(forbidden_roots):
        forb_lex = _validate_clean_path(forb_raw, label=f"forbidden_mount_roots[{idx}]")
        forb_res = forb_lex.resolve()
        if _paths_overlap(lexical, forb_lex) or _paths_overlap(resolved, forb_res):
            raise IsolationPolicyError(
                f"{label} ({resolved}) overlaps forbidden_mount_roots[{idx}] ({forb_res})"
            )

    _scan_mount_for_forbidden_descendants(resolved, label=label)
    return resolved


def build_minimal_docker_verify_argv(
    *,
    image: str,
    candidate_worktree: Path | str,
    command: Sequence[str],
    trusted_harness_dir: Path | str | None = None,
    network_mode: str = "none",
    read_only_root: bool = True,
    forbidden_mount_roots: Sequence[Path | str] = (),
) -> tuple[tuple[str, ...], IsolationLevelDisclosure]:
    """Build a fail-closed FINAL-05 minimal Docker verification argv and isolation disclosure."""
    validated_image = _validate_docker_image_ref(image)
    if network_mode != "none":
        raise IsolationPolicyError(f"FINAL-05 requires network_mode='none', got {network_mode!r}")
    if type(read_only_root) is not bool:
        raise IsolationPolicyError("read_only_root must be a boolean")
    if not read_only_root:
        raise IsolationPolicyError("FINAL-05 requires read_only_root=True")
    if isinstance(command, str | bytes) or not command:
        raise IsolationPolicyError("command must be a non-empty sequence of strings")

    cmd_parts: list[str] = []
    for item in command:
        if not isinstance(item, str) or not item.strip() or "\x00" in item:
            raise IsolationPolicyError("command entries must be non-empty strings")
        cmd_parts.append(item)

    forbidden_paths = [Path(r) for r in forbidden_mount_roots]
    cand_resolved = _validate_docker_mount(
        candidate_worktree,
        label="candidate_worktree",
        forbidden_roots=forbidden_paths,
    )
    harness_resolved: Path | None = None
    if trusted_harness_dir is not None:
        harness_resolved = _validate_docker_mount(
            trusted_harness_dir,
            label="trusted_harness_dir",
            forbidden_roots=forbidden_paths,
        )

    argv: list[str] = [
        "docker",
        "run",
        "--rm",
        "--network",
        "none",
        "--read-only",
    ]
    argv.extend(
        [
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
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=256m",
            "-e",
            "HOME=/tmp",
            "-e",
            "PYTHONDONTWRITEBYTECODE=1",
            "-v",
            f"{cand_resolved}:/workspace:ro",
        ]
    )
    if harness_resolved is not None:
        argv.extend(["-v", f"{harness_resolved}:/aipc_harness:ro"])
    argv.extend(["-w", "/workspace", validated_image, *cmd_parts])

    try:
        checked = assert_argv_no_credentials(argv)
    except ChildEnvironmentIsolationError as exc:
        raise IsolationPolicyError(f"Docker argv failed credential isolation: {exc}") from exc

    return checked, IsolationLevelDisclosure()


def _is_excluded_closeout_entry(rel_parts: tuple[str, ...], name: str) -> bool:
    lower_parts = tuple(p.lower() for p in rel_parts)
    if any(p in {"worktrees", "logs"} for p in lower_parts):
        return True
    low = name.lower()
    return low.endswith((".lock", ".key")) or low == ".env" or low.startswith(".env.")


def _collect_closeout_files(
    runtime_root: Path,
    *,
    include_audit: bool,
    include_state: bool,
    include_approved_plans: bool,
) -> list[tuple[str, Path]]:
    selected_roots: list[Path] = []
    if include_audit:
        selected_roots.append(runtime_root / "audit")
    if include_state:
        selected_roots.append(runtime_root / "state")
    if include_approved_plans:
        selected_roots.append(runtime_root / "plans" / "approved")

    collected: list[tuple[str, Path]] = []
    for root_dir in selected_roots:
        st_root = _assert_not_symlink_lstat(root_dir, label="closeout_subdir")
        if st_root is None:
            continue
        if not stat.S_ISDIR(st_root.st_mode):
            raise IsolationPolicyError(f"Expected directory in runtime_root: {root_dir}")

        stack: list[Path] = [root_dir]
        while stack:
            current = stack.pop()
            try:
                with os.scandir(current) as it:
                    entries = sorted(it, key=lambda e: e.name)
            except OSError as exc:
                raise IsolationPolicyError(f"Cannot scan {current}: {exc}") from exc
            for entry in entries:
                entry_path = Path(entry.path)
                st = _assert_not_symlink_lstat(entry_path, label="closeout_entry")
                if st is None:
                    continue
                rel = entry_path.relative_to(runtime_root)
                if _is_excluded_closeout_entry(rel.parts, entry.name):
                    continue
                if stat.S_ISDIR(st.st_mode):
                    stack.append(entry_path)
                elif stat.S_ISREG(st.st_mode):
                    collected.append((rel.as_posix(), entry_path))
                else:
                    raise IsolationPolicyError(
                        f"Unsupported file type in closeout source: {entry_path}"
                    )

    collected.sort(key=lambda item: item[0])
    return collected


def _compute_file_sha256(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(_SHA256_CHUNK_BYTES)
            if not chunk:
                break
            hasher.update(chunk)
    return hasher.hexdigest()


def export_release_closeout_zip(
    project_runtime_root: Path | str,
    output_zip_path: Path | str,
    *,
    include_audit: bool = True,
    include_state: bool = True,
    include_approved_plans: bool = True,
) -> CloseoutExportReceipt:
    """Create a deterministic LF-safe release closeout ``.zip`` and ``.sha256`` sidecar."""
    rt_lex = _validate_clean_path(project_runtime_root, label="project_runtime_root")
    rt_st = _assert_not_symlink_lstat(rt_lex, label="project_runtime_root")
    if rt_st is None or not stat.S_ISDIR(rt_st.st_mode):
        raise IsolationPolicyError(f"project_runtime_root must be an existing directory: {rt_lex}")
    rt_resolved = rt_lex.resolve()

    out_lex = _validate_clean_path(output_zip_path, label="output_zip_path")
    _assert_not_symlink_lstat(out_lex, label="output_zip_path")
    out_lex.parent.mkdir(parents=True, exist_ok=True)
    out_resolved = out_lex.parent.resolve() / out_lex.name
    if _paths_overlap(out_resolved, rt_resolved):
        raise IsolationPolicyError("output_zip_path cannot reside inside project_runtime_root")

    files = _collect_closeout_files(
        rt_resolved,
        include_audit=include_audit,
        include_state=include_state,
        include_approved_plans=include_approved_plans,
    )

    tmp_zip: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=out_resolved.parent,
            prefix=f".{out_resolved.name}.",
            suffix=".tmp",
            delete=False,
        ) as tmp_handle:
            tmp_zip = Path(tmp_handle.name)
            with zipfile.ZipFile(tmp_handle, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
                for rel_posix, src_path in files:
                    raw_bytes = src_path.read_bytes()
                    zinfo = zipfile.ZipInfo(
                        filename=rel_posix,
                        date_time=_FIXED_ZIP_DATE_TIME,
                    )
                    zinfo.compress_type = zipfile.ZIP_DEFLATED
                    zinfo.create_system = 3
                    zinfo.external_attr = 0o644 << 16
                    zf.writestr(zinfo, raw_bytes)
            tmp_handle.flush()
            os.fsync(tmp_handle.fileno())

        _assert_not_symlink_lstat(tmp_zip, label="output_zip_tmp")
        os.replace(tmp_zip, out_resolved)
        tmp_zip = None
    except IsolationPolicyError:
        raise
    except OSError as exc:
        raise IsolationPolicyError(f"Failed writing closeout zip {out_resolved}: {exc}") from exc
    finally:
        if tmp_zip is not None:
            tmp_zip.unlink(missing_ok=True)

    zip_sha256 = _compute_file_sha256(out_resolved)
    sha_path = Path(f"{out_resolved}.sha256")
    _assert_not_symlink_lstat(sha_path, label="output_sha256_path")
    sha_content = f"{zip_sha256}  {out_resolved.name}\n"

    tmp_sha: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=sha_path.parent,
            prefix=f".{sha_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as sha_handle:
            tmp_sha = Path(sha_handle.name)
            sha_handle.write(sha_content)
            sha_handle.flush()
            os.fsync(sha_handle.fileno())
        os.replace(tmp_sha, sha_path)
        tmp_sha = None
    except OSError as exc:
        raise IsolationPolicyError(f"Failed writing closeout sha256 {sha_path}: {exc}") from exc
    finally:
        if tmp_sha is not None:
            tmp_sha.unlink(missing_ok=True)

    return CloseoutExportReceipt(
        zip_path=str(out_resolved),
        sha256_path=str(sha_path),
        zip_sha256=zip_sha256,
        file_count=len(files),
        included_files=tuple(rel for rel, _ in files),
    )


_WINDOWS_RESERVED_DEVICE_NAMES: frozenset[str] = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }
)
_UNSAFE_ZIP_ENTRY_CHARS: frozenset[str] = frozenset(':<>"|?*')


def _validate_zip_entry_name(name: str) -> PurePosixPath:
    if not name or "\\" in name or any(ord(c) < 32 or c in _UNSAFE_ZIP_ENTRY_CHARS for c in name):
        raise IsolationPolicyError(f"Unsafe zip entry name: {name!r}")
    if name.startswith("/") or PureWindowsPath(name).drive or PurePosixPath(name).is_absolute():
        raise IsolationPolicyError(f"Absolute zip entry path rejected: {name!r}")
    posix = PurePosixPath(name)
    if any(part in ("", ".", "..") for part in posix.parts):
        raise IsolationPolicyError(f"Zip-slip or invalid relative path rejected: {name!r}")
    for part in posix.parts:
        if part.endswith((".", " ")):
            raise IsolationPolicyError(
                f"Ambiguous trailing dot/space zip entry segment rejected: {name!r}"
            )
        stem = part.split(".", 1)[0].upper()
        if stem in _WINDOWS_RESERVED_DEVICE_NAMES:
            raise IsolationPolicyError(
                f"Reserved Windows device name in zip entry rejected: {name!r}"
            )
    return posix


def verify_and_restore_closeout_zip(
    zip_path: Path | str,
    restore_target_dir: Path | str,
    *,
    expected_sha256: str | None = None,
) -> CloseoutRestoreReceipt:
    """Verify closeout ``.zip`` against its ``.sha256`` sidecar and safely extract."""
    zip_lex = _validate_clean_path(zip_path, label="zip_path")
    zip_st = _assert_not_symlink_lstat(zip_lex, label="zip_path")
    if zip_st is None or not stat.S_ISREG(zip_st.st_mode):
        raise IsolationPolicyError(f"Closeout zip not found: {zip_lex}")
    zip_resolved = zip_lex.resolve()

    sha_path = Path(f"{zip_resolved}.sha256")
    sha_st = _assert_not_symlink_lstat(sha_path, label="zip_sha256_sidecar")
    if sha_st is None or not stat.S_ISREG(sha_st.st_mode):
        raise IsolationPolicyError(f"Missing closeout .sha256 sidecar: {sha_path}")

    sidecar_tokens = sha_path.read_text(encoding="utf-8").strip().split()
    if not sidecar_tokens:
        raise IsolationPolicyError(f"Empty closeout .sha256 sidecar: {sha_path}")
    sidecar_digest = sidecar_tokens[0].lower()
    if len(sidecar_digest) != 64 or any(c not in "0123456789abcdef" for c in sidecar_digest):
        raise IsolationPolicyError(f"Malformed digest in .sha256 sidecar: {sha_path}")

    actual_digest = _compute_file_sha256(zip_resolved)
    if not hmac.compare_digest(actual_digest, sidecar_digest):
        raise IsolationPolicyError(
            f"Closeout zip SHA-256 mismatch (expected {sidecar_digest}, got {actual_digest})"
        )
    if expected_sha256 is not None:
        clean_expected = expected_sha256.strip().lower()
        if not hmac.compare_digest(actual_digest, clean_expected):
            raise IsolationPolicyError(
                f"Closeout zip SHA-256 mismatch against expected_sha256 ({clean_expected})"
            )

    target_lex = _validate_clean_path(restore_target_dir, label="restore_target_dir")
    _assert_not_symlink_lstat(target_lex, label="restore_target_dir")
    target_lex.mkdir(parents=True, exist_ok=True)
    target_resolved = target_lex.resolve()

    restored_files: list[str] = []
    try:
        with zipfile.ZipFile(zip_resolved, mode="r") as zf:
            infos = zf.infolist()
            validated_entries: list[tuple[zipfile.ZipInfo, Path, str]] = []
            seen_files: dict[str, str] = {}
            seen_explicit_dirs: dict[str, str] = {}
            seen_implicit_dirs: dict[str, str] = {}
            for info in infos:
                unix_mode = info.external_attr >> 16
                if stat.S_ISLNK(unix_mode):
                    raise IsolationPolicyError(
                        f"Symlink entry rejected in closeout zip: {info.filename!r}"
                    )
                is_dir_entry = info.is_dir()
                raw_member = info.filename.rstrip("/") if is_dir_entry else info.filename
                rel_posix = _validate_zip_entry_name(raw_member)
                norm_name = rel_posix.as_posix()
                norm_cf = norm_name.casefold()
                for parent in rel_posix.parents:
                    if parent == PurePosixPath("."):
                        continue
                    p_name = parent.as_posix()
                    p_cf = p_name.casefold()
                    if p_cf in seen_files:
                        raise IsolationPolicyError(
                            f"File/directory collision rejected in closeout zip: {norm_name!r}"
                        )
                    if (p_cf in seen_explicit_dirs and seen_explicit_dirs[p_cf] != p_name) or (
                        p_cf in seen_implicit_dirs and seen_implicit_dirs[p_cf] != p_name
                    ):
                        raise IsolationPolicyError(
                            f"Case-colliding directory path rejected in closeout zip: {norm_name!r}"
                        )
                    seen_implicit_dirs.setdefault(p_cf, p_name)
                if is_dir_entry:
                    if norm_cf in seen_explicit_dirs:
                        raise IsolationPolicyError(
                            f"Duplicate zip member name rejected in closeout zip: {norm_name!r}"
                        )
                    if norm_cf in seen_files:
                        raise IsolationPolicyError(
                            f"File/directory collision rejected in closeout zip: {norm_name!r}"
                        )
                    if norm_cf in seen_implicit_dirs and seen_implicit_dirs[norm_cf] != norm_name:
                        raise IsolationPolicyError(
                            f"Case-colliding directory path rejected in closeout zip: {norm_name!r}"
                        )
                    seen_explicit_dirs[norm_cf] = norm_name
                    continue
                if norm_cf in seen_files:
                    raise IsolationPolicyError(
                        f"Duplicate zip member name rejected in closeout zip: {norm_name!r}"
                    )
                if norm_cf in seen_explicit_dirs or norm_cf in seen_implicit_dirs:
                    raise IsolationPolicyError(
                        f"File/directory collision rejected in closeout zip: {norm_name!r}"
                    )
                seen_files[norm_cf] = norm_name
                if _is_excluded_closeout_entry(rel_posix.parts, rel_posix.name):
                    raise IsolationPolicyError(
                        f"Excluded/sensitive entry rejected in closeout zip: {info.filename!r}"
                    )
                dest_path = target_resolved.joinpath(*rel_posix.parts)
                if not dest_path.resolve(strict=False).is_relative_to(target_resolved):
                    raise IsolationPolicyError(
                        f"Zip entry escapes restore_target_dir: {info.filename!r}"
                    )
                validated_entries.append((info, dest_path, norm_name))

            for info, dest_path, rel_str in validated_entries:
                dest_path.parent.mkdir(parents=True, exist_ok=True)
                _assert_not_symlink_lstat(dest_path.parent, label="restore_parent_dir")
                _assert_not_symlink_lstat(dest_path, label="restore_dest_file")
                data = zf.read(info.filename)
                dest_path.write_bytes(data)
                restored_files.append(rel_str)
    except zipfile.BadZipFile as exc:
        raise IsolationPolicyError(f"Corrupt closeout zip {zip_resolved}: {exc}") from exc

    return CloseoutRestoreReceipt(
        zip_path=str(zip_resolved),
        restore_target_dir=str(target_resolved),
        zip_sha256=actual_digest,
        restored_file_count=len(restored_files),
        restored_files=tuple(sorted(restored_files)),
    )


class _SealedArchiveRecord(NamedTuple):
    relpath: str
    size: int
    entry_kind: Literal["regular_file", "reparse_point", "other"]
    content_sha256: str | None
    metadata_sha256: str | None


def _compute_archive_file_content_sha256(path: Path) -> str:
    hasher = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            while True:
                chunk = handle.read(_SHA256_CHUNK_BYTES)
                if not chunk:
                    break
                hasher.update(chunk)
    except OSError as exc:
        raise IsolationPolicyError(f"Cannot read archive file {path}: {exc}") from exc
    return hasher.hexdigest()


def _is_reparse_or_symlink(st: os.stat_result) -> bool:
    is_reparse_attr = bool(
        getattr(st, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    )
    has_reparse_tag = getattr(st, "st_reparse_tag", 0) != 0
    return stat.S_ISLNK(st.st_mode) or is_reparse_attr or has_reparse_tag


def _compute_reparse_metadata_sha256(path: Path, st: os.stat_result) -> str:
    reparse_tag = int(getattr(st, "st_reparse_tag", 0))
    file_attributes = int(getattr(st, "st_file_attributes", 0))
    try:
        target = os.readlink(path)
    except (OSError, ValueError):
        payload = (
            f"reparse_tag={reparse_tag}|file_attributes={file_attributes}"
            f"|st_size={int(st.st_size)}|st_mode={int(st.st_mode)}"
        )
    else:
        payload = f"readlink={target}|reparse_tag={reparse_tag}|st_mode={int(st.st_mode)}"
    return hashlib.sha256(payload.encode("utf-8", errors="surrogateescape")).hexdigest()


def _collect_sealed_archive_records(root_resolved: Path) -> list[_SealedArchiveRecord]:
    records: list[_SealedArchiveRecord] = []
    stack: list[Path] = [root_resolved]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as it:
                entries = sorted(it, key=lambda e: e.name)
        except OSError as exc:
            raise IsolationPolicyError(f"Cannot scandir archive {current}: {exc}") from exc
        for entry in entries:
            entry_path = Path(entry.path)
            try:
                st = os.lstat(entry_path)
            except OSError as exc:
                raise IsolationPolicyError(
                    f"Cannot lstat archive entry {entry_path}: {exc}"
                ) from exc
            relpath = entry_path.relative_to(root_resolved).as_posix()
            if _is_reparse_or_symlink(st):
                records.append(
                    _SealedArchiveRecord(
                        relpath=relpath,
                        size=int(st.st_size),
                        entry_kind="reparse_point",
                        content_sha256=None,
                        metadata_sha256=_compute_reparse_metadata_sha256(entry_path, st),
                    )
                )
            elif stat.S_ISDIR(st.st_mode):
                stack.append(entry_path)
            elif stat.S_ISREG(st.st_mode):
                records.append(
                    _SealedArchiveRecord(
                        relpath=relpath,
                        size=int(st.st_size),
                        entry_kind="regular_file",
                        content_sha256=_compute_archive_file_content_sha256(entry_path),
                        metadata_sha256=None,
                    )
                )
            else:
                records.append(
                    _SealedArchiveRecord(
                        relpath=relpath,
                        size=int(st.st_size),
                        entry_kind="other",
                        content_sha256=None,
                        metadata_sha256=hashlib.sha256(
                            f"non_regular_mode:{stat.S_IFMT(st.st_mode)}".encode()
                        ).hexdigest(),
                    )
                )

    records.sort(key=lambda item: item.relpath)
    return records


def compute_sealed_archive_manifest(archive_root: Path | str) -> tuple[str, int]:
    """Read-only ``os.scandir`` + ``os.lstat`` content-hashed walk."""
    root_lex = _validate_clean_path(archive_root, label="archive_root")
    root_st = _assert_not_symlink_lstat(root_lex, label="archive_root")
    if root_st is None or not stat.S_ISDIR(root_st.st_mode):
        raise IsolationPolicyError(f"archive_root must be an existing directory: {root_lex}")
    root_resolved = root_lex.resolve()

    records = _collect_sealed_archive_records(root_resolved)
    manifest_lines = "".join(
        f"{rec.relpath}\t{rec.size}\t{rec.entry_kind}\t"
        f"{rec.content_sha256 or ''}\t{rec.metadata_sha256 or ''}\n"
        for rec in records
    )
    digest = hashlib.sha256(manifest_lines.encode("utf-8")).hexdigest()
    return digest, len(records)


def write_sealed_archive_baseline_manifest(
    archive_root: Path | str,
    manifest_path: Path | str,
) -> dict[str, Any]:
    """Write a baseline manifest JSON outside ``archive_root`` without mutating ``archive_root``."""
    root_lex = _validate_clean_path(archive_root, label="archive_root")
    root_st = _assert_not_symlink_lstat(root_lex, label="archive_root")
    if root_st is None or not stat.S_ISDIR(root_st.st_mode):
        raise IsolationPolicyError(f"archive_root must be an existing directory: {root_lex}")
    root_resolved = root_lex.resolve()

    out_lex = _validate_clean_path(manifest_path, label="baseline_manifest_path")
    _assert_not_symlink_lstat(out_lex, label="baseline_manifest_path")
    out_resolved = out_lex.resolve(strict=False)
    if _paths_overlap(out_lex, root_lex) or _paths_overlap(out_resolved, root_resolved):
        raise IsolationPolicyError(
            f"baseline_manifest_path ({out_resolved}) cannot reside inside "
            f"or alias archive_root ({root_resolved})"
        )

    digest, file_count = compute_sealed_archive_manifest(root_resolved)
    payload: dict[str, Any] = {
        "schema_version": "1.0",
        "hash_mode": _HASH_MODE_SCANDIR_LSTAT,
        "digest": digest,
        "file_count": file_count,
    }
    assert_no_secrets(payload)
    out_resolved.parent.mkdir(parents=True, exist_ok=True)
    out_resolved.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload


def verify_sealed_archive_untouched(
    archive_root: Path | str,
    baseline_manifest_path: Path | str,
) -> SealedArchiveVerificationReport:
    """Verify ``archive_root`` is untouched via read-only ``os.scandir`` + ``os.lstat`` walk."""
    manifest_lex = _validate_clean_path(baseline_manifest_path, label="baseline_manifest_path")
    manifest_st = _assert_not_symlink_lstat(manifest_lex, label="baseline_manifest_path")
    if manifest_st is None or not stat.S_ISREG(manifest_st.st_mode):
        raise IsolationPolicyError(f"baseline_manifest_path not found: {manifest_lex}")

    try:
        raw_manifest = json.loads(manifest_lex.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise IsolationPolicyError(
            f"Cannot parse baseline_manifest_path {manifest_lex}: {exc}"
        ) from exc

    if not isinstance(raw_manifest, dict):
        raise IsolationPolicyError("baseline_manifest_path must contain a JSON object")

    hash_mode = raw_manifest.get("hash_mode")
    if hash_mode != _HASH_MODE_SCANDIR_LSTAT:
        raise IsolationPolicyError(
            f"Unsupported baseline hash_mode {hash_mode!r}; expected {_HASH_MODE_SCANDIR_LSTAT!r}"
        )

    expected_digest_raw = (
        raw_manifest.get("expected_digest")
        or raw_manifest.get("aggregate_manifest_sha256")
        or raw_manifest.get("digest")
        or raw_manifest.get("manifest_sha256")
        or raw_manifest.get("sha256")
    )
    if not isinstance(expected_digest_raw, str) or len(expected_digest_raw.strip()) != 64:
        raise IsolationPolicyError("baseline_manifest_path missing valid 64-hex digest")
    expected_digest = expected_digest_raw.strip().lower()

    expected_count_raw = (
        raw_manifest.get("expected_file_count")
        if "expected_file_count" in raw_manifest
        else raw_manifest.get("file_count")
    )
    if type(expected_count_raw) is not int or expected_count_raw < 0:
        raise IsolationPolicyError("baseline_manifest_path missing non-negative file_count")

    observed_digest, observed_count = compute_sealed_archive_manifest(archive_root)
    passed = (
        hmac.compare_digest(observed_digest, expected_digest)
        and observed_count == expected_count_raw
    )

    return SealedArchiveVerificationReport(
        hash_mode=_HASH_MODE_SCANDIR_LSTAT,
        passed=passed,
        expected_digest=expected_digest,
        observed_digest=observed_digest,
        expected_file_count=expected_count_raw,
        observed_file_count=observed_count,
        archive_root=str(Path(archive_root).resolve()),
        baseline_manifest_path=str(manifest_lex.resolve()),
    )


__all__ = [
    "CloseoutExportReceipt",
    "CloseoutRestoreReceipt",
    "IsolationLevelDisclosure",
    "IsolationPolicyError",
    "SealedArchiveVerificationReport",
    "build_minimal_docker_verify_argv",
    "compute_sealed_archive_manifest",
    "export_release_closeout_zip",
    "verify_and_restore_closeout_zip",
    "verify_sealed_archive_untouched",
    "write_sealed_archive_baseline_manifest",
]
