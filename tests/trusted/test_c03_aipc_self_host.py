"""Focused operator-owned C03 self-host isolation criterion on the real AIPC layout."""

from __future__ import annotations

import sys
from pathlib import Path


def _is_candidate_conftest(obj: object, snapshot_root: Path) -> bool:
    """Return True if *obj* is a module/plugin loaded from a conftest.py under *snapshot_root*."""
    mod_file = getattr(obj, "__file__", None)
    if not isinstance(mod_file, str) or not mod_file:
        return False
    resolved = Path(mod_file).resolve()
    if resolved.name.lower() != "conftest.py":
        return False
    return resolved.is_relative_to(snapshot_root)


def test_real_aipc_conftest_is_inactive(pytestconfig: object) -> None:
    """Verify real AIPC conftest.py files exist in the snapshot yet remain completely inactive."""
    snapshot_root = Path(__file__).resolve().parents[2]

    harness_file = snapshot_root / "src" / "aipc" / "review" / "trusted_acceptance_harness.py"
    root_conftest = snapshot_root / "tests" / "conftest.py"
    approvals_conftest = snapshot_root / "tests" / "approvals" / "conftest.py"
    assert harness_file.is_file()
    assert root_conftest.is_file()
    assert approvals_conftest.is_file()

    assert pytestconfig.getoption("noconftest") is True
    assert pytestconfig.getini("filterwarnings") == ["default"]

    pm = pytestconfig.pluginmanager
    assert pm.get_plugin("cacheprovider") is None
    assert not pm.has_plugin("cacheprovider")

    for mod in tuple(sys.modules.values()):
        assert not _is_candidate_conftest(mod, snapshot_root)

    for plugin in tuple(pm.get_plugins()):
        assert not _is_candidate_conftest(plugin, snapshot_root)

    for conftest_plugin in tuple(pm._conftest_plugins):
        assert not _is_candidate_conftest(conftest_plugin, snapshot_root)
