import asyncio
import importlib
from types import SimpleNamespace

import pytest


def test_denial_guard_never_accepts_provider_escalation():
    module = importlib.import_module("aipc.adapters.codex.transport")
    for method in ("item/commandExecution/requestApproval", "item/fileChange/requestApproval"):
        assert module.deny_escalation(method, {"anything": "untrusted"}) == {"decision": "decline"}
    with pytest.raises(module.CodexProtocolError):
        module.deny_escalation("unknown/requestApproval", {})


def test_guard_installed_before_sdk_initialization():
    module = importlib.import_module("aipc.adapters.codex.transport")
    sync = SimpleNamespace(_approval_handler=lambda *args: {"decision": "accept"})
    client = SimpleNamespace(_client=SimpleNamespace(_sync=sync))
    module.install_denial_guard(client)
    assert sync._approval_handler("item/fileChange/requestApproval", {}) == {"decision": "decline"}
    with pytest.raises(module.CodexUnsupportedVersionError):
        module.install_denial_guard(SimpleNamespace())


def test_sdk_options_never_inherit_auto_review(tmp_path, monkeypatch):
    async def exercise():
        module = importlib.import_module("aipc.adapters.codex.transport")
        config_api = importlib.import_module("aipc.adapters.codex.config")
        config = config_api.CodexAdapterConfig(
            primary_workspace=tmp_path, state_root=tmp_path / "state"
        )
        transport = module.SdkCodexTransport(config)
        monkeypatch.setattr(
            transport,
            "_sdk",
            lambda: SimpleNamespace(
                ApprovalMode=SimpleNamespace(deny_all=SimpleNamespace(value="deny_all")),
                Sandbox=SimpleNamespace(workspace_write=SimpleNamespace(value="workspace-write")),
            ),
        )
        options = transport.thread_options(tmp_path)
        assert options["approval_mode"].value == "deny_all"
        assert options["sandbox"].value == "workspace-write"
        assert options["config"]["sandbox_workspace_write"]["network_access"] is False
        assert options["config"]["sandbox_workspace_write"]["writable_roots"] == []

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "change",
    ["approval", "reviewer", "network", "roots", "mcp", "hooks", "tmpdir", "slash_tmp", "web"],
)
def test_effective_runtime_policy_must_not_expand_authority(change):
    module = importlib.import_module("aipc.adapters.codex.transport")
    c = {
        "approval_policy": "never",
        "approvals_reviewer": "user",
        "sandbox_mode": "workspace-write",
        "sandbox_workspace_write": {
            "network_access": False,
            "writable_roots": [],
            "exclude_tmpdir_env_var": True,
            "exclude_slash_tmp": True,
        },
        "web_search": "disabled",
        "mcp_servers": {},
        "hooks": {},
    }
    if change == "approval":
        c["approval_policy"] = "on-request"
    elif change == "reviewer":
        c["approvals_reviewer"] = "auto_review"
    elif change == "network":
        c["sandbox_workspace_write"]["network_access"] = True
    elif change == "roots":
        c["sandbox_workspace_write"]["writable_roots"] = ["/outside"]
    elif change == "mcp":
        c["mcp_servers"] = {"server": {"enabled": True}}
    elif change == "hooks":
        c["hooks"] = {"PreToolUse": [{"command": "external"}]}
    elif change == "web":
        c["web_search"] = "live"
    else:
        key = "exclude_tmpdir_env_var" if change == "tmpdir" else "exclude_slash_tmp"
        c["sandbox_workspace_write"][key] = False
    with pytest.raises(module.CodexProtocolError):
        module.validate_effective_config(c, "workspace_write")


def test_mcp_overrides_use_runtime_key_syntax_and_reject_ambiguous_names():
    module = importlib.import_module("aipc.adapters.codex.transport")
    assert module.mcp_disables({"fixture": {}}) == ("mcp_servers.fixture.enabled=false",)
    for name in ('quoted"key', "dotted.key", "line\nbreak"):
        with pytest.raises(module.CodexProtocolError):
            module.mcp_disables({name: {}})
