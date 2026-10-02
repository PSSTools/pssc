"""Timing tests run only when asked for: ``pytest -m perf tests/perf``."""
import pytest


def pytest_collection_modifyitems(config, items):
    if "perf" in (config.getoption("markexpr") or ""):
        return
    skip = pytest.mark.skip(reason="timing: select with -m perf")
    for item in items:
        if item.get_closest_marker("perf") is not None:
            item.add_marker(skip)
