"""Help-only CLI foundation; operational commands belong to later phases."""

import argparse
from collections.abc import Sequence


def main(argv: Sequence[str] | None = None) -> int:
    """Display bootstrap help and reject unsupported operations."""
    parser = argparse.ArgumentParser(
        prog="aipc",
        description="AI Project Controller - Phase 00 repository bootstrap.",
        epilog="Operational commands will be added in their authorized implementation phases.",
    )
    parser.parse_args(argv)
    parser.print_help()
    return 0
