"""Client-side proxy for remote method calls."""

from __future__ import annotations

import socket
import threading
import weakref
from typing import Any, Callable

from .errors import DaemonConnectionError, RemoteError
from .serialization import get_serializer
from .transport import recv_message, send_message


class Proxy:
    def __init__(self, sock: socket.socket, serializer_name: str):
        self._sock = sock
        self._serializer = get_serializer(serializer_name)
        self._closed = False
        self._io_lock = threading.Lock()
        self._finalizer = weakref.finalize(self, Proxy._finalize_socket, sock)

    @staticmethod
    def _finalize_socket(sock: socket.socket) -> None:
        try:
            sock.close()
        except OSError:
            pass

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._finalizer()

    def __enter__(self) -> "Proxy":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def __del__(self) -> None:
        self.close()

    def _call(self, method_name: str, *args: Any, **kwargs: Any) -> Any:
        if self._closed:
            raise DaemonConnectionError("Proxy is closed")
        try:
            with self._io_lock:
                send_message(self._sock, ("CALL", method_name, args, kwargs), self._serializer)
                resp = recv_message(self._sock, self._serializer)
        except OSError as exc:
            raise DaemonConnectionError(str(exc)) from exc
        status = resp[0]
        if status == "OK":
            return resp[1]
        if status == "ERR":
            raise RemoteError(resp[1])
        raise DaemonConnectionError(f"Unknown response: {resp!r}")

    def ping_daemon(self) -> dict[str, Any]:
        if self._closed:
            raise DaemonConnectionError("Proxy is closed")
        try:
            with self._io_lock:
                send_message(self._sock, ("PING",), self._serializer)
                resp = recv_message(self._sock, self._serializer)
        except OSError as exc:
            raise DaemonConnectionError(str(exc)) from exc
        if resp[0] == "OK":
            return resp[1]
        raise DaemonConnectionError(f"Bad ping response: {resp!r}")

    def __getattr__(self, name: str) -> Callable[..., Any]:
        if name.startswith("_"):
            raise AttributeError(name)

        def remote_method(*args: Any, **kwargs: Any) -> Any:
            return self._call(name, *args, **kwargs)

        return remote_method
