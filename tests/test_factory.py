from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from loopback_singleton.factory import normalize_factory

TESTS_DIR = Path(__file__).parent
os.environ["PYTHONPATH"] = (
    f"{TESTS_DIR}{os.pathsep}{os.environ.get('PYTHONPATH', '')}".rstrip(os.pathsep)
)
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from fixtures_pkg.services import TestCounter, make_counter  # noqa: E402


def test_normalize_factory_accepts_import_string() -> None:
    assert normalize_factory("fixtures_pkg.services:TestCounter") == "fixtures_pkg.services:TestCounter"


def test_normalize_factory_accepts_module_level_class() -> None:
    assert normalize_factory(TestCounter) == "fixtures_pkg.services:TestCounter"


def test_normalize_factory_accepts_module_level_function() -> None:
    assert normalize_factory(make_counter) == "fixtures_pkg.services:make_counter"


def test_normalize_factory_rejects_lambda() -> None:
    with pytest.raises(TypeError, match="Factory must be importable"):
        normalize_factory(lambda: object())


def test_normalize_factory_rejects_nested_function() -> None:
    def nested() -> object:
        return object()

    with pytest.raises(TypeError, match="Factory must be importable"):
        normalize_factory(nested)
