"""Provider-neutral universal project profiles, stack detection, and onboarding."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Literal, Self

import yaml
from pydantic import Field, StrictBool, model_validator

from aipc.domain.models import CanonicalRecord, NonBlank
from aipc.hardening.secrets import assert_no_secrets
from aipc.project.models import (
    AgentAssignments,
    ControllerConfig,
    PhaseDefinition,
    ProjectDefinition,
    ProjectIdentity,
)
from aipc.project.project_loader import load_project

ProjectStackType = Literal[
    "python_cli_or_package",
    "python_web_or_api",
    "node_javascript_typescript",
    "static_web_frontend",
    "google_apps_script_offline_mock",
    "custom_toolchain",
]

_REQUIRED_HUMAN_APPROVAL_ACTIONS: tuple[str, ...] = ("merge", "deploy", "new_milestone")
_REQUIRED_FORBIDDEN_GLOBS: tuple[str, ...] = (".git/**", ".env*")
_GAS_DISCLAIMER = (
    "Offline simulation only: offline GAS mocks do not confirm live TradingView, "
    "Google Sheets, OKX, or Google Apps Script deployment safety."
)
_WEB_FRAMEWORK_MARKERS = ("fastapi", "flask", "django", "uvicorn", "starlette")
_ALLOWED_ENV_TEMPLATE_NAMES = frozenset({".env.example", ".env.sample", ".env.template"})
PROFILE_COMPANION_FILENAME = "PROJECT.profile.json"
_PROFILE_BUNDLE_COMMENT_PREFIX = "# aipc_profile_bundle_json:"


def _validate_relative_repo_path(path_value: str) -> None:
    """Reject absolute paths or parent-directory traversal in repository-relative paths."""
    if (
        path_value.startswith(("/", "\\"))
        or PurePosixPath(path_value).is_absolute()
        or PureWindowsPath(path_value).is_absolute()
    ):
        raise ValueError(f"instruction path must be relative to repository root: {path_value!r}")
    segments = re.split(r"[/\\]", path_value)
    if ".." in segments:
        raise ValueError(f"instruction path must not contain parent traversal '..': {path_value!r}")


class ExternalSystemSafetyPolicy(CanonicalRecord):
    """Fail-closed external system and live-operation safety boundary for a project."""

    external_network_allowed: StrictBool = False
    live_trading_or_financial_ops_allowed: Literal[False] = False
    live_external_deployment_allowed: Literal[False] = False
    external_system_denylist: tuple[NonBlank, ...] = (
        "live_trading",
        "okx_live_api",
        "tradingview_live_webhook",
        "google_sheets_live_mutation",
        "production_deploy",
    )
    simulation_disclaimer: NonBlank | None = None


class ValidationPackSpec(CanonicalRecord):
    """Stack-specific toolchain and verification command specification."""

    pack_id: NonBlank
    stack_type: ProjectStackType
    required_toolchains: tuple[NonBlank, ...]
    test_commands: tuple[NonBlank, ...]
    lint_commands: tuple[NonBlank, ...] = ()
    build_commands: tuple[NonBlank, ...] = ()
    default_verification_isolation: Literal["blocked", "container", "isolated_subprocess"] = (
        "blocked"
    )
    recommended_container_image: NonBlank | None = None
    offline_mock_only: StrictBool = False

    @model_validator(mode="before")
    @classmethod
    def reject_unsafe_host_opt_in_input(cls, data: Any) -> Any:
        if isinstance(data, dict) and "unsafe_host_opt_in" in data:
            raise ValueError("unsafe_host_opt_in is prohibited in ValidationPackSpec")
        return data

    @model_validator(mode="after")
    def validate_pack_constraints(self) -> Self:
        if not self.test_commands:
            raise ValueError("test_commands must not be empty")
        for cmd in (*self.test_commands, *self.lint_commands, *self.build_commands):
            if "unsafe_host_opt_in" in cmd.lower():
                raise ValueError("unsafe_host_opt_in is prohibited in validation commands")
        if self.stack_type == "google_apps_script_offline_mock" and not self.offline_mock_only:
            raise ValueError(
                "google_apps_script_offline_mock stack requires offline_mock_only=True"
            )
        return self


class RepoInstructionRefs(CanonicalRecord):
    """Repository-relative paths to agent instruction documents."""

    agents_md_path: NonBlank = "AGENTS.md"
    skill_md_path: NonBlank | None = None
    custom_instruction_paths: tuple[NonBlank, ...] = ()

    @model_validator(mode="after")
    def validate_relative_paths(self) -> Self:
        _validate_relative_repo_path(self.agents_md_path)
        if self.skill_md_path is not None:
            _validate_relative_repo_path(self.skill_md_path)
        for custom_path in self.custom_instruction_paths:
            _validate_relative_repo_path(custom_path)
        return self


class UniversalProjectProfile(CanonicalRecord):
    """Provider-neutral onboarding and governance profile for any target repository."""

    schema_version: Literal["1.0"] = "1.0"
    profile_id: NonBlank
    stack_type: ProjectStackType
    display_name: NonBlank
    description: NonBlank
    operating_profile: Literal["subscription_only"] = "subscription_only"
    allow_paid_api_keys: Literal[False] = False
    validation_pack: ValidationPackSpec
    safety_policy: ExternalSystemSafetyPolicy
    instruction_refs: RepoInstructionRefs = Field(default_factory=RepoInstructionRefs)
    default_in_scope_globs: tuple[NonBlank, ...]
    default_forbidden_globs: tuple[NonBlank, ...] = (".git/**", ".env*", "*.pem", "*.key")
    human_approval_required_for: tuple[NonBlank, ...] = ("merge", "deploy", "new_milestone")

    @model_validator(mode="after")
    def validate_profile_contracts(self) -> Self:
        if self.validation_pack.stack_type != self.stack_type:
            raise ValueError(
                f"validation_pack.stack_type ({self.validation_pack.stack_type!r}) "
                f"must match profile stack_type ({self.stack_type!r})"
            )

        missing_approvals = [
            action
            for action in _REQUIRED_HUMAN_APPROVAL_ACTIONS
            if action not in self.human_approval_required_for
        ]
        if missing_approvals:
            raise ValueError(
                "human_approval_required_for must include: " + ", ".join(missing_approvals)
            )

        missing_globs = [
            pattern
            for pattern in _REQUIRED_FORBIDDEN_GLOBS
            if pattern not in self.default_forbidden_globs
        ]
        if missing_globs:
            raise ValueError("default_forbidden_globs must include: " + ", ".join(missing_globs))

        if self.stack_type == "google_apps_script_offline_mock":
            disclaimer = self.safety_policy.simulation_disclaimer
            if disclaimer is None or not disclaimer.strip():
                raise ValueError(
                    "google_apps_script_offline_mock requires a non-blank "
                    "safety_policy.simulation_disclaimer"
                )

        assert_no_secrets(self.model_dump(mode="json"))
        return self


class OnboardedProjectBundle(CanonicalRecord):
    """Complete validated onboarding artifact for a target repository."""

    project_definition: ProjectDefinition
    profile: UniversalProjectProfile
    workspace_root: NonBlank
    detected_instruction_files: tuple[NonBlank, ...]
    detected_stack_markers: tuple[NonBlank, ...]
    onboarding_status: Literal["ONBOARDED_READY"] = "ONBOARDED_READY"


BUILTIN_UNIVERSAL_PROFILES: dict[ProjectStackType, UniversalProjectProfile] = {
    "python_cli_or_package": UniversalProjectProfile(
        profile_id="python-cli-or-package",
        stack_type="python_cli_or_package",
        display_name="Python CLI or Package",
        description="Standard Python package or CLI repository verified with pytest and ruff.",
        validation_pack=ValidationPackSpec(
            pack_id="python-cli-or-package-pack",
            stack_type="python_cli_or_package",
            required_toolchains=("python", "pytest"),
            test_commands=("python -m pytest -q",),
            lint_commands=("python -m ruff check .",),
            default_verification_isolation="isolated_subprocess",
            recommended_container_image="python:3.11-slim",
            offline_mock_only=False,
        ),
        safety_policy=ExternalSystemSafetyPolicy(),
        default_in_scope_globs=("src/**", "tests/**", "pyproject.toml"),
    ),
    "python_web_or_api": UniversalProjectProfile(
        profile_id="python-web-or-api",
        stack_type="python_web_or_api",
        display_name="Python Web or API Service",
        description="Python web or API repository with isolated container-first verification.",
        validation_pack=ValidationPackSpec(
            pack_id="python-web-or-api-pack",
            stack_type="python_web_or_api",
            required_toolchains=("python", "pytest"),
            test_commands=("python -m pytest -q",),
            lint_commands=("python -m ruff check .",),
            default_verification_isolation="container",
            recommended_container_image="python:3.11-slim",
            offline_mock_only=False,
        ),
        safety_policy=ExternalSystemSafetyPolicy(),
        default_in_scope_globs=("src/**", "app/**", "tests/**", "pyproject.toml"),
    ),
    "node_javascript_typescript": UniversalProjectProfile(
        profile_id="node-javascript-typescript",
        stack_type="node_javascript_typescript",
        display_name="Node.js / JavaScript / TypeScript",
        description="Node.js JavaScript or TypeScript repository verified via npm scripts.",
        validation_pack=ValidationPackSpec(
            pack_id="node-javascript-typescript-pack",
            stack_type="node_javascript_typescript",
            required_toolchains=("node", "npm"),
            test_commands=("npm test",),
            lint_commands=("npm run lint",),
            build_commands=("npm run build",),
            default_verification_isolation="container",
            recommended_container_image="node:20-slim",
            offline_mock_only=False,
        ),
        safety_policy=ExternalSystemSafetyPolicy(),
        default_in_scope_globs=("src/**", "test/**", "tests/**", "package.json", "tsconfig.json"),
    ),
    "static_web_frontend": UniversalProjectProfile(
        profile_id="static-web-frontend",
        stack_type="static_web_frontend",
        display_name="Static Web Frontend",
        description="Static HTML/CSS/JS frontend repository with offline verification.",
        validation_pack=ValidationPackSpec(
            pack_id="static-web-frontend-pack",
            stack_type="static_web_frontend",
            required_toolchains=("node",),
            test_commands=("npm test",),
            build_commands=("npm run build",),
            default_verification_isolation="isolated_subprocess",
            recommended_container_image="node:20-slim",
            offline_mock_only=False,
        ),
        safety_policy=ExternalSystemSafetyPolicy(),
        default_in_scope_globs=("index.html", "src/**", "public/**", "assets/**"),
    ),
    "google_apps_script_offline_mock": UniversalProjectProfile(
        profile_id="google-apps-script-offline-mock",
        stack_type="google_apps_script_offline_mock",
        display_name="Google Apps Script (Offline Mock Only)",
        description=(
            "Google Apps Script repository verified strictly with offline local mocks "
            "and zero live external mutations."
        ),
        validation_pack=ValidationPackSpec(
            pack_id="google-apps-script-offline-mock-pack",
            stack_type="google_apps_script_offline_mock",
            required_toolchains=("node",),
            test_commands=("npm test",),
            lint_commands=("npm run lint",),
            default_verification_isolation="isolated_subprocess",
            recommended_container_image="node:20-slim",
            offline_mock_only=True,
        ),
        safety_policy=ExternalSystemSafetyPolicy(
            external_network_allowed=False,
            simulation_disclaimer=_GAS_DISCLAIMER,
        ),
        default_in_scope_globs=("*.gs", "src/**", "tests/**", "appsscript.json", ".clasp.json"),
    ),
}


def get_builtin_project_profile(stack_type: ProjectStackType) -> UniversalProjectProfile:
    """Return the built-in canonical profile for a supported stack type."""
    if stack_type not in BUILTIN_UNIVERSAL_PROFILES:
        raise ValueError(f"No built-in universal profile for stack_type: {stack_type!r}")
    return BUILTIN_UNIVERSAL_PROFILES[stack_type]


def _check_workspace_safety(workspace: Path) -> Path:
    """Verify workspace exists, has no root .env secret files, and has no escaping symlinks."""
    if not workspace.exists() or not workspace.is_dir():
        raise ValueError(f"Workspace does not exist or is not a directory: {workspace}")

    resolved_workspace = workspace.resolve()

    for entry in workspace.iterdir():
        if entry.is_file() and (
            entry.name == ".env"
            or (entry.name.startswith(".env.") and entry.name not in _ALLOWED_ENV_TEMPLATE_NAMES)
        ):
            raise ValueError(f"Prohibited secret environment file found in workspace: {entry.name}")

    for root, dirs, files in os.walk(workspace, followlinks=False):
        root_path = Path(root)
        for name in (*dirs, *files):
            candidate = root_path / name
            if candidate.is_symlink():
                target = candidate.resolve(strict=False)
                if not target.is_relative_to(resolved_workspace):
                    raise ValueError(f"Symlink escapes workspace boundary: {candidate}")

    return resolved_workspace


def _find_markers(workspace: Path, candidates: tuple[str, ...]) -> tuple[str, ...]:
    found: list[str] = []
    for rel in candidates:
        if (workspace / rel).is_file():
            found.append(rel)
    return tuple(found)


def _looks_like_python_web(workspace: Path, python_markers: tuple[str, ...]) -> bool:
    for rel in python_markers:
        if rel not in {"pyproject.toml", "requirements.txt"}:
            continue
        try:
            content = (workspace / rel).read_text(encoding="utf-8", errors="ignore").lower()
        except OSError:
            continue
        if any(framework in content for framework in _WEB_FRAMEWORK_MARKERS):
            return True
    return False


def detect_repository_stack(workspace: Path) -> tuple[ProjectStackType, tuple[str, ...]]:
    """Inspect a repository directory and infer its stack type and detected markers."""
    if not workspace.exists() or not workspace.is_dir():
        raise ValueError(f"Workspace does not exist or is not a directory: {workspace}")

    gas_markers = _find_markers(
        workspace, ("appsscript.json", ".clasp.json", "src/appsscript.json", "src/.clasp.json")
    )
    if gas_markers:
        return "google_apps_script_offline_mock", gas_markers

    node_markers = _find_markers(workspace, ("package.json",))
    if node_markers:
        return "node_javascript_typescript", node_markers

    python_markers = _find_markers(workspace, ("pyproject.toml", "setup.py", "requirements.txt"))
    if python_markers:
        stack: ProjectStackType = (
            "python_web_or_api"
            if _looks_like_python_web(workspace, python_markers)
            else "python_cli_or_package"
        )
        return stack, python_markers

    static_markers = _find_markers(workspace, ("index.html", "public/index.html"))
    if static_markers:
        return "static_web_frontend", static_markers

    raise ValueError(f"No recognized stack markers found in workspace: {workspace}")


def _discover_instruction_files(
    workspace: Path, instruction_refs: RepoInstructionRefs
) -> tuple[str, ...]:
    candidates: list[str] = [
        instruction_refs.agents_md_path,
        "AGENTS.md",
    ]
    if instruction_refs.skill_md_path is not None:
        candidates.append(instruction_refs.skill_md_path)
    candidates.append("SKILL.md")
    candidates.extend(instruction_refs.custom_instruction_paths)

    discovered: list[str] = []
    seen: set[str] = set()
    for rel in candidates:
        normalized = Path(rel).as_posix()
        if normalized in seen:
            continue
        if (workspace / rel).is_file():
            seen.add(normalized)
            discovered.append(normalized)
    return tuple(discovered)


def onboard_universal_project(
    *,
    workspace: Path,
    project_id: str,
    project_name: str,
    phases: tuple[PhaseDefinition, ...],
    stack_type: ProjectStackType | None = None,
    profile: UniversalProjectProfile | None = None,
    planner_agent: str = "codex-planner",
    worker_agent: str = "gemini-worker",
    reviewer_agent: str = "codex-reviewer",
) -> OnboardedProjectBundle:
    """Onboard any heterogeneous repository into a validated AIPC ProjectDefinition bundle."""
    resolved_workspace = _check_workspace_safety(workspace)

    detected_markers: tuple[str, ...] = ()
    if profile is not None:
        if stack_type is not None and profile.stack_type != stack_type:
            raise ValueError(
                f"Explicit profile stack_type ({profile.stack_type!r}) does not match "
                f"requested stack_type ({stack_type!r})"
            )
        resolved_profile = profile
        try:
            _, detected_markers = detect_repository_stack(resolved_workspace)
        except ValueError:
            detected_markers = ()
    elif stack_type is None:
        detected_stack, detected_markers = detect_repository_stack(resolved_workspace)
        resolved_profile = get_builtin_project_profile(detected_stack)
    else:
        resolved_profile = get_builtin_project_profile(stack_type)
        try:
            _, detected_markers = detect_repository_stack(resolved_workspace)
        except ValueError:
            detected_markers = ()

    detected_instruction_files = _discover_instruction_files(
        resolved_workspace, resolved_profile.instruction_refs
    )

    project_definition = ProjectDefinition(
        schema_version="1.0",
        project=ProjectIdentity(
            id=project_id,
            name=project_name,
            workspace=str(resolved_workspace),
        ),
        controller=ControllerConfig(),
        agents=AgentAssignments(
            planner=planner_agent,
            worker=worker_agent,
            reviewer=reviewer_agent,
        ),
        phases=phases,
    )
    assert_no_secrets(project_definition.model_dump(mode="json"))

    return OnboardedProjectBundle(
        project_definition=project_definition,
        profile=resolved_profile,
        workspace_root=str(resolved_workspace),
        detected_instruction_files=detected_instruction_files,
        detected_stack_markers=detected_markers,
        onboarding_status="ONBOARDED_READY",
    )


def render_project_yaml_template(bundle: OnboardedProjectBundle) -> str:
    """Serialize an OnboardedProjectBundle into PROJECT.yaml loadable by load_project."""
    bundle_json = json.dumps(
        bundle.model_dump(mode="json"),
        separators=(",", ":"),
        ensure_ascii=False,
    )
    header_lines = [
        f"# profile_id: {bundle.profile.profile_id}",
        f"# stack_type: {bundle.profile.stack_type}",
        f"# operating_profile: {bundle.profile.operating_profile}",
        f"# onboarding_status: {bundle.onboarding_status}",
        f"{_PROFILE_BUNDLE_COMMENT_PREFIX} {bundle_json}",
    ]
    payload = bundle.project_definition.model_dump(mode="json")
    yaml_body = yaml.safe_dump(payload, sort_keys=False, allow_unicode=True)
    return "\n".join(header_lines) + "\n" + yaml_body


def write_onboarded_project_artifacts(
    bundle: OnboardedProjectBundle, output_dir: Path
) -> tuple[Path, Path]:
    """Write PROJECT.yaml and PROJECT.profile.json after secret-hygiene validation."""
    assert_no_secrets(bundle.model_dump(mode="json"))
    output_dir.mkdir(parents=True, exist_ok=True)
    project_yaml_path = output_dir / "PROJECT.yaml"
    companion_path = output_dir / PROFILE_COMPANION_FILENAME
    project_yaml_path.write_text(render_project_yaml_template(bundle), encoding="utf-8")
    companion_path.write_text(bundle.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return project_yaml_path, companion_path


def _extract_embedded_bundle_json(project_yaml_path: Path) -> str | None:
    for line in project_yaml_path.read_text(encoding="utf-8").splitlines():
        if line.startswith(_PROFILE_BUNDLE_COMMENT_PREFIX):
            return line[len(_PROFILE_BUNDLE_COMMENT_PREFIX) :].strip()
    return None


def load_onboarded_project_bundle(
    project_yaml_path: Path, *, companion_profile_path: Path | None = None
) -> OnboardedProjectBundle:
    """Load PROJECT.yaml and losslessly restore the validated OnboardedProjectBundle."""
    project_def = load_project(project_yaml_path)

    raw_json: str | None = None
    if companion_profile_path is not None:
        if not companion_profile_path.is_file():
            raise ValueError(f"Companion profile file not found: {companion_profile_path}")
        raw_json = companion_profile_path.read_text(encoding="utf-8")
    else:
        default_companion = project_yaml_path.parent / PROFILE_COMPANION_FILENAME
        if default_companion.is_file():
            raw_json = default_companion.read_text(encoding="utf-8")
        else:
            raw_json = _extract_embedded_bundle_json(project_yaml_path)

    if not raw_json:
        raise ValueError(
            f"No companion profile or embedded profile bundle found for {project_yaml_path}"
        )

    raw_payload = json.loads(raw_json)
    bundle = OnboardedProjectBundle.model_validate(raw_payload)
    if bundle.project_definition != project_def:
        raise ValueError(
            "Restored OnboardedProjectBundle project_definition does not match PROJECT.yaml"
        )
    return bundle
