"""Presence-only readiness, no login, credential contents or CLI execution."""

import importlib.util
import os
import shutil
from pathlib import Path


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
    sdk_installed = False
    try:
        sdk_installed = importlib.util.find_spec("google.antigravity") is not None
    except (ModuleNotFoundError, ValueError):
        sdk_installed = False

    return {
        "agy_cli_present": shutil.which("agy") is not None,
        "antigravity_desktop_present": desktop_exe.is_file(),
        "agy_settings_present": (
            Path.home() / ".gemini" / "antigravity-cli" / "settings.json"
        ).is_file(),
        "sdk_installed": sdk_installed,
        "headless_supported_by_desktop": False,
        "effective_security_verified": False,
        "sandbox_verified": False,
        "live_status": "LIVE_RUNTIME_BLOCKED",
        "blocker_reason": (
            "agy CLI and gemini CLI are not installed on PATH; "
            "Antigravity Desktop is an interactive IDE without a qualified "
            "headless sandbox/approval bridge; "
            "google-antigravity SDK requires separate API/Vertex billing"
        ),
    }


__all__ = ["runtime_info", "antigravity_runtime_info"]
