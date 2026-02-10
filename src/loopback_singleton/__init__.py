from .api import LocalSingletonService, local_singleton
from .errors import (
    ConnectionFailedError,
    DaemonConnectionError,
    FactoryMismatchError,
    HandshakeError,
    LoopbackSingletonError,
    ProtocolError,
    RemoteError,
)

__all__ = [
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
