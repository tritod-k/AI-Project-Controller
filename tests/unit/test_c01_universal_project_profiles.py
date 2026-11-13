"""Deterministic unit tests for Workstream C01: Universal Project Profiles & Onboarding."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from pydantic import ValidationError

import aipc.project as project_pkg
from aipc.hardening.errors import SecretExposureError
from aipc.project.models import AcceptanceCriterion, PhaseDefinition
from aipc.project.project_loader import load_project
from aipc.project.universal_profiles import (
    BUILTIN_UNIVERSAL_PROFILES,
    ExternalSystemSafetyPolicy,
    OnboardedProjectBundle,
    RepoInstructionRefs,
    UniversalProjectProfile,
    ValidationPackSpec,
    detect_repository_stack,
    get_builtin_project_profile,
    onboard_universal_project,
    render_project_yaml_template,
)


def _sample_phases() -> tuple[PhaseDefinition, ...]:
    return (
        PhaseDefinition(
            id="phase-01",
            name="Initial Verification",
            objective="Verify onboarded repository test suite passes offline.",
            acceptance_criteria=(
                AcceptanceCriterion(
                    id="AC-01",
                    statement="Offline tests pass in isolated environment.",
                ),
            ),
        ),
    )


def test_public_exports_and_provider_neutrality() -> None:
    expected_exports = {
        "BUILTIN_UNIVERSAL_PROFILES",
        "ExternalSystemSafetyPolicy",
        "OnboardedProjectBundle",
        "ProjectStackType",
        "RepoInstructionRefs",
        "UniversalProjectProfile",
        "ValidationPackSpec",
        "detect_repository_stack",
        "get_builtin_project_profile",
        "onboard_universal_project",
        "render_project_yaml_template",
    }
    assert expected_exports.issubset(set(project_pkg.__all__))
    for symbol in expected_exports:
        assert hasattr(project_pkg, symbol)

    module_path = Path(project_pkg.__file__).resolve().parent / "universal_profiles.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    imported_modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported_modules.append(node.module)

    prohibited_prefixes = ("aipc.adapters", "openai", "anthropic", "google.genai")
    for mod in imported_modules:
        assert not mod.startswith(prohibited_prefixes), f"Prohibited provider import: {mod}"


def test_builtin_profiles_cover_required_stacks() -> None:
    required_stacks = (
        "python_cli_or_package",
        "python_web_or_api",
        "node_javascript_typescript",
        "static_web_frontend",
        "google_apps_script_offline_mock",
    )
    for stack in required_stacks:
        profile = get_builtin_project_profile(stack)
        assert profile is BUILTIN_UNIVERSAL_PROFILES[stack]
        assert profile.operating_profile == "subscription_only"
        assert profile.allow_paid_api_keys is False
        assert profile.safety_policy.live_trading_or_financial_ops_allowed is False
        assert profile.safety_policy.live_external_deployment_allowed is False

    gas_profile = get_builtin_project_profile("google_apps_script_offline_mock")
    assert gas_profile.validation_pack.offline_mock_only is True
    assert gas_profile.safety_policy.external_network_allowed is False
    assert gas_profile.safety_policy.simulation_disclaimer is not None
    assert "TradingView" in gas_profile.safety_policy.simulation_disclaimer
    assert "Google Sheets" in gas_profile.safety_policy.simulation_disclaimer
    assert "OKX" in gas_profile.safety_policy.simulation_disclaimer

    with pytest.raises(ValueError, match="No built-in universal profile"):
        get_builtin_project_profile("custom_toolchain")


def test_onboard_python_package_layout_and_render_yaml(tmp_path: Path) -> None:
    repo = tmp_path / "py_pkg"
    (repo / "src" / "demo_pkg").mkdir(parents=True)
    (repo / "pyproject.toml").write_text(
        '[project]\nname = "demo-pkg"\nversion = "0.1.0"\n',
        encoding="utf-8",
    )
    (repo / "src" / "demo_pkg" / "__init__.py").write_text("", encoding="utf-8")

    bundle = onboard_universal_project(
        workspace=repo,
        project_id="demo-py-pkg",
        project_name="Demo Python Package",
        phases=_sample_phases(),
    )

    assert isinstance(bundle, OnboardedProjectBundle)
    assert bundle.onboarding_status == "ONBOARDED_READY"
    assert bundle.profile.stack_type == "python_cli_or_package"
    assert bundle.detected_stack_markers == ("pyproject.toml",)
    assert bundle.detected_instruction_files == ()

    yaml_text = render_project_yaml_template(bundle)
    assert "# profile_id: python-cli-or-package" in yaml_text

    project_yaml_path = repo / "PROJECT.yaml"
    project_yaml_path.write_text(yaml_text, encoding="utf-8")
    loaded = load_project(project_yaml_path)
    assert loaded == bundle.project_definition


def test_onboard_node_typescript_layout_and_render_yaml(tmp_path: Path) -> None:
    repo = tmp_path / "node_app"
    repo.mkdir()
    (repo / "package.json").write_text(
        '{"name": "demo-node", "version": "1.0.0"}',
        encoding="utf-8",
    )
    (repo / "index.js").write_text("console.log('ok');\n", encoding="utf-8")

    bundle = onboard_universal_project(
        workspace=repo,
        project_id="demo-node-app",
        project_name="Demo Node App",
        phases=_sample_phases(),
    )

    assert bundle.profile.stack_type == "node_javascript_typescript"
    assert bundle.detected_stack_markers == ("package.json",)
    assert bundle.project_definition.project.id == "demo-node-app"

    project_yaml_path = repo / "PROJECT.yaml"
    project_yaml_path.write_text(render_project_yaml_template(bundle), encoding="utf-8")
    loaded = load_project(project_yaml_path)
    assert loaded == bundle.project_definition


def test_onboard_gas_offline_mock_layout_with_instruction_files(tmp_path: Path) -> None:
    repo = tmp_path / "gas_offline"
    repo.mkdir()
    (repo / "appsscript.json").write_text('{"timeZone": "UTC"}', encoding="utf-8")
    (repo / "Code.gs").write_text("function doPost(e) { return null; }\n", encoding="utf-8")
    (repo / "AGENTS.md").write_text("# Agents\n", encoding="utf-8")
    (repo / "SKILL.md").write_text("# Skill\n", encoding="utf-8")

    bundle = onboard_universal_project(
        workspace=repo,
        project_id="demo-gas-mock",
        project_name="Demo GAS Offline Mock",
        phases=_sample_phases(),
    )

    assert bundle.profile.stack_type == "google_apps_script_offline_mock"
    assert bundle.profile.validation_pack.offline_mock_only is True
    assert bundle.detected_stack_markers == ("appsscript.json",)
    assert bundle.detected_instruction_files == ("AGENTS.md", "SKILL.md")

    project_yaml_path = repo / "PROJECT.yaml"
    project_yaml_path.write_text(render_project_yaml_template(bundle), encoding="utf-8")
    loaded = load_project(project_yaml_path)
    assert loaded == bundle.project_definition


def test_detect_python_web_and_static_frontend(tmp_path: Path) -> None:
    web_repo = tmp_path / "py_web"
    web_repo.mkdir()
    (web_repo / "requirements.txt").write_text("fastapi==0.115.0\nuvicorn\n", encoding="utf-8")
    stack, markers = detect_repository_stack(web_repo)
    assert stack == "python_web_or_api"
    assert markers == ("requirements.txt",)

    static_repo = tmp_path / "static_site"
    static_repo.mkdir()
    (static_repo / "index.html").write_text("<html></html>\n", encoding="utf-8")
    static_stack, static_markers = detect_repository_stack(static_repo)
    assert static_stack == "static_web_frontend"
    assert static_markers == ("index.html",)


def test_custom_toolchain_explicit_profile_onboarding(tmp_path: Path) -> None:
    repo = tmp_path / "custom_repo"
    repo.mkdir()
    (repo / "Makefile").write_text("test:\n\techo ok\n", encoding="utf-8")

    custom_profile = UniversalProjectProfile(
        profile_id="custom-makefile-profile",
        stack_type="custom_toolchain",
        display_name="Custom Makefile Project",
        description="Custom toolchain repo onboarded with explicit profile.",
        validation_pack=ValidationPackSpec(
            pack_id="custom-make-pack",
            stack_type="custom_toolchain",
            required_toolchains=("make",),
            test_commands=("make test",),
            default_verification_isolation="isolated_subprocess",
        ),
        safety_policy=ExternalSystemSafetyPolicy(),
        default_in_scope_globs=("src/**", "Makefile"),
    )

    bundle = onboard_universal_project(
        workspace=repo,
        project_id="custom-proj",
        project_name="Custom Project",
        phases=_sample_phases(),
        profile=custom_profile,
    )
    assert bundle.profile == custom_profile
    assert bundle.detected_stack_markers == ()


def test_negative_gas_mock_requires_disclaimer_and_offline_mock_only() -> None:
    with pytest.raises(ValidationError, match="offline_mock_only=True"):
        ValidationPackSpec(
            pack_id="gas-pack",
            stack_type="google_apps_script_offline_mock",
            required_toolchains=("node",),
            test_commands=("npm test",),
            offline_mock_only=False,
        )

    with pytest.raises(ValidationError, match="simulation_disclaimer"):
        UniversalProjectProfile(
            profile_id="gas-no-disclaimer",
            stack_type="google_apps_script_offline_mock",
            display_name="GAS",
            description="GAS without disclaimer",
            validation_pack=ValidationPackSpec(
                pack_id="gas-pack",
                stack_type="google_apps_script_offline_mock",
                required_toolchains=("node",),
                test_commands=("npm test",),
                offline_mock_only=True,
            ),
            safety_policy=ExternalSystemSafetyPolicy(simulation_disclaimer=None),
            default_in_scope_globs=("*.gs",),
        )


def test_negative_human_approval_and_forbidden_globs_cannot_be_weakened() -> None:
    base_pack = ValidationPackSpec(
        pack_id="py-pack",
        stack_type="python_cli_or_package",
        required_toolchains=("python",),
        test_commands=("pytest",),
    )

    with pytest.raises(ValidationError, match="human_approval_required_for"):
        UniversalProjectProfile(
            profile_id="weak-approval-merge",
            stack_type="python_cli_or_package",
            display_name="Weak",
            description="Missing merge approval",
            validation_pack=base_pack,
            safety_policy=ExternalSystemSafetyPolicy(),
            default_in_scope_globs=("src/**",),
            human_approval_required_for=("deploy", "new_milestone"),
        )

    with pytest.raises(ValidationError, match="human_approval_required_for"):
        UniversalProjectProfile(
            profile_id="weak-approval-deploy",
            stack_type="python_cli_or_package",
            display_name="Weak",
            description="Missing deploy approval",
            validation_pack=base_pack,
            safety_policy=ExternalSystemSafetyPolicy(),
            default_in_scope_globs=("src/**",),
            human_approval_required_for=("merge", "new_milestone"),
        )

    with pytest.raises(ValidationError, match="default_forbidden_globs"):
        UniversalProjectProfile(
            profile_id="weak-globs",
            stack_type="python_cli_or_package",
            display_name="Weak",
            description="Missing .env* glob",
            validation_pack=base_pack,
            safety_policy=ExternalSystemSafetyPolicy(),
            default_in_scope_globs=("src/**",),
            default_forbidden_globs=(".git/**",),
        )


def test_negative_repo_instruction_path_traversal_and_absolute_paths() -> None:
    with pytest.raises(ValidationError, match="parent traversal"):
        RepoInstructionRefs(agents_md_path="../AGENTS.md")

    with pytest.raises(ValidationError, match="parent traversal"):
        RepoInstructionRefs(custom_instruction_paths=("docs/../../secret.md",))

    with pytest.raises(ValidationError, match="relative to repository root"):
        RepoInstructionRefs(skill_md_path="/etc/passwd")

    with pytest.raises(ValidationError, match="relative to repository root"):
        RepoInstructionRefs(agents_md_path="C:\\Users\\admin\\AGENTS.md")


def test_negative_validation_pack_constraints() -> None:
    with pytest.raises(ValidationError, match="test_commands must not be empty"):
        ValidationPackSpec(
            pack_id="empty-tests",
            stack_type="python_cli_or_package",
            required_toolchains=("python",),
            test_commands=(),
        )

    with pytest.raises(ValidationError, match="unsafe_host_opt_in"):
        ValidationPackSpec.model_validate(
            {
                "pack_id": "unsafe-pack",
                "stack_type": "python_cli_or_package",
                "required_toolchains": ("python",),
                "test_commands": ("pytest",),
                "unsafe_host_opt_in": True,
            }
        )


def test_negative_env_file_in_workspace_fails_onboarding(tmp_path: Path) -> None:
    repo = tmp_path / "dirty_env_repo"
    repo.mkdir()
    (repo / "pyproject.toml").write_text('[project]\nname = "dirty"\n', encoding="utf-8")
    (repo / ".env").write_text("ENV_VAR=1\n", encoding="utf-8")

    with pytest.raises(ValueError, match="Prohibited secret environment file"):
        onboard_universal_project(
            workspace=repo,
            project_id="dirty-proj",
            project_name="Dirty Repo",
            phases=_sample_phases(),
        )


def test_negative_secret_in_profile_fails_with_secret_exposure_error() -> None:
    fake_val = "s" + "k-" + ("a" * 16)
    with pytest.raises(SecretExposureError):
        UniversalProjectProfile(
            profile_id="leaked-profile",
            stack_type="python_cli_or_package",
            display_name="Leaked Profile",
            description=f"Contains {fake_val}",
            validation_pack=ValidationPackSpec(
                pack_id="py-pack",
                stack_type="python_cli_or_package",
                required_toolchains=("python",),
                test_commands=("pytest",),
            ),
            safety_policy=ExternalSystemSafetyPolicy(),
            default_in_scope_globs=("src/**",),
        )


def test_negative_unrecognized_or_missing_workspace(tmp_path: Path) -> None:
    missing = tmp_path / "does_not_exist"
    with pytest.raises(ValueError, match="does not exist"):
        detect_repository_stack(missing)

    empty_dir = tmp_path / "empty_dir"
    empty_dir.mkdir()
    with pytest.raises(ValueError, match="No recognized stack markers"):
        detect_repository_stack(empty_dir)
