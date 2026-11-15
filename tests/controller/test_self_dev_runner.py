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
from aipc.hardening.env_isolation import (
    assert_child_env_isolated,
    build_sanitized_child_env,
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
            assert (ctrl_out / "nonce.json").is_file()
            script = (probe_dir / "run_verify.py").read_text(encoding="utf-8")
            m_o = re.search(r"AIPC_CONTAINER_ORIGIN_([0-9a-f]+):", script)
            m_d = re.search(r"AIPC_CONTAINER_DONE_([0-9a-f]+):0", script)
            assert m_o and m_d
            auth_nonce_b = json.loads((ctrl_out / "nonce.json").read_text(encoding="utf-8"))[
                "auth_nonce"
            ].encode("utf-8")
            sig = hmac.new(
                auth_nonce_b,
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


def test_isolated_subprocess_blocks_sys_getframe_from_candidate(tmp_path: Path) -> None:
    stable_root = Path(__file__).resolve().parents[2]
    candidate_root = _create_candidate_tree(tmp_path / "candidate_wt")
    (candidate_root / "tests" / "test_getframe_blocked.py").write_text(
        "\n".join(
            [
                "import sys, pytest",
                "def test_getframe_raises_permission_error():",
                "    with pytest.raises(PermissionError, match='WORKSPACE_WRITE_ISOLATION'):",
                "        sys._getframe(0)",
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
        pytest_targets=("tests/test_getframe_blocked.py",),
    )
    assert receipt.passed is True
    assert receipt.exit_code == 0


def test_isolated_subprocess_blocks_self_link_parent_traversal_escape(tmp_path: Path) -> None:
    stable_root = Path(__file__).resolve().parents[2]
    candidate_root = _create_candidate_tree(tmp_path / "candidate_wt")
    self_link = candidate_root / "self_link"
    outside_victim = (tmp_path / "outside_victim.txt").resolve()
    try:
        os.symlink(candidate_root, self_link)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")

    escape_target = str(candidate_root / "self_link" / ".." / "outside_victim.txt")
    (candidate_root / "tests" / "test_self_link_escape.py").write_text(
        "\n".join(
            [
                "import pytest",
                "def test_self_link_dotdot_escape_blocked():",
                "    with pytest.raises(PermissionError):",
                f"        open({escape_target!r}, 'w', encoding='utf-8').write('escape')",
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
        pytest_targets=("tests/test_self_link_escape.py",),
    )
    assert receipt.passed is True
    assert not outside_victim.exists()


def test_container_and_subprocess_reject_candidate_pytest_shadow_and_hardens_probe(
    tmp_path: Path,
) -> None:
    stable_root = Path(__file__).resolve().parents[2]
    candidate_root = _create_candidate_tree(tmp_path / "candidate_wt")
    shadow_pytest = candidate_root / "src" / "pytest" / "__init__.py"
    shadow_pytest.parent.mkdir(parents=True, exist_ok=True)
    shadow_pytest.write_text("def main(*a, **kw):\n    return 0\n", encoding="utf-8")

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

    shadow_pytest.unlink()
    captured_scripts: list[tuple[str, str]] = []
    init_sha = hashlib.sha256(
        (candidate_root / "src" / "aipc" / "__init__.py").read_bytes()
    ).hexdigest()

    def inspect_probe_runner(
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
        parent_script = (probe_dir / "run_verify.py").read_text(encoding="utf-8")
        child_script = (probe_dir / "child_verify.py").read_text(encoding="utf-8")
        captured_scripts.append((parent_script, child_script))
        assert (ctrl_out / "nonce.json").is_file()
        auth_nonce_b = json.loads((ctrl_out / "nonce.json").read_text(encoding="utf-8"))[
            "auth_nonce"
        ].encode("utf-8")
        m_o = re.search(r"AIPC_CONTAINER_ORIGIN_([0-9a-f]+):", parent_script)
        m_d = re.search(r"AIPC_CONTAINER_DONE_([0-9a-f]+):0", parent_script)
        assert m_o and m_d
        sig = hmac.new(
            auth_nonce_b,
            f"/workspace/src/aipc/__init__.py|{init_sha}|0".encode(),
            hashlib.sha256,
        ).hexdigest()
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

    inspect_runner = SafeSelfDevelopmentRunner(cfg, subprocess_runner=inspect_probe_runner)
    receipt = inspect_runner.run_candidate_verification(
        pytest_targets=("tests/test_candidate_smoke.py",),
    )
    assert receipt.passed is True
    assert len(captured_scripts) == 1
    parent_script, child_script = captured_scripts[0]
    assert "os.unlink('/aipc_ctrl_out/nonce.json')" in parent_script
    assert "globals().pop('_aipc_parent_verify', None)" in parent_script
    assert "/aipc_probe/child_verify.py" in parent_script
    assert "import aipc" not in parent_script
    assert "sys.path.insert(0, '/workspace/src')" not in parent_script
    assert child_script.index("import pytest") < child_script.index(
        "sys.path.insert(0, '/workspace/src')"
    )
    assert child_script.index("import pytest") < child_script.index(
        "sys.addaudithook(_probe_audit)"
    )
    assert "globals().pop('_aipc_container_verify', None)" in child_script
    assert "if ev == 'os.chdir':" in child_script
    assert "_c_lstat(" in child_script
    assert "os.path.realpath" not in child_script
    assert "sys.modules.get('pytest') is not _real_pytest_mod" in child_script
    assert "class _CtrlPlugin:" in child_script
    assert "__slots__ = ()" in child_script
    assert "_guarded_runtest" in child_script
    assert "'--noconftest'" in child_script
    assert "list_name_plugin" in child_script
    assert child_script.index("_real_pytest_main(") < child_script.index("AIPC_CONTAINER_ORIGIN_")
    assert "plugins=[_plugin]" in child_script
    assert "nonce.json" not in child_script


def test_bootstrap_ignores_candidate_socket_shadow_and_prescan_rejects(tmp_path: Path) -> None:
    stable_root = Path(__file__).resolve().parents[2]
    candidate_root = _create_candidate_tree(tmp_path / "candidate_wt")
    outside_marker = (tmp_path / "outside_socket_marker.txt").resolve()
    malicious_socket = candidate_root / "src" / "socket.py"
    malicious_socket.write_text(
        f"from pathlib import Path\nPath({str(outside_marker)!r}).write_text('pwned')\n",
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

    with pytest.raises(ControllerError, match=IMPORT_ORIGIN_MISMATCH):
        runner.run_candidate_verification(pytest_targets=("tests/test_candidate_smoke.py",))
    assert not outside_marker.exists()

    # Even if pre-scan were bypassed, sitecustomize.py must not import candidate src/socket.py
    # before installing sandbox guards (and must not put candidate src on PYTHONPATH pre-boot).
    dummy_origin = CandidateImportOriginReport(
        verified=True,
        expected_candidate_src=str((candidate_root / "src").resolve()),
        actual_imported_file=str((candidate_root / "src" / "aipc" / "__init__.py").resolve()),
        leaked_stable_paths=(),
        status_code="OK",
    )
    runner._run_isolated_subprocess_verification(
        stable_root=stable_root,
        candidate_root=candidate_root,
        candidate_src=(candidate_root / "src").resolve(),
        origin_report=dummy_origin,
        pytest_targets=("tests/test_candidate_smoke.py",),
        python_executable=None,
        parent_env=None,
    )
    assert not outside_marker.exists()


def test_isolated_subprocess_seals_popen_and_blocks_scratch_pythonpath(
    tmp_path: Path,
) -> None:
    stable_root = Path(__file__).resolve().parents[2]
    candidate_root = _create_candidate_tree(tmp_path / "candidate_wt")
    (candidate_root / "tests" / "test_popen_seal_and_scratch_pp.py").write_text(
        "\n".join(
            [
                "import os, pathlib, subprocess, sys, pytest",
                "def test_popen_sealed_and_scratch_pythonpath_blocked():",
                "    scratch = pathlib.Path(os.environ['TMPDIR'])",
                "    marker = scratch / 'scratch_socket_pwned.txt'",
                "    _m_str = str(marker)",
                "    _code = f'import pathlib\\npathlib.Path({_m_str!r}).write_text(\"p\")\\n'",
                "    (scratch / 'socket.py').write_text(_code, encoding='utf-8')",
                "    proc = subprocess.run(",
                "        [sys.executable, '-c', 'import socket; print(\"ok\")'],",
                "        env={'PYTHONPATH': str(scratch), 'PATH': os.environ.get('PATH', '')},",
                "        capture_output=True,",
                "        text=True,",
                "        check=True,",
                "    )",
                "    assert 'ok' in proc.stdout",
                "    assert not marker.exists()",
                "    with pytest.raises(PermissionError):",
                "        _ = subprocess.Popen.__init__",
                "    with pytest.raises(PermissionError):",
                "        _ = subprocess.Popen.__dict__['__init__'].__closure__",
                "    p = subprocess.Popen(",
                "        [sys.executable, '-c', 'pass'],",
                "        env={'PYTHONPATH': '', 'PATH': os.environ.get('PATH', '')},",
                "    )",
                "    assert p.wait() == 0",
                "    with pytest.raises(PermissionError):",
                "        _ = p.__init__.__closure__",
                "    import gc",
                "    caught = None",
                "    try:",
                "        gc.get_objects()",
                "    except PermissionError as exc:",
                "        caught = exc",
                "    assert caught is not None",
                "    tb = caught.__traceback__",
                "    tb_blocked = False",
                "    while tb is not None:",
                "        try:",
                "            locs = tb.tb_frame.f_locals",
                "            assert '_a_boot' not in locs and '_a_tmp' not in locs",
                "        except PermissionError:",
                "            tb_blocked = True",
                "        tb = tb.tb_next",
                "    assert tb_blocked",
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
        pytest_targets=("tests/test_popen_seal_and_scratch_pp.py",),
    )
    assert receipt.passed is True
    assert receipt.exit_code == 0


def test_isolated_subprocess_popen_closure_mutation_does_not_bypass_audit(
    tmp_path: Path,
) -> None:
    stable_root = Path(__file__).resolve().parents[2]
    candidate_root = _create_candidate_tree(tmp_path / "candidate_wt")
    (candidate_root / "tests" / "test_popen_closure_unshared.py").write_text(
        "\n".join(
            [
                "import gc, os, pathlib, subprocess, sys, pytest",
                "def test_popen_closure_cells_unshared_from_audit_hook():",
                "    scratch = pathlib.Path(os.environ['TMPDIR'])",
                "    d = subprocess.Popen.__dict__['__init__']",
                "    sealed_fns = None",
                "    for cell in type(d).__init__.__closure__ or ():",
                "        if isinstance(cell.cell_contents, dict) and id(d) in cell.cell_contents:",
                "            sealed_fns = cell.cell_contents",
                "            break",
                "    assert sealed_fns is not None",
                "    guarded = sealed_fns[id(d)]",
                "    for cell in guarded.__closure__ or ():",
                "        if isinstance(cell.cell_contents, str):",
                "            cell.cell_contents = str(scratch)",
                "    with pytest.raises(",
                "        PermissionError,",
                "        match='SUBPROCESS_SANDBOX_BYPASS_BLOCKED: untrusted child environment',",
                "    ):",
                "        subprocess.Popen([sys.executable, '-c', 'pass'])",
                "    with pytest.raises(",
                "        PermissionError,",
                "        match='WORKSPACE_WRITE_ISOLATION_BLOCKED: gc introspection',",
                "    ):",
                "        gc.get_objects()",
                "    with pytest.raises(",
                "        PermissionError,",
                "        match='WORKSPACE_WRITE_ISOLATION_BLOCKED: gc introspection',",
                "    ):",
                "        gc.get_referrers(d)",
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
        pytest_targets=("tests/test_popen_closure_unshared.py",),
    )
    assert receipt.passed is True
    assert receipt.exit_code == 0


def test_container_child_verify_ctrlplugin_compat_and_smap_retarget_rejection(
    tmp_path: Path,
) -> None:
    stable_root = Path(__file__).resolve().parents[2]
    candidate_root = _create_candidate_tree(tmp_path / "candidate_wt")
    init_sha = hashlib.sha256(
        (candidate_root / "src" / "aipc" / "__init__.py").read_bytes()
    ).hexdigest()
    captured_child: list[str] = []

    def capture_runner(
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
        parent_script = (probe_dir / "run_verify.py").read_text(encoding="utf-8")
        captured_child.append((probe_dir / "child_verify.py").read_text(encoding="utf-8"))
        m_o = re.search(r"AIPC_CONTAINER_ORIGIN_([0-9a-f]+):", parent_script)
        m_d = re.search(r"AIPC_CONTAINER_DONE_([0-9a-f]+):0", parent_script)
        assert m_o and m_d
        auth_nonce_b = json.loads((ctrl_out / "nonce.json").read_text(encoding="utf-8"))[
            "auth_nonce"
        ].encode("utf-8")
        sig = hmac.new(
            auth_nonce_b,
            f"/workspace/src/aipc/__init__.py|{init_sha}|0".encode(),
            hashlib.sha256,
        ).hexdigest()
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

    cfg = StableCandidateConfig(
        stable_root=str(stable_root),
        candidate_worktree_root=str(candidate_root),
        pinned_stable_sha=STABLE_SHA,
        candidate_sha=CANDIDATE_SHA,
        verification_isolation="container",
    )
    SafeSelfDevelopmentRunner(cfg, subprocess_runner=capture_runner).run_candidate_verification(
        pytest_targets=("tests/test_candidate_smoke.py",),
    )
    assert len(captured_child) == 1
    child_script = captured_child[0]
    assert "def pytest_runtest_logreport(self, report):" in child_script
    assert "def pytest_sessionfinish(self, session, exitstatus):" in child_script
    assert "pytest_runtest_logreport = _seal_method" not in child_script
    assert "pytest_sessionfinish = _seal_method" not in child_script

    child_path = tmp_path / "child_verify.py"
    child_path.write_text(child_script, encoding="utf-8")

    sanitized_env = build_sanitized_child_env({**os.environ, "OPENAI_API_KEY": SYNTHETIC_API_KEY})
    assert "OPENAI_API_KEY" not in sanitized_env
    assert_child_env_isolated(sanitized_env)

    ok_test = candidate_root / "tests" / "test_pluggy_compat.py"
    ok_test.write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    ok_proc = subprocess.run(
        [sys.executable, "-B", str(child_path), str(ok_test)],
        cwd=str(candidate_root),
        env=sanitized_env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )
    assert ok_proc.returncode == 0
    assert "AIPC_CONTAINER_ORIGIN_" in ok_proc.stdout

    retarget_test = candidate_root / "tests" / "test_smap_retarget.py"
    retarget_test.write_text(
        "\n".join(
            [
                "import _pytest.python",
                "def test_retarget_smap_runtest():",
                "    d = _pytest.python.Function.__dict__['runtest']",
                "    for cell in type(d).__init__.__closure__ or ():",
                "        if isinstance(cell.cell_contents, dict) and id(d) in cell.cell_contents:",
                "            cell.cell_contents[id(d)] = lambda self_item: None",
                "            break",
                "",
            ]
        ),
        encoding="utf-8",
    )
    bad_proc = subprocess.run(
        [sys.executable, "-B", str(child_path), str(retarget_test)],
        cwd=str(candidate_root),
        env=sanitized_env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )
    assert bad_proc.returncode != 0
    assert "AIPC_CONTAINER_ORIGIN_" not in bad_proc.stdout
    assert "IMPORT_ORIGIN_MISMATCH" in (bad_proc.stdout + bad_proc.stderr)

    traceback_test = candidate_root / "tests" / "test_traceback_walk.py"
    traceback_test.write_text(
        "\n".join(
            [
                "import gc",
                "def test_traceback_walk_cannot_expose_vault():",
                "    caught = None",
                "    try:",
                "        gc.get_objects()",
                "    except PermissionError as exc:",
                "        caught = exc",
                "    assert caught is not None",
                "    tb = caught.__traceback__",
                "    blocked = False",
                "    while tb is not None:",
                "        try:",
                "            locs = tb.tb_frame.f_locals",
                "            for val in locs.values():",
                "                if isinstance(val, dict) and 'ran' in val:",
                "                    val['ran'] = True",
                "        except PermissionError:",
                "            blocked = True",
                "        tb = tb.tb_next",
                "    assert blocked",
                "    raise RuntimeError('EXPECTED_TEST_FAILURE_AFTER_TB_CHECK')",
                "",
            ]
        ),
        encoding="utf-8",
    )
    tb_proc = subprocess.run(
        [sys.executable, "-B", str(child_path), str(traceback_test)],
        cwd=str(candidate_root),
        env=sanitized_env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )
    assert tb_proc.returncode != 0
    assert "AIPC_CONTAINER_ORIGIN_" not in tb_proc.stdout
    assert "EXPECTED_TEST_FAILURE_AFTER_TB_CHECK" in (tb_proc.stdout + tb_proc.stderr)


def test_isolated_subprocess_blocks_builtin_isinstance_mutation_and_native_open(
    tmp_path: Path,
) -> None:
    stable_root = Path(__file__).resolve().parents[2]
    candidate_root = _create_candidate_tree(tmp_path / "candidate_wt")
    ext_sentinel = tmp_path / "external_sentinel.txt"
    (candidate_root / "tests" / "test_isinstance_native_open.py").write_text(
        "\n".join(
            [
                "import builtins, os, pathlib, pytest",
                "def test_isinstance_mutation_and_native_open_blocked():",
                "    orig_isinstance = builtins.isinstance",
                "    builtins.isinstance = (",
                "        lambda obj, cls: False if cls is int else orig_isinstance(obj, cls)",
                "    )",
                "    try:",
                "        nat = __import__('nt' if os.name == 'nt' else 'posix')",
                f"        target = {str(ext_sentinel)!r}",
                "        with pytest.raises(PermissionError):",
                "            fd = nat.open(target, os.O_WRONLY | os.O_CREAT, 0o600)",
                "            nat.close(fd)",
                "        with pytest.raises(PermissionError):",
                "            with open(target, 'w', encoding='utf-8') as fh:",
                "                fh.write('pwned')",
                "        assert not pathlib.Path(target).exists()",
                "    finally:",
                "        builtins.isinstance = orig_isinstance",
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
        pytest_targets=("tests/test_isinstance_native_open.py",),
    )
    assert receipt.passed is True
    assert receipt.exit_code == 0
    assert not ext_sentinel.exists()


def test_container_verification_fails_closed_on_bypass_and_forged_origin_exit_zero(
    tmp_path: Path,
) -> None:
    stable_root = Path(__file__).resolve().parents[2]
    candidate_root = _create_candidate_tree(tmp_path / "candidate_wt")
    init_sha = hashlib.sha256(
        (candidate_root / "src" / "aipc" / "__init__.py").read_bytes()
    ).hexdigest()
    captured: dict[str, str] = {}

    def capture_runner(
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
        captured["child"] = (probe_dir / "child_verify.py").read_text(encoding="utf-8")
        captured["parent"] = (probe_dir / "run_verify.py").read_text(encoding="utf-8")
        captured["nonce"] = (ctrl_out / "nonce.json").read_text(encoding="utf-8")
        return subprocess.CompletedProcess(argv, 1, stdout="", stderr="capture")

    cfg = StableCandidateConfig(
        stable_root=str(stable_root),
        candidate_worktree_root=str(candidate_root),
        pinned_stable_sha=STABLE_SHA,
        candidate_sha=CANDIDATE_SHA,
        verification_isolation="container",
    )
    SafeSelfDevelopmentRunner(cfg, subprocess_runner=capture_runner).run_candidate_verification(
        pytest_targets=("tests/test_candidate_smoke.py",),
    )
    assert "child" in captured and "parent" in captured
    m_o = re.search(r"AIPC_CONTAINER_ORIGIN_([0-9a-f]+):", captured["parent"])
    m_d = re.search(r"AIPC_CONTAINER_DONE_([0-9a-f]+):0", captured["parent"])
    assert m_o and m_d

    exec_probe = tmp_path / "exec_probe"
    exec_ctrl = tmp_path / "exec_ctrl"
    exec_probe.mkdir()
    exec_ctrl.mkdir()
    (exec_ctrl / "nonce.json").write_text(captured["nonce"], encoding="utf-8")

    child_src = (
        captured["child"]
        .replace("/aipc_probe", exec_probe.as_posix())
        .replace("/aipc_ctrl_out", exec_ctrl.as_posix())
    )
    parent_src = (
        captured["parent"]
        .replace("/aipc_probe", exec_probe.as_posix())
        .replace("/aipc_ctrl_out", exec_ctrl.as_posix())
        .replace("'-I', '-S', '-B'", "'-B'")
    )
    child_file = exec_probe / "child_verify.py"
    parent_file = exec_probe / "run_verify.py"
    child_file.write_text(child_src, encoding="utf-8")
    parent_file.write_text(parent_src, encoding="utf-8")

    forged_payload = json.dumps(
        {
            "file": "/workspace/src/aipc/__init__.py",
            "sha256": init_sha,
            "path": ["/workspace/src"],
        }
    )
    forged_line = f"{m_o.group(0)}{forged_payload}\\n".encode()
    bypass_test = candidate_root / "tests" / "test_container_bypass_attempts.py"
    bypass_test.write_text(
        "\n".join(
            [
                "import os, pytest, subprocess, sys",
                "def test_container_blocks_descendant_and_native_exit():",
                "    with pytest.raises(PermissionError):",
                "        subprocess.Popen([sys.executable, '-c', 'pass'])",
                "    with pytest.raises(PermissionError):",
                f"        open({str(child_file)!r}, 'r', encoding='utf-8')",
                "    nat = __import__('nt' if os.name == 'nt' else 'posix')",
                f"    os.write(1, {forged_line!r})",
                "    with pytest.raises(PermissionError):",
                "        nat._exit(0)",
                "",
            ]
        ),
        encoding="utf-8",
    )

    sanitized_env = build_sanitized_child_env({**os.environ, "OPENAI_API_KEY": SYNTHETIC_API_KEY})
    assert_child_env_isolated(sanitized_env)
    proc = subprocess.run(
        [sys.executable, "-B", str(parent_file), str(bypass_test)],
        cwd=str(candidate_root),
        env=sanitized_env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode != 0
    assert not (exec_ctrl / "receipt.json").exists()
    assert not (exec_ctrl / "child_seal.json").exists()
    assert m_d.group(0) not in proc.stdout

    # Even if a compromised child emits forged AIPC_CONTAINER_ORIGIN_ on fd 1 and exits 0,
    # run_verify.py must still fail closed without child_seal.json.
    (exec_ctrl / "nonce.json").write_text(captured["nonce"], encoding="utf-8")
    child_file.write_text(
        "\n".join(
            [
                "import os",
                f"os.write(1, {forged_line!r})",
                "os._exit(0)",
                "",
            ]
        ),
        encoding="utf-8",
    )
    forged_proc = subprocess.run(
        [sys.executable, "-B", str(parent_file), str(bypass_test)],
        cwd=str(candidate_root),
        env=sanitized_env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )
    assert forged_proc.returncode != 0
    assert not (exec_ctrl / "receipt.json").exists()
    assert m_d.group(0) not in forged_proc.stdout


def test_c16_mc_rr_01_02_isolated_subprocess_synthetic_audit_fake_py_and_winapi_blocked(
    tmp_path: Path,
) -> None:
    stable_root = Path(__file__).resolve().parents[2]
    candidate_root = _create_candidate_tree(tmp_path / "candidate_wt")
    ext_sentinel = tmp_path / "external_sentinel.txt"
    fake_py = candidate_root / ("python.exe" if os.name == "nt" else "python")
    fake_py.write_text("", encoding="utf-8")

    (candidate_root / "tests" / "test_c16_mc_rr_01_02.py").write_text(
        "\n".join(
            [
                "import pathlib, pytest, subprocess, sys",
                "def test_synthetic_audit_fake_py_and_winapi_blocked():",
                "    called = []",
                "    class Evil:",
                "        @property",
                "        def tb_frame(self):",
                "            called.append(True)",
                f"            with open({str(ext_sentinel)!r}, 'w', encoding='utf-8') as fh:",
                "                fh.write('pwned')",
                "            return None",
                "    evil_obj = Evil()",
                "    with pytest.raises(PermissionError):",
                "        sys.audit('object.__getattr__', evil_obj, 'tb_frame')",
                "    assert called == []",
                f"    assert not pathlib.Path({str(ext_sentinel)!r}).exists()",
                "    with pytest.raises(PermissionError):",
                f"        subprocess.Popen([{str(fake_py)!r}, '-c', 'pass'])",
                "    with pytest.raises(PermissionError):",
                "        sys.audit(",
                "            '_winapi.CreateProcess', 'cmd.exe', 'cmd.exe /c echo hi',",
                "            None, None, False, 0, None, None, None,",
                "        )",
                "    if hasattr(subprocess, '_winapi'):",
                "        with pytest.raises(PermissionError):",
                "            subprocess._winapi.CreateProcess(",
                "                'cmd.exe', 'cmd.exe /c echo hi',",
                "                None, None, False, 0, None, None, None,",
                "            )",
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
        pytest_targets=("tests/test_c16_mc_rr_01_02.py",),
    )
    assert receipt.passed is True
    assert receipt.exit_code == 0
    assert not ext_sentinel.exists()


def test_c16_mc_rr_03_container_sealed_descriptor_type_closure_and_body_immutability(
    tmp_path: Path,
) -> None:
    stable_root = Path(__file__).resolve().parents[2]
    candidate_root = _create_candidate_tree(tmp_path / "candidate_wt")
    captured: dict[str, str] = {}

    def capture_runner(
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
        captured["child"] = (probe_dir / "child_verify.py").read_text(encoding="utf-8")
        captured["parent"] = (probe_dir / "run_verify.py").read_text(encoding="utf-8")
        captured["nonce"] = (ctrl_out / "nonce.json").read_text(encoding="utf-8")
        return subprocess.CompletedProcess(argv, 1, stdout="", stderr="capture")

    cfg = StableCandidateConfig(
        stable_root=str(stable_root),
        candidate_worktree_root=str(candidate_root),
        pinned_stable_sha=STABLE_SHA,
        candidate_sha=CANDIDATE_SHA,
        verification_isolation="container",
    )
    SafeSelfDevelopmentRunner(cfg, subprocess_runner=capture_runner).run_candidate_verification(
        pytest_targets=("tests/test_candidate_smoke.py",),
    )
    assert "child" in captured and "parent" in captured

    exec_probe = tmp_path / "exec_probe"
    exec_ctrl = tmp_path / "exec_ctrl"
    exec_probe.mkdir()
    exec_ctrl.mkdir()
    child_src = (
        captured["child"]
        .replace("/aipc_probe", exec_probe.as_posix())
        .replace("/aipc_ctrl_out", exec_ctrl.as_posix())
    )
    child_file = exec_probe / "child_verify.py"
    child_file.write_text(child_src, encoding="utf-8")

    mc_rr_03_test = candidate_root / "tests" / "test_c16_mc_rr_03.py"
    mc_rr_03_test.write_text(
        "\n".join(
            [
                "import _pytest.python, pytest, types",
                "def test_sealed_descriptor_type_and_closure_isolated():",
                "    desc = _pytest.python.Function.__dict__['runtest']",
                "    desc_cls = type(desc)",
                "    with pytest.raises(PermissionError):",
                "        _ = desc_cls.__get__",
                "    cls_dict = object.__getattribute__(desc_cls, '__dict__')",
                "    get_fn = cls_dict['__get__']",
                "    for cell in get_fn.__closure__ or ():",
                "        assert not isinstance(cell.cell_contents, (dict, set, types.CodeType))",
                "",
            ]
        ),
        encoding="utf-8",
    )

    sanitized_env = build_sanitized_child_env({**os.environ, "OPENAI_API_KEY": SYNTHETIC_API_KEY})
    assert_child_env_isolated(sanitized_env)
    proc = subprocess.run(
        [sys.executable, "-B", str(child_file), str(mc_rr_03_test)],
        cwd=str(candidate_root),
        env=sanitized_env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0
    assert "AIPC_CONTAINER_ORIGIN_" in proc.stdout


def test_c16_mc_rr2_01_02_blocks_addaudithook_and_symlinked_python_in_isolated_and_container(
    tmp_path: Path,
) -> None:
    stable_root = Path(__file__).resolve().parents[2]
    candidate_root = _create_candidate_tree(tmp_path / "candidate_wt")

    iso_test = candidate_root / "tests" / "test_c16_mc_rr2_iso.py"
    iso_test.write_text(
        "\n".join(
            [
                "import os, pathlib, pytest, subprocess, sys",
                "def test_addaudithook_and_symlink_py_blocked():",
                "    with pytest.raises(PermissionError):",
                "        sys.addaudithook(lambda _e, _a: None)",
                "    def _execute_child():",
                "        sys.audit(",
                "            'subprocess.Popen',",
                "            sys.executable,",
                "            [sys.executable, '-c', 'pass'],",
                "            None,",
                "            os.environ,",
                "        )",
                "        sys.audit(",
                "            '_winapi.CreateProcess',",
                "            sys.executable,",
                "            f'{sys.executable} -c pass',",
                "            None,",
                "        )",
                "    with pytest.raises(",
                "        PermissionError, match='SUBPROCESS_SANDBOX_BYPASS_BLOCKED'",
                "    ):",
                "        _execute_child()",
                "    scratch = pathlib.Path(os.environ['TMPDIR'])",
                "    sym_py = scratch / ('python_sym.exe' if os.name == 'nt' else 'python_sym')",
                "    try:",
                "        os.symlink(sys.executable, sym_py)",
                "    except OSError:",
                "        return",
                "    with pytest.raises(",
                "        PermissionError, match='SUBPROCESS_SANDBOX_BYPASS_BLOCKED'",
                "    ):",
                "        subprocess.Popen([str(sym_py), '-c', 'pass'])",
                "",
            ]
        ),
        encoding="utf-8",
    )

    cfg_iso = StableCandidateConfig(
        stable_root=str(stable_root),
        candidate_worktree_root=str(candidate_root),
        pinned_stable_sha=STABLE_SHA,
        candidate_sha=CANDIDATE_SHA,
        verification_isolation="isolated_subprocess",
    )
    receipt = SafeSelfDevelopmentRunner(cfg_iso).run_candidate_verification(
        pytest_targets=("tests/test_c16_mc_rr2_iso.py",),
    )
    assert receipt.passed is True
    assert receipt.exit_code == 0

    captured: dict[str, str] = {}

    def capture_runner(
        argv: list[str],
        env: Mapping[str, str],
        cwd: Path,
    ) -> subprocess.CompletedProcess[str]:
        probe_dir = Path("")
        for item in argv:
            if item.endswith(":/aipc_probe:ro"):
                probe_dir = Path(item[: -len(":/aipc_probe:ro")])
        captured["child"] = (probe_dir / "child_verify.py").read_text(encoding="utf-8")
        return subprocess.CompletedProcess(argv, 1, stdout="", stderr="capture")

    cfg_cont = StableCandidateConfig(
        stable_root=str(stable_root),
        candidate_worktree_root=str(candidate_root),
        pinned_stable_sha=STABLE_SHA,
        candidate_sha=CANDIDATE_SHA,
        verification_isolation="container",
    )
    SafeSelfDevelopmentRunner(
        cfg_cont, subprocess_runner=capture_runner
    ).run_candidate_verification(
        pytest_targets=("tests/test_candidate_smoke.py",),
    )
    assert "child" in captured

    exec_probe = tmp_path / "exec_probe_rr2"
    exec_ctrl = tmp_path / "exec_ctrl_rr2"
    exec_probe.mkdir()
    exec_ctrl.mkdir()
    child_src = (
        captured["child"]
        .replace("/aipc_probe", exec_probe.as_posix())
        .replace("/aipc_ctrl_out", exec_ctrl.as_posix())
    )
    child_file = exec_probe / "child_verify.py"
    child_file.write_text(child_src, encoding="utf-8")

    cont_test = candidate_root / "tests" / "test_c16_mc_rr2_cont.py"
    cont_test.write_text(
        "\n".join(
            [
                "import os, pytest, sys",
                "def test_container_addaudithook_blocked():",
                "    with pytest.raises(PermissionError):",
                "        sys.addaudithook(lambda _e, _a: None)",
                "    def _execute_child():",
                "        sys.audit(",
                "            'subprocess.Popen',",
                "            sys.executable,",
                "            [sys.executable, '-c', 'pass'],",
                "            None,",
                "            os.environ,",
                "        )",
                "        sys.audit(",
                "            '_winapi.CreateProcess',",
                "            sys.executable,",
                "            f'{sys.executable} -c pass',",
                "            None,",
                "        )",
                "    with pytest.raises(",
                "        PermissionError, match='SUBPROCESS_SANDBOX_BYPASS_BLOCKED'",
                "    ):",
                "        _execute_child()",
                "",
            ]
        ),
        encoding="utf-8",
    )
    sanitized_env = build_sanitized_child_env(os.environ)
    assert_child_env_isolated(sanitized_env)
    proc = subprocess.run(
        [sys.executable, "-B", str(child_file), str(cont_test)],
        cwd=str(candidate_root),
        env=sanitized_env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0
    assert "AIPC_CONTAINER_ORIGIN_" in proc.stdout


def test_c19_mc_ir_01_forged_compile_name_and_filename_cannot_register_audithook_or_leak_locals(
    tmp_path: Path,
) -> None:
    stable_root = Path(__file__).resolve().parents[2]
    candidate_root = _create_candidate_tree(tmp_path / "candidate_mc_ir_01")

    forged_check_lines = [
        "import os, pathlib, sys",
        "_sc_mod = sys.modules.get('sitecustomize')",
        "_sc_file = getattr(_sc_mod, '__file__', '') if _sc_mod is not None else ''",
        "_base_dir = os.environ.get('TMPDIR') or os.environ.get('PYTHONPATH', '.')",
        "_spoof_files = [str(pathlib.Path(_base_dir) / 'spoof.py')]",
        "if _sc_file:",
        "    _spoof_files.append(_sc_file)",
        "for _sf in _spoof_files:",
        "    for _fn_name in ('_install_crit_vault', '_guarded_addaudithook'):",
        "        _ns_ah = {'sys': sys}",
        "        _co_ah = compile(",
        "            f'def {_fn_name}():\\n    sys.addaudithook(lambda _e, _a: None)\\n',",
        "            _sf,",
        "            'exec',",
        "        )",
        "        exec(_co_ah, _ns_ah)",
        "        _blocked_ah = False",
        "        try:",
        "            _ns_ah[_fn_name]()",
        "        except PermissionError:",
        "            _blocked_ah = True",
        "        assert _blocked_ah, (_sf, _fn_name)",
        "        if _sf == _sc_file:",
        "            _ns_gf = {'sys': sys}",
        "            _co_gf = compile(",
        "                f'def {_fn_name}():\\n    return sys._getframe(0)\\n',",
        "                _sf,",
        "                'exec',",
        "            )",
        "            exec(_co_gf, _ns_gf)",
        "            _blocked_gf = False",
        "            try:",
        "                _ns_gf[_fn_name]()",
        "            except PermissionError:",
        "                _blocked_gf = True",
        "            assert _blocked_gf, (_sf, _fn_name)",
        "    _blocked_tb = False",
        "    try:",
        "        sys._getframe(0)",
        "    except PermissionError as _exc:",
        "        try:",
        "            _ = _exc.__traceback__.tb_next.tb_frame",
        "        except PermissionError:",
        "            _blocked_tb = True",
        "    assert _blocked_tb",
    ]

    (candidate_root / "src" / "aipc" / "__init__.py").write_text(
        "\n".join(
            [
                "__version__ = '0.24.0-candidate'",
                *forged_check_lines,
                "",
            ]
        ),
        encoding="utf-8",
    )

    test_file = candidate_root / "tests" / "test_mc_ir_01.py"
    test_file.write_text(
        "\n".join(
            [
                "import aipc",
                "",
                "def test_forged_compile_blocked():",
                "    assert aipc.__version__ == '0.24.0-candidate'",
                *("    " + line for line in forged_check_lines),
                "",
            ]
        ),
        encoding="utf-8",
    )

    cfg_iso = StableCandidateConfig(
        stable_root=str(stable_root),
        candidate_worktree_root=str(candidate_root),
        pinned_stable_sha=STABLE_SHA,
        candidate_sha=CANDIDATE_SHA,
        verification_isolation="isolated_subprocess",
    )
    receipt = SafeSelfDevelopmentRunner(cfg_iso).run_candidate_verification(
        pytest_targets=("tests/test_mc_ir_01.py",),
    )
    assert receipt.passed is True
    assert receipt.exit_code == 0
