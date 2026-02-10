from __future__ import annotations

from uuid import uuid4

import pytest

from loopback_singleton.api import local_singleton


def test_local_singleton_requires_factory_import_string() -> None:
    with pytest.raises(TypeError, match="MVP requires factory as import string"):
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
