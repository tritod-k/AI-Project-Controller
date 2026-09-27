"""Fixed read-only Git commands. Never run worker-provided command strings."""

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from aipc.domain.errors import EvidencePathError, GitEvidenceError
from aipc.domain.models import FileChange
from aipc.evidence.models import GitEvidence
from aipc.evidence.paths import relative_path


@dataclass(frozen=True)
class GitSnapshot:
    evidence: GitEvidence
    status: bytes = b""
    diff: bytes = b""
    summary: bytes = b""


def _run(
    workspace: Path,
    args: list[str],
    max_bytes: int,
    *,
    overrides: tuple[str, ...] = (),
    no_match: bool = False,
) -> bytes:
    try:
        result = subprocess.run(
            [
                "git",
                "--no-pager",
                "-c",
                "core.fsmonitor=false",
                "-c",
                "core.untrackedCache=false",
                *overrides,
                "-C",
                str(workspace),
                *args,
            ],
            capture_output=True,
            timeout=15,
            check=False,
            env={**os.environ, "GIT_OPTIONAL_LOCKS": "0", "GIT_NO_LAZY_FETCH": "1"},
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GitEvidenceError("Git command unavailable or timed out") from exc
    if result.returncode != 0 and not (no_match and result.returncode == 1):
        raise GitEvidenceError("Git observation returned nonzero exit code")
    if len(result.stdout) > max_bytes:
        raise GitEvidenceError("Git output exceeds configured byte limit")
    return result.stdout


def _status_changes(data: bytes) -> list[FileChange]:
    entries = data.decode("utf-8").split("\0")
    result = []
    index = 0
    while index < len(entries) and entries[index]:
        entry = entries[index]
        code, name = entry[:2], entry[3:]
        if len(entry) < 4:
            raise GitEvidenceError("malformed Git status")
        change = (
            "renamed"
            if "R" in code
            else "deleted"
            if "D" in code
            else "added"
            if "A" in code or code == "??"
            else "modified"
        )
        result.append(FileChange(path=relative_path(name).as_posix(), change_type=change))
        index += 2 if "R" in code or "C" in code else 1
    return result


def _diff_changes(data: bytes) -> list[FileChange]:
    fields = data.decode("utf-8").split("\0")
    result = []
    index = 0
    while index < len(fields) and fields[index]:
        code = fields[index][0]
        if code in ("R", "C"):
            index += 1  # discard original path; following path is the observed destination
        name = fields[index + 1]
        change = {"A": "added", "D": "deleted", "R": "renamed", "C": "added"}.get(code, "modified")
        result.append(FileChange(path=relative_path(name).as_posix(), change_type=change))
        index += 2
    return result


def observe_git(workspace: Path, checkpoint: str | None, max_bytes: int) -> GitSnapshot:
    try:
        # A plain directory is unavailable, rather than an execution failure.
        if not (workspace / ".git").exists():
            return GitSnapshot(
                GitEvidence(
                    status="unavailable",
                    reason="workspace has no Git repository",
                    checkpoint_ref=checkpoint,
                )
            )
        keys = _run(
            workspace,
            [
                "config",
                "--null",
                "--name-only",
                "--get-regexp",
                r"^filter\..*\.(clean|smudge|process|required)$",
            ],
            max_bytes,
            no_match=True,
        )
        drivers = []
        for key in keys.decode("utf-8").split("\0"):
            if not key:
                continue
            match = re.fullmatch(r"filter\.(.+)\.(clean|smudge|process|required)", key)
            if match is None or any(ord(c) < 32 or c == "=" for c in key):
                raise GitEvidenceError("unsupported Git filter configuration")
            if match[1] not in drivers:
                drivers.append(match[1])
        overrides = tuple(
            value
            for driver in drivers
            for setting in ("clean=", "smudge=", "process=", "required=false")
            for value in ("-c", f"filter.{driver}.{setting}")
        )

        def read(args: list[str]) -> bytes:
            return _run(workspace, args, max_bytes, overrides=overrides)

        top = read(["rev-parse", "--show-toplevel"]).decode().strip()
        if Path(top).resolve() != workspace:
            raise GitEvidenceError("workspace must be the repository root")
        head = read(["rev-parse", "--verify", "HEAD"]).decode().strip()
        branch = read(["branch", "--show-current"]).decode().strip() or None
        baseline = head
        if checkpoint is not None:
            baseline = (
                read(
                    ["rev-parse", "--verify", "--end-of-options", checkpoint + "^{commit}"],
                )
                .decode()
                .strip()
            )
        entries = read(["ls-files", "--stage", "-z"])
        entries += read(["ls-tree", "-r", "-z", baseline])
        if any(entry.startswith(b"160000 ") for entry in entries.split(b"\0")):
            return GitSnapshot(
                GitEvidence(
                    status="unavailable",
                    head=head,
                    branch=branch,
                    baseline=baseline,
                    checkpoint_ref=checkpoint,
                    reason=(
                        "submodule workspaces require a separate safe observer; "
                        "Git evidence unavailable"
                    ),
                )
            )
        status = read(
            ["status", "--porcelain=v1", "-z", "--untracked-files=all", "--ignore-submodules=all"]
        )
        common = [
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            "--find-renames",
            "--ignore-submodules=all",
            "--submodule=short",
        ]
        diff = read([*common, "--binary", baseline, "--"])
        summary = read([*common, "--stat", baseline, "--"])
        names = read([*common, "--name-status", "-z", baseline, "--"])
        changes = {record.path: record for record in _diff_changes(names)}
        # The patch is authoritative for tracked changes; status adds untracked/conflicts.
        for record in _status_changes(status):
            changes.setdefault(record.path, record)
        return GitSnapshot(
            GitEvidence(
                status="collected",
                head=head,
                branch=branch,
                baseline=baseline,
                checkpoint_ref=checkpoint,
                changes=tuple(changes.values()),
            ),
            status,
            diff,
            summary,
        )
    except (GitEvidenceError, EvidencePathError, UnicodeError, ValueError, IndexError):
        return GitSnapshot(
            GitEvidence(
                status="failed", checkpoint_ref=checkpoint, reason="Git evidence collection failed"
            )
        )
