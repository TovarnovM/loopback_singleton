from __future__ import annotations

import loopback_singleton
from loopback_singleton.errors import (
    ConnectionFailedError,
    DaemonConnectionError,
    FactoryMismatchError,
    HandshakeError,
    LoopbackSingletonError,
    ProtocolError,
    RemoteError,
)
from loopback_singleton.serialization import PickleSerializer, get_serializer


def test_pickle_serializer_round_trip_for_nested_structures() -> None:
    serializer = PickleSerializer()
    payload = {
        "items": [
            {"name": "alpha", "values": [1, 2, 3]},
            {"name": "beta", "metadata": {"enabled": True, "ratio": 0.5}},
        ],
        "config": {
            "retries": 3,
            "backoff": (0.1, 0.2, 0.4),
            "tags": {"region": "eu", "service": "worker"},
        },
        "flags": {"debug": False, "dry_run": True},
    }

    encoded = serializer.dumps(payload)
    decoded = serializer.loads(encoded)

    assert decoded == payload


def test_get_serializer_pickle_returns_named_serializer() -> None:
    serializer = get_serializer("pickle")

    assert serializer.name == "pickle"


def test_public_exports_and_root_exception_accessibility() -> None:
    expected_exports = [
        "local_singleton",
        "LocalSingletonService",
        "LoopbackSingletonError",
        "ProtocolError",
        "DaemonConnectionError",
        "ConnectionFailedError",
        "FactoryMismatchError",
        "HandshakeError",
        "RemoteError",
    ]

    assert loopback_singleton.__all__ == expected_exports

    assert loopback_singleton.LoopbackSingletonError is LoopbackSingletonError
    assert loopback_singleton.ProtocolError is ProtocolError
    assert loopback_singleton.DaemonConnectionError is DaemonConnectionError
    assert loopback_singleton.ConnectionFailedError is ConnectionFailedError
    assert loopback_singleton.FactoryMismatchError is FactoryMismatchError
    assert loopback_singleton.HandshakeError is HandshakeError
    assert loopback_singleton.RemoteError is RemoteError
