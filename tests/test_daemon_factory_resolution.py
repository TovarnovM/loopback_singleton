from __future__ import annotations

import sys
from pathlib import Path

import pytest

from loopback_singleton.daemon import _build_instance, _resolve_factory


def test_resolve_factory_bad_format_raises_value_error() -> None:
    with pytest.raises(ValueError):
        _resolve_factory("badformat")


def test_resolve_factory_missing_module_raises_module_not_found_error() -> None:
    with pytest.raises(ModuleNotFoundError):
        _resolve_factory("missing_module:Foo")


def test_resolve_factory_missing_attr_raises_attribute_error() -> None:
    tests_dir = str(Path(__file__).parent)
    if tests_dir not in sys.path:
        sys.path.insert(0, tests_dir)

    with pytest.raises(AttributeError):
        _resolve_factory("fixtures_pkg.services:MissingAttr")


def test_build_instance_positive_case_returns_counter_with_methods() -> None:
    tests_dir = str(Path(__file__).parent)
    if tests_dir not in sys.path:
        sys.path.insert(0, tests_dir)

    instance = _build_instance("fixtures_pkg.services:TestCounter")

    assert hasattr(instance, "ping")
    assert callable(instance.ping)
    assert hasattr(instance, "inc")
    assert callable(instance.inc)
