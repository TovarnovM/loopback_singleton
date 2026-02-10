from __future__ import annotations

from uuid import uuid4

import pytest

from loopback_singleton.api import local_singleton


def test_local_singleton_allows_callable_factory() -> None:
    svc = local_singleton(name=uuid4().hex, factory=dict)
    assert svc.factory == "builtins:dict"


def test_local_singleton_rejects_non_importable_callable() -> None:
    with pytest.raises(TypeError, match="Factory must be importable"):
        local_singleton(name=uuid4().hex, factory=lambda: object())


def test_local_singleton_rejects_unknown_serializer() -> None:
    with pytest.raises(ValueError, match="Unknown serializer: json"):
        local_singleton(
            name=uuid4().hex,
            factory="fixtures_pkg.services:TestCounter",
            serializer="json",
        )


def test_local_singleton_msgpack_not_implemented() -> None:
    with pytest.raises(NotImplementedError, match="msgpack serializer is not implemented"):
        local_singleton(
            name=uuid4().hex,
            factory="fixtures_pkg.services:TestCounter",
            serializer="msgpack",
        )
