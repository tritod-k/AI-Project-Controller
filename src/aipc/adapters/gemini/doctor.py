"""Presence and dynamic runtime qualification for Gemini and Antigravity CLI."""

import importlib.util
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

QUALIFIED_AGY_VERSIONS: frozenset[str] = frozenset({"1.5.2"})
REQUIRED_AGY_FLAGS: tuple[str, ...] = (
    "--sandbox",
    "--mode",
    "--settings",
    "--output-format",
    "--disable-slash-commands",
)


@dataclass(frozen=True)
class AntigravityQualificationResult:
    """Detailed facts regarding Antigravity CLI runtime security qualification."""

    qualified: bool
    version: str | None
    reason: str
    cli_compatible: bool = False
    sandbox_verified: bool = False
    effective_security_verified: bool = False


_cli_compat_cache: dict[tuple[str, float], tuple[bool, str | None, str]] = {}


def find_agy_executable() -> Path | None:
    """Locate the official Antigravity CLI binary without executing it."""
    which_path = shutil.which("agy")
    if which_path and Path(which_path).is_file():
        return Path(which_path).resolve()
    local_appdata = os.environ.get("LOCALAPPDATA", "")
    if local_appdata:
        candidate = Path(local_appdata) / "agy" / "bin" / "agy.exe"
        if candidate.is_file():
            return candidate.resolve()
    posix_candidate = Path.home() / ".local" / "bin" / "agy"
    if posix_candidate.is_file():
        return posix_candidate.resolve()
    return None


def qualify_antigravity_runtime(
    config=None, *, executable: Path | None = None, security_verified: bool = False
) -> AntigravityQualificationResult:
    """Dynamically verify Antigravity CLI executable, version, security flags, and configuration."""
    exe = executable or find_agy_executable()
    if exe is None or not Path(exe).is_file():
        return AntigravityQualificationResult(
            qualified=False,
            version=None,
            reason="agy CLI executable not found on host",
            cli_compatible=False,
            sandbox_verified=False,
            effective_security_verified=False,
        )

    try:
        mtime = Path(exe).stat().st_mtime
    except OSError as exc:
        return AntigravityQualificationResult(
            qualified=False,
            version=None,
            reason=f"cannot stat agy executable: {exc}",
            cli_compatible=False,
            sandbox_verified=False,
            effective_security_verified=False,
        )

    cache_key = (str(Path(exe).resolve()), mtime)
    cached = _cli_compat_cache.get(cache_key)

    if cached is None:
        # 1. Verify exact version
        try:
            ver_res = subprocess.run(
                [str(exe), "--version"],
                capture_output=True,
                text=True,
                check=False,
                timeout=5,
            )
            raw_ver = (ver_res.stdout or ver_res.stderr or "").strip().split()
            version_str = raw_ver[0] if raw_ver else "unknown"
            if ver_res.returncode != 0 or version_str not in QUALIFIED_AGY_VERSIONS:
                cached = (
                    False,
                    version_str,
                    (
                        f"unqualified agy version '{version_str}', "
                        f"expected one of {sorted(QUALIFIED_AGY_VERSIONS)}"
                    ),
                )
            else:
                # 2. Verify required security flags in help output
                help_res = subprocess.run(
                    [str(exe), "-h"],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=5,
                )
                help_text = (help_res.stdout or "") + (help_res.stderr or "")
                missing = [flag for flag in REQUIRED_AGY_FLAGS if flag not in help_text]
                if missing:
                    cached = (
                        False,
                        version_str,
                        f"agy CLI missing required security flags: {missing}",
                    )
                else:
                    cached = (True, version_str, "qualified")
        except (subprocess.TimeoutExpired, OSError) as exc:
            cached = (False, None, f"failed executing agy qualification probe: {exc}")
        _cli_compat_cache[cache_key] = cached

    cli_compatible, version_str, reason = cached

    if not cli_compatible:
        return AntigravityQualificationResult(
            qualified=False,
            version=version_str,
            reason=reason,
            cli_compatible=False,
            sandbox_verified=False,
            effective_security_verified=False,
        )

    sandbox_verified = False
    effective_security_verified = False

    if security_verified:
        effective_security_verified = True
    elif config is not None:
        mode = getattr(config, "approval_mode", "accept-edits")
        slash = getattr(config, "disable_slash_commands", True)
        isolation = getattr(config, "verification_isolation", "blocked")
        ctrl_verified = getattr(config, "security_controls_verified", True)

        if mode not in ("plan", "accept-edits"):
            return AntigravityQualificationResult(
                qualified=False,
                version=version_str,
                reason=f"unsafe approval_mode '{mode}' (only 'plan' or 'accept-edits' permitted)",
                cli_compatible=True,
                sandbox_verified=False,
                effective_security_verified=False,
            )
        if slash is False:
            return AntigravityQualificationResult(
                qualified=False,
                version=version_str,
                reason="disable_slash_commands must be True",
                cli_compatible=True,
                sandbox_verified=False,
                effective_security_verified=False,
            )
        if isolation == "unsafe_host_opt_in":
            return AntigravityQualificationResult(
                qualified=False,
                version=version_str,
                reason=(
                    "verification_isolation='unsafe_host_opt_in' "
                    "fails safe for production qualification"
                ),
                cli_compatible=True,
                sandbox_verified=False,
                effective_security_verified=False,
            )
        if not ctrl_verified:
            return AntigravityQualificationResult(
                qualified=False,
                version=version_str,
                reason="security_controls_verified is False",
                cli_compatible=True,
                sandbox_verified=False,
                effective_security_verified=False,
            )
        effective_security_verified = True

    qualified = bool(cli_compatible and effective_security_verified)
    fail_reason = (
        "qualified"
        if qualified
        else (
            "security controls unverified (requires verified policy "
            "and container/blocked verification isolation)"
        )
    )

    return AntigravityQualificationResult(
        qualified=qualified,
        version=version_str,
        reason=fail_reason,
        cli_compatible=cli_compatible,
        sandbox_verified=sandbox_verified,
        effective_security_verified=effective_security_verified,
    )


def runtime_info():
    """Inspect official Gemini ACP runtime readiness."""
    return {
        "node_present": shutil.which("node") is not None,
        "npm_present": shutil.which("npm") is not None,
        "cli_present": shutil.which("gemini") is not None,
        "runtime_version": None,
        "authentication_present": any(
            bool(os.environ.get(name))
            for name in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_APPLICATION_CREDENTIALS")
        )
        or (Path.home() / ".gemini" / "oauth_creds.json").is_file(),
        "effective_security_verified": False,
        "sandbox_verified": False,
        "live_status": "LIVE_RUNTIME_BLOCKED",
    }


def antigravity_runtime_info(
    config=None,
    *,
    security_verified: bool = False,
) -> dict[str, object]:
    """Inspect Antigravity presence and dynamic readiness qualification."""
    local_appdata = os.environ.get("LOCALAPPDATA", "")
    desktop_exe = (
        Path(local_appdata) / "Programs" / "antigravity" / "Antigravity.exe"
        if local_appdata
        else Path("")
    )
    agy_exe = find_agy_executable()
    oauth_dir = Path.home() / ".gemini" / "antigravity"
    oauth_present = oauth_dir.is_dir()
    sdk_installed = False
    try:
        sdk_installed = importlib.util.find_spec("google.antigravity") is not None
    except (ModuleNotFoundError, ValueError):
        sdk_installed = False

    qual_result = qualify_antigravity_runtime(
        config=config, executable=agy_exe, security_verified=security_verified
    )
    sandbox_verified = False
    effective_security_verified = bool(
        qual_result.cli_compatible and oauth_present and qual_result.effective_security_verified
    )

    if qual_result.cli_compatible and oauth_present and effective_security_verified:
        live_status = "QUALIFIED_FILESYSTEM_ONLY"
    else:
        live_status = "LIVE_RUNTIME_BLOCKED"

    return {
        "agy_cli_present": agy_exe is not None,
        "agy_executable": str(agy_exe) if agy_exe is not None else None,
        "antigravity_desktop_present": desktop_exe.is_file(),
        "oauth_session_present": oauth_present,
        "agy_settings_present": (
            Path.home() / ".gemini" / "antigravity-cli" / "settings.json"
        ).is_file(),
        "sdk_installed": sdk_installed,
        "headless_supported_by_desktop": False,
        "headless_supported_by_cli": qual_result.cli_compatible,
        "cli_compatible": qual_result.cli_compatible,
        "effective_security_verified": effective_security_verified,
        "sandbox_verified": sandbox_verified,
        "runtime_version": qual_result.version,
        "qualified_capabilities": {
            "filesystem": (
                "AVAILABLE"
                if (qual_result.cli_compatible and effective_security_verified)
                else "UNVERIFIED"
            ),
            "filesystem_write": (
                "AVAILABLE"
                if (qual_result.cli_compatible and effective_security_verified)
                else "UNVERIFIED"
            ),
            "streaming_events": "AVAILABLE" if qual_result.cli_compatible else "UNVERIFIED",
            "shell": "BLOCKED",
            "git": "BLOCKED",
            "network": "BLOCKED",
            "resume_session": "UNVERIFIED",
        },
        "live_status": live_status,
        "blocker_reason": (
            "Windows OS kernel terminal sandbox is not active for arbitrary shell commands; "
            "shell/git/network tools are blocked via permission deny rules while isolated "
            "workspace filesystem read/write is qualified via AntigravityCliTransport"
            if (qual_result.cli_compatible and effective_security_verified)
            else "Security controls unverified on host runtime (fails closed)"
            if (qual_result.cli_compatible and oauth_present and not effective_security_verified)
            else qual_result.reason
            if not qual_result.cli_compatible
            else "Antigravity OAuth session is not present on this host (~/.gemini/antigravity)"
        ),
    }


__all__ = [
    "AntigravityQualificationResult",
    "QUALIFIED_AGY_VERSIONS",
    "REQUIRED_AGY_FLAGS",
    "antigravity_runtime_info",
    "find_agy_executable",
    "qualify_antigravity_runtime",
    "runtime_info",
]
