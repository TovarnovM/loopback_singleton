"""Serialization helpers for protocol messages."""

from __future__ import annotations

import pickle
from typing import Any


class PickleSerializer:
    name = "pickle"

    def dumps(self, obj: Any) -> bytes:
        return pickle.dumps(obj, protocol=pickle.HIGHEST_PROTOCOL)

    def loads(self, data: bytes) -> Any:
        return pickle.loads(data)


def get_serializer(name: str) -> PickleSerializer:
    if name == "pickle":
        return PickleSerializer()
    if name == "msgpack":
        raise NotImplementedError("msgpack serializer is not implemented in MVP")
    raise ValueError(f"Unknown serializer: {name}")
