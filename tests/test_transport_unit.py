from __future__ import annotations

import socket

import pytest

from loopback_singleton import transport
from loopback_singleton.errors import ProtocolError
from loopback_singleton.serialization import PickleSerializer


class SequenceRecvSocket:
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = list(chunks)

    def recv(self, n: int, flags: int = 0) -> bytes:  # noqa: ARG002
        if not self._chunks:
            return b""
        chunk = self._chunks.pop(0)
        if len(chunk) <= n:
            return chunk
        self._chunks.insert(0, chunk[n:])
        return chunk[:n]


class BufferSocket:
    def __init__(self, data: bytes, timeout: float | None = None) -> None:
        self._buffer = bytearray(data)
        self._timeout = timeout
        self.peek_calls = 0

    def recv(self, n: int, flags: int = 0) -> bytes:
        if flags == socket.MSG_PEEK:
            self.peek_calls += 1
            return bytes(self._buffer[:n])
        chunk = bytes(self._buffer[:n])
        del self._buffer[:n]
        return chunk

    def gettimeout(self) -> float | None:
        return self._timeout

    def settimeout(self, timeout: float | None) -> None:
        self._timeout = timeout

    def setblocking(self, flag: bool) -> None:  # noqa: ARG002
        return None


class ProbeSocket:
    def __init__(self, *, timeout: float | None, recv_result: bytes | Exception) -> None:
        self._timeout = timeout
        self._recv_result = recv_result
        self.settimeout_calls: list[float | None] = []
        self.setblocking_calls: list[bool] = []

    def gettimeout(self) -> float | None:
        return self._timeout

    def settimeout(self, timeout: float | None) -> None:
        self._timeout = timeout
        self.settimeout_calls.append(timeout)

    def setblocking(self, flag: bool) -> None:
        self.setblocking_calls.append(flag)

    def recv(self, n: int, flags: int = 0) -> bytes:  # noqa: ARG002
        if isinstance(self._recv_result, Exception):
            raise self._recv_result
        return self._recv_result


def test_recv_exact_collects_data_across_multiple_recv_calls() -> None:
    sock = SequenceRecvSocket([b"ab", b"cd", b"ef"])

    result = transport._recv_exact(sock, 6)

    assert result == b"abcdef"


def test_recv_exact_raises_connection_error_when_socket_closes() -> None:
    sock = SequenceRecvSocket([b"ab", b""])

    with pytest.raises(ConnectionError, match="Socket closed while receiving"):
        transport._recv_exact(sock, 3)


def test_recv_message_decodes_valid_frame() -> None:
    serializer = PickleSerializer()
    payload = serializer.dumps({"ok": True, "value": 7})
    frame = transport._LEN_STRUCT.pack(len(payload)) + payload
    sock = SequenceRecvSocket([frame[:2], frame[2:6], frame[6:]])

    result = transport.recv_message(sock, serializer)

    assert result == {"ok": True, "value": 7}


def test_recv_message_raises_protocol_error_for_too_large_frame() -> None:
    serializer = PickleSerializer()
    oversized_len = transport.MAX_FRAME_BYTES + 1
    sock = SequenceRecvSocket([transport._LEN_STRUCT.pack(oversized_len)])

    with pytest.raises(ProtocolError, match="Frame too large"):
        transport.recv_message(sock, serializer)


def test_recv_message_timeout_returns_none_when_select_times_out(monkeypatch: pytest.MonkeyPatch) -> None:
    serializer = PickleSerializer()
    sock = BufferSocket(b"")
    monkeypatch.setattr(transport.select, "select", lambda r, w, x, t: ([], [], []))

    result = transport.recv_message_timeout(sock, serializer, timeout=0.1)

    assert result is None


def test_recv_message_timeout_reads_full_message_after_peek(monkeypatch: pytest.MonkeyPatch) -> None:
    serializer = PickleSerializer()
    payload = serializer.dumps(("pong", 42))
    frame = transport._LEN_STRUCT.pack(len(payload)) + payload
    sock = BufferSocket(frame)

    monkeypatch.setattr(transport.select, "select", lambda r, w, x, t: ([sock], [], []))

    result = transport.recv_message_timeout(sock, serializer, timeout=0.5)

    assert result == ("pong", 42)
    assert sock.peek_calls >= 2
    assert sock.recv(1) == b""


def test_peer_disconnected_fallback_returns_false_on_blocking_io(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(transport, "_HAS_POLL", False)
    sock = ProbeSocket(timeout=3.0, recv_result=BlockingIOError())

    assert transport._peer_disconnected(sock) is False


def test_peer_disconnected_fallback_returns_true_on_oserror(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(transport, "_HAS_POLL", False)
    sock = ProbeSocket(timeout=4.0, recv_result=OSError("boom"))

    assert transport._peer_disconnected(sock) is True


def test_peer_disconnected_fallback_returns_true_on_empty_peek_and_restores_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(transport, "_HAS_POLL", False)
    sock = ProbeSocket(timeout=5.0, recv_result=b"")

    assert transport._peer_disconnected(sock) is True
    assert sock.setblocking_calls == [False]
    assert sock.settimeout_calls == [5.0]
    assert sock.gettimeout() == 5.0
