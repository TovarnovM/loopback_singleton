"""TCP framing and message transport."""

from __future__ import annotations

import socket
import struct
from typing import Any

from .serialization import PickleSerializer

_LEN_STRUCT = struct.Struct("!I")


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < n:
        chunk = sock.recv(n - len(chunks))
        if not chunk:
            raise ConnectionError("Socket closed while receiving")
        chunks.extend(chunk)
    return bytes(chunks)


def send_message(sock: socket.socket, obj: Any, serializer: PickleSerializer) -> None:
    payload = serializer.dumps(obj)
    sock.sendall(_LEN_STRUCT.pack(len(payload)))
    sock.sendall(payload)


def recv_message(sock: socket.socket, serializer: PickleSerializer) -> Any:
    raw_len = _recv_exact(sock, _LEN_STRUCT.size)
    (payload_len,) = _LEN_STRUCT.unpack(raw_len)
    payload = _recv_exact(sock, payload_len)
    return serializer.loads(payload)
