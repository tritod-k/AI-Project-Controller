import ast
import asyncio
import subprocess
import sys
from pathlib import Path

import pytest
from tests.codex.test_adapter import setup

from aipc.adapters.codex import CodexAdapterConfig


def test_sdk_imports_do_not_escape_provider_package():
    root = Path(__file__).resolve().parents[2] / "src/aipc"
    for path in root.rglob("*.py"):
        if path.is_relative_to(root / "adapters/codex"):
            continue
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert not any(
                    a.name.startswith(("openai_codex", "codex_cli_bin")) for a in node.names
                )
            if isinstance(node, ast.ImportFrom):
                assert not (node.module or "").startswith(("openai_codex", "aipc.adapters.codex"))


def test_core_and_codex_imports_work_without_installed_sdk():
    script = """
import sys
class Block:
 def find_spec(self, name, *args):
  if name.startswith(('openai_codex', 'codex_cli_bin')):
   raise ImportError('SDK intentionally absent')
sys.meta_path.insert(0, Block())
import aipc.adapters.codex, aipc.controller, aipc.review
assert 'openai_codex' not in sys.modules
"""
    assert (
        subprocess.run([sys.executable, "-c", script], capture_output=True, timeout=30).returncode
        == 0
    )


@pytest.mark.parametrize("sandbox", ["full_access", "danger-full-access", "bypass", "unknown"])
def test_unsafe_sandbox_config_rejected(sandbox, tmp_path):
    with pytest.raises(ValueError):
        CodexAdapterConfig(
            primary_workspace=tmp_path, state_root=tmp_path / "state", sandbox=sandbox
        )


@pytest.mark.parametrize("path", ["primary", "parent", "child", "provider"])
def test_overlapping_roots_never_launch(path, tmp_path, canonical_payloads):
    async def exercise():
        worker, task, transport, api = setup(tmp_path, canonical_payloads)
        roots = {
            "primary": worker.primary,
            "parent": tmp_path,
            "child": worker.primary / "child",
            "provider": worker.store.root,
        }
        root = roots[path]
        root.mkdir(exist_ok=True)
        worker.authorize_workspace = lambda t: root
        with pytest.raises(api.CodexAdapterError):
            await worker.start(task.model_copy(update={"workspace": str(root)}))
        assert not transport.calls

    asyncio.run(exercise())


def test_mutated_canonical_task_is_revalidated(tmp_path, canonical_payloads):
    async def exercise():
        worker, task, transport, api = setup(tmp_path, canonical_payloads)
        task.allowed_tools.append(7)
        with pytest.raises(api.CodexAdapterError):
            await worker.start(task)
        assert not transport.calls

    asyncio.run(exercise())
