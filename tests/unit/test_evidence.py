"""Portable paths and narrow test parsing, independent of worker prose."""

import importlib

import pytest


def module(name):
    return importlib.import_module(f"aipc.evidence.{name}")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("=== 10 passed in 0.5s ===", (10, 10, 0, 0)),
        ("1 failed, 2 passed, 3 skipped in 1.0s", (6, 2, 1, 3)),
        ("=== 4 skipped in 0.01s ===", (4, 0, 0, 4)),
        ("no tests ran in 0.01s", (0, 0, 0, 0)),
        ("\x1b[32m2 passed\x1b[0m in 1.23s", (2, 2, 0, 0)),
        ("everything passed\n1 failed in 1.2s", (1, 0, 1, 0)),
        ("2 passed in 0.1s\n3 passed in 0.2s", (3, 3, 0, 0)),
    ],
)
def test_pytest_parser(text, expected):
    result = module("test_parser").PytestResultParser().parse(text)
    assert (result.total, result.passed, result.failed, result.skipped) == expected


@pytest.mark.parametrize(
    "text",
    [
        "",
        "Everything passed",
        "FAILED tests/test_x.py",
        "1 error in 1s",
        "2 passed, 1 error in 1s",
        "2 passed, 1 xfailed in 1s",
        "1 warning in 1s",
        "1 passed, 2 passed in 1s",
        "-1 passed in 1s",
        "2 passed in 1s garbage",
        "2 passed in 1s\n1 error in 0.1s",
    ],
)
def test_unsupported_test_log_is_not_success(text):
    with pytest.raises(module("test_parser").TestParseError):
        module("test_parser").PytestResultParser().parse(text)


@pytest.mark.parametrize(
    "path",
    [
        "../outside",
        "..\\outside",
        "a/../b",
        "/outside",
        "C:\\outside",
        "C:relative",
        "\\\\server\\share",
        "a:b",
        "a\\..\\b",
        "",
        ".",
        "a//b",
        "a/./b",
        "NUL.txt",
        "CON",
        "com1.log",
        "a.",
        "a ",
        "a\x00b",
        "a\nb",
        "a?b",
        "a|b",
        "https://host/file",
    ],
)
def test_unsafe_portable_path(path):
    with pytest.raises(module("paths").EvidencePathError):
        module("paths").relative_path(path)


@pytest.mark.parametrize(
    "path", ["logs/tests.log", "logs\\tests.log", "report space.txt", "ไทย.txt"]
)
def test_safe_portable_path(path):
    assert module("paths").relative_path(path).as_posix() == path.replace("\\", "/")


def test_paths_reject_missing_and_directory(tmp_path):
    paths = module("paths")
    with pytest.raises(FileNotFoundError):
        paths.source_file(tmp_path, "missing.txt")
    (tmp_path / "folder").mkdir()
    with pytest.raises(paths.EvidencePathError):
        paths.source_file(tmp_path, "folder")


def test_symlink_escape(tmp_path):
    outside = tmp_path / "outside"
    outside.write_text("secret")
    root = tmp_path / "workspace"
    root.mkdir()
    link = root / "link"
    try:
        link.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")
    with pytest.raises(module("paths").EvidencePathError):
        module("paths").source_file(root, "link")


def test_manifest_item_availability_invariants():
    from pydantic import ValidationError

    models = module("models")
    with pytest.raises(ValidationError):
        models.EvidenceItem(evidence_id="test", kind="test_log", status="collected")
    with pytest.raises(ValidationError):
        models.EvidenceItem(evidence_id="test", kind="test_log", status="unavailable")
    with pytest.raises(ValidationError):
        models.ArtifactRecord(
            artifact_id="a",
            path="../escape",
            kind="file",
            source="generated",
            size=0,
            sha256="0" * 64,
        )
