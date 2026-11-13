"""Deterministic unit, negative, and host tests for Workstream A01 Subscription Qualification."""

from __future__ import annotations

from pathlib import Path

import pytest

from aipc.adapters.gemini.doctor import QUALIFIED_AGY_VERSIONS, AntigravityQualificationResult
from aipc.adapters.subscription_qualification import (
    QUALIFIED_ANTIGRAVITY_HEADLESS_MODELS,
    QUALIFIED_CODEX_SUBSCRIPTION_MODELS,
    UNVERIFIED_ANTIGRAVITY_MODELS,
    enforce_subscription_only_profile,
    evaluate_runtime_eligibility,
    qualify_all_subscription_runtimes,
    qualify_antigravity_subscription_runtime,
    qualify_codex_subscription_runtime,
    qualify_desktop_only_surface,
)
from aipc.hardening.env_isolation import (
    ChildEnvironmentIsolationError,
    SubscriptionBoundaryError,
)
from aipc.hardening.secrets import SecretExposureError

SYNTHETIC_API_KEY = "sk-" + "A" * 48
SYNTHETIC_GOOGLE_KEY = "AIza" + "B" * 35


def test_a01_probes_subscription_metadata_without_reading_raw_credentials(
    tmp_path: Path,
) -> None:
    """Verify A01 checks session presence via path/stat metadata and never reads token files."""
    home = tmp_path / "home"
    agy_oauth_dir = home / ".gemini" / "antigravity"
    agy_oauth_dir.mkdir(parents=True)
    # Place a synthetic secret inside the session directory; A01 must never read file contents
    (agy_oauth_dir / "oauth_token.json").write_text(
        f'{{"access_token": "{SYNTHETIC_GOOGLE_KEY}"}}',
        encoding="utf-8",
    )

    codex_dir = home / ".codex"
    codex_dir.mkdir(parents=True)
    (codex_dir / "auth.json").write_text(
        f'{{"token": "{SYNTHETIC_API_KEY}"}}',
        encoding="utf-8",
    )

    fake_agy_exe = tmp_path / "agy.exe"
    fake_agy_exe.write_text("binary", encoding="utf-8")

    agy_report = qualify_antigravity_subscription_runtime(
        executable=fake_agy_exe,
        home_dir=home,
        qualification_override=AntigravityQualificationResult(
            qualified=True,
            version="1.5.5",
            reason="qualified",
            cli_compatible=True,
            sandbox_verified=False,
            effective_security_verified=True,
        ),
        desktop_present_override=True,
    )
    assert agy_report.eligibility_state == "QUALIFIED"
    assert agy_report.available is True
    assert agy_report.subscription_auth_present is True
    assert agy_report.account_independent_metadata["secret_read_performed"] is False
    assert agy_report.supported_model_ids == QUALIFIED_ANTIGRAVITY_HEADLESS_MODELS
    assert agy_report.unverified_or_unavailable_model_ids == UNVERIFIED_ANTIGRAVITY_MODELS

    codex_report = qualify_codex_subscription_runtime(
        home_dir=home,
        sdk_installed_override=True,
        sdk_version_override="0.160.0",
        runtime_version_override="0.160.0",
        cli_executable_override=str(tmp_path / "codex.exe"),
        desktop_present_override=False,
    )
    assert codex_report.eligibility_state == "QUALIFIED"
    assert codex_report.available is True
    assert codex_report.subscription_auth_present is True
    assert codex_report.account_independent_metadata["secret_read_performed"] is False
    assert codex_report.supported_model_ids == QUALIFIED_CODEX_SUBSCRIPTION_MODELS


def test_a01_separates_desktop_ui_presence_from_headless_and_reviewer_modes(
    tmp_path: Path,
) -> None:
    """Desktop GUI presence must never be spoofed as headless worker/reviewer capability."""
    desktop_report = qualify_desktop_only_surface(
        "antigravity-desktop",
        desktop_present=True,
    )
    assert desktop_report.eligibility_state == "BLOCKED"
    assert desktop_report.available is False
    assert desktop_report.surfaces.desktop_ui_present is True
    assert desktop_report.surfaces.desktop_ui_callable_headlessly is False
    assert desktop_report.surfaces.headless_worker_supported is False
    assert desktop_report.surfaces.headless_reviewer_supported is False
    assert desktop_report.surfaces.headless_planner_supported is False

    for role in ("planner", "worker", "reviewer"):
        decision = evaluate_runtime_eligibility(desktop_report, role=role)
        assert decision.eligibility_state == "BLOCKED"
        assert decision.eligible is False


@pytest.mark.parametrize(
    ("operating_profile", "auth_mode", "fallback"),
    [
        ("paid_api", "codex_subscription", False),
        ("hybrid", "antigravity_subscription", False),
        ("subscription_only", "openai_responses_api", False),
        ("subscription_only", "gemini_api_key", False),
        ("subscription_only", "google_api_key", False),
        ("subscription_only", "vertex_ai", False),
        ("subscription_only", "anthropic_api_key", False),
        ("subscription_only", "api_key", False),
        ("subscription_only", "codex_subscription", True),
    ],
)
def test_a01_hard_disables_billed_api_key_paths_in_subscription_only_profile(
    operating_profile: str,
    auth_mode: str,
    fallback: bool,
) -> None:
    """Any paid API profile, API-key auth mode, or paid fallback is rejected fail-closed."""
    with pytest.raises(SubscriptionBoundaryError):
        enforce_subscription_only_profile(
            operating_profile,
            auth_mode=auth_mode,
            allow_api_key_fallback=fallback,
        )

    with pytest.raises(SubscriptionBoundaryError):
        qualify_codex_subscription_runtime(
            operating_profile=operating_profile,
            auth_mode=auth_mode,
            allow_api_key_fallback=fallback,
        )


def test_a01_rejects_unsanitized_child_env_with_api_keys() -> None:
    """Unsanitized child environment dictionaries containing API keys fail closed."""
    with pytest.raises(ChildEnvironmentIsolationError):
        enforce_subscription_only_profile(
            "subscription_only",
            auth_mode="codex_subscription",
            child_env={"OPENAI_API_KEY": "dummy-value"},
        )


def test_a01_deterministic_blocked_and_unavailable_states_for_missing_prereqs(
    tmp_path: Path,
) -> None:
    """Missing CLI, missing auth, bad version, or unverified model yields deterministic state."""
    empty_home = tmp_path / "empty_home"
    empty_home.mkdir()

    # 1. Missing CLI executable -> UNAVAILABLE
    missing_cli = qualify_antigravity_subscription_runtime(
        executable=tmp_path / "nonexistent_agy.exe",
        home_dir=empty_home,
    )
    assert missing_cli.eligibility_state == "UNAVAILABLE"
    assert missing_cli.available is False

    # 2. Incompatible CLI version -> BLOCKED
    fake_agy = tmp_path / "agy.exe"
    fake_agy.write_text("bin", encoding="utf-8")
    bad_ver = qualify_antigravity_subscription_runtime(
        executable=fake_agy,
        home_dir=empty_home,
        qualification_override=AntigravityQualificationResult(
            qualified=False,
            version="0.9.0",
            reason="unqualified agy version '0.9.0'",
            cli_compatible=False,
        ),
        oauth_present_override=True,
    )
    assert bad_ver.eligibility_state == "BLOCKED"
    assert bad_ver.available is False

    # 3. Missing OAuth session -> UNAVAILABLE
    missing_oauth = qualify_antigravity_subscription_runtime(
        executable=fake_agy,
        home_dir=empty_home,
        qualification_override=AntigravityQualificationResult(
            qualified=True,
            version="1.5.5",
            reason="qualified",
            cli_compatible=True,
            effective_security_verified=True,
        ),
        oauth_present_override=False,
    )
    assert missing_oauth.eligibility_state == "UNAVAILABLE"
    assert missing_oauth.available is False

    # 4. Unverified security controls -> BLOCKED
    unverified_sec = qualify_antigravity_subscription_runtime(
        executable=fake_agy,
        home_dir=empty_home,
        security_verified=False,
        qualification_override=AntigravityQualificationResult(
            qualified=False,
            version="1.5.5",
            reason="security controls unverified",
            cli_compatible=True,
            effective_security_verified=False,
        ),
        oauth_present_override=True,
    )
    assert unverified_sec.eligibility_state == "BLOCKED"
    assert unverified_sec.available is False

    # 5. Qualified Antigravity runtime, but requesting unverified "argon" model -> UNAVAILABLE
    qualified_agy = qualify_antigravity_subscription_runtime(
        executable=fake_agy,
        home_dir=empty_home,
        qualification_override=AntigravityQualificationResult(
            qualified=True,
            version="1.5.5",
            reason="qualified",
            cli_compatible=True,
            effective_security_verified=True,
        ),
        oauth_present_override=True,
    )
    argon_decision = evaluate_runtime_eligibility(
        qualified_agy,
        role="planner",
        requested_model="argon",
    )
    assert argon_decision.eligibility_state == "UNAVAILABLE"
    assert argon_decision.eligible is False
    assert "argon" in argon_decision.reason

    flash_decision = evaluate_runtime_eligibility(
        qualified_agy,
        role="worker",
        requested_model="gemini-3.8-flash-medium",
    )
    assert flash_decision.eligibility_state == "QUALIFIED"
    assert flash_decision.eligible is True
    assert flash_decision.resolved_model == "gemini-3.8-flash-medium"

    # 6. Codex missing SDK / wrong SDK version / missing auth
    no_sdk = qualify_codex_subscription_runtime(
        home_dir=empty_home,
        sdk_installed_override=False,
    )
    assert no_sdk.eligibility_state == "UNAVAILABLE"

    wrong_sdk = qualify_codex_subscription_runtime(
        home_dir=empty_home,
        sdk_installed_override=True,
        sdk_version_override="0.100.0",
        runtime_version_override="0.160.0",
        auth_present_override=True,
    )
    assert wrong_sdk.eligibility_state == "BLOCKED"

    no_codex_auth = qualify_codex_subscription_runtime(
        home_dir=empty_home,
        sdk_installed_override=True,
        sdk_version_override="0.160.0",
        runtime_version_override="0.160.0",
        auth_present_override=False,
    )
    assert no_codex_auth.eligibility_state == "UNAVAILABLE"


def test_a01_suite_strips_outer_credentials_and_blocks_secret_leakage(
    tmp_path: Path,
) -> None:
    """Suite report records stripped Outer vars and blocks any secret in report fields."""
    home = tmp_path / "home"
    (home / ".gemini" / "antigravity").mkdir(parents=True)
    (home / ".codex").mkdir(parents=True)
    (home / ".codex" / "auth.json").write_text("{}", encoding="utf-8")

    fake_agy = tmp_path / "agy.exe"
    fake_agy.write_text("bin", encoding="utf-8")
    agy_rep = qualify_antigravity_subscription_runtime(
        executable=fake_agy,
        home_dir=home,
        qualification_override=AntigravityQualificationResult(
            qualified=True,
            version="1.5.5",
            reason="qualified",
            cli_compatible=True,
            effective_security_verified=True,
        ),
    )
    codex_rep = qualify_codex_subscription_runtime(
        home_dir=home,
        sdk_installed_override=True,
        sdk_version_override="0.160.0",
        runtime_version_override="0.160.0",
    )

    suite = qualify_all_subscription_runtimes(
        base_env={
            "PATH": "/usr/bin",
            "GEMINI_API_KEY": "outer-supervision-only",
            "GOOGLE_API_KEY": "outer-supervision-only",
        },
        home_dir=home,
        antigravity_report=agy_rep,
        codex_report=codex_rep,
    )
    assert suite.operating_profile == "subscription_only"
    assert suite.environment_isolation.status == "ISOLATED"
    assert "GEMINI_API_KEY" in suite.environment_isolation.removed_var_names
    assert suite.environment_isolation.remaining_prohibited_vars == ()
    assert suite.evaluate("codex", "reviewer").eligible is True
    assert suite.evaluate("gemini-antigravity", "worker").eligible is True
    assert suite.evaluate("antigravity-desktop", "worker").eligibility_state == "BLOCKED"
    assert suite.evaluate("unknown-provider", "worker").eligibility_state == "UNAVAILABLE"

    # Attempting to inject a secret into a report reason fails closed via assert_no_secrets
    with pytest.raises(SecretExposureError):
        tampered = agy_rep.model_copy(update={"reasons": (f"Leaked {SYNTHETIC_API_KEY}",)})
        tampered.validate_report_invariants()


def test_antigravity_1_5_8_and_hydrogen_qualification(tmp_path: Path) -> None:
    """Antigravity 1.5.8 is qualified with Hydrogen supported and argon unverified."""
    assert "1.5.8" in QUALIFIED_AGY_VERSIONS

    home = tmp_path / "home"
    (home / ".gemini" / "antigravity").mkdir(parents=True)
    fake_agy = tmp_path / "agy.exe"
    fake_agy.write_text("binary", encoding="utf-8")

    report = qualify_antigravity_subscription_runtime(
        executable=fake_agy,
        home_dir=home,
        qualification_override=AntigravityQualificationResult(
            qualified=True,
            version="1.5.8",
            reason="qualified",
            cli_compatible=True,
            effective_security_verified=True,
        ),
    )
    assert report.eligibility_state == "QUALIFIED"
    assert "gemini-4-hydrogen-medium" in report.supported_model_ids
    assert "argon" in report.unverified_or_unavailable_model_ids
