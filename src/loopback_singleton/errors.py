"""Custom exception types for loopback_singleton."""


class LoopbackSingletonError(Exception):
    """Base exception for the package."""


class DaemonConnectionError(LoopbackSingletonError):
    """Raised when a client cannot connect/handshake with the daemon."""


class ConnectionFailedError(DaemonConnectionError):
    """Raised when a client cannot connect to the daemon."""


class HandshakeError(DaemonConnectionError):
    """Raised when protocol handshake fails."""


class ProtocolError(LoopbackSingletonError):
    """Raised when wire protocol frames/messages are invalid."""


class RemoteError(LoopbackSingletonError):
    """Raised when the remote daemon reports an execution error."""
