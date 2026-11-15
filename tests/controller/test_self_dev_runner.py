"""Deterministic unit and contract tests for Phase 24.4 SafeSelfDevelopmentRunner."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path

import pytest

from aipc.controller import (
    IMPORT_ORIGIN_MISMATCH,
    VERIFICATION_ISOLATION_BLOCKED,
    CandidateImportOriginReport,
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

    def _extract_probe_nonces(argv: list[str]) -> tuple[str, str]:
        for item in argv:
            if item.endswith(":/aipc_probe:ro"):
                probe_dir = Path(item[: -len(":/aipc_probe:ro")])
                script = (probe_dir / "run_verify.py").read_text(encoding="utf-8")
                m_orig = re.search(r"AIPC_CONTAINER_ORIGIN_([0-9a-f]+):", script)
                m_done = re.search(r"AIPC_CONTAINER_DONE_([0-9a-f]+):0", script)
                if m_orig and m_done:
                    return m_orig.group(1), m_done.group(1)
        return "", ""

    init_sha = hashlib.sha256(
        (candidate_root / "src" / "aipc" / "__init__.py").read_bytes()
    ).hexdigest()

    def fake_subprocess_runner(
        argv: list[str],
        env: Mapping[str, str],
        cwd: Path,
    ) -> subprocess.CompletedProcess[str]:
        observed_calls.append((list(argv), dict(env), cwd))
        probe_nonce, done_nonce = _extract_probe_nonces(argv)
        payload = json.dumps(
            {
                "file": "/workspace/src/aipc/__init__.py",
                "sha256": init_sha,
                "path": ["/workspace/src"],
            }
        )
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout=(
                f"AIPC_CONTAINER_ORIGIN_{probe_nonce}:{payload}\n"
                f"1 passed in 0.05s\n"
                f"AIPC_CONTAINER_DONE_{done_nonce}:0\n"
            ),
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
    assert "PYTHONPATH=/workspace/src" not in docker_argv
    assert not any(arg.startswith("AIPC_ORIGIN_NONCE=") for arg in docker_argv)
    assert "-I" in docker_argv
    assert "-S" in docker_argv
    assert "-B" in docker_argv
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


def test_container_verification_rejects_candidate_spoofed_origin_without_nonce(
    tmp_path: Path,
) -> None:
    stable_root = Path(__file__).resolve().parents[2]
    candidate_root = _create_candidate_tree(tmp_path / "candidate_wt")
    cfg = StableCandidateConfig(
        stable_root=str(stable_root),
        candidate_worktree_root=str(candidate_root),
        pinned_stable_sha=STABLE_SHA,
        candidate_sha=CANDIDATE_SHA,
        verification_isolation="container",
    )

    def spoofed_json_runner(
        argv: list[str],
        env: Mapping[str, str],
        cwd: Path,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout='{"aipc_import_origin": "/workspace/src/aipc/__init__.py"}\n1 passed',
            stderr="",
        )

    runner_spoof = SafeSelfDevelopmentRunner(cfg, subprocess_runner=spoofed_json_runner)
    with pytest.raises(ControllerError, match=IMPORT_ORIGIN_MISMATCH):
        runner_spoof.run_candidate_verification(pytest_targets=("tests/test_candidate_smoke.py",))

    def wrong_nonce_runner(
        argv: list[str],
        env: Mapping[str, str],
        cwd: Path,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout=(
                "AIPC_CONTAINER_ORIGIN_wrong:"
                '{"file": "/workspace/src/aipc/__init__.py", "path": ["/workspace/src"]}\n'
                "1 passed"
            ),
            stderr="",
        )

    runner_wrong_nonce = SafeSelfDevelopmentRunner(cfg, subprocess_runner=wrong_nonce_runner)
    with pytest.raises(ControllerError, match=IMPORT_ORIGIN_MISMATCH):
        runner_wrong_nonce.run_candidate_verification(
            pytest_targets=("tests/test_candidate_smoke.py",),
        )

    init_sha = hashlib.sha256(
        (candidate_root / "src" / "aipc" / "__init__.py").read_bytes()
    ).hexdigest()

    def traversal_runner(
        argv: list[str],
        env: Mapping[str, str],
        cwd: Path,
    ) -> subprocess.CompletedProcess[str]:
        probe_nonce, done_nonce = "", ""
        for item in argv:
            if item.endswith(":/aipc_probe:ro"):
                script = (Path(item[: -len(":/aipc_probe:ro")]) / "run_verify.py").read_text(
                    encoding="utf-8"
                )
                m_o = re.search(r"AIPC_CONTAINER_ORIGIN_([0-9a-f]+):", script)
                m_d = re.search(r"AIPC_CONTAINER_DONE_([0-9a-f]+):0", script)
                if m_o and m_d:
                    probe_nonce, done_nonce = m_o.group(1), m_d.group(1)
                break
        payload = json.dumps(
            {
                "file": "/workspace/src/aipc/../../other/aipc/__init__.py",
                "sha256": init_sha,
                "path": ["/workspace/src"],
            }
        )
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout=(
                f"AIPC_CONTAINER_ORIGIN_{probe_nonce}:{payload}\n"
                f"1 passed\nAIPC_CONTAINER_DONE_{done_nonce}:0\n"
            ),
            stderr="",
        )

    runner_traversal = SafeSelfDevelopmentRunner(cfg, subprocess_runner=traversal_runner)
    with pytest.raises(ControllerError, match=IMPORT_ORIGIN_MISMATCH):
        runner_traversal.run_candidate_verification(
            pytest_targets=("tests/test_candidate_smoke.py",),
        )


def test_isolated_subprocess_blocks_ntpath_getfinalpathname_monkeypatch(tmp_path: Path) -> None:
    stable_root = Path(__file__).resolve().parents[2]
    candidate_root = _create_candidate_tree(tmp_path / "candidate_wt")
    outside_file = (tmp_path / "outside_ntpath_bypass.txt").resolve()
    (candidate_root / "tests" / "test_ntpath_bypass.py").write_text(
        "\n".join(
            [
                "import ntpath, os, posixpath, pytest",
                "def test_ntpath_monkeypatch_cannot_bypass_audit():",
                f"    allowed = {str(candidate_root / 'allowed.txt')!r}",
                f"    outside = {str(outside_file)!r}",
                "    ntpath._getfinalpathname = lambda p: allowed",
                "    ntpath.realpath = lambda p: allowed",
                "    posixpath.realpath = lambda p: allowed",
                "    os.path.realpath = lambda p: allowed",
                "    with pytest.raises(PermissionError):",
                "        open(outside, 'w', encoding='utf-8').write('escape')",
                "    with pytest.raises(PermissionError):",
                "        os.unlink(outside)",
                "",
            ]
        ),
        encoding="utf-8",
    )
    cfg = StableCandidateConfig(
        stable_root=str(stable_root),
        candidate_worktree_root=str(candidate_root),
        pinned_stable_sha=STABLE_SHA,
        candidate_sha=CANDIDATE_SHA,
        verification_isolation="isolated_subprocess",
    )
    runner = SafeSelfDevelopmentRunner(cfg)
    receipt = runner.run_candidate_verification(
        pytest_targets=("tests/test_ntpath_bypass.py",),
    )
    assert receipt.passed is True
    assert not outside_file.exists()


def test_container_verification_rejects_candidate_sitecustomize_and_missing_done_nonce(
    tmp_path: Path,
) -> None:
    stable_root = Path(__file__).resolve().parents[2]
    candidate_root = _create_candidate_tree(tmp_path / "candidate_wt")
    site_hook = candidate_root / "src" / "sitecustomize.py"
    site_hook.write_text(
        "import os\nprint('AIPC_CONTAINER_ORIGIN_forged:{}', flush=True)\nos._exit(0)\n",
        encoding="utf-8",
    )
    cfg = StableCandidateConfig(
        stable_root=str(stable_root),
        candidate_worktree_root=str(candidate_root),
        pinned_stable_sha=STABLE_SHA,
        candidate_sha=CANDIDATE_SHA,
        verification_isolation="container",
    )
    runner = SafeSelfDevelopmentRunner(
        cfg,
        subprocess_runner=lambda argv, env, cwd: subprocess.CompletedProcess(
            argv, 0, stdout="", stderr=""
        ),
    )
    with pytest.raises(ControllerError, match=IMPORT_ORIGIN_MISMATCH):
        runner.run_candidate_verification(pytest_targets=("tests/test_candidate_smoke.py",))

    sub_runner = SafeSelfDevelopmentRunner(
        cfg.model_copy(update={"verification_isolation": "isolated_subprocess"})
    )
    with pytest.raises(ControllerError, match=IMPORT_ORIGIN_MISMATCH):
        sub_runner.run_candidate_verification(pytest_targets=("tests/test_candidate_smoke.py",))

    site_hook.unlink()
    init_sha = hashlib.sha256(
        (candidate_root / "src" / "aipc" / "__init__.py").read_bytes()
    ).hexdigest()

    def missing_done_nonce_runner(
        argv: list[str],
        env: Mapping[str, str],
        cwd: Path,
    ) -> subprocess.CompletedProcess[str]:
        probe_nonce = ""
        for item in argv:
            if item.endswith(":/aipc_probe:ro"):
                script = (Path(item[: -len(":/aipc_probe:ro")]) / "run_verify.py").read_text(
                    encoding="utf-8"
                )
                m_o = re.search(r"AIPC_CONTAINER_ORIGIN_([0-9a-f]+):", script)
                if m_o:
                    probe_nonce = m_o.group(1)
                break
        payload = json.dumps(
            {
                "file": "/workspace/src/aipc/__init__.py",
                "sha256": init_sha,
                "path": ["/workspace/src"],
            }
        )
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout=f"AIPC_CONTAINER_ORIGIN_{probe_nonce}:{payload}\n1 passed\n",
            stderr="",
        )

    runner_no_done = SafeSelfDevelopmentRunner(cfg, subprocess_runner=missing_done_nonce_runner)
    with pytest.raises(ControllerError, match=IMPORT_ORIGIN_MISMATCH):
        runner_no_done.run_candidate_verification(pytest_targets=("tests/test_candidate_smoke.py",))


def test_container_verification_hmac_receipt_valid_and_tampered(tmp_path: Path) -> None:
    stable_root = Path(__file__).resolve().parents[2]
    candidate_root = _create_candidate_tree(tmp_path / "candidate_wt")
    init_sha = hashlib.sha256(
        (candidate_root / "src" / "aipc" / "__init__.py").read_bytes()
    ).hexdigest()
    cfg = StableCandidateConfig(
        stable_root=str(stable_root),
        candidate_worktree_root=str(candidate_root),
        pinned_stable_sha=STABLE_SHA,
        candidate_sha=CANDIDATE_SHA,
        verification_isolation="container",
    )

    def _run_with_hmac(tamper: bool):
        def _runner(
            argv: list[str],
            env: Mapping[str, str],
            cwd: Path,
        ) -> subprocess.CompletedProcess[str]:
            probe_dir = Path("")
            ctrl_out = Path("")
            for item in argv:
                if item.endswith(":/aipc_probe:ro"):
                    probe_dir = Path(item[: -len(":/aipc_probe:ro")])
                elif item.endswith(":/aipc_ctrl_out:rw"):
                    ctrl_out = Path(item[: -len(":/aipc_ctrl_out:rw")])
            assert (probe_dir / "secret.json").is_file()
            script = (probe_dir / "run_verify.py").read_text(encoding="utf-8")
            m_o = re.search(r"AIPC_CONTAINER_ORIGIN_([0-9a-f]+):", script)
            m_d = re.search(r"AIPC_CONTAINER_DONE_([0-9a-f]+):0", script)
            assert m_o and m_d
            sec_key = json.loads((probe_dir / "secret.json").read_text(encoding="utf-8"))[
                "hmac_key"
            ].encode("utf-8")
            sig = hmac.new(
                sec_key,
                f"/workspace/src/aipc/__init__.py|{init_sha}|0".encode(),
                hashlib.sha256,
            ).hexdigest()
            if tamper:
                sig = "0" * 64
            (ctrl_out / "receipt.json").write_text(
                json.dumps(
                    {
                        "file": "/workspace/src/aipc/__init__.py",
                        "sha256": init_sha,
                        "path": ["/workspace/src"],
                        "rc": 0,
                        "hmac": sig,
                    }
                ),
                encoding="utf-8",
            )
            payload = json.dumps(
                {
                    "file": "/workspace/src/aipc/__init__.py",
                    "sha256": init_sha,
                    "path": ["/workspace/src"],
                }
            )
            return subprocess.CompletedProcess(
                argv,
                0,
                stdout=(
                    f"AIPC_CONTAINER_ORIGIN_{m_o.group(1)}:{payload}\n"
                    f"1 passed\nAIPC_CONTAINER_DONE_{m_d.group(1)}:0\n"
                ),
                stderr="",
            )

        return _runner

    good_runner = SafeSelfDevelopmentRunner(cfg, subprocess_runner=_run_with_hmac(tamper=False))
    receipt = good_runner.run_candidate_verification(
        pytest_targets=("tests/test_candidate_smoke.py",),
    )
    assert receipt.passed is True

    bad_runner = SafeSelfDevelopmentRunner(cfg, subprocess_runner=_run_with_hmac(tamper=True))
    with pytest.raises(ControllerError, match=IMPORT_ORIGIN_MISMATCH):
        bad_runner.run_candidate_verification(
            pytest_targets=("tests/test_candidate_smoke.py",),
        )


def test_isolated_subprocess_blocks_dangling_symlink_escape(tmp_path: Path) -> None:
    stable_root = Path(__file__).resolve().parents[2]
    candidate_root = _create_candidate_tree(tmp_path / "candidate_wt")
    outside_missing = (tmp_path / "outside_dangling_target.txt").resolve()
    dangling = candidate_root / "dangling_escape.txt"
    try:
        os.symlink(outside_missing, dangling)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")

    (candidate_root / "tests" / "test_dangling_link.py").write_text(
        "\n".join(
            [
                "import pytest",
                "def test_dangling_symlink_write_blocked():",
                "    with pytest.raises(PermissionError):",
                f"        open({str(dangling)!r}, 'w', encoding='utf-8').write('escape')",
                "",
            ]
        ),
        encoding="utf-8",
    )
    cfg = StableCandidateConfig(
        stable_root=str(stable_root),
        candidate_worktree_root=str(candidate_root),
        pinned_stable_sha=STABLE_SHA,
        candidate_sha=CANDIDATE_SHA,
        verification_isolation="isolated_subprocess",
    )
    runner = SafeSelfDevelopmentRunner(cfg)
    with pytest.raises(ControllerError, match="escape_link"):
        runner.run_candidate_verification(
            pytest_targets=("tests/test_dangling_link.py",),
        )
    origin_rep = CandidateImportOriginReport(
        verified=True,
        expected_candidate_src=str(candidate_root / "src"),
        actual_imported_file=str(candidate_root / "src" / "aipc" / "__init__.py"),
        leaked_stable_paths=(),
        status_code="OK",
    )
    sub_receipt = runner._run_isolated_subprocess_verification(
        stable_root=stable_root,
        candidate_root=candidate_root,
        candidate_src=candidate_root / "src",
        origin_report=origin_rep,
        pytest_targets=("tests/test_dangling_link.py",),
        python_executable=None,
        parent_env=None,
    )
    assert sub_receipt.passed is True
    assert not outside_missing.exists()
