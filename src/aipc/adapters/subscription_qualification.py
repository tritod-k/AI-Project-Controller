"""Workstream A01 — Subscription Authentication & Runtime Qualification.

Probes officially supported Codex and Antigravity subscription runtimes without
reading or exporting raw credentials/OAuth tokens. Captures supported model IDs,
account-independent qualification metadata, availability, sandbox constraints,
and capability evidence without secrets. Explicitly separates Desktop/UI presence
from headless execution and reviewer-mode support, and hard-disables separately
billed API-key paths under the `subscription_only` operating profile.
"""

from __future__ import annotations

import importlib.metadata
import importlib.util
import os
import shutil
from collections.abc import Mapping
from pathlib import Path
from typing import Literal, Self

from pydantic import StrictBool, model_validator

from aipc.adapters.codex.transport import RUNTIME_VERSIONS as CODEX_QUALIFIED_RUNTIME_VERSIONS
from aipc.adapters.codex.transport import SDK_VERSION as CODEX_PINNED_SDK_VERSION
from aipc.adapters.gemini.doctor import (
    QUALIFIED_AGY_VERSIONS,
    AntigravityQualificationResult,
    find_agy_executable,
    qualify_antigravity_runtime,
)
from aipc.domain.models import CanonicalRecord, NonBlank
from aipc.hardening.env_isolation import (
    ChildEnvironmentIsolationError,
    EnvironmentIsolationReport,
    SubscriptionBoundaryError,
    assert_child_env_isolated,
    assert_subscription_only_mode,
    build_sanitized_child_env,
    inspect_credential_var_names,
)
from aipc.hardening.secrets import assert_no_secrets

OperatingProfile = Literal["subscription_only"]
RuntimeEligibilityState = Literal["QUALIFIED", "BLOCKED", "UNAVAILABLE"]
SubscriptionAuthMode = Literal["codex_subscription", "antigravity_subscription"]
ProviderFamily = Literal["openai_codex", "google_antigravity"]
OrchestrationRole = Literal["planner", "worker", "reviewer"]
CapabilityState = Literal["AVAILABLE", "BLOCKED", "UNVERIFIED", "SANDBOXED"]

PRIMARY_ANTIGRAVITY_WORKER_MODEL: str = "gemini-4-hydrogen-medium"
FALLBACK_ANTIGRAVITY_WORKER_MODEL: str = "gemini-3.8-flash-medium"
QUALIFIED_ANTIGRAVITY_HEADLESS_MODELS: tuple[str, ...] = (
    FALLBACK_ANTIGRAVITY_WORKER_MODEL,
    "flash",
    PRIMARY_ANTIGRAVITY_WORKER_MODEL,
    "gemini-4-hydrogen-low",
    "gemini-4-hydrogen-high",
    "gemini-4-hydrogen-xhigh",
)
UNVERIFIED_ANTIGRAVITY_MODELS: tuple[str, ...] = (
    "argon",
    "gemini-argon",
)
QUALIFIED_CODEX_SUBSCRIPTION_MODELS: tuple[str, ...] = (
    "codex-subscription-default",
    "gpt-5.3-codex",
    "gpt-5-codex",
    "o4-mini",
)


def enforce_subscription_only_profile(
    operating_profile: str = "subscription_only",
    *,
    auth_mode: str,
    allow_api_key_fallback: bool = False,
    child_env: Mapping[str, str] | None = None,
) -> None:
    """Hard-disable separately billed API-key paths in the `subscription_only` profile."""
    normalized_profile = str(operating_profile).strip().lower()
    if normalized_profile != "subscription_only":
        raise SubscriptionBoundaryError(
            f"Operating profile '{operating_profile}' is prohibited; "
            "AIPC 2.0 enforces operating_profile='subscription_only'."
        )
    assert_subscription_only_mode(
        auth_mode,
        component="SubscriptionQualification",
        allow_api_key_fallback=allow_api_key_fallback,
    )
    if child_env is not None:
        assert_child_env_isolated(child_env)


class SurfaceCapabilitySummary(CanonicalRecord):
    """Explicit separation between Desktop/UI presence and headless CLI/SDK execution."""

    desktop_ui_present: StrictBool
    desktop_ui_callable_headlessly: Literal[False] = False
    cli_headless_present: StrictBool
    sdk_present: StrictBool
    headless_worker_supported: StrictBool
    headless_reviewer_supported: StrictBool
    headless_planner_supported: StrictBool


class SandboxConstraintSummary(CanonicalRecord):
    """Sandbox and execution constraints for a subscription runtime."""

    os_kernel_sandbox_verified: StrictBool
    workspace_isolation_mode: NonBlank
    allowed_modes: tuple[str, ...]
    shell_execution: CapabilityState
    git_execution: CapabilityState
    network_access: CapabilityState
    verification_isolation_default: Literal["blocked"] = "blocked"


class ProviderRuntimeQualificationReport(CanonicalRecord):
    """Reproducible, secret-free qualification report for one subscription runtime."""

    provider_id: NonBlank
    provider_family: ProviderFamily
    operating_profile: OperatingProfile = "subscription_only"
    auth_mode: SubscriptionAuthMode
    eligibility_state: RuntimeEligibilityState
    available: StrictBool
    cli_executable: str | None = None
    runtime_version: str | None = None
    sdk_version: str | None = None
    subscription_auth_present: StrictBool
    supported_model_ids: tuple[str, ...] = ()
    unverified_or_unavailable_model_ids: tuple[str, ...] = ()
    account_independent_metadata: dict[str, str | bool | int] = {}
    surfaces: SurfaceCapabilitySummary
    sandbox_constraints: SandboxConstraintSummary
    capability_evidence: dict[str, CapabilityState]
    role_eligibility: dict[OrchestrationRole, RuntimeEligibilityState]
    reasons: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_report_invariants(self) -> Self:
        assert_no_secrets(self.model_dump(mode="json"))
        expected_available = self.eligibility_state == "QUALIFIED"
        if self.available != expected_available:
            raise ValueError(
                f"available ({self.available}) must match eligibility_state=='QUALIFIED' "
                f"(got {self.eligibility_state})"
            )
        if self.surfaces.desktop_ui_callable_headlessly is not False:
            raise ValueError("Desktop UI surfaces must never be marked callable headlessly")
        if self.account_independent_metadata.get("secret_read_performed") is not False:
            raise ValueError("account_independent_metadata must record secret_read_performed=False")
        for role in ("planner", "worker", "reviewer"):
            if role not in self.role_eligibility:
                raise ValueError(f"role_eligibility missing required role '{role}'")
        return self


class RoleModelEligibilityDecision(CanonicalRecord):
    """Deterministic eligibility evaluation for a provider, role, and model request."""

    provider_id: NonBlank
    role: OrchestrationRole
    requested_model: str | None = None
    resolved_model: str | None = None
    eligibility_state: RuntimeEligibilityState
    eligible: StrictBool
    reason: NonBlank

    @model_validator(mode="after")
    def validate_decision(self) -> Self:
        assert_no_secrets(self.model_dump(mode="json"))
        if self.eligible != (self.eligibility_state == "QUALIFIED"):
            raise ValueError("eligible must equal (eligibility_state == 'QUALIFIED')")
        return self


class SubscriptionQualificationSuiteReport(CanonicalRecord):
    """Aggregated qualification report across supported subscription runtimes."""

    operating_profile: OperatingProfile = "subscription_only"
    environment_isolation: EnvironmentIsolationReport
    providers: dict[str, ProviderRuntimeQualificationReport]

    @model_validator(mode="after")
    def validate_suite(self) -> Self:
        assert_no_secrets(self.model_dump(mode="json"))
        return self

    def evaluate(
        self,
        provider_id: str,
        role: OrchestrationRole,
        requested_model: str | None = None,
    ) -> RoleModelEligibilityDecision:
        """Deterministically evaluate eligibility for a provider, role, and optional model."""
        report = self.providers.get(provider_id)
        if report is None:
            return RoleModelEligibilityDecision(
                provider_id=provider_id,
                role=role,
                requested_model=requested_model,
                resolved_model=None,
                eligibility_state="UNAVAILABLE",
                eligible=False,
                reason=f"Provider '{provider_id}' is not registered in qualification suite.",
            )
        return evaluate_runtime_eligibility(report, role=role, requested_model=requested_model)


def evaluate_runtime_eligibility(
    report: ProviderRuntimeQualificationReport,
    *,
    role: OrchestrationRole,
    requested_model: str | None = None,
) -> RoleModelEligibilityDecision:
    """Return deterministic QUALIFIED / BLOCKED / UNAVAILABLE state for role & model."""
    if report.eligibility_state != "QUALIFIED":
        reason = (
            "; ".join(report.reasons)
            if report.reasons
            else f"Provider '{report.provider_id}' is {report.eligibility_state}."
        )
        return RoleModelEligibilityDecision(
            provider_id=report.provider_id,
            role=role,
            requested_model=requested_model,
            resolved_model=None,
            eligibility_state=report.eligibility_state,
            eligible=False,
            reason=reason,
        )

    role_state = report.role_eligibility.get(role, "BLOCKED")
    if role_state != "QUALIFIED":
        return RoleModelEligibilityDecision(
            provider_id=report.provider_id,
            role=role,
            requested_model=requested_model,
            resolved_model=None,
            eligibility_state=role_state,
            eligible=False,
            reason=(
                f"Role '{role}' is {role_state} on provider '{report.provider_id}' "
                f"({'; '.join(report.reasons) or 'role unsupported'})."
            ),
        )

    if requested_model is not None:
        normalized_req = requested_model.strip()
        if normalized_req in report.unverified_or_unavailable_model_ids:
            return RoleModelEligibilityDecision(
                provider_id=report.provider_id,
                role=role,
                requested_model=normalized_req,
                resolved_model=None,
                eligibility_state="UNAVAILABLE",
                eligible=False,
                reason=(
                    f"Model '{normalized_req}' is unverified or unavailable on subscription "
                    f"runtime '{report.provider_id}'."
                ),
            )
        if normalized_req not in report.supported_model_ids:
            return RoleModelEligibilityDecision(
                provider_id=report.provider_id,
                role=role,
                requested_model=normalized_req,
                resolved_model=None,
                eligibility_state="UNAVAILABLE",
                eligible=False,
                reason=(
                    f"Model '{normalized_req}' is not in supported_model_ids "
                    f"{list(report.supported_model_ids)} for '{report.provider_id}'."
                ),
            )
        resolved = normalized_req
    else:
        if not report.supported_model_ids:
            return RoleModelEligibilityDecision(
                provider_id=report.provider_id,
                role=role,
                requested_model=None,
                resolved_model=None,
                eligibility_state="UNAVAILABLE",
                eligible=False,
                reason=f"No qualified models available on provider '{report.provider_id}'.",
            )
        resolved = report.supported_model_ids[0]

    return RoleModelEligibilityDecision(
        provider_id=report.provider_id,
        role=role,
        requested_model=requested_model,
        resolved_model=resolved,
        eligibility_state="QUALIFIED",
        eligible=True,
        reason=(
            f"Provider '{report.provider_id}' is QUALIFIED for role '{role}' "
            f"with model '{resolved}'."
        ),
    )


def _probe_antigravity_oauth_dir_present(home_dir: Path | None = None) -> bool:
    """Check presence of Antigravity OAuth session directory without reading any file contents."""
    base_home = Path(home_dir) if home_dir is not None else Path.home()
    oauth_dir = base_home / ".gemini" / "antigravity"
    try:
        return oauth_dir.is_dir()
    except OSError:
        return False


def _probe_codex_auth_metadata_present(
    home_dir: Path | None = None,
    sanitized_env: Mapping[str, str] | None = None,
) -> bool:
    """Check presence of Codex subscription session metadata without reading auth.json contents."""
    env_map = sanitized_env or {}
    codex_home_str = env_map.get("CODEX_HOME")
    if codex_home_str:
        codex_dir = Path(codex_home_str)
    else:
        base_home = Path(home_dir) if home_dir is not None else Path.home()
        codex_dir = base_home / ".codex"
    auth_file = codex_dir / "auth.json"
    try:
        return auth_file.is_file() and auth_file.stat().st_size > 0
    except OSError:
        return False


def _probe_codex_sdk_and_bundled_binary() -> tuple[bool, str | None, str | None]:
    """Inspect pinned openai-codex SDK metadata and bundled binary presence without secrets."""
    sdk_installed = False
    sdk_ver: str | None = None
    cli_path: str | None = None
    try:
        sdk_ver = importlib.metadata.version("openai-codex")
        spec = importlib.util.find_spec("openai_codex")
        sdk_installed = spec is not None
        if spec and spec.origin:
            pkg_dir = Path(spec.origin).resolve().parent
            for candidate in (
                pkg_dir / "bin" / "codex.exe",
                pkg_dir / "bin" / "codex",
                pkg_dir / "codex.exe",
                pkg_dir / "codex",
            ):
                if candidate.is_file():
                    cli_path = str(candidate)
                    break
    except (ModuleNotFoundError, importlib.metadata.PackageNotFoundError, ValueError):
        sdk_installed = False

    if cli_path is None:
        which_codex = shutil.which("codex")
        if which_codex and Path(which_codex).is_file():
            cli_path = str(Path(which_codex).resolve())
    return sdk_installed, sdk_ver, cli_path


def qualify_antigravity_subscription_runtime(
    *,
    operating_profile: str = "subscription_only",
    auth_mode: str = "antigravity_subscription",
    allow_api_key_fallback: bool = False,
    security_verified: bool = True,
    executable: Path | None = None,
    home_dir: Path | None = None,
    qualification_override: AntigravityQualificationResult | None = None,
    oauth_present_override: bool | None = None,
    desktop_present_override: bool | None = None,
    supported_models: tuple[str, ...] | None = None,
    base_env: Mapping[str, str] | None = None,
) -> ProviderRuntimeQualificationReport:
    """Probe and qualify the Antigravity CLI subscription runtime without reading credentials."""
    sanitized_env = build_sanitized_child_env(base_env)
    enforce_subscription_only_profile(
        operating_profile,
        auth_mode=auth_mode,
        allow_api_key_fallback=allow_api_key_fallback,
        child_env=sanitized_env,
    )

    if desktop_present_override is not None:
        desktop_present = desktop_present_override
    else:
        local_appdata = sanitized_env.get("LOCALAPPDATA", "")
        desktop_exe = (
            Path(local_appdata) / "Programs" / "antigravity" / "Antigravity.exe"
            if local_appdata
            else Path("")
        )
        desktop_present = desktop_exe.is_file()

    agy_exe = executable if executable is not None else find_agy_executable()
    oauth_present = (
        oauth_present_override
        if oauth_present_override is not None
        else _probe_antigravity_oauth_dir_present(home_dir)
    )
    qual = (
        qualification_override
        if qualification_override is not None
        else qualify_antigravity_runtime(
            executable=agy_exe,
            security_verified=security_verified,
        )
    )

    cli_present = agy_exe is not None and Path(agy_exe).is_file()
    reasons: list[str] = []

    if not cli_present and qualification_override is None:
        state: RuntimeEligibilityState = "UNAVAILABLE"
        reasons.append("Antigravity CLI executable (agy) is not installed on host.")
    elif not qual.cli_compatible:
        state = "BLOCKED"
        reasons.append(f"Antigravity CLI failed compatibility/version check: {qual.reason}")
    elif not oauth_present:
        state = "UNAVAILABLE"
        reasons.append("Antigravity OAuth subscription session directory is not present.")
    elif not (security_verified and qual.effective_security_verified):
        state = "BLOCKED"
        reasons.append("Antigravity headless security/permission deny controls are not verified.")
    else:
        state = "QUALIFIED"
        reasons.append(
            "Antigravity CLI subscription runtime qualified for isolated filesystem worker "
            "and read-only plan/reviewer modes."
        )

    is_qualified = state == "QUALIFIED"
    models = (
        supported_models
        if supported_models is not None
        else (QUALIFIED_ANTIGRAVITY_HEADLESS_MODELS if is_qualified else ())
    )

    surfaces = SurfaceCapabilitySummary(
        desktop_ui_present=desktop_present,
        desktop_ui_callable_headlessly=False,
        cli_headless_present=bool(cli_present or qual.cli_compatible),
        sdk_present=False,
        headless_worker_supported=is_qualified,
        headless_reviewer_supported=is_qualified,
        headless_planner_supported=is_qualified,
    )
    sandbox = SandboxConstraintSummary(
        os_kernel_sandbox_verified=False,
        workspace_isolation_mode="filesystem_only_deny_rules",
        allowed_modes=("plan", "accept-edits"),
        shell_execution="BLOCKED",
        git_execution="BLOCKED",
        network_access="BLOCKED",
        verification_isolation_default="blocked",
    )
    capabilities: dict[str, CapabilityState] = {
        "filesystem_read": "AVAILABLE" if is_qualified else "UNVERIFIED",
        "filesystem_write": "AVAILABLE" if is_qualified else "UNVERIFIED",
        "streaming_json": "AVAILABLE" if qual.cli_compatible else "UNVERIFIED",
        "readonly_plan_mode": "AVAILABLE" if is_qualified else "UNVERIFIED",
        "shell": "BLOCKED",
        "git": "BLOCKED",
        "network": "BLOCKED",
    }
    role_map: dict[OrchestrationRole, RuntimeEligibilityState] = {
        "planner": state,
        "worker": state,
        "reviewer": state,
    }

    return ProviderRuntimeQualificationReport(
        provider_id="gemini-antigravity",
        provider_family="google_antigravity",
        operating_profile="subscription_only",
        auth_mode="antigravity_subscription",
        eligibility_state=state,
        available=is_qualified,
        cli_executable=str(agy_exe) if agy_exe is not None else None,
        runtime_version=qual.version,
        sdk_version=None,
        subscription_auth_present=oauth_present,
        supported_model_ids=models,
        unverified_or_unavailable_model_ids=UNVERIFIED_ANTIGRAVITY_MODELS,
        account_independent_metadata={
            "qualified_cli_versions": ",".join(sorted(QUALIFIED_AGY_VERSIONS)),
            "secret_read_performed": False,
            "sanitized_child_env": True,
        },
        surfaces=surfaces,
        sandbox_constraints=sandbox,
        capability_evidence=capabilities,
        role_eligibility=role_map,
        reasons=tuple(reasons),
    )


def qualify_codex_subscription_runtime(
    *,
    operating_profile: str = "subscription_only",
    auth_mode: str = "codex_subscription",
    allow_api_key_fallback: bool = False,
    security_verified: bool = True,
    home_dir: Path | None = None,
    sdk_installed_override: bool | None = None,
    sdk_version_override: str | None = None,
    runtime_version_override: str | None = None,
    cli_executable_override: str | None = None,
    auth_present_override: bool | None = None,
    desktop_present_override: bool | None = None,
    supported_models: tuple[str, ...] | None = None,
    base_env: Mapping[str, str] | None = None,
) -> ProviderRuntimeQualificationReport:
    """Probe and qualify the Codex subscription runtime without reading credentials/tokens."""
    sanitized_env = build_sanitized_child_env(base_env)
    enforce_subscription_only_profile(
        operating_profile,
        auth_mode=auth_mode,
        allow_api_key_fallback=allow_api_key_fallback,
        child_env=sanitized_env,
    )

    probed_sdk_ok, probed_sdk_ver, probed_cli = _probe_codex_sdk_and_bundled_binary()
    sdk_present = sdk_installed_override if sdk_installed_override is not None else probed_sdk_ok
    sdk_ver = sdk_version_override if sdk_version_override is not None else probed_sdk_ver
    cli_exe = cli_executable_override if cli_executable_override is not None else probed_cli
    runtime_ver = (
        runtime_version_override
        if runtime_version_override is not None
        else (sdk_ver if sdk_ver in CODEX_QUALIFIED_RUNTIME_VERSIONS else None)
    )
    auth_present = (
        auth_present_override
        if auth_present_override is not None
        else _probe_codex_auth_metadata_present(home_dir, sanitized_env)
    )
    desktop_present = (
        bool(desktop_present_override) if desktop_present_override is not None else False
    )

    reasons: list[str] = []
    if not sdk_present:
        state: RuntimeEligibilityState = "UNAVAILABLE"
        reasons.append("Official openai-codex Python SDK is not installed.")
    elif sdk_ver != CODEX_PINNED_SDK_VERSION:
        state = "BLOCKED"
        reasons.append(
            f"Unqualified openai-codex SDK version '{sdk_ver}' "
            f"(expected pinned '{CODEX_PINNED_SDK_VERSION}')."
        )
    elif runtime_ver not in CODEX_QUALIFIED_RUNTIME_VERSIONS:
        state = "BLOCKED"
        reasons.append(
            f"Unqualified Codex runtime version '{runtime_ver}' "
            f"(expected one of {sorted(CODEX_QUALIFIED_RUNTIME_VERSIONS)})."
        )
    elif not auth_present:
        state = "UNAVAILABLE"
        reasons.append("Codex subscription authentication session is not present on host.")
    elif not security_verified:
        state = "BLOCKED"
        reasons.append("Codex sandbox and denial-guard security controls are not verified.")
    else:
        state = "QUALIFIED"
        reasons.append(
            "Codex subscription SDK runtime qualified with denial guard and read_only / "
            "workspace_write sandboxes."
        )

    is_qualified = state == "QUALIFIED"
    models = (
        supported_models
        if supported_models is not None
        else (QUALIFIED_CODEX_SUBSCRIPTION_MODELS if is_qualified else ())
    )

    surfaces = SurfaceCapabilitySummary(
        desktop_ui_present=desktop_present,
        desktop_ui_callable_headlessly=False,
        cli_headless_present=bool(cli_exe is not None or sdk_present),
        sdk_present=sdk_present,
        headless_worker_supported=is_qualified,
        headless_reviewer_supported=is_qualified,
        headless_planner_supported=is_qualified,
    )
    sandbox = SandboxConstraintSummary(
        os_kernel_sandbox_verified=is_qualified,
        workspace_isolation_mode="codex_sdk_workspace_write_or_read_only",
        allowed_modes=("read_only", "workspace_write"),
        shell_execution="SANDBOXED" if is_qualified else "BLOCKED",
        git_execution="SANDBOXED" if is_qualified else "BLOCKED",
        network_access="BLOCKED",
        verification_isolation_default="blocked",
    )
    capabilities: dict[str, CapabilityState] = {
        "filesystem_read": "AVAILABLE" if is_qualified else "UNVERIFIED",
        "filesystem_write": "AVAILABLE" if is_qualified else "UNVERIFIED",
        "readonly_review_mode": "AVAILABLE" if is_qualified else "UNVERIFIED",
        "resume_thread": "AVAILABLE" if is_qualified else "UNVERIFIED",
        "shell": "AVAILABLE" if is_qualified else "BLOCKED",
        "git": "AVAILABLE" if is_qualified else "BLOCKED",
        "network": "BLOCKED",
    }
    role_map: dict[OrchestrationRole, RuntimeEligibilityState] = {
        "planner": state,
        "worker": state,
        "reviewer": state,
    }

    return ProviderRuntimeQualificationReport(
        provider_id="codex",
        provider_family="openai_codex",
        operating_profile="subscription_only",
        auth_mode="codex_subscription",
        eligibility_state=state,
        available=is_qualified,
        cli_executable=cli_exe,
        runtime_version=runtime_ver,
        sdk_version=sdk_ver,
        subscription_auth_present=auth_present,
        supported_model_ids=models,
        unverified_or_unavailable_model_ids=(),
        account_independent_metadata={
            "pinned_sdk_version": CODEX_PINNED_SDK_VERSION,
            "qualified_runtime_versions": ",".join(sorted(CODEX_QUALIFIED_RUNTIME_VERSIONS)),
            "secret_read_performed": False,
            "sanitized_child_env": True,
        },
        surfaces=surfaces,
        sandbox_constraints=sandbox,
        capability_evidence=capabilities,
        role_eligibility=role_map,
        reasons=tuple(reasons),
    )


def qualify_desktop_only_surface(
    surface_id: Literal["antigravity-desktop", "codex-desktop"],
    *,
    desktop_present: bool,
) -> ProviderRuntimeQualificationReport:
    """Explicitly classify Desktop/GUI surfaces as BLOCKED for headless execution."""
    is_agy = surface_id == "antigravity-desktop"
    return ProviderRuntimeQualificationReport(
        provider_id=surface_id,
        provider_family="google_antigravity" if is_agy else "openai_codex",
        operating_profile="subscription_only",
        auth_mode="antigravity_subscription" if is_agy else "codex_subscription",
        eligibility_state="BLOCKED",
        available=False,
        cli_executable=None,
        runtime_version=None,
        sdk_version=None,
        subscription_auth_present=False,
        supported_model_ids=(),
        unverified_or_unavailable_model_ids=UNVERIFIED_ANTIGRAVITY_MODELS if is_agy else (),
        account_independent_metadata={
            "desktop_only_surface": True,
            "secret_read_performed": False,
            "sanitized_child_env": True,
        },
        surfaces=SurfaceCapabilitySummary(
            desktop_ui_present=desktop_present,
            desktop_ui_callable_headlessly=False,
            cli_headless_present=False,
            sdk_present=False,
            headless_worker_supported=False,
            headless_reviewer_supported=False,
            headless_planner_supported=False,
        ),
        sandbox_constraints=SandboxConstraintSummary(
            os_kernel_sandbox_verified=False,
            workspace_isolation_mode="none_interactive_gui",
            allowed_modes=(),
            shell_execution="BLOCKED",
            git_execution="BLOCKED",
            network_access="BLOCKED",
            verification_isolation_default="blocked",
        ),
        capability_evidence={
            "filesystem_read": "BLOCKED",
            "filesystem_write": "BLOCKED",
            "shell": "BLOCKED",
            "git": "BLOCKED",
            "network": "BLOCKED",
        },
        role_eligibility={
            "planner": "BLOCKED",
            "worker": "BLOCKED",
            "reviewer": "BLOCKED",
        },
        reasons=(
            f"Interactive Desktop UI surface '{surface_id}' is not a qualified headless "
            "execution or reviewer transport.",
        ),
    )


def qualify_all_subscription_runtimes(
    *,
    operating_profile: str = "subscription_only",
    base_env: Mapping[str, str] | None = None,
    home_dir: Path | None = None,
    antigravity_report: ProviderRuntimeQualificationReport | None = None,
    codex_report: ProviderRuntimeQualificationReport | None = None,
) -> SubscriptionQualificationSuiteReport:
    """Produce a reproducible qualification suite report across all subscription runtimes."""
    source_env = os.environ if base_env is None else base_env
    removed_vars = inspect_credential_var_names(source_env)
    sanitized_env = build_sanitized_child_env(source_env)
    remaining_vars = inspect_credential_var_names(sanitized_env)
    env_report = EnvironmentIsolationReport(
        status="ISOLATED" if not remaining_vars else "CHILD_ENVIRONMENT_ISOLATION_BLOCKED",
        removed_var_names=removed_vars,
        remaining_prohibited_vars=remaining_vars,
        argv_checked=True,
        platform_case_insensitive=True,
    )

    agy = antigravity_report or qualify_antigravity_subscription_runtime(
        operating_profile=operating_profile,
        home_dir=home_dir,
        base_env=sanitized_env,
    )
    codex = codex_report or qualify_codex_subscription_runtime(
        operating_profile=operating_profile,
        home_dir=home_dir,
        base_env=sanitized_env,
    )
    agy_desktop = qualify_desktop_only_surface(
        "antigravity-desktop",
        desktop_present=agy.surfaces.desktop_ui_present,
    )

    return SubscriptionQualificationSuiteReport(
        operating_profile="subscription_only",
        environment_isolation=env_report,
        providers={
            "codex": codex,
            "gemini-antigravity": agy,
            "antigravity-desktop": agy_desktop,
        },
    )


__all__ = [
    "CapabilityState",
    "ChildEnvironmentIsolationError",
    "FALLBACK_ANTIGRAVITY_WORKER_MODEL",
    "OperatingProfile",
    "OrchestrationRole",
    "PRIMARY_ANTIGRAVITY_WORKER_MODEL",
    "ProviderFamily",
    "ProviderRuntimeQualificationReport",
    "QUALIFIED_ANTIGRAVITY_HEADLESS_MODELS",
    "QUALIFIED_CODEX_SUBSCRIPTION_MODELS",
    "RoleModelEligibilityDecision",
    "RuntimeEligibilityState",
    "SandboxConstraintSummary",
    "SubscriptionAuthMode",
    "SubscriptionQualificationSuiteReport",
    "SurfaceCapabilitySummary",
    "UNVERIFIED_ANTIGRAVITY_MODELS",
    "enforce_subscription_only_profile",
    "evaluate_runtime_eligibility",
    "qualify_all_subscription_runtimes",
    "qualify_antigravity_subscription_runtime",
    "qualify_codex_subscription_runtime",
    "qualify_desktop_only_surface",
]
