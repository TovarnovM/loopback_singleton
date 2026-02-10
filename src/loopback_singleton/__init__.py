from .api import LocalSingletonService, local_singleton
from .errors import (
    ConnectionFailedError,
    DaemonConnectionError,
    HandshakeError,
    LoopbackSingletonError,
    RemoteError,
)

__all__ = [
    "local_singleton",
    "LocalSingletonService",
    "LoopbackSingletonError",
    "DaemonConnectionError",
    "ConnectionFailedError",
    "HandshakeError",
    "RemoteError",
]
