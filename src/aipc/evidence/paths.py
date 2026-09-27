"""Portable paths and conservative link/reparse containment checks."""

import os
import re
import stat
from pathlib import Path, PureWindowsPath

from aipc.domain.errors import EvidencePathError

_RESERVED = re.compile(r"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)", re.I)


def relative_path(value: str) -> Path:
    """Reject unsafe syntax independently of the host operating system."""
    if not isinstance(value, str) or not value or PureWindowsPath(value).drive:
        raise EvidencePathError("expected a portable relative evidence path")
    text = value.replace("\\", "/")
    parts = text.split("/")
    if any(
        part in ("", ".", "..")
        or part.endswith((".", " "))
        or _RESERVED.match(part)
        or any(ord(c) < 32 or c in '<>:"|?*' for c in part)
        for part in parts
    ):
        raise EvidencePathError("unsafe relative evidence path")
    return Path(*parts)


def check_ancestors(path: Path) -> None:
    """Never follow links/junctions, including those inside an authorized root."""
    for candidate in (path.absolute(), *path.absolute().parents):
        try:
            info = candidate.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & getattr(
            stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400
        ):
            raise EvidencePathError("evidence path crosses a link or reparse point")


def authorized_root(path: Path, *, must_exist: bool = True) -> Path:
    try:
        if ".." in path.parts:
            raise EvidencePathError("root path contains traversal")
        check_ancestors(path)
        root = path.resolve(strict=must_exist)
        if (must_exist or root.exists()) and not root.is_dir():
            raise EvidencePathError("evidence root must be a directory")
        return root
    except OSError as exc:
        raise EvidencePathError("evidence root is unavailable") from exc


def source_file(root: Path, reference: str) -> Path:
    relative = relative_path(reference)
    # Repository internals and conventional secret files are not diagnostic artifacts.
    if any(
        part == ".git" or part == ".env" or part.startswith(".env.") and part != ".env.example"
        for part in (segment.casefold() for segment in relative.parts)
    ):
        raise EvidencePathError("restricted evidence source")
    path = root / relative
    check_ancestors(path)
    resolved = path.resolve(strict=True)
    if not resolved.is_relative_to(root) or not stat.S_ISREG(path.stat().st_mode):
        raise EvidencePathError("evidence source must be an authorized regular file")
    return path


def exclusive_directory(path: Path) -> None:
    """mkdir provides the exclusive reservation; callers never overwrite an attempt."""
    check_ancestors(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    check_ancestors(path.parent)
    os.mkdir(path)
