"""Opt-in readiness receipts; never downgrades sandbox or self-runs the AIPC checkout."""

import json
import os

from aipc.adapters.claude.doctor import runtime_info


def run():
    names = ("AUTH/RUNTIME", "READ", "WRITE", "DENY", "RESUME", "CANCEL", "CONTROLLER")
    if os.environ.get("AIPC_CLAUDE_LIVE") != "1":
        return {
            "status": "SKIPPED_WITH_REASON",
            "reason": "AIPC_CLAUDE_LIVE=1 required",
            "live": {name: "UNVERIFIED" for name in names},
        }
    info = runtime_info()
    reasons = []
    if not info.get("sandbox_available"):
        reasons.append("required official sandbox unavailable (native Windows unsupported)")
    if not info.get("authenticated"):
        reasons.append("AUTH_REQUIRED; no interactive login attempted")
    if not info.get("approval_events"):
        reasons.append("official same-session tool bridge lacks real-runtime compatibility proof")
    # Until this gate is satisfied there are no paid calls, even read-only provider tasks.
    # This script is deliberately not a disguised fake live execution suite.
    return {
        "status": "BLOCKED_WITH_REASON",
        "reason": "; ".join(reasons),
        "runtime": info,
        "live": {
            name: "BLOCKED_WITH_REASON" if name == "AUTH/RUNTIME" else "UNVERIFIED / NOT_RUN"
            for name in names
        },
        "paid_calls": 0,
        "mutation_calls": 0,
    }


if __name__ == "__main__":
    print(json.dumps(run(), indent=2))
