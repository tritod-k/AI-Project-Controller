"""Agent contracts validate boundaries; the registry never silently replaces IDs."""

import importlib
from types import SimpleNamespace

import pytest
from pydantic import ValidationError


def models():
    return importlib.import_module("aipc.agents.models")


def registry():
    return importlib.import_module("aipc.agents.registry").AgentRegistry()


class StructuralAdapter:
    agent_id = "independent-agent"

    @property
    def capabilities(self):
        return models().AgentCapabilities(approval_events=True)

    async def start(self, task):
        pass

    async def wait(self, run):
        pass

    async def cancel(self, run):
        pass

    async def respond_to_approval(self, run, decision):
        pass


@pytest.mark.parametrize(
    "field",
    [
        "filesystem",
        "shell",
        "git",
        "computer_use",
        "streaming_events",
        "approval_events",
        "resume_session",
        "structured_output",
    ],
)
@pytest.mark.parametrize("value", [1, "true", None])
def test_capability_flags_reject_coercion(field, value):
    with pytest.raises(ValidationError):
        models().AgentCapabilities.model_validate({field: value})


@pytest.mark.parametrize("extra", [{"": True}, {"x": "true"}, {"x": 1}])
def test_capability_extensions_are_named_boolean_features(extra):
    with pytest.raises(ValidationError):
        models().AgentCapabilities(extensions=extra)


def test_capabilities_round_trip_and_extension_isolation():
    data = {"custom_feature": True}
    caps = models().AgentCapabilities(extensions=data)
    data["custom_feature"] = False
    assert caps.extensions["custom_feature"] is True
    assert not caps.filesystem and not caps.shell
    assert models().AgentCapabilities.model_validate_json(caps.model_dump_json()) == caps


@pytest.mark.parametrize("field", ["agent_id", "task_id", "run_id"])
def test_handle_identity_requires_nonblank_values(field):
    data = {"agent_id": "agent", "task_id": "task", "run_id": "run"}
    data[field] = " "
    with pytest.raises(ValidationError):
        models().AgentRunHandle.model_validate(data)


def test_registry_lookup_capabilities_order_and_duplicate_protection():
    reg = registry()
    adapter = StructuralAdapter()
    reg.register(adapter)
    second = StructuralAdapter()
    second.agent_id = "second"
    reg.register(second)
    assert reg.agent_ids() == ("independent-agent", "second")
    assert reg.get("independent-agent") is adapter
    assert reg.capabilities("independent-agent").approval_events
    with pytest.raises(importlib.import_module("aipc.domain.errors").DuplicateAgentError):
        reg.register(StructuralAdapter())
    assert reg.get("independent-agent") is adapter


def test_unknown_registry_lookup_fails_clearly():
    with pytest.raises(importlib.import_module("aipc.domain.errors").AgentNotFoundError):
        registry().get("absent")


@pytest.mark.parametrize("bad", [SimpleNamespace(agent_id="x"), object(), None])
def test_registry_rejects_non_adapters(bad):
    with pytest.raises(importlib.import_module("aipc.domain.errors").AgentError):
        registry().register(bad)


def test_registry_rejects_sync_lifecycle_method():
    adapter = StructuralAdapter()
    adapter.wait = lambda run: None
    with pytest.raises(importlib.import_module("aipc.domain.errors").AgentError):
        registry().register(adapter)


def test_registry_revalidates_mutated_capabilities_and_identity():
    reg = registry()
    adapter = StructuralAdapter()
    reg.register(adapter)
    adapter.agent_id = "changed"
    with pytest.raises(importlib.import_module("aipc.domain.errors").AgentError):
        reg.get("independent-agent")

    class BadCapabilities(StructuralAdapter):
        @property
        def capabilities(self):
            return models().AgentCapabilities().model_copy(update={"shell": "true"})

    with pytest.raises(importlib.import_module("aipc.domain.errors").AgentError):
        reg.register(BadCapabilities())


def test_raw_envelope_preserves_malformed_content_without_canonical_validation():
    raw = models().RawAgentResult(
        run=models().AgentRunHandle(agent_id="a", task_id="t", run_id="r"),
        status=models().RunStatus.COMPLETED,
        payload="not JSON",
    )
    assert raw.payload == "not JSON"
    assert not isinstance(raw, importlib.import_module("aipc.domain").AgentResult)
