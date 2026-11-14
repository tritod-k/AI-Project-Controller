"""Deterministic unit and contract tests for Phase 24.4 SafeSelfDevelopmentRunner."""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path

import pytest

from aipc.controller import (
    IMPORT_ORIGIN_MISMATCH,
    VERIFICATION_ISOLATION_BLOCKED,
    ControllerError,
    SafeSelfDevelopmentRunner,
    StableCandidateConfig,
    assert_stable_process_free_of_candidate_imports,
    verify_candidate_import_origin,
)

STABLE_SHA = "1" * 40
CANDIDATE_SHA = "2" * 40
SYNTHETIC_API_KEY = "AIza" + "W" * 35


def _create_candidate_tree(root: Path) -> Path:
    cand_pkg = root / "src" / "aipc"
    cand_pkg.mkdir(parents=True, exist_ok=True)
    (cand_pkg / "__init__.py").write_text("__version__ = '0.24.0-candidate'", encoding="utf-8")
    tests_dir = root / "tests"
    tests_dir.mkdir(parents=True, exist_ok=True)
    nl = chr(10)
    (tests_dir / "test_candidate_smoke.py").write_text(
        nl.join(
            [
                "import aipc",
                "def test_ver():",
                "    assert aipc.__version__ == '0.24.0-candidate'",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return root


def test_stable_candidate_config_enforces_disjoint_roots(tmp_path: Path) -> None:
    stable = tmp_path / "stable"
    stable.mkdir()
    nested = stable / "worktree"
    nested.mkdir()

    with pytest.raises(ValueError, match="must be disjoint"):
        StableCandidateConfig(
            stable_root=str(stable),
            candidate_worktree_root=str(stable),
            pinned_stable_sha=STABLE_SHA,
            candidate_sha=CANDIDATE_SHA,
        )

    with pytest.raises(ValueError, match="must be disjoint"):
        StableCandidateConfig(
            stable_root=str(stable),
            candidate_worktree_root=str(nested),
            pinned_stable_sha=STABLE_SHA,
            candidate_sha=CANDIDATE_SHA,
        )


def test_verify_candidate_import_origin_detects_mismatch_and_verifies_isolated_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", SYNTHETIC_API_KEY)
    stable_root = Path(__file__).resolve().parents[2]
    candidate_root = _create_candidate_tree(tmp_path / "candidate_wt")

    # 1. Without isolation bootstrap, editable .venv resolves `import aipc` to Stable -> FAIL CLOSED
    with pytest.raises(ControllerError, match=IMPORT_ORIGIN_MISMATCH):
        verify_candidate_import_origin(
            stable_root=stable_root,
            candidate_root=candidate_root,
            python_executable=sys.executable,
            use_isolation_bootstrap=False,
        )

    # 2. With isolation bootstrap, `import aipc` resolves strictly to Candidate src/aipc
    report = verify_candidate_import_origin(
        stable_root=stable_root,
        candidate_root=candidate_root,
        python_executable=sys.executable,
        use_isolation_bootstrap=True,
    )
    assert report.verified is True
    assert report.status_code == "OK"
    assert Path(report.actual_imported_file).is_relative_to(candidate_root / "src")
    assert report.leaked_stable_paths == ()


def test_default_blocked_isolation_and_forbidden_auto_promotion(tmp_path: Path) -> None:
    stable_root = Path(__file__).resolve().parents[2]
    candidate_root = _create_candidate_tree(tmp_path / "candidate_wt")

    cfg = StableCandidateConfig(
        stable_root=str(stable_root),
        candidate_worktree_root=str(candidate_root),
        pinned_stable_sha=STABLE_SHA,
        candidate_sha=CANDIDATE_SHA,
    )
    assert cfg.verification_isolation == "blocked"
    runner = SafeSelfDevelopmentRunner(cfg)

    with pytest.raises(ControllerError, match=VERIFICATION_ISOLATION_BLOCKED):
        runner.run_candidate_verification()

    with pytest.raises(
        ControllerError,
        match="Automatic Stable Controller replacement is forbidden",
    ):
        runner.promote_candidate_to_stable()


def test_container_and_subprocess_verification_use_sanitized_env_without_api_keys(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", SYNTHETIC_API_KEY)
    monkeypatch.setenv("GOOGLE_API_KEY", SYNTHETIC_API_KEY)
    stable_root = Path(__file__).resolve().parents[2]
    candidate_root = _create_candidate_tree(tmp_path / "candidate_wt")

    observed_calls: list[tuple[list[str], dict[str, str], Path]] = []

    def fake_subprocess_runner(
        argv: list[str],
        env: Mapping[str, str],
        cwd: Path,
    ) -> subprocess.CompletedProcess[str]:
        observed_calls.append((list(argv), dict(env), cwd))
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout='{"aipc_import_origin": "/workspace/src/aipc/__init__.py"}\n1 passed in 0.05s',
            stderr="",
        )

    container_cfg = StableCandidateConfig(
        stable_root=str(stable_root),
        candidate_worktree_root=str(candidate_root),
        pinned_stable_sha=STABLE_SHA,
        candidate_sha=CANDIDATE_SHA,
        verification_isolation="container",
    )
    container_runner = SafeSelfDevelopmentRunner(
        container_cfg,
        subprocess_runner=fake_subprocess_runner,
    )
    receipt = container_runner.run_candidate_verification(
        pytest_targets=("tests/test_candidate_smoke.py",),
    )
    assert receipt.passed is True
    assert receipt.import_origin_verified is True
    assert receipt.sanitized_env_verified is True
    assert len(observed_calls) == 1
    docker_argv, docker_env, _cwd = observed_calls[0]
    assert docker_argv[:4] == ["docker", "run", "--rm", "--network"]
    assert "GEMINI_API_KEY" not in docker_env
    assert "GOOGLE_API_KEY" not in docker_env
    assert all(SYNTHETIC_API_KEY not in arg for arg in docker_argv)

    # Also verify real isolated_subprocess execution against the candidate smoke test
    subproc_cfg = container_cfg.model_copy(update={"verification_isolation": "isolated_subprocess"})
    subproc_runner = SafeSelfDevelopmentRunner(subproc_cfg)
    real_receipt = subproc_runner.run_candidate_verification(
        pytest_targets=("tests/test_candidate_smoke.py",),
    )
    assert real_receipt.passed is True
    assert real_receipt.exit_code == 0
    assert real_receipt.import_origin_verified is True


def test_stable_process_rejects_loaded_candidate_module(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate_root = _create_candidate_tree(tmp_path / "candidate_wt")
    fake_mod = type(sys)("aipc_candidate_leaked_mod")
    fake_mod.__file__ = str(candidate_root / "src" / "aipc" / "__init__.py")
    monkeypatch.setitem(sys.modules, "aipc_candidate_leaked_mod", fake_mod)

    with pytest.raises(ControllerError, match=IMPORT_ORIGIN_MISMATCH):
        assert_stable_process_free_of_candidate_imports(candidate_root)
