"""FINAL-04 (ARCH-GIT-01..02) Private Git quiescence verifier and exclusive lock guard."""

from __future__ import annotations

import argparse
import bisect
import contextlib
import functools
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
from aipc.hardening.env_isolation import build_sanitized_child_env
from aipc.hardening.errors import ChildEnvironmentIsolationError, SecretExposureError
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
_ALLOWED_GITIGNORE_PATTERNS: dict[str, frozenset[str]] = {
    "stable_c/": frozenset(
        {
            "stable_c/",
            "/stable_c/",
            "stable_c",
            "/stable_c",
            "**/stable_c/",
            "**/stable_c",
        }
    ),
    "worktrees/": frozenset(
        {
            "worktrees/",
            "worktrees",
            "**/worktrees/",
            "**/worktrees",
            "**/runtime/worktrees/",
            "**/runtime/worktrees",
        }
    ),
    "logs/": frozenset(
        {
            "logs/",
            "logs",
            "**/logs/",
            "**/logs",
            "**/runtime/logs/",
            "**/runtime/logs",
        }
    ),
    "*.lock": frozenset({"*.lock", "**/*.lock"}),
    "*.key": frozenset({"*.key", "**/*.key"}),
    ".env*": frozenset({".env*", "**/.env*"}),
}
_PROTECTED_FILE_GLOBS: dict[str, tuple[str, ...]] = {
    "*.lock": ("*.lock", "controller.lock"),
    "*.key": ("*.key", "*.pem", "*secret*", "*credential*", "*token*"),
    ".env*": (".env*",),
}
_QUIESCENT_TASK_STATUSES: frozenset[str] = frozenset(
    {
        "PENDING",
        "ELIGIBLE",
        "QUEUED",
        "DEFERRED",
        "COMPLETED",
        "PASSED",
        "FAILED",
        "BLOCKED",
        "CANCELLED",
    }
)
_SKIPPED_WALK_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        "stable_c",
        ".venv",
        ".venv_stable",
        "runtime_c",
        "worktrees",
        "logs",
        "__pycache__",
    }
)
_PORTABLE_ROOT_DOC_NAMES: frozenset[str] = frozenset({"GEMINI.md", "AGENTS.md", "README.md"})
_SECRET_SCAN_SUFFIXES: frozenset[str] = frozenset({".json", ".yaml", ".yml", ".md", ".bat"})
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")

_BEARER_RE = re.compile(r"Bearer\s+[A-Za-z0-9_.\-]+", re.IGNORECASE)
_SK_RE = re.compile(r"(?<![A-Za-z0-9])sk-[A-Za-z0-9_\-]{8,}")
_AIZA_RE = re.compile(r"AIza[0-9A-Za-z_\-]{35}")
_GH_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9])"
    r"(?:ghp_[A-Za-z0-9]{20,}|gho_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})"
)
_PEM_PRIVATE_KEY_RE = re.compile(r"-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----")
_KV_SECRET_RE = re.compile(
    r"""(?i)(api[_-]?key|token|password|secret)['"]?\s*([:=])\s*"""
    r"""(?:"([^"\r\n]*)"|'([^'\r\n]*)'|([^\s'"\r\n]+))"""
)
_AUTH_HEADER_RE = re.compile(
    r"""(?i)(authorization)['"]?\s*([:=])\s*"""
    r"""(?:"([^"\r\n]*)"|'([^'\r\n]*)'|([^\s'"\r\n]+))"""
)
_EXPRESSION_PREFIXES = ("(", "$", "{", "[", "<", "*", "%", ",")
_NON_SECRET_LITERALS: frozenset[str] = frozenset({"null", "none", "true", "false"})
_PURE_PLACEHOLDER_RE = re.compile(
    r"^(?:\*+|<[A-Za-z0-9_.:-]+>|\$\{[A-Za-z_][A-Za-z0-9_]*\}"
    r"|\$\([A-Za-z_][A-Za-z0-9_]*\)|%[A-Za-z_][A-Za-z0-9_]*%"
    r"|\{\{[A-Za-z0-9_.\s-]+\}\})$"
)


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
    """Walk workspace files without following symlinks, skipping ignored engine directories."""
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
                if entry.name in _SKIPPED_WALK_DIRS:
                    continue
                _visit(entry)
            elif stat.S_ISREG(st.st_mode):
                files.append(entry)

    _visit(workspace_root)
    return files, violations


def _read_class_char(pat: str, idx: int, end: int) -> tuple[str, int]:
    if pat[idx] == "\\" and idx + 1 < end:
        return pat[idx + 1], idx + 2
    return pat[idx], idx + 1


def _case_key(ch: str) -> str:
    cf = ch.casefold()
    return cf if len(cf) == 1 else ch.lower()


@functools.lru_cache(maxsize=1)
def _unicode_case_tables() -> tuple[dict[str, tuple[int, ...]], tuple[int, ...]]:
    buckets: dict[str, list[int]] = {}
    for cp in range(0x110000):
        if cp == 0x2F or 0xD800 <= cp < 0xE000:
            continue
        key = _case_key(chr(cp))
        buckets.setdefault(key, []).append(cp)
    equiv_groups: dict[str, tuple[int, ...]] = {}
    cased_cps: list[int] = []
    for key, cps in buckets.items():
        if len(cps) >= 2:
            tup = tuple(cps)
            equiv_groups[key] = tup
            cased_cps.extend(tup)
    cased_cps.sort()
    return equiv_groups, tuple(cased_cps)


def _add_char_boundaries(boundaries: set[int], ch: str) -> None:
    ch_ord = ord(ch)
    boundaries.add(max(0, min(ch_ord, 0x110000)))
    boundaries.add(max(0, min(ch_ord + 1, 0x110000)))
    equiv_groups, _ = _unicode_case_tables()
    for member in equiv_groups.get(_case_key(ch), ()):
        boundaries.add(max(0, min(member, 0x110000)))
        boundaries.add(max(0, min(member + 1, 0x110000)))


def _add_range_boundaries(boundaries: set[int], lo_ch: str, hi_ch: str) -> None:
    lo_ord = ord(lo_ch)
    hi_ord = ord(hi_ch)
    if lo_ord > hi_ord:
        return
    boundaries.add(max(0, min(lo_ord, 0x110000)))
    boundaries.add(max(0, min(hi_ord + 1, 0x110000)))
    equiv_groups, all_cased_cps = _unicode_case_tables()
    start = bisect.bisect_left(all_cased_cps, lo_ord)
    end = bisect.bisect_right(all_cased_cps, hi_ord)
    for cp in all_cased_cps[start:end]:
        boundaries.add(max(0, min(cp, 0x110000)))
        boundaries.add(max(0, min(cp + 1, 0x110000)))
        for member in equiv_groups[_case_key(chr(cp))]:
            boundaries.add(max(0, min(member, 0x110000)))
            boundaries.add(max(0, min(member + 1, 0x110000)))


def _scan_bracket_expression_end(pat: str, open_idx: int) -> tuple[int, bool] | None:
    """Return (closing_idx, has_posix_class) for a '[...]' starting at open_idx."""
    n = len(pat)
    j = open_idx + 1
    if j < n and pat[j] in ("!", "^"):
        j += 1
    if j < n and pat[j] == "]":
        j += 1
    has_posix = False
    while j < n:
        if pat[j] == "\\" and j + 1 < n:
            j += 2
        elif pat[j] == "[" and j + 1 < n and pat[j + 1] in (":", ".", "="):
            marker = pat[j + 1]
            m = j + 2
            while m + 1 < n:
                if pat[m] == marker and pat[m + 1] == "]":
                    has_posix = True
                    j = m + 2
                    break
                m += 1
            else:
                j += 1
        elif pat[j] == "]":
            return j, has_posix
        else:
            j += 1
    return None


def _contains_posix_bracket_class(pat: str) -> bool:
    """Return True if pat contains a Git POSIX bracket expression ([:...:], [....], [=...=])."""
    i = 0
    n = len(pat)
    while i < n:
        ch = pat[i]
        if ch == "\\":
            i += 2 if i + 1 < n else 1
        elif ch == "[":
            scanned = _scan_bracket_expression_end(pat, i)
            if scanned is None:
                i += 1
            else:
                close_idx, has_posix = scanned
                if has_posix:
                    return True
                i = close_idx + 1
        else:
            i += 1
    return False


def _collect_pattern_boundaries(pat: str) -> set[int]:
    boundaries: set[int] = set()
    i = 0
    n = len(pat)
    while i < n:
        ch = pat[i]
        if ch in ("*", "?"):
            i += 1
        elif ch == "\\":
            if i + 1 < n:
                lit = pat[i + 1]
                i += 2
            else:
                lit = "\\"
                i += 1
            _add_char_boundaries(boundaries, lit)
        elif ch == "[":
            scanned = _scan_bracket_expression_end(pat, i)
            if scanned is None:
                _add_char_boundaries(boundaries, "[")
                i += 1
                continue
            j, has_posix = scanned
            if has_posix:
                i = j + 1
                continue
            content_start = i + 1
            if content_start < j and pat[content_start] in ("!", "^"):
                content_start += 1
            k = content_start
            while k < j:
                lo_ch, next_k = _read_class_char(pat, k, j)
                if next_k + 1 < j and pat[next_k] == "-":
                    hi_ch, k = _read_class_char(pat, next_k + 1, j)
                    _add_range_boundaries(boundaries, lo_ch, hi_ch)
                else:
                    _add_char_boundaries(boundaries, lo_ch)
                    k = next_k
            i = j + 1
        else:
            _add_char_boundaries(boundaries, ch)
            i += 1
    return boundaries


def _build_comparison_alphabet(pat_a: str, pat_b: str) -> frozenset[str]:
    boundaries: set[int] = {0, ord("/"), ord("/") + 1, 0xD800, 0xE000, 0x110000}
    boundaries.update(_collect_pattern_boundaries(pat_a))
    boundaries.update(_collect_pattern_boundaries(pat_b))
    sorted_b = sorted(b for b in boundaries if 0 <= b <= 0x110000)
    alphabet: set[str] = set()
    for b_i, b_next in zip(sorted_b, sorted_b[1:], strict=False):
        if b_i >= b_next or b_i == ord("/") or 0xD800 <= b_i < 0xE000:
            continue
        alphabet.add(chr(b_i))
    return frozenset(alphabet)


def _char_matches_literal(rep: str, lit: str) -> bool:
    return rep != "/" and _case_key(rep) == _case_key(lit)


def _char_matches_range(rep: str, lo_ord: int, hi_ord: int) -> bool:
    if rep == "/" or lo_ord > hi_ord:
        return False
    if lo_ord <= ord(rep) <= hi_ord:
        return True
    equiv_groups, _ = _unicode_case_tables()
    return any(lo_ord <= cp <= hi_ord for cp in equiv_groups.get(_case_key(rep), ()))


def _tokenize_segment_glob(
    pat: str,
    alphabet: frozenset[str],
    *,
    ignorecase: bool = True,
) -> list[tuple[str, frozenset[str]]]:
    def _lit_match(rep: str, lit: str) -> bool:
        if ignorecase:
            return _char_matches_literal(rep, lit)
        return rep != "/" and rep == lit

    def _rng_match(rep: str, lo_ord: int, hi_ord: int) -> bool:
        if ignorecase:
            return _char_matches_range(rep, lo_ord, hi_ord)
        return rep != "/" and lo_ord <= ord(rep) <= hi_ord

    tokens: list[tuple[str, frozenset[str]]] = []
    i = 0
    n = len(pat)
    while i < n:
        ch = pat[i]
        if ch == "*":
            if not tokens or tokens[-1][0] != "star":
                tokens.append(("star", alphabet))
            i += 1
        elif ch == "?":
            tokens.append(("char", alphabet))
            i += 1
        elif ch == "\\":
            if i + 1 < n:
                lit = pat[i + 1]
                i += 2
            else:
                lit = "\\"
                i += 1
            matched = frozenset(a for a in alphabet if _lit_match(a, lit))
            tokens.append(("char", matched))
        elif ch == "[":
            scanned = _scan_bracket_expression_end(pat, i)
            if scanned is None:
                matched = frozenset(a for a in alphabet if _lit_match(a, "["))
                tokens.append(("char", matched))
                i += 1
                continue
            j, has_posix = scanned
            if has_posix:
                tokens.append(("char", alphabet))
                i = j + 1
                continue
            negated = False
            content_start = i + 1
            if content_start < j and pat[content_start] in ("!", "^"):
                negated = True
                content_start += 1
            class_chars: set[str] = set()
            k = content_start
            while k < j:
                lo_ch, next_k = _read_class_char(pat, k, j)
                if next_k + 1 < j and pat[next_k] == "-":
                    hi_ch, k = _read_class_char(pat, next_k + 1, j)
                    lo_ord = ord(lo_ch)
                    hi_ord = ord(hi_ch)
                    if lo_ord <= hi_ord:
                        for a in alphabet:
                            if _rng_match(a, lo_ord, hi_ord):
                                class_chars.add(a)
                else:
                    for a in alphabet:
                        if _lit_match(a, lo_ch):
                            class_chars.add(a)
                    k = next_k
            matched = alphabet - class_chars if negated else frozenset(class_chars)
            tokens.append(("char", matched))
            i = j + 1
        else:
            matched = frozenset(a for a in alphabet if _lit_match(a, ch))
            tokens.append(("char", matched))
            i += 1
    return tokens


def _deduplicate_token_alphabet(
    alphabet: frozenset[str],
    tokens_a: list[tuple[str, frozenset[str]]],
    tokens_b: list[tuple[str, frozenset[str]]],
) -> tuple[list[tuple[str, frozenset[str]]], list[tuple[str, frozenset[str]]]]:
    char_sets = [chars for kind, chars in (*tokens_a, *tokens_b) if kind == "char"]
    if not char_sets:
        rep_alphabet = frozenset({min(alphabet)}) if alphabet else frozenset()
    else:
        seen_signatures: set[tuple[bool, ...]] = set()
        kept: list[str] = []
        for ch in sorted(alphabet):
            sig = tuple(ch in chars for chars in char_sets)
            if sig not in seen_signatures:
                seen_signatures.add(sig)
                kept.append(ch)
        rep_alphabet = frozenset(kept)
    dedup_a = [(kind, chars & rep_alphabet) for kind, chars in tokens_a]
    dedup_b = [(kind, chars & rep_alphabet) for kind, chars in tokens_b]
    return dedup_a, dedup_b


def _segment_tokens_intersect(
    tokens_a: list[tuple[str, frozenset[str]]],
    tokens_b: list[tuple[str, frozenset[str]]],
) -> bool:
    len_a = len(tokens_a)
    len_b = len(tokens_b)
    dp = [[False] * (len_b + 1) for _ in range(len_a + 1)]
    dp[len_a][len_b] = True

    for i in range(len_a, -1, -1):
        for j in range(len_b, -1, -1):
            if i == len_a and j == len_b:
                continue
            can_match = False
            if i < len_a and tokens_a[i][0] == "star":
                if dp[i + 1][j]:
                    can_match = True
                elif j < len_b:
                    if tokens_b[j][0] == "star":
                        if dp[i][j + 1] or dp[i + 1][j + 1]:
                            can_match = True
                    elif tokens_a[i][1] & tokens_b[j][1] and dp[i][j + 1]:
                        can_match = True
            if not can_match and j < len_b and tokens_b[j][0] == "star":
                if dp[i][j + 1]:
                    can_match = True
                elif i < len_a:
                    if tokens_a[i][0] == "star":
                        if dp[i + 1][j] or dp[i + 1][j + 1]:
                            can_match = True
                    elif tokens_b[j][1] & tokens_a[i][1] and dp[i + 1][j]:
                        can_match = True
            if (
                not can_match
                and i < len_a
                and j < len_b
                and tokens_a[i][0] == "char"
                and tokens_b[j][0] == "char"
                and tokens_a[i][1] & tokens_b[j][1]
                and dp[i + 1][j + 1]
            ):
                can_match = True
            dp[i][j] = can_match
    return dp[0][0]


def _segment_globs_intersect(
    pat_a: str,
    pat_b: str,
    *,
    case_sensitive: bool = False,
) -> bool:
    if _contains_posix_bracket_class(pat_a) or _contains_posix_bracket_class(pat_b):
        return True
    alphabet = _build_comparison_alphabet(pat_a, pat_b)
    ignorecase = not case_sensitive
    tokens_a = _tokenize_segment_glob(pat_a, alphabet, ignorecase=ignorecase)
    tokens_b = _tokenize_segment_glob(pat_b, alphabet, ignorecase=ignorecase)
    tokens_a, tokens_b = _deduplicate_token_alphabet(alphabet, tokens_a, tokens_b)
    return _segment_tokens_intersect(tokens_a, tokens_b)


def _path_globs_intersect(
    seq_a: Sequence[str],
    seq_b: Sequence[str],
    *,
    case_sensitive: bool = False,
) -> bool:
    len_a = len(seq_a)
    len_b = len(seq_b)
    dp = [[False] * (len_b + 1) for _ in range(len_a + 1)]
    dp[len_a][len_b] = True

    for i in range(len_a, -1, -1):
        for j in range(len_b, -1, -1):
            if i == len_a and j == len_b:
                continue
            can_match = False
            if i < len_a and seq_a[i] == "**":
                if dp[i + 1][j]:
                    can_match = True
                elif j < len_b and dp[i][j + 1]:
                    can_match = True
            if not can_match and j < len_b and seq_b[j] == "**":
                if dp[i][j + 1]:
                    can_match = True
                elif i < len_a and dp[i + 1][j]:
                    can_match = True
            if (
                not can_match
                and i < len_a
                and j < len_b
                and seq_a[i] != "**"
                and seq_b[j] != "**"
                and _segment_globs_intersect(
                    seq_a[i],
                    seq_b[j],
                    case_sensitive=case_sensitive,
                )
                and dp[i + 1][j + 1]
            ):
                can_match = True
            dp[i][j] = can_match
    return dp[0][0]


def _contains_negated_bracket_class(pat: str) -> bool:
    """Return True if pat contains a negated bracket expression ('[!...]' or '[^...]')."""
    i = 0
    n = len(pat)
    while i < n:
        ch = pat[i]
        if ch == "\\":
            i += 2 if i + 1 < n else 1
        elif ch == "[":
            scanned = _scan_bracket_expression_end(pat, i)
            if scanned is None:
                i += 1
            else:
                close_idx, _ = scanned
                if i + 1 < close_idx and pat[i + 1] in ("!", "^"):
                    return True
                i = close_idx + 1
        else:
            i += 1
    return False


def _negation_affects_exclusion(neg_pattern: str, required: str) -> bool:
    norm = neg_pattern.strip("/")
    if not norm:
        return False
    if _contains_negated_bracket_class(norm):
        return True
    parts = [part for part in norm.split("/") if part]
    if not parts:
        return False
    last = parts[-1]
    if required in ("stable_c/", "worktrees/", "logs/"):
        dir_name = required.rstrip("/")
        for case_sensitive in (False, True):
            if any(
                _segment_globs_intersect(part, dir_name, case_sensitive=case_sensitive)
                for part in parts
            ):
                return True
            if _path_globs_intersect(parts, (dir_name, "**"), case_sensitive=case_sensitive):
                return True
            if _path_globs_intersect(
                parts,
                ("**", dir_name, "**"),
                case_sensitive=case_sensitive,
            ):
                return True
        return False
    if required in _PROTECTED_FILE_GLOBS:
        if last == "**":
            return True
        for prot in _PROTECTED_FILE_GLOBS[required]:
            for case_sensitive in (False, True):
                if _segment_globs_intersect(last, prot, case_sensitive=case_sensitive):
                    return True
                if _path_globs_intersect(parts, ("**", prot), case_sensitive=case_sensitive):
                    return True
                if _path_globs_intersect(parts, (prot,), case_sensitive=case_sensitive):
                    return True
        return False
    return False


def _evaluate_gitignore_exclusion(lines: Sequence[str], required: str) -> tuple[bool, str | None]:
    allowed = _ALLOWED_GITIGNORE_PATTERNS.get(required, frozenset({required}))
    covered = False
    negated_by: str | None = None
    for line in lines:
        if line in allowed:
            covered = True
            negated_by = None
        elif line.startswith("!"):
            neg_pattern = _strip_unescaped_trailing_spaces(line[1:])
            if _negation_affects_exclusion(neg_pattern, required):
                covered = False
                negated_by = line
    return covered, negated_by


def _gitignore_covers_exclusion(active_lines: Sequence[str], required: str) -> bool:
    covered, _ = _evaluate_gitignore_exclusion(active_lines, required)
    return covered


def _strip_unescaped_trailing_spaces(line: str) -> str:
    """Strip unescaped trailing spaces per gitignore(5), preserving leading whitespace."""
    end = len(line)
    while end > 0 and line[end - 1] == " ":
        bs = 0
        idx = end - 2
        while idx >= 0 and line[idx] == "\\":
            bs += 1
            idx -= 1
        if bs % 2 == 1:
            break
        end -= 1
    return line[:end]


_PROTECTED_GITIGNORE_PROBE_PATHS: tuple[str, ...] = (
    "stable_c/probe.txt",
    "projects/p1/runtime/worktrees/probe.txt",
    "projects/p1/runtime/logs/probe.txt",
    "controller.lock",
    "secret.key",
    ".env",
    ".env.local",
    "sub/.env",
)


def _run_git_check_ignore_oracle(workspace_root: Path) -> list[str]:
    """Verify real Git ignores canonical protected probes in both ignorecase modes."""
    violations: list[str] = []
    stdin_payload = (
        b"\x00".join(p.encode("utf-8") for p in _PROTECTED_GITIGNORE_PROBE_PATHS) + b"\x00"
    )
    for ignorecase_val in ("false", "true"):
        try:
            sanitized_env = build_sanitized_child_env(os.environ)
            proc = subprocess.run(
                [
                    "git",
                    "-C",
                    str(workspace_root),
                    "-c",
                    f"core.ignorecase={ignorecase_val}",
                    "check-ignore",
                    "--no-index",
                    "-z",
                    "--stdin",
                ],
                input=stdin_payload,
                capture_output=True,
                check=False,
                env=sanitized_env,
            )
        except (ChildEnvironmentIsolationError, OSError) as exc:
            violations.append(
                f".gitignore real-git check-ignore failed (core.ignorecase={ignorecase_val}): {exc}"
            )
            continue
        if proc.returncode not in (0, 1):
            violations.append(
                f".gitignore real-git check-ignore returned {proc.returncode} "
                f"(core.ignorecase={ignorecase_val})"
            )
            continue
        ignored = {
            item.decode("utf-8", errors="replace") for item in proc.stdout.split(b"\x00") if item
        }
        for probe in _PROTECTED_GITIGNORE_PROBE_PATHS:
            if probe not in ignored:
                violations.append(
                    f".gitignore failed real-git check-ignore (core.ignorecase={ignorecase_val}) "
                    f"for protected probe: {probe}"
                )
    return violations


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
                ign_text = gitignore.read_bytes().decode("utf-8")
            except (OSError, UnicodeError) as exc:
                violations.append(f"Cannot read .gitignore: {exc}")
            else:
                active_lines = []
                for raw_line in re.split(r"\r\n|\n", ign_text):
                    cleaned = _strip_unescaped_trailing_spaces(raw_line)
                    if not cleaned or cleaned.startswith("#"):
                        continue
                    active_lines.append(cleaned)
                for line in active_lines:
                    if line.startswith("!") and len(line) > 1 and line[1].isspace():
                        violations.append(
                            f".gitignore has ambiguous negation with leading whitespace: {line}"
                        )
                for req in _REQUIRED_GITIGNORE_EXCLUSIONS:
                    covered, negated_by = _evaluate_gitignore_exclusion(active_lines, req)
                    if negated_by is not None:
                        violations.append(
                            f".gitignore negates required exclusion {req}: {negated_by}"
                        )
                    elif not covered:
                        violations.append(f".gitignore missing required exclusion: {req}")
                if (workspace_root / ".git").exists():
                    violations.extend(_run_git_check_ignore_oracle(workspace_root))
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
                        if queue_data.get("reconciliation_required"):
                            violations.append(
                                f"Non-quiescent queue in {project_dir.name}: "
                                "top-level reconciliation_required is truthy"
                            )
                        if queue_data.get("unresolved_reservation_ids"):
                            violations.append(
                                f"Non-quiescent queue in {project_dir.name}: "
                                "unresolved_reservation_ids is non-empty"
                            )
                        if "tasks" not in queue_data:
                            violations.append(
                                f"Malformed canonical_queue.json in {project_dir.name}: "
                                "missing 'tasks' list"
                            )
                        elif not isinstance(queue_data["tasks"], list):
                            violations.append(
                                f"Malformed canonical_queue.json in {project_dir.name}: "
                                "'tasks' must be a list"
                            )
                        else:
                            for idx, task in enumerate(queue_data["tasks"]):
                                if not isinstance(task, dict):
                                    violations.append(
                                        f"Malformed canonical_queue.json in "
                                        f"{project_dir.name}: "
                                        f"task entry at index {idx} must be a JSON object"
                                    )
                                    continue
                                contract = task.get("contract")
                                nested_tid = (
                                    contract.get("task_id") if isinstance(contract, dict) else None
                                )
                                task_id = str(
                                    nested_tid
                                    or task.get("task_id")
                                    or task.get("id")
                                    or f"index-{idx}"
                                )
                                if task.get("reconciliation_required"):
                                    violations.append(
                                        f"Non-quiescent task {task_id} in {project_dir.name}: "
                                        "reconciliation_required is truthy"
                                    )
                                    continue
                                raw_status = task.get("status")
                                if not isinstance(raw_status, str) or not raw_status.strip():
                                    violations.append(
                                        f"Malformed task {task_id} in {project_dir.name}: "
                                        "missing or non-string 'status'"
                                    )
                                    continue
                                status = raw_status.strip().upper()
                                if status not in _QUIESCENT_TASK_STATUSES:
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
        sanitized_env = build_sanitized_child_env(os.environ)
        proc = subprocess.run(
            ["git", "-C", str(workspace_root), *args],
            capture_output=True,
            check=False,
            env=sanitized_env,
        )
    except (ChildEnvironmentIsolationError, OSError):
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


def _is_portable_root_doc_or_launcher(candidate_file: Path, workspace_root: Path) -> bool:
    if candidate_file.parent != workspace_root:
        return False
    name = candidate_file.name
    if name in _PORTABLE_ROOT_DOC_NAMES:
        return True
    if name.startswith("AIPC_") and name.endswith(".md"):
        return True
    return candidate_file.suffix.lower() == ".bat"


def _is_suspicious_kv_value(value: str, *, is_quoted: bool = False) -> bool:
    stripped = value.strip()
    if not stripped or stripped.lower() in _NON_SECRET_LITERALS:
        return False
    if not any(ch.isalnum() for ch in stripped):
        return False
    if _PURE_PLACEHOLDER_RE.fullmatch(stripped):
        return False
    if not is_quoted and stripped.startswith(_EXPRESSION_PREFIXES):
        return False
    return True


def _extract_kv_match_value(match: re.Match[str]) -> tuple[str, bool]:
    if match.group(3) is not None:
        return match.group(3), True
    if match.group(4) is not None:
        return match.group(4), True
    return match.group(5) or "", False


def _has_workspace_secret(content: str, *, is_portable_doc: bool) -> bool:
    if (
        _BEARER_RE.search(content)
        or _SK_RE.search(content)
        or _AIZA_RE.search(content)
        or _GH_TOKEN_RE.search(content)
        or _PEM_PRIVATE_KEY_RE.search(content)
    ):
        return True
    if not is_portable_doc:
        try:
            assert_no_secrets(content)
        except SecretExposureError:
            return True
        return False
    for match in _KV_SECRET_RE.finditer(content):
        value, is_quoted = _extract_kv_match_value(match)
        if _is_suspicious_kv_value(value, is_quoted=is_quoted):
            return True
    for match in _AUTH_HEADER_RE.finditer(content):
        value, is_quoted = _extract_kv_match_value(match)
        if _is_suspicious_kv_value(value, is_quoted=is_quoted):
            return True
    return False


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
            is_portable_doc = _is_portable_root_doc_or_launcher(candidate_file, clean_ws)
            try:
                content = candidate_file.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                secret_violations.append(f"Cannot read workspace text file as UTF-8: {rel}")
                continue
            if _has_workspace_secret(content, is_portable_doc=is_portable_doc):
                secret_violations.append(f"Secret detected in workspace file: {rel}")

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
