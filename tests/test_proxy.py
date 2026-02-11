from __future__ import annotations

import socket

import pytest

from loopback_singleton.errors import DaemonConnectionError, RemoteError
from loopback_singleton.proxy import Proxy


def _make_proxy() -> tuple[Proxy, socket.socket]:
    sock, peer = socket.socketpair()
    return Proxy(sock=sock, serializer_name="pickle", service_name="svc"), peer


def test_close_is_idempotent() -> None:
    proxy, peer = _make_proxy()
    try:
        proxy.close()
        proxy.close()
        assert proxy._closed is True
    finally:
        peer.close()


def test_context_manager_and_repr_states() -> None:
    proxy, peer = _make_proxy()
    try:
        with proxy as managed:
            assert managed is proxy
            assert "state=open" in repr(proxy)
        assert "state=closed" in repr(proxy)
    finally:
        peer.close()


@pytest.mark.parametrize(
    ("response", "expected", "error"),
    [
        (("OK", {"x": 1}), {"x": 1}, None),
        (("ERR", "boom"), None, RemoteError),
        (("MAYBE", "?"), None, DaemonConnectionError),
    ],
)
def test_call_handles_status_responses(monkeypatch, response, expected, error) -> None:
    proxy, peer = _make_proxy()
    calls: list[tuple] = []

    def fake_send(sock, payload, serializer):
        calls.append(payload)

    def fake_recv(sock, serializer):
        return response

    monkeypatch.setattr("loopback_singleton.proxy.send_message", fake_send)
    monkeypatch.setattr("loopback_singleton.proxy.recv_message", fake_recv)

    try:
        if error is None:
            assert proxy._call("sum", 1, b=2) == expected
        else:
            with pytest.raises(error):
                proxy._call("sum", 1, b=2)
        assert calls == [("CALL", "sum", (1,), {"b": 2})]
    finally:
        proxy.close()
        peer.close()


@pytest.mark.parametrize("method_name", ["_call", "ping_daemon"])
@pytest.mark.parametrize("failing_fn", ["send", "recv"])
def test_oserror_is_wrapped_as_connection_error(monkeypatch, method_name: str, failing_fn: str) -> None:
    proxy, peer = _make_proxy()

    def fake_send(sock, payload, serializer):
        if failing_fn == "send":
            raise OSError("socket write failed")

    def fake_recv(sock, serializer):
        if failing_fn == "recv":
            raise OSError("socket read failed")
        return ("OK", {"alive": True})

    monkeypatch.setattr("loopback_singleton.proxy.send_message", fake_send)
    monkeypatch.setattr("loopback_singleton.proxy.recv_message", fake_recv)

    try:
        method = getattr(proxy, method_name)
        if method_name == "_call":
            with pytest.raises(DaemonConnectionError, match="socket"):
                method("work")
        else:
            with pytest.raises(DaemonConnectionError, match="socket"):
                method()
    finally:
        proxy.close()
        peer.close()


def test_getattr_private_name_raises_attribute_error() -> None:
    proxy, peer = _make_proxy()
    try:
        with pytest.raises(AttributeError):
            proxy.__getattr__("_hidden")
    finally:
        proxy.close()
        peer.close()


def test_getattr_returns_callable_routed_to_call(monkeypatch) -> None:
    proxy, peer = _make_proxy()
    observed: dict[str, object] = {}

    def fake_call(method_name, *args, **kwargs):
        observed["method_name"] = method_name
        observed["args"] = args
        observed["kwargs"] = kwargs
        return "result"

    monkeypatch.setattr(proxy, "_call", fake_call)

    try:
        fn = proxy.compute
        assert callable(fn)
        assert fn(1, test=True) == "result"
        assert observed == {
            "method_name": "compute",
            "args": (1,),
            "kwargs": {"test": True},
        }
    finally:
        proxy.close()
        peer.close()


@pytest.mark.parametrize("method_name", ["_call", "ping_daemon"])
def test_closed_proxy_methods_raise_connection_error(method_name: str) -> None:
    proxy, peer = _make_proxy()
    proxy._closed = True
    try:
        method = getattr(proxy, method_name)
        with pytest.raises(DaemonConnectionError, match="^Proxy is closed$"):
            if method_name == "_call":
                method("method")
            else:
                method()
    finally:
        peer.close()
