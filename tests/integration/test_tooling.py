"""Guard blueprint documents against repository-wide formatter mutations."""

import subprocess
import sys
from pathlib import Path


def test_formatter_preserves_markdown_blueprints(tmp_path: Path) -> None:
    blueprint = tmp_path / "blueprint.md"
    original = b"# Blueprint\n\n```python\ndef future_contract():\n    ...\n```\n"
    blueprint.write_bytes(original)
    config = Path(__file__).resolve().parents[2] / "pyproject.toml"
    result = subprocess.run(
        [sys.executable, "-m", "ruff", "format", "--config", str(config), str(tmp_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert blueprint.read_bytes() == original
