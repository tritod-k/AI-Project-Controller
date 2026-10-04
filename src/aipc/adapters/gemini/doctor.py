"""Presence-only readiness, no login, credential contents or CLI execution."""

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
