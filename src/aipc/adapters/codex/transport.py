"""Only SDK seam, pinned to the inspected official Python SDK 0.160.0."""

import asyncio
import importlib
import importlib.metadata
import re
from pathlib import Path
from typing import Protocol

from aipc.evidence.paths import authorized_root, check_ancestors

from .errors import (
    CodexAuthenticationError,
    CodexProtocolError,
    CodexSessionLostError,
    CodexTransportError,
    CodexUnavailableError,
    CodexUnsupportedVersionError,
)
from .events import redact

SDK_VERSION = "0.160.0"
RUNTIME_VERSIONS = {"0.159.2", "0.160.0"}


class CodexTransport(Protocol):
    async def readiness(self, workspace: Path) -> dict: ...
    async def start_thread(self, workspace: Path) -> str: ...
    async def start_turn(self, thread_id: str, prompt: str, workspace: Path) -> str: ...
    async def attach(self, thread_id: str, turn_id: str, workspace: Path) -> None: ...
    def events(self, thread_id: str, turn_id: str): ...
    async def interrupt(self, thread_id: str, turn_id: str) -> None: ...
    async def close(self) -> None: ...


def deny_escalation(method, params):
    """No authority inferred from a missing bridge, low risk or provider-native review."""
    if method in {"item/commandExecution/requestApproval", "item/fileChange/requestApproval"}:
        return {"decision": "decline"}
    if method == "item/permissions/requestApproval":
        return {"permissions": {}, "scope": "turn"}
    raise CodexProtocolError("unsupported server request denied; close transport")


def install_denial_guard(client):
    """The public facade has no handler argument. Fail on any pinned-layout drift.

    This sole compatibility hook is installed BEFORE SDK initialization. All
    execution lifecycle uses public APIs. Never fall back to the SDK's accept handler.
    """
    try:
        sync = client._client._sync
        if not callable(sync._approval_handler):
            raise AttributeError
        sync._approval_handler = deny_escalation
    except AttributeError:
        raise CodexUnsupportedVersionError() from None


def validate_effective_config(config, sandbox):
    """Observe effective policy rather than assuming an override replaced host tables."""
    workspace = config.get("sandbox_workspace_write") or {}
    if (
        config.get("approval_policy") != "never"
        or config.get("approvals_reviewer") != "user"
        or config.get("sandbox_mode") != sandbox.replace("_", "-")
        or workspace.get("network_access") is not False
        or workspace.get("writable_roots") != []
        or workspace.get("exclude_tmpdir_env_var") is not True
        or workspace.get("exclude_slash_tmp") is not True
        or config.get("web_search") != "disabled"
        or config.get("hooks")
        or config.get("default_permissions")
        or any(server.get("enabled", True) for server in (config.get("mcp_servers") or {}).values())
        or any(
            (config.get("features") or {}).get(key)
            for key in ("multi_agent", "plugins", "computer_use", "remote_control", "js_repl")
        )
    ):
        raise CodexProtocolError("effective runtime policy expands authorized tool/sandbox scope")


def mcp_disables(servers):
    # Runtime -c parser splits keys on dots; TOML-quoted dotted keys are NOT safe.
    if any(not re.fullmatch(r"[A-Za-z0-9_-]+", name) for name in servers):
        raise CodexProtocolError("ambiguous MCP config key cannot be safely disabled")
    return tuple(f"mcp_servers.{name}.enabled=false" for name in servers)


class SdkCodexTransport:
    def __init__(self, config):
        self.config = config
        self.client = None
        self.sdk = None
        self.threads = {}
        self.turns = {}
        self.workspace = None
        self.info = None

    def _sdk(self):
        if self.sdk is None:
            try:
                version = importlib.metadata.version("openai-codex")
                if version != SDK_VERSION:
                    raise CodexUnsupportedVersionError()
                self.sdk = importlib.import_module("openai_codex")
            except (ModuleNotFoundError, importlib.metadata.PackageNotFoundError):
                raise CodexUnavailableError() from None
        return self.sdk

    def thread_options(self, workspace):
        sdk = self._sdk()
        return dict(
            cwd=str(workspace),
            model=self.config.model,
            approval_mode=sdk.ApprovalMode.deny_all,
            sandbox=getattr(sdk.Sandbox, self.config.sandbox),
            config=self.safe_config(),
        )

    def safe_config(self):
        return {
            "approval_policy": "never",
            "approvals_reviewer": "user",
            "sandbox_mode": self.config.sandbox.replace("_", "-"),
            "sandbox_workspace_write": {
                "network_access": False,
                "writable_roots": [],
                "exclude_tmpdir_env_var": True,
                "exclude_slash_tmp": True,
            },
            "web_search": "disabled",
            "mcp_servers": {},
            "features": {
                "multi_agent": False,
                "plugins": False,
                "js_repl": False,
                "computer_use": False,
                "remote_control": False,
                "memories": False,
                "remote_plugin": False,
                "tool_suggest": False,
                "auth_elicitation": False,
            },
        }

    async def _effective(self, client, workspace):
        # No public high-level config/read. This pinned, read-only compatibility seam
        # verifies sandbox/host tools and never prints or persists the full config.
        response = importlib.import_module("openai_codex.generated.v2_all").ConfigReadResponse
        result = await client._client.request(
            "config/read", {"cwd": str(workspace), "includeLayers": False}, response_model=response
        )
        return result.config.model_dump(mode="json")

    async def readiness(self, workspace):
        workspace = authorized_root(workspace)
        if self.client is not None:
            if workspace != self.workspace:
                raise CodexProtocolError("one transport instance binds one attempt workspace")
            return dict(self.info)
        sdk = self._sdk()
        runtime = self.config.runtime_path
        if runtime is not None:
            check_ancestors(runtime)
            if not runtime.is_file():
                raise CodexUnavailableError()
        import json

        overrides = tuple(
            f"{key}={json.dumps(value)}"
            for key, value in self.safe_config().items()
            if not isinstance(value, dict)
        )
        overrides += (
            "sandbox_workspace_write.network_access=false",
            "sandbox_workspace_write.writable_roots=[]",
            "sandbox_workspace_write.exclude_tmpdir_env_var=true",
            "sandbox_workspace_write.exclude_slash_tmp=true",
            "mcp_servers={}",
            *(f"features.{key}=false" for key in self.safe_config()["features"]),
        )
        client = sdk.AsyncCodex(
            sdk.CodexConfig(
                codex_bin=None if runtime is None else str(runtime),
                cwd=str(workspace),
                config_overrides=overrides,
                client_name="aipc_codex_adapter",
            )
        )
        install_denial_guard(client)
        try:
            await client.__aenter__()
            version = client.metadata.serverInfo.version.split()[0]
            if version not in RUNTIME_VERSIONS:
                raise CodexUnsupportedVersionError()
            account = await client.account(refresh_token=False)
            if account.account is None:
                raise CodexAuthenticationError()
            effective = await self._effective(client, workspace)
            servers = effective.get("mcp_servers") or {}
            if any(server.get("enabled", True) for server in servers.values()):
                # TOML tables merge: mcp_servers={} alone DOES NOT clear host servers.
                # Re-launch before thread creation with exact named disable overrides.
                await client.close()
                disables = mcp_disables(servers)
                client = sdk.AsyncCodex(
                    sdk.CodexConfig(
                        codex_bin=None if runtime is None else str(runtime),
                        cwd=str(workspace),
                        config_overrides=overrides + disables,
                        client_name="aipc_codex_adapter",
                    )
                )
                install_denial_guard(client)
                await client.__aenter__()
                effective = await self._effective(client, workspace)
            validate_effective_config(effective, self.config.sandbox)
        except BaseException as exc:
            await client.close()
            if isinstance(exc, asyncio.CancelledError):
                raise
            self._raise(exc)
        self.client, self.workspace = client, workspace
        self.info = dict(sdk_version=SDK_VERSION, runtime_version=version)
        return dict(self.info)

    @staticmethod
    def _raise(exc):
        if isinstance(exc, (CodexProtocolError, CodexUnavailableError, CodexSessionLostError)):
            raise exc from None
        name = type(exc).__name__
        if name == "ServerBusyError":
            from aipc.recovery.models import ProviderCondition

            raise CodexTransportError(ProviderCondition.RATE_LIMITED) from None
        if isinstance(exc, (FileNotFoundError, ImportError)):
            raise CodexUnavailableError() from None
        raise CodexTransportError() from None

    async def start_thread(self, workspace):
        await self.readiness(workspace)
        try:
            validate_effective_config(
                await self._effective(self.client, workspace), self.config.sandbox
            )
            thread = await self.client.thread_start(**self.thread_options(workspace))
            if thread.id in self.threads:
                raise CodexProtocolError("thread/start reused an owned provider thread")
            self.threads[thread.id] = thread
            return thread.id
        except Exception as exc:
            self._raise(exc)

    async def start_turn(self, thread_id, prompt, workspace):
        await self.readiness(workspace)
        sdk = self._sdk()
        try:
            validate_effective_config(
                await self._effective(self.client, workspace), self.config.sandbox
            )
            turn = await self.threads[thread_id].turn(
                prompt,
                approval_mode=sdk.ApprovalMode.deny_all,
                cwd=str(workspace),
                # Inherit thread policy. SDK enum override loses configured temp exclusions.
                model=self.config.model,
            )
            if turn.thread_id != thread_id:
                raise CodexProtocolError("started turn has wrong provider thread")
            if (thread_id, turn.id) in self.turns:
                raise CodexProtocolError("turn/start joined an already owned turn")
            self.turns[(thread_id, turn.id)] = turn
            return turn.id
        except Exception as exc:
            self._raise(exc)

    async def attach(self, thread_id, turn_id, workspace):
        await self.readiness(workspace)
        try:
            validate_effective_config(
                await self._effective(self.client, workspace), self.config.sandbox
            )
            thread = await self.client.thread_resume(thread_id, **self.thread_options(workspace))
            snapshot = await thread.read(include_turns=True)
            if snapshot.thread.id != thread_id or Path(str(snapshot.thread.cwd.root)) != workspace:
                raise CodexSessionLostError("saved provider thread/workspace association differs")
            if not any(t.id == turn_id for t in snapshot.thread.turns):
                raise CodexSessionLostError("stored turn no longer present")
            if any(
                t.id != turn_id and t.status.value == "inProgress" for t in snapshot.thread.turns
            ):
                raise CodexProtocolError("unowned active turn prevents safe attachment")
            self.threads[thread_id] = thread
        except Exception as exc:
            self._raise(exc)

    async def events(self, thread_id, turn_id):
        try:
            handle = self.turns.get((thread_id, turn_id))
            if handle is not None:
                async for notification in handle.stream():
                    if notification.method not in {
                        "item/completed",
                        "turn/completed",
                        "thread/tokenUsage/updated",
                        "error",
                    }:
                        continue  # never retain hidden reasoning or output deltas
                    payload = notification.payload.model_dump(mode="json", by_alias=True)
                    if notification.method == "item/completed" and payload["item"]["type"] in {
                        "reasoning",
                        "userMessage",
                    }:
                        continue
                    yield {"method": notification.method, "params": redact(payload)}
            else:
                # Fresh client polls the exact existing turn; no new prompt or turn/start.
                while True:
                    snapshot = await self.threads[thread_id].read(include_turns=True)
                    turn = next((t for t in snapshot.thread.turns if t.id == turn_id), None)
                    if turn is None:
                        raise CodexSessionLostError("owned turn no longer available")
                    if turn.status.value != "inProgress":
                        yield {
                            "method": "turn/completed",
                            "params": {
                                "threadId": thread_id,
                                "turn": redact(turn.model_dump(mode="json", by_alias=True)),
                            },
                        }
                        return
                    await asyncio.sleep(0.25)
        except Exception as exc:
            self._raise(exc)

    async def interrupt(self, thread_id, turn_id):
        try:
            handle = self.turns.get((thread_id, turn_id))
            if handle is None:
                # Public exported handle constructor binds exact IDs, never starts a turn.
                handle = self._sdk().AsyncTurnHandle(self.client, thread_id, turn_id)
            await handle.interrupt()
        except Exception as exc:
            self._raise(exc)

    async def close(self):
        if self.client is not None:
            await self.client.close()
            self.client = None


class WorkspaceCodexTransport:
    """One SDK client per exact attempt workspace; repairs cannot inherit permissions."""

    def __init__(self, config, *, factory=SdkCodexTransport):
        self.config, self.factory = config, factory
        self.workspaces, self.owners = {}, {}

    def _workspace(self, workspace):
        workspace = authorized_root(Path(workspace))
        if workspace not in self.workspaces:
            self.workspaces[workspace] = self.factory(self.config)
        return self.workspaces[workspace]

    def _bind(self, thread, workspace):
        child = self._workspace(workspace)
        if thread in self.owners and self.owners[thread] is not child:
            raise CodexProtocolError("provider thread crosses attempt client ownership")
        self.owners[thread] = child
        return child

    async def readiness(self, workspace):
        return await self._workspace(workspace).readiness(workspace)

    async def start_thread(self, workspace):
        child = self._workspace(workspace)
        thread = await child.start_thread(workspace)
        self._bind(thread, workspace)
        return thread

    async def start_turn(self, thread, prompt, workspace):
        return await self._bind(thread, workspace).start_turn(thread, prompt, workspace)

    async def attach(self, thread, turn, workspace):
        await self._bind(thread, workspace).attach(thread, turn, workspace)

    def events(self, thread, turn):
        if thread not in self.owners:
            raise CodexSessionLostError("provider thread not attached")
        return self.owners[thread].events(thread, turn)

    async def interrupt(self, thread, turn):
        if thread not in self.owners:
            raise CodexSessionLostError("provider thread not attached")
        await self.owners[thread].interrupt(thread, turn)

    async def close(self):
        for child in self.workspaces.values():
            await child.close()
