"""Safe provider lifecycle metadata; no prompts, SDK errors or account responses."""

import json
import logging

from .events import redact


def lifecycle(event, *, record=None, task=None, run=None, payload=None, agent_id=None):
    if record is not None:
        run = record.handle
    data = {
        "provider": "openai",
        "event": event,
        "agent_id": run.agent_id if run is not None else agent_id,
        "run_id": run.run_id if run is not None else None,
        "task_id": run.task_id if run is not None else getattr(task, "task_id", None),
        "project_id": getattr(record or task, "project_id", None),
        "phase_id": getattr(record or task, "phase_id", None),
        "attempt": getattr(record or task, "attempt", None),
        "thread_id": getattr(record or payload, "thread_id", None),
        "turn_id": getattr(record or payload, "turn_id", None),
    }
    safe = redact(data)
    logging.getLogger("aipc.adapters.codex").info(json.dumps(safe, sort_keys=True), extra=safe)
