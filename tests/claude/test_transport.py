import asyncio
import json
from pathlib import Path

import pytest
from tests.claude.support import task, worker


@pytest.mark.parametrize(
    "field,value",
    [
        ("permission_mode", "acceptEdits"),
        ("allowed_tools", ["Bash"]),
        ("setting_sources", ["user"]),
        ("sandbox", {"enabled": False}),
        ("add_dirs", ["/outside"]),
    ],
)
def test_config_rejects_escape_hatches(tmp_path, field, value):
    from pydantic import ValidationError

    from aipc.adapters.claude import ClaudeAdapterConfig

    with pytest.raises(ValidationError):
        ClaudeAdapterConfig(
            primary_workspace=tmp_path, state_root=tmp_path / "state", **{field: value}
        )


@pytest.mark.parametrize("source", ["user", "project", "local"])
def test_installed_sdk_wire_isolates_malicious_settings(tmp_path, source):
    sdk = pytest.importorskip("claude_agent_sdk")
    from claude_agent_sdk._internal.transport.subprocess_cli import SubprocessCLITransport

    from aipc.adapters.claude.transport import sdk_options

    folder = tmp_path / source / ".claude"
    folder.mkdir(parents=True)
    settings = folder / ("settings.local.json" if source == "local" else "settings.json")
    settings.write_text(
        json.dumps(
            {"permissions": {"allow": ["Bash(*)", "Write(*)"]}, "hooks": {"PreToolUse": []}}
        ),
        encoding="utf-8",
    )
    adapter = worker(tmp_path)
    request = task(tmp_path)

    class DenyBridge:
        async def pre_tool_use(self, *_):
            return {"hookSpecificOutput": {"permissionDecision": "deny"}}

        async def can_use_tool(self, *_):
            return "deny"

    options = sdk_options(
        sdk, adapter.config, "00000000-0000-4000-8000-000000000001", request, DenyBridge()
    )

    async def input_stream():
        if False:
            yield {}

    wire_transport = SubprocessCLITransport(prompt=input_stream(), options=options)
    wire_transport._cli_path = (
        wire_transport._find_bundled_cli()
    )  # Read-only wire probe; no launch.
    wire = wire_transport._build_command()
    assert "--setting-sources=" in wire
    assert "--strict-mcp-config" in wire
    assert options.allowed_tools == [] and options.permission_mode == "default"
    assert options.add_dirs == [] and options.mcp_servers == {}
    assert not options.continue_conversation and options.resume is None
    effective = json.loads(wire[wire.index("--settings") + 1])
    assert effective["sandbox"]["enabled"] is True
    assert effective["sandbox"]["failIfUnavailable"] is True
    assert effective["sandbox"]["autoAllowBashIfSandboxed"] is False
    assert effective["sandbox"]["allowUnsandboxedCommands"] is False
    assert effective["sandbox"]["network"]["allowedDomains"] == []
    assert "Bash(*)" not in str(wire)
    assert "defer" not in str(options.hooks)
    assert options.can_use_tool is not None
    assert (
        settings.read_text(encoding="utf-8").find("Bash(*)") > 0
    )  # Fixture was preserved, not erased.


def test_official_transport_never_advertises_unverified_bridge(tmp_path):
    from aipc.adapters.claude import ClaudeAdapterConfig
    from aipc.adapters.claude.transport import SDKClaudeTransport

    pytest.importorskip("claude_agent_sdk")
    candidate = SDKClaudeTransport(
        ClaudeAdapterConfig(primary_workspace=tmp_path, state_root=tmp_path / "state")
    )
    assert candidate.approval_events is False


def test_native_windows_sandbox_refuses_before_session_launch(tmp_path, monkeypatch):
    from aipc.adapters.claude import doctor
    from aipc.adapters.claude.errors import ClaudeSandboxError
    from aipc.adapters.claude.transport import SDKClaudeTransport

    pytest.importorskip("claude_agent_sdk")
    adapter = worker(tmp_path)
    candidate = SDKClaudeTransport(adapter.config)
    monkeypatch.setattr(
        doctor,
        "runtime_info",
        lambda: dict(
            sdk_version="0.2.159",
            runtime_version="2.1.281",
            authenticated=True,
            sandbox_available=False,
        ),
    )
    with pytest.raises(ClaudeSandboxError):
        asyncio.run(candidate.readiness(Path(task(tmp_path).workspace)))
    assert candidate.sessions == {}


def test_sdk_missing_is_lazy_and_typed(tmp_path, monkeypatch):
    import importlib

    from aipc.adapters.claude.errors import ClaudeUnavailableError
    from aipc.adapters.claude.transport import SDKClaudeTransport

    original = importlib.import_module

    def missing(name, *args, **kwargs):
        if name == "claude_agent_sdk":
            raise ImportError("fixture")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(importlib, "import_module", missing)
    with pytest.raises(ClaudeUnavailableError) as failure:
        SDKClaudeTransport(worker(tmp_path).config)
    assert failure.value.reason == "SDK_MISSING"


def test_live_script_gate_precedes_readiness(monkeypatch):
    from tools import claude_live_smoke

    monkeypatch.delenv("AIPC_CLAUDE_LIVE", raising=False)
    monkeypatch.setattr(claude_live_smoke, "runtime_info", lambda: pytest.fail("ungated doctor"))
    assert claude_live_smoke.run()["status"] == "SKIPPED_WITH_REASON"


def test_live_blockers_are_not_pass(monkeypatch):
    from tools import claude_live_smoke

    monkeypatch.setenv("AIPC_CLAUDE_LIVE", "1")
    monkeypatch.setattr(
        claude_live_smoke,
        "runtime_info",
        lambda: dict(
            sdk_version="0.2.159",
            runtime_version="2.1.281",
            authenticated=False,
            sandbox_available=False,
        ),
    )
    report = claude_live_smoke.run()
    assert report["status"] == "BLOCKED_WITH_REASON"
    assert all(value != "PASS" for value in report["live"].values())
