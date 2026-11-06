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


_qualification_cache: dict[tuple[str, float], AntigravityQualificationResult] = {}


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
    config=None, *, executable: Path | None = None
) -> AntigravityQualificationResult:
    """Dynamically verify Antigravity CLI executable, version, security flags, and configuration."""
    exe = executable or find_agy_executable()
    if exe is None or not Path(exe).is_file():
        return AntigravityQualificationResult(
            qualified=False,
            version=None,
            reason="agy CLI executable not found on host",
        )

    try:
        mtime = Path(exe).stat().st_mtime
    except OSError as exc:
        return AntigravityQualificationResult(
            qualified=False,
            version=None,
            reason=f"cannot stat agy executable: {exc}",
        )

    cache_key = (str(Path(exe).resolve()), mtime)
    cached = _qualification_cache.get(cache_key)

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
                cached = AntigravityQualificationResult(
                    qualified=False,
                    version=version_str,
                    reason=(
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
                    cached = AntigravityQualificationResult(
                        qualified=False,
                        version=version_str,
                        reason=f"agy CLI missing required security flags: {missing}",
                    )
                else:
                    cached = AntigravityQualificationResult(
                        qualified=True,
                        version=version_str,
                        reason="qualified",
                    )
        except (subprocess.TimeoutExpired, OSError) as exc:
            cached = AntigravityQualificationResult(
                qualified=False,
                version=None,
                reason=f"failed executing agy qualification probe: {exc}",
            )
        _qualification_cache[cache_key] = cached

    if not cached.qualified:
        return cached

    # 3. Verify configuration constraints if config provided
    if config is not None:
        mode = getattr(config, "approval_mode", "accept-edits")
        if mode not in ("plan", "accept-edits"):
            return AntigravityQualificationResult(
                qualified=False,
                version=cached.version,
                reason=f"unsafe approval_mode '{mode}' (only 'plan' or 'accept-edits' permitted)",
            )
        if getattr(config, "disable_slash_commands", True) is False:
            return AntigravityQualificationResult(
                qualified=False,
                version=cached.version,
                reason="disable_slash_commands must be True",
            )

    return cached


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


def antigravity_runtime_info() -> dict[str, object]:
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

    qual_result = qualify_antigravity_runtime(executable=agy_exe)
    cli_ready = qual_result.qualified and oauth_present

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
        "headless_supported_by_cli": qual_result.qualified,
        "effective_security_verified": cli_ready,
        "sandbox_verified": cli_ready,
        "runtime_version": qual_result.version,
        "qualified_capabilities": {
            "filesystem": "AVAILABLE" if cli_ready else "UNVERIFIED",
            "filesystem_write": "AVAILABLE" if cli_ready else "UNVERIFIED",
            "streaming_events": "AVAILABLE" if cli_ready else "UNVERIFIED",
            "shell": "BLOCKED",
            "git": "BLOCKED",
            "network": "BLOCKED",
            "resume_session": "UNVERIFIED",
        },
        "live_status": "QUALIFIED_FILESYSTEM_ONLY" if cli_ready else "LIVE_RUNTIME_BLOCKED",
        "blocker_reason": (
            "Windows OS kernel terminal sandbox is not active for arbitrary shell commands; "
            "shell/git/network tools are blocked via permission deny rules while isolated "
            "workspace filesystem read/write is qualified via AntigravityCliTransport"
            if cli_ready
            else qual_result.reason
            if not qual_result.qualified
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
