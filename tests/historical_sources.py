"""Run pinned historical-source tests when their original snapshot is installed."""

import importlib
import importlib.util

import pytest


def import_historical_snapshot(name):
    package = name.partition(".")[0]
    if importlib.util.find_spec(package) is None:
        pytest.skip("Historical source snapshot is absent from this checkout")
    return importlib.import_module(name)
