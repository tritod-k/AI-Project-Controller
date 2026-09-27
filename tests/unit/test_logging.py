"""Verify application logging output and isolation."""

import importlib
import io
import json
import logging
from datetime import datetime

import pytest


def test_log_output_has_utc_timestamp_and_empty_context() -> None:
    module = importlib.import_module("aipc.observability.logger")
    stream = io.StringIO()
    logger = module.configure_logging(stream=stream)
    logger.info("Bootstrap ready")
    record = json.loads(stream.getvalue())
    assert record["message"] == "Bootstrap ready"
    assert record["level"] == "INFO"
    assert record["logger"] == "aipc"
    assert datetime.fromisoformat(record["timestamp"]).utcoffset().total_seconds() == 0
    for field in (
        "project_id",
        "run_id",
        "phase_id",
        "attempt",
        "task_id",
        "agent_id",
        "provider",
        "event",
    ):
        assert record[field] is None


def test_log_output_preserves_supplied_context() -> None:
    module = importlib.import_module("aipc.observability.logger")
    stream = io.StringIO()
    module.configure_logging(stream=stream)
    logging.getLogger("aipc.bootstrap").info(
        "Ready",
        extra={
            "project_id": "example",
            "run_id": "run-1",
            "phase_id": "00",
            "attempt": 1,
            "task_id": "task-1",
            "agent_id": "worker-1",
            "provider": "unassigned",
            "event": "BOOTSTRAP_READY",
        },
    )
    record = json.loads(stream.getvalue())
    assert record["project_id"] == "example"
    assert record["run_id"] == "run-1"
    assert record["phase_id"] == "00"
    assert record["attempt"] == 1
    assert record["task_id"] == "task-1"
    assert record["agent_id"] == "worker-1"
    assert record["provider"] == "unassigned"
    assert record["event"] == "BOOTSTRAP_READY"


def test_log_level_filters_lower_severity() -> None:
    module = importlib.import_module("aipc.observability.logger")
    stream = io.StringIO()
    logger = module.configure_logging("WARNING", stream=stream)
    logger.info("Filtered")
    logger.warning("Visible")
    records = stream.getvalue().splitlines()
    assert len(records) == 1
    assert json.loads(records[0])["message"] == "Visible"


def test_reconfiguration_does_not_duplicate_output() -> None:
    module = importlib.import_module("aipc.observability.logger")
    old_stream, new_stream = io.StringIO(), io.StringIO()
    module.configure_logging(stream=old_stream)
    logger = module.configure_logging(stream=new_stream)
    logger.info("Once")
    assert old_stream.getvalue() == ""
    assert len(new_stream.getvalue().splitlines()) == 1


def test_configuration_does_not_change_root_logging() -> None:
    module = importlib.import_module("aipc.observability.logger")
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    module.configure_logging(stream=io.StringIO()).info("Isolated")
    assert root.handlers == handlers
    assert root.level == level


def test_invalid_level_preserves_existing_configuration() -> None:
    module = importlib.import_module("aipc.observability.logger")
    stream = io.StringIO()
    logger = module.configure_logging(stream=stream)
    with pytest.raises(ValueError, match="level"):
        module.configure_logging("INVALID", stream=io.StringIO())
    logger.info("Still configured")
    assert json.loads(stream.getvalue())["message"] == "Still configured"
