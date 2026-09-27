"""Verify the installed package is usable without a checkout import path."""

import importlib


def test_package_imports() -> None:
    package = importlib.import_module("aipc")
    assert package.__name__ == "aipc"
