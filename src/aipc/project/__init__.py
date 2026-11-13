"""Provider-independent project definitions and persistent state."""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from aipc.project.universal_profiles import (
        BUILTIN_UNIVERSAL_PROFILES,
        PROFILE_COMPANION_FILENAME,
        ExternalSystemSafetyPolicy,
        OnboardedProjectBundle,
        ProjectStackType,
        RepoInstructionRefs,
        UniversalProjectProfile,
        ValidationPackSpec,
        detect_repository_stack,
        get_builtin_project_profile,
        load_onboarded_project_bundle,
        onboard_universal_project,
        render_project_yaml_template,
        write_onboarded_project_artifacts,
    )

__all__ = [
    "BUILTIN_UNIVERSAL_PROFILES",
    "PROFILE_COMPANION_FILENAME",
    "ExternalSystemSafetyPolicy",
    "OnboardedProjectBundle",
    "ProjectStackType",
    "RepoInstructionRefs",
    "UniversalProjectProfile",
    "ValidationPackSpec",
    "detect_repository_stack",
    "get_builtin_project_profile",
    "load_onboarded_project_bundle",
    "onboard_universal_project",
    "render_project_yaml_template",
    "write_onboarded_project_artifacts",
]


def __getattr__(name: str) -> Any:
    if name in __all__:
        from aipc.project import universal_profiles

        return getattr(universal_profiles, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
