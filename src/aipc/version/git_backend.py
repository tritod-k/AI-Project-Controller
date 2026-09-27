"""Centralized argument-array Git operations; no staging/reset/clean/forced removal."""

import os
import re
import subprocess
from pathlib import Path

from aipc.domain.errors import EvidencePathError
from aipc.evidence.paths import authorized_root
from aipc.version.errors import DirtyWorkspaceError, GitRepositoryError
from aipc.version.models import GitCommandResult, GitWorkspace


class GitBackend:
    def __init__(self, repository: Path):
        try:
            self.root = authorized_root(repository)
        except (EvidencePathError, OSError) as exc:
            raise GitRepositoryError("repository root unavailable or unsafe") from exc
        self.commands: list[GitCommandResult] = []
        self.overrides: list[str] = []
        top = Path(self.run(["rev-parse", "--show-toplevel"]).decode().strip()).resolve()
        if top != self.root:
            raise GitRepositoryError("expected exact repository root")
        common = self.run(["rev-parse", "--path-format=absolute", "--git-common-dir"])
        try:
            self.common_dir = authorized_root(Path(common.decode().strip()))
        except EvidencePathError as exc:
            raise GitRepositoryError("unsafe Git common directory") from exc
        if not self.common_dir.is_relative_to(self.root):
            raise GitRepositoryError("manager requires owning primary repository")
        self._filters()

    def _filters(self):
        keys = self.run(
            [
                "config",
                "--null",
                "--name-only",
                "--get-regexp",
                r"^filter\..*\.(clean|smudge|process|required)$",
            ],
            allowed=(0, 1),
        )
        drivers = set()
        for key in keys.decode().split("\0"):
            if not key:
                continue
            match = re.fullmatch(r"filter\.(.+)\.(clean|smudge|process|required)", key)
            if match is None or any(ord(c) < 32 or c == "=" for c in key):
                raise GitRepositoryError("unsupported Git filter configuration")
            drivers.add(match[1])
        self.overrides = [
            part
            for driver in sorted(drivers)
            for setting in ("clean=", "smudge=", "process=", "required=false")
            for part in ("-c", f"filter.{driver}.{setting}")
        ]

    def run(self, args: list[str], *, workspace: Path | None = None, allowed=(0,)) -> bytes:
        if not args or args[0] not in {
            "rev-parse",
            "symbolic-ref",
            "status",
            "ls-files",
            "ls-tree",
            "show-ref",
            "merge-base",
            "update-ref",
            "worktree",
            "config",
            "for-each-ref",
        }:
            raise GitRepositoryError("unsupported version Git command")
        environment = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        environment.update(
            GIT_CONFIG_GLOBAL=os.devnull,
            GIT_CONFIG_SYSTEM=os.devnull,
            GIT_NO_LAZY_FETCH="1",
            GIT_TERMINAL_PROMPT="0",
            GIT_OPTIONAL_LOCKS="0",
        )
        try:
            result = subprocess.run(
                [
                    "git",
                    "--no-pager",
                    "-c",
                    "core.hooksPath=" + os.devnull,
                    "-c",
                    "core.fsmonitor=false",
                    "-c",
                    "core.untrackedCache=false",
                    "-c",
                    "submodule.recurse=false",
                    *self.overrides,
                    "-C",
                    str(workspace or self.root),
                    *args,
                ],
                capture_output=True,
                timeout=30,
                check=False,
                env=environment,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise GitRepositoryError(
                "Git unavailable or timed out; inspect partial operation"
            ) from exc
        self.commands.append(
            GitCommandResult(
                intent=args[0],
                arguments=tuple(args),
                exit_code=result.returncode,
                stdout=result.stdout,
                stderr=result.stderr,
            )
        )
        if result.returncode not in allowed:
            raise GitRepositoryError(
                f"Git {args[0]} failed (exit {result.returncode}); preserve state"
            )
        return result.stdout

    def inspect(self, workspace: Path | None = None) -> GitWorkspace:
        self._filters()
        path = workspace or self.root
        try:
            path = authorized_root(path)
        except EvidencePathError as exc:
            raise GitRepositoryError("unsafe workspace") from exc
        top = Path(
            self.run(["rev-parse", "--show-toplevel"], workspace=path).decode().strip()
        ).resolve()
        common = Path(
            self.run(["rev-parse", "--path-format=absolute", "--git-common-dir"], workspace=path)
            .decode()
            .strip()
        ).resolve()
        if top != path or common != self.common_dir:
            raise GitRepositoryError("workspace repository identity differs")
        head = self.run(["rev-parse", "--verify", "HEAD"], workspace=path).decode().strip()
        branch = (
            self.run(["symbolic-ref", "--quiet", "--short", "HEAD"], workspace=path)
            .decode()
            .strip()
        )
        status = self.run(
            [
                "status",
                "--porcelain=v1",
                "-z",
                "--untracked-files=all",
                "--ignored=matching",
                "--ignore-submodules=all",
            ],
            workspace=path,
        )
        submodules = self.run(["ls-files", "--stage", "-z"], workspace=path)
        submodules += self.run(["ls-tree", "-r", "-z", head], workspace=path)
        if any(item.startswith(b"160000 ") for item in submodules.split(b"\0")):
            raise GitRepositoryError("submodule version operations unsupported")
        return GitWorkspace(
            root=str(path), common_dir=str(common), head=head, branch=branch, status=status
        )

    def clean(self, workspace: Path | None = None) -> GitWorkspace:
        state = self.inspect(workspace)
        flags = self.run(["ls-files", "-v", "-z"], workspace=workspace)
        if any(
            entry[:1].islower() or entry.startswith(b"S ") for entry in flags.split(b"\0") if entry
        ):
            raise DirtyWorkspaceError(
                "hidden index flags can conceal human edits; mutation refused"
            )
        if state.status:
            raise DirtyWorkspaceError(
                "unknown tracked/staged/untracked/ignored changes; operation refused"
            )
        return state

    def ref(self, name: str) -> str | None:
        data = self.run(["show-ref", "--verify", "--hash", name], allowed=(0, 1, 128))
        oid = data.decode().strip()
        if not oid:
            loose = self.common_dir / name
            if loose.exists() or loose.is_symlink():
                raise GitRepositoryError("unresolved existing Git ref must be preserved")
        return oid or None

    def ancestor(self, parent: str, child: str) -> bool:
        self.run(["merge-base", "--is-ancestor", parent, child], allowed=(0, 1))
        return self.commands[-1].exit_code == 0

    def create_ref(self, name: str, commit: str):
        self.run(["update-ref", "--no-deref", name, commit, "0" * len(commit)])
