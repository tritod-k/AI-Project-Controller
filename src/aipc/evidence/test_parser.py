"""Only supported pytest final summaries; unknown outcomes remain unavailable."""

import re
from typing import Protocol, runtime_checkable

from aipc.domain.errors import TestParseError
from aipc.domain.models import TestSummary


@runtime_checkable
class TestResultParser(Protocol):
    def parse(self, text: str) -> TestSummary: ...


class PytestResultParser:
    def parse(self, text: str) -> TestSummary:
        text = re.sub(r"\x1b\[[0-9;]*m", "", text)
        candidates = [
            line.strip().strip("= ")
            for line in text.splitlines()
            if re.search(r"\bin\s+[0-9.]+s\b", line)
        ]
        if not candidates:
            raise TestParseError("no supported pytest final summary")
        match = re.fullmatch(r"(.+?) in [0-9]+(?:\.[0-9]+)?s(?: \([0-9:]+\))?", candidates[-1])
        if match is None:
            raise TestParseError("unsupported pytest summary")
        body = match[1]
        if body == "no tests ran":
            return TestSummary(total=0, passed=0, failed=0, skipped=0)
        counts = {}
        for token in body.split(", "):
            count = re.fullmatch(r"([0-9]+) (passed|failed|skipped|warnings?)", token)
            if count is None or count[2] in counts:
                raise TestParseError("unsupported or duplicate pytest outcome")
            counts[count[2]] = int(count[1])
        if not any(key in counts for key in ("passed", "failed", "skipped")):
            raise TestParseError("pytest summary has no supported test counts")
        values = {name: counts.get(name, 0) for name in ("passed", "failed", "skipped")}
        return TestSummary(total=sum(values.values()), **values)
