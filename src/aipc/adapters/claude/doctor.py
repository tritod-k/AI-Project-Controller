"""Read-only bounded runtime metadata. Never reads or displays credential files."""

import importlib
import importlib.metadata
import json
import os
import platform
import shutil
import subprocess
from pathlib import Path

from .config import CLI_VERSION, SDK_VERSION


def runtime_info():
    info = dict(
        python=platform.python_version(),
        platform=platform.system(),
        sdk_version=None,
        runtime_version=None,
        authenticated=False,
        auth_source="unavailable",
        sandbox_available=False,
        approval_events=False,
        model="provider default (unobserved)",
    )
    try:
        sdk = importlib.import_module("claude_agent_sdk")
        info["sdk_version"] = importlib.metadata.version("claude-agent-sdk")
        exe = (
            Path(sdk.__file__).parent / "_bundled" / ("claude.exe" if os.name == "nt" else "claude")
        )
        version = subprocess.run(
            [str(exe), "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
            shell=False,
        )
        if version.returncode == 0 and version.stdout.split()[:1] == [CLI_VERSION]:
            info["runtime_version"] = CLI_VERSION
        auth = subprocess.run(
            [str(exe), "auth", "status", "--json"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
            shell=False,
        )
        status = json.loads(auth.stdout)
        info["authenticated"] = status.get("loggedIn") is True
        method = status.get("authMethod")
        info["auth_source"] = (
            method
            if method in ("none", "api_key", "oauth", "claude.ai", "bedrock", "vertex")
            else "runtime-reported (details withheld)"
        )
        if os.environ.get("ANTHROPIC_API_KEY"):
            info["authenticated"], info["auth_source"] = (
                True,
                "ANTHROPIC_API_KEY present (not authenticated by a paid call)",
            )
        # This is a dependency/platform prerequisite, not proof that the runtime sandbox started.
        info["sandbox_available"] = platform.system() == "Darwin" or (
            platform.system() == "Linux"
            and bool(shutil.which("bwrap"))
            and bool(shutil.which("socat"))
        )
        info["compatible"] = (
            info["sdk_version"] == SDK_VERSION and info["runtime_version"] == CLI_VERSION
        )
    except (
        ImportError,
        importlib.metadata.PackageNotFoundError,
        OSError,
        subprocess.SubprocessError,
        ValueError,
    ):
        info["reason"] = "runtime/auth readiness unavailable; details withheld"
    return info
