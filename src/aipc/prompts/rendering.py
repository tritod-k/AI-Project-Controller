"""Small deterministic Markdown helpers; supplied text remains literal data."""

import re
from collections.abc import Iterable


def literal(text: str) -> str:
    size = max((len(match.group()) for match in re.finditer(r"`+", text)), default=0)
    fence = "`" * max(3, size + 1)
    return f"{fence}text\n{text}\n{fence}"


def values(items: Iterable[str], empty: str = "Not supplied.") -> str:
    blocks = [literal(item) for item in items]
    return "\n\n".join(blocks) if blocks else empty


def sections(items: Iterable[tuple[str, str]]) -> str:
    return "\n\n".join(f"## {name}\n\n{body}" for name, body in items) + "\n"
