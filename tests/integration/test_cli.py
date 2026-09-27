"""Exercise real entry points from an empty working directory."""

import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("entry", ["module", "console"])
def test_installed_help_succeeds_without_side_effects(tmp_path: Path, entry: str) -> None:
    executable = Path(sys.executable).with_name("aipc.exe" if os.name == "nt" else "aipc")
    command = [sys.executable, "-m", "aipc"] if entry == "module" else [str(executable)]
    result = subprocess.run(
        [*command, "--help"], cwd=tmp_path, capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    assert "usage:" in result.stdout
    assert "AI Project Controller" in result.stdout
    assert result.stderr == ""
    assert list(tmp_path.iterdir()) == []


def test_no_arguments_displays_help(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "aipc"], cwd=tmp_path, capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    assert "usage:" in result.stdout


def test_future_command_is_rejected(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "aipc", "run", "example"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert "unrecognized arguments" in result.stderr
    assert list(tmp_path.iterdir()) == []
