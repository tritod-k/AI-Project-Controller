"""Official launch is gated separately from the testable ACP protocol machinery."""

from typing import Protocol

from aipc.agents.models import AgentCapabilities

from .doctor import runtime_info
from .errors import GeminiUnavailableError


class GeminiTransport(Protocol):
    capabilities: AgentCapabilities

    async def readiness(self, workspace) -> dict: ...
    async def start_session(self, workspace) -> str: ...
    async def load_session(self, session_id, workspace) -> None: ...
    async def prompt(self, session_id, turn_id, text) -> None: ...
    async def wait(self, session_id, turn_id): ...
    async def cancel(self, session_id, turn_id) -> None: ...
    async def close(self) -> None: ...


def runtime_plan(config, workspace):
    """Requested configuration only, never a receipt of effective enforcement.

    GEMINI_CLI_HOME/system settings isolation alone cannot exclude project settings,
    inherited admin policy, cached auth paths, extensions or native autoexecution.
    This plan cannot be used to launch by the official transport.
    """
    return {
        "argv": ["gemini", "--acp", "--approval-mode=default", "--sandbox"],
        "cwd": str(workspace),
        "qualified": False,
        "settings": {
            "security": {
                "disableYoloMode": True,
                "disableAlwaysAllow": True,
                "enablePermanentToolApproval": False,
                "autoAddToPolicyByDefault": False,
            },
            "tools": {"sandboxNetworkAccess": False, "sandboxAllowedPaths": []},
            "mcpServers": {},
            "includeDirectories": [],
        },
        "extensions": [],
        "mcp": [],
        "model": config.model,
        "effective_audit": "UNVERIFIED",
    }


class OfficialGeminiTransport:
    # No local boolean, provider native config or documentation can enable authority.
    capabilities = AgentCapabilities()

    def __init__(self, config):
        self.config = config

    async def readiness(self, workspace):
        info = runtime_info()
        if not info["cli_present"] or not info["node_present"]:
            raise GeminiUnavailableError("RUNTIME_UNAVAILABLE")
        if not info["authentication_present"]:
            raise GeminiUnavailableError("AUTH_REQUIRED")
        raise GeminiUnavailableError("EFFECTIVE_SECURITY_UNQUALIFIED")

    async def start_session(self, workspace):
        await self.readiness(workspace)

    async def load_session(self, session_id, workspace):
        await self.readiness(workspace)

    async def prompt(self, session_id, turn_id, text):
        raise GeminiUnavailableError("EFFECTIVE_SECURITY_UNQUALIFIED")

    async def wait(self, session_id, turn_id):
        raise GeminiUnavailableError("EFFECTIVE_SECURITY_UNQUALIFIED")

    async def cancel(self, session_id, turn_id):
        raise GeminiUnavailableError("NO_OWNED_RUNTIME")

    async def close(self):
        return None
