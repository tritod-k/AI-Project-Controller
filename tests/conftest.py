"""Literal, independent canonical payload fixtures shared by model/schema tests."""

import json
from pathlib import Path
from typing import Any

import pytest


@pytest.fixture
def canonical_payloads() -> dict[str, dict[str, Any]]:
    directory = Path(__file__).parent / "fixtures" / "canonical"
    return {
        name: json.loads((directory / filename).read_text(encoding="utf-8"))
        for name, filename in {
            "TaskRequest": "task_request.json",
            "AgentResult": "agent_result.json",
            "ReviewResult": "review_result.json",
            "ApprovalRequest": "approval_request.json",
        }.items()
    }
