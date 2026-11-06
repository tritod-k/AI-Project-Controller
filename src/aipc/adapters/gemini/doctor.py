"""Presence-only readiness, no login, credential contents or CLI execution."""

import importlib.util
import os
import shutil
from pathlib import Path


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


def runtime_info():
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
    """Inspect Antigravity presence and readiness without credential or process execution."""
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

    cli_ready = agy_exe is not None and oauth_present
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
        "headless_supported_by_cli": agy_exe is not None,
        "effective_security_verified": cli_ready,
        "sandbox_verified": False,
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
            else "agy CLI or Antigravity OAuth session is not present on this host"
        ),
    }


__all__ = ["find_agy_executable", "runtime_info", "antigravity_runtime_info"]
