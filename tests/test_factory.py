from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from loopback_singleton.factory import compute_factory_id, normalize_factory

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


def test_compute_factory_id_is_order_independent_for_kwargs() -> None:
    factory_id_a = compute_factory_id(
        "fixtures_pkg.services:make_counter",
        (),
        {"a": 1, "b": 2},
    )
    factory_id_b = compute_factory_id(
        "fixtures_pkg.services:make_counter",
        (),
        {"b": 2, "a": 1},
    )

    assert factory_id_a == factory_id_b


def test_compute_factory_id_is_order_independent_for_nested_kwargs_dicts() -> None:
    factory_id_a = compute_factory_id(
        "fixtures_pkg.services:make_counter",
        (),
        {"cfg": {"x": 1, "y": [3, {"a": 1, "b": 2}]}, "z": 9},
    )
    factory_id_b = compute_factory_id(
        "fixtures_pkg.services:make_counter",
        (),
        {"z": 9, "cfg": {"y": [3, {"b": 2, "a": 1}], "x": 1}},
    )

    assert factory_id_a == factory_id_b
