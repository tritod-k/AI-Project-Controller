import asyncio
import importlib


def test_repair_workspaces_get_separate_clients_and_threads(tmp_path):
    async def exercise():
        module = importlib.import_module("aipc.adapters.codex.transport")
        from tests.codex.support import FakeTransport

        from aipc.adapters.codex import CodexAdapterConfig

        made = []

        class Child(FakeTransport):
            async def start_thread(self, workspace):
                await super().start_thread(workspace)
                return workspace.name

        def factory(config):
            child = Child()
            made.append(child)
            return child

        pool = module.WorkspaceCodexTransport(
            CodexAdapterConfig(primary_workspace=tmp_path, state_root=tmp_path / "unused"),
            factory=factory,
        )
        first, second = tmp_path / "attempt-1", tmp_path / "attempt-2"
        first.mkdir()
        second.mkdir()
        assert await pool.start_thread(first) == "attempt-1"
        assert await pool.start_thread(second) == "attempt-2"
        assert len(made) == 2
        await pool.close()
        assert all("close" in child.calls for child in made)

    asyncio.run(exercise())
