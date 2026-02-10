"""TCP framing and message transport."""

from __future__ import annotations

import socket
import struct
import time
import select
from typing import Any

from .errors import ProtocolError
from .serialization import PickleSerializer

_LEN_STRUCT = struct.Struct("!I")
MAX_FRAME_BYTES = 16 * 1024 * 1024


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
    if payload_len < 0:
        raise ProtocolError(f"Invalid frame length: {payload_len}")
    if payload_len > MAX_FRAME_BYTES:
        raise ProtocolError(
            f"Frame too large: {payload_len} bytes exceeds max {MAX_FRAME_BYTES} bytes"
        )
    payload = _recv_exact(sock, payload_len)
    return serializer.loads(payload)


def recv_message_timeout(
    sock: socket.socket, serializer: PickleSerializer, timeout: float
) -> Any | None:
    """Receive a full framed message within timeout without consuming partial frames.

    Returns None if no complete frame becomes available before timeout.
    """

    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None

        readable, _, _ = select.select([sock], [], [], remaining)
        if not readable:
            return None

        try:
            raw_len = sock.recv(_LEN_STRUCT.size, socket.MSG_PEEK)
        except socket.timeout:
            continue
        if not raw_len:
            raise ConnectionError("Socket closed while receiving")
        if len(raw_len) < _LEN_STRUCT.size:
            continue

        (payload_len,) = _LEN_STRUCT.unpack(raw_len)
        if payload_len < 0:
            raise ProtocolError(f"Invalid frame length: {payload_len}")
        if payload_len > MAX_FRAME_BYTES:
            raise ProtocolError(
                f"Frame too large: {payload_len} bytes exceeds max {MAX_FRAME_BYTES} bytes"
            )

        frame_len = _LEN_STRUCT.size + payload_len
        try:
            frame = sock.recv(frame_len, socket.MSG_PEEK)
        except socket.timeout:
            continue
        if not frame:
            raise ConnectionError("Socket closed while receiving")
        if len(frame) < frame_len:
            continue

        return recv_message(sock, serializer)
