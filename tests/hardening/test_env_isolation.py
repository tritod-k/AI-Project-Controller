"""Deterministic tests for Phase 24.0 child environment isolation and credential boundaries.

All tests use synthetic dummy credential markers only and never access real API keys.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

from aipc.hardening import (
    CHILD_ENVIRONMENT_ISOLATION_BLOCKED,
    ChildEnvironmentIsolationError,
    EnvironmentIsolationReport,
    SubscriptionBoundaryError,
    assert_argv_no_credentials,
    assert_child_env_isolated,
    assert_subscription_only_mode,
    build_sanitized_child_env,
    inspect_credential_var_names,
    is_prohibited_child_env_var,
    prepare_isolated_child_invocation,
)

SYNTHETIC_GEMINI_MARKER = "SYNTHETIC_DUMMY_GEMINI_MARKER_NOT_REAL_000"
SYNTHETIC_AIZA_TOKEN = "AIza" + "X" * 35
SYNTHETIC_SK_TOKEN = "sk-synthetic_dummy_marker_1234567890"


def test_prohibited_var_name_detection_is_case_insensitive() -> None:
    assert is_prohibited_child_env_var("GEMINI_API_KEY") is True
    assert is_prohibited_child_env_var("gemini_api_key") is True
    assert is_prohibited_child_env_var("Gemini_Api_Key") is True
    assert is_prohibited_child_env_var("GOOGLE_API_KEY") is True
    assert is_prohibited_child_env_var("OPENAI_API_KEY") is True
    assert is_prohibited_child_env_var("ANTHROPIC_API_KEY") is True
    assert is_prohibited_child_env_var("GOOGLE_GENAI_USE_VERTEXAI") is True
    assert is_prohibited_child_env_var("GEMINI_CLI") is True
    assert is_prohibited_child_env_var("AIPC_CUSTOM_API_KEY") is True
    assert is_prohibited_child_env_var("GEMINI_SECRET_KEY") is True
    assert is_prohibited_child_env_var("PATH") is False
    assert is_prohibited_child_env_var("SYSTEMROOT") is False


def test_inspect_credential_var_names_never_exposes_values() -> None:
    fake_outer_env = {
        "PATH": "/usr/bin",
        "gemini_api_key": SYNTHETIC_GEMINI_MARKER,
        "GOOGLE_API_KEY": SYNTHETIC_AIZA_TOKEN,
        "OPENAI_API_KEY": SYNTHETIC_SK_TOKEN,
        "SAFE_VAR": "hello",
    }
    names = inspect_credential_var_names(fake_outer_env)
    assert names == ("GEMINI_API_KEY", "GOOGLE_API_KEY", "OPENAI_API_KEY")
    for item in names:
        assert SYNTHETIC_GEMINI_MARKER not in item
        assert SYNTHETIC_AIZA_TOKEN not in item
        assert SYNTHETIC_SK_TOKEN not in item


def test_build_sanitized_child_env_strips_outer_credentials_case_insensitively() -> None:
    fake_outer_env = {
        "Path": r"C:\Windows\System32",
        "SystemRoot": r"C:\Windows",
        "GEMINI_API_KEY": SYNTHETIC_GEMINI_MARKER,
        "gemini_api_key": SYNTHETIC_GEMINI_MARKER,
        "Google_Api_Key": SYNTHETIC_AIZA_TOKEN,
        "OPENAI_API_KEY": SYNTHETIC_SK_TOKEN,
        "ANTHROPIC_API_KEY": "synthetic-anthropic-marker",
        "GEMINI_CLI": "1",
        "GEMINI_CLI_NO_RELAUNCH": "true",
        "UNEXPECTED_SECRET_HOLDER": f"Bearer {SYNTHETIC_SK_TOKEN}",
        "AIPC_SAFE_FLAG": "enabled",
    }
    sanitized = build_sanitized_child_env(fake_outer_env)

    # Parent dict must remain untouched (Outer Supervisor session preserved)
    assert fake_outer_env["GEMINI_API_KEY"] == SYNTHETIC_GEMINI_MARKER

    upper_keys = {k.upper() for k in sanitized}
    assert "GEMINI_API_KEY" not in upper_keys
    assert "GOOGLE_API_KEY" not in upper_keys
    assert "OPENAI_API_KEY" not in upper_keys
    assert "ANTHROPIC_API_KEY" not in upper_keys
    assert "GEMINI_CLI" not in upper_keys
    assert "UNEXPECTED_SECRET_HOLDER" not in upper_keys
    assert sanitized["AIPC_SAFE_FLAG"] == "enabled"
    assert_child_env_isolated(sanitized)


def test_build_sanitized_child_env_fails_closed_on_extra_env_injection() -> None:
    base = {"PATH": r"C:\Windows\System32"}
    with pytest.raises(ChildEnvironmentIsolationError, match=CHILD_ENVIRONMENT_ISOLATION_BLOCKED):
        build_sanitized_child_env(base, extra_env={"gemini_api_key": SYNTHETIC_GEMINI_MARKER})

    with pytest.raises(ChildEnvironmentIsolationError, match=CHILD_ENVIRONMENT_ISOLATION_BLOCKED):
        build_sanitized_child_env(base, extra_env={"CUSTOM_VAR": SYNTHETIC_AIZA_TOKEN})


def test_assert_argv_no_credentials_blocks_cli_flag_leakage() -> None:
    safe_argv = ("python", "-m", "pytest", "-q")
    assert assert_argv_no_credentials(safe_argv) == safe_argv

    for bad_arg in (
        f"--api-key={SYNTHETIC_GEMINI_MARKER}",
        "--gemini-api-key",
        f"GEMINI_API_KEY={SYNTHETIC_GEMINI_MARKER}",
        SYNTHETIC_AIZA_TOKEN,
        SYNTHETIC_SK_TOKEN,
    ):
        with pytest.raises(
            ChildEnvironmentIsolationError, match=CHILD_ENVIRONMENT_ISOLATION_BLOCKED
        ) as exc_info:
            assert_argv_no_credentials(["agy", "-p", "hello", bad_arg])
        # Error message must never echo the secret value itself
        assert SYNTHETIC_GEMINI_MARKER not in str(exc_info.value)
        assert SYNTHETIC_AIZA_TOKEN not in str(exc_info.value)
        assert SYNTHETIC_SK_TOKEN not in str(exc_info.value)


def test_prepare_isolated_child_invocation_produces_secret_free_report() -> None:
    fake_outer_env = {
        "SystemRoot": r"C:\Windows",
        "GEMINI_API_KEY": SYNTHETIC_GEMINI_MARKER,
        "GOOGLE_API_KEY": SYNTHETIC_AIZA_TOKEN,
    }
    argv, sanitized, report = prepare_isolated_child_invocation(["python", "-V"], fake_outer_env)
    assert argv == ("python", "-V")
    assert isinstance(report, EnvironmentIsolationReport)
    assert report.status == "ISOLATED"
    assert report.removed_var_names == ("GEMINI_API_KEY", "GOOGLE_API_KEY")
    assert report.remaining_prohibited_vars == ()
    serialized = report.model_dump_json()
    assert SYNTHETIC_GEMINI_MARKER not in serialized
    assert SYNTHETIC_AIZA_TOKEN not in serialized
    assert "GEMINI_API_KEY" not in {k.upper() for k in sanitized}


def test_child_subprocess_does_not_receive_synthetic_outer_gemini_api_key() -> None:
    fake_outer_env = {
        **build_sanitized_child_env(),
        "GEMINI_API_KEY": SYNTHETIC_GEMINI_MARKER,
        "GOOGLE_API_KEY": SYNTHETIC_AIZA_TOKEN,
        "OPENAI_API_KEY": SYNTHETIC_SK_TOKEN,
    }
    argv, sanitized_env, report = prepare_isolated_child_invocation(
        [
            sys.executable,
            "-c",
            (
                "import os, json; "
                "u = {k.upper() for k in os.environ}; "
                "vals = ' '.join(os.environ.values()); "
                "print(json.dumps({"
                "'has_gemini_key': 'GEMINI_API_KEY' in u, "
                "'has_google_key': 'GOOGLE_API_KEY' in u, "
                "'has_openai_key': 'OPENAI_API_KEY' in u, "
                f"'marker_in_vals': {SYNTHETIC_GEMINI_MARKER!r} in vals"
                "}))"
            ),
        ],
        fake_outer_env,
    )
    assert report.status == "ISOLATED"
    proc = subprocess.run(
        list(argv),
        env=sanitized_env,
        capture_output=True,
        text=True,
        check=True,
        timeout=15,
    )
    assert '"has_gemini_key": false' in proc.stdout
    assert '"has_google_key": false' in proc.stdout
    assert '"has_openai_key": false' in proc.stdout
    assert '"marker_in_vals": false' in proc.stdout


def test_assert_subscription_only_mode_rejects_api_key_billing() -> None:
    assert_subscription_only_mode("subscription_oauth", component="AntigravityWorker")
    assert_subscription_only_mode("codex_subscription_cli", component="CodexReviewer")

    for forbidden_mode in (
        "api_key",
        "gemini_api_key",
        "google_api_key",
        "openai_responses_api",
        "vertex_ai",
        "paid_api",
        "",
    ):
        with pytest.raises(SubscriptionBoundaryError):
            assert_subscription_only_mode(forbidden_mode, component="InnerReviewer")

    with pytest.raises(SubscriptionBoundaryError):
        assert_subscription_only_mode(
            "subscription_oauth",
            component="InnerRouter",
            allow_api_key_fallback=True,
        )
