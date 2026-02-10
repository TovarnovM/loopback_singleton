from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from loopback_singleton.daemon import _build_instance, _resolve_factory

TESTS_DIR = Path(__file__).parent
os.environ["PYTHONPATH"] = (
    f"{TESTS_DIR}{os.pathsep}{os.environ.get('PYTHONPATH', '')}".rstrip(os.pathsep)
)
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))


def test_resolve_factory_bad_format_raises_value_error() -> None:
    with pytest.raises(ValueError):
        _resolve_factory("badformat")


def test_resolve_factory_missing_module_raises_module_not_found_error() -> None:
    with pytest.raises(ModuleNotFoundError):
        _resolve_factory("missing_module:Foo")


def test_resolve_factory_missing_attr_raises_attribute_error() -> None:
    with pytest.raises(AttributeError):
        _resolve_factory("fixtures_pkg.services:MissingAttr")


def test_build_instance_positive_case_returns_counter_with_methods() -> None:
    instance = _build_instance("fixtures_pkg.services:TestCounter")

    assert hasattr(instance, "ping")
    assert callable(instance.ping)
    assert hasattr(instance, "inc")
    assert callable(instance.inc)


def test_build_instance_supports_args_kwargs() -> None:
    instance = _build_instance("fixtures_pkg.services:make_counter", factory_args=(10,), factory_kwargs={"step": 2})
    assert instance.inc() == 12
