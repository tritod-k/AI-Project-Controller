"""Read-only workspace observations; hashes bind plans without retaining file content."""

import hashlib
import json
import os
from pathlib import Path

from aipc.evidence.paths import check_ancestors
from aipc.recovery.errors import RecoveryError


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    ).hexdigest()


def file_hash(path):
    check_ancestors(path)
    total = 0
    h = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            total += len(chunk)
            if total > 512 * 1024 * 1024:
                raise RecoveryError("observation byte limit exceeded; human inspection required")
            h.update(chunk)
    return h.hexdigest()


def tree_facts(root, *, exclude_git=False):
    result = {}
    check_ancestors(root)
    if not root.exists():
        return {"absent": True}
    for directory, dirs, files in os.walk(root, followlinks=False):
        if exclude_git:
            dirs[:] = [d for d in dirs if d != ".git"]
            files = [f for f in files if f != ".git"]
        for name in dirs:
            check_ancestors(Path(directory) / name)
        for name in sorted(files):
            path = Path(directory) / name
            result[path.relative_to(root).as_posix()] = file_hash(path)
            if len(result) > 10000:
                raise RecoveryError("observation file limit exceeded; human inspection required")
    return result


def workspace_facts(versions, path):
    state = versions.git.inspect(path)
    flags = versions.git.run(["ls-files", "-v", "-z"], workspace=path)
    index = Path(
        versions.git.run(
            ["rev-parse", "--path-format=absolute", "--git-path", "index"], workspace=path
        )
        .decode()
        .strip()
    )
    if not index.is_relative_to(versions.git.common_dir):
        raise RecoveryError("workspace index outside owning repository")
    index_lock = index.with_name(index.name + ".lock")
    return {
        "head": state.head,
        "branch": state.branch,
        "status": state.status.hex(),
        "flags": flags.hex(),
        "index": file_hash(index),
        "index_lock": file_hash(index_lock) if index_lock.exists() else None,
        "files": tree_facts(path, exclude_git=True),
    }


def hidden_flags(facts):
    flags = bytes.fromhex(facts["flags"])
    return any(e[:1].islower() or e.startswith(b"S ") for e in flags.split(b"\0") if e)


def conflicts(facts):
    return any(
        e[:2] in {b"DD", b"AU", b"UD", b"UA", b"DU", b"AA", b"UU"}
        for e in bytes.fromhex(facts["status"]).split(b"\0")
        if e
    )
