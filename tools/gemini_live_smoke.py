"""Opt-in readiness only while real Gemini execution remains unqualified."""

import json
import os

from aipc.adapters.gemini.doctor import runtime_info


def main():
    if os.environ.get("AIPC_GEMINI_LIVE") != "1":
        print("NOT_RUN: set AIPC_GEMINI_LIVE=1 for presence-only readiness")
        return 2
    print(json.dumps(runtime_info(), sort_keys=True))
    print("READ/DENY/WRITE/RESUME/CANCEL/CONTROLLER: UNVERIFIED (no provider execution)")
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
