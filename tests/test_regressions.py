from __future__ import annotations

import os
import pickle
import socket
import subprocess
import sys
import time
import uuid
import stat
import importlib

import pytest
from pathlib import Path

from loopback_singleton import local_singleton
from loopback_singleton.api import LocalSingletonService
from loopback_singleton.errors import (
    ConnectionFailedError,
    DaemonConnectionError,
    HandshakeError,
)
from loopback_singleton.runtime import ensure_auth_token, get_runtime_paths, read_runtime, remove_runtime
from loopback_singleton.serialization import get_serializer
from loopback_singleton.transport import recv_message, send_message
from loopback_singleton.version import PROTOCOL_VERSION

FACTORY = "fixtures_pkg.services:TestCounter"


def _wait_for_runtime(name: str, timeout: float = 5.0) -> dict:
    paths = get_runtime_paths(name)
    deadline = time.time() + timeout
    while time.time() < deadline:
        if paths.runtime_file.exists():
            return pickle.loads(paths.runtime_file.read_bytes())
        time.sleep(0.05)
    raise AssertionError("runtime file not created")


def test_connection_error_compatibility_classes() -> None:
    assert issubclass(ConnectionFailedError, DaemonConnectionError)
    assert issubclass(HandshakeError, DaemonConnectionError)


def test_connect_once_missing_runtime_raises_connection_failed() -> None:
    name = f"missing-runtime-{uuid.uuid4().hex}"
    paths = get_runtime_paths(name)
    remove_runtime(paths)

    svc = LocalSingletonService(name=name, factory=FACTORY, idle_ttl=1.0)
    try:
        svc._connect_once()
    except ConnectionFailedError:
        pass
    else:
        raise AssertionError("Expected ConnectionFailedError for missing runtime metadata")


def test_daemon_startup_grace_before_first_connection() -> None:
    name = f"grace-{uuid.uuid4().hex}"
    paths = get_runtime_paths(name)
    token = ensure_auth_token(paths)
    env = os.environ.copy()
    test_path = str(Path(__file__).parent)
    src_path = str(Path(__file__).parent.parent / "src")
    env["PYTHONPATH"] = os.pathsep.join([src_path, test_path, env.get("PYTHONPATH", "")]).strip(os.pathsep)

    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "loopback_singleton.daemon",
            "--name",
            name,
            "--factory",
            FACTORY,
            "--idle-ttl",
            "0.1",
            "--serializer",
            "pickle",
            "--scope",
            "user",
        ],
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=(sys.platform != "win32"),
        creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0),
    )
    try:
        runtime = _wait_for_runtime(name)
        pid = runtime["pid"]

        time.sleep(0.25)

        serializer = get_serializer("pickle")
        with socket.create_connection((runtime["host"], runtime["port"]), timeout=2.0) as sock:
            send_message(sock, ("HELLO", PROTOCOL_VERSION, token), serializer)
            response = recv_message(sock, serializer)
            assert response[0] == "OK"
            assert response[1] == pid
            send_message(sock, ("SHUTDOWN", False), serializer)
            shutdown_response = recv_message(sock, serializer)
            assert shutdown_response[0] == "OK"
    finally:
        proc.terminate()
        proc.wait(timeout=5)
        remove_runtime(paths)


def test_connect_once_raises_handshake_error_when_auth_token_changes() -> None:
    name = f"bad-auth-{uuid.uuid4().hex}"
    paths = get_runtime_paths(name)
    ensure_auth_token(paths)
    env = os.environ.copy()
    test_path = str(Path(__file__).parent)
    src_path = str(Path(__file__).parent.parent / "src")
    env["PYTHONPATH"] = os.pathsep.join([src_path, test_path, env.get("PYTHONPATH", "")]).strip(os.pathsep)

    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "loopback_singleton.daemon",
            "--name",
            name,
            "--factory",
            FACTORY,
            "--idle-ttl",
            "1.0",
            "--serializer",
            "pickle",
            "--scope",
            "user",
        ],
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=(sys.platform != "win32"),
        creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0),
    )
    try:
        _wait_for_runtime(name)
        paths.auth_file.write_text("invalid-token", encoding="utf-8")

        svc = LocalSingletonService(name=name, factory=FACTORY, idle_ttl=1.0)
        with pytest.raises(HandshakeError):
            svc._connect_once()
    finally:
        proc.terminate()
        proc.wait(timeout=5)
        remove_runtime(paths)


def test_handshake_rejects_protocol_version_mismatch() -> None:
    name = f"bad-proto-{uuid.uuid4().hex}"
    paths = get_runtime_paths(name)
    token = ensure_auth_token(paths)
    env = os.environ.copy()
    test_path = str(Path(__file__).parent)
    src_path = str(Path(__file__).parent.parent / "src")
    env["PYTHONPATH"] = os.pathsep.join([src_path, test_path, env.get("PYTHONPATH", "")]).strip(os.pathsep)

    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "loopback_singleton.daemon",
            "--name",
            name,
            "--factory",
            FACTORY,
            "--idle-ttl",
            "1.0",
            "--serializer",
            "pickle",
            "--scope",
            "user",
        ],
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=(sys.platform != "win32"),
        creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0),
    )
    try:
        runtime = _wait_for_runtime(name)
        serializer = get_serializer("pickle")
        with socket.create_connection((runtime["host"], runtime["port"]), timeout=2.0) as sock:
            send_message(sock, ("HELLO", PROTOCOL_VERSION + 999, token), serializer)
            response = recv_message(sock, serializer)
            assert response == ("ERR", "handshake failed")
    finally:
        proc.terminate()
        proc.wait(timeout=5)
        remove_runtime(paths)


@pytest.mark.skipif(os.name == "nt", reason="POSIX-only permission behavior")
def test_auth_token_runtime_dir_is_traversable(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    name = f"auth-perm-{uuid.uuid4().hex}"
    paths = get_runtime_paths(name)

    ensure_auth_token(paths)

    assert paths.base_dir.exists()
    assert paths.auth_file.stat().st_size > 0
    mode = stat.S_IMODE(os.stat(paths.base_dir).st_mode)
    assert mode & 0o111 != 0


@pytest.mark.skipif(os.name == "nt", reason="POSIX-only runtime fallback behavior")
def test_get_runtime_dir_falls_back_when_xdg_runtime_unusable(monkeypatch, tmp_path: Path) -> None:
    blocked = tmp_path / "blocked"
    blocked.mkdir()
    try:
        blocked.chmod(0o000)
    except OSError:
        pytest.skip("chmod not supported in this environment")

    if os.access(blocked, os.W_OK | os.X_OK):
        pytest.skip("Cannot simulate blocked XDG_RUNTIME_DIR for this user")

    try:
        monkeypatch.setenv("XDG_RUNTIME_DIR", str(blocked))
        name = f"fallback-{uuid.uuid4().hex}"
        runtime_dir = get_runtime_paths(name).base_dir

        assert runtime_dir.parent.parent == Path.home() / ".cache"
        assert blocked not in runtime_dir.parents
    finally:
        blocked.chmod(0o700)




@pytest.mark.skipif(os.name == "nt", reason="POSIX-only runtime fallback behavior")
def test_ensure_auth_token_uses_fallback_when_xdg_runtime_unusable(monkeypatch, tmp_path: Path) -> None:
    blocked = tmp_path / "blocked-auth"
    blocked.mkdir()
    try:
        blocked.chmod(0o000)
    except OSError:
        pytest.skip("chmod not supported in this environment")

    if os.access(blocked, os.W_OK | os.X_OK):
        pytest.skip("Cannot simulate blocked XDG_RUNTIME_DIR for this user")

    try:
        monkeypatch.setenv("XDG_RUNTIME_DIR", str(blocked))
        name = f"fallback-auth-{uuid.uuid4().hex}"
        paths = get_runtime_paths(name)

        token = ensure_auth_token(paths)

        assert token
        assert paths.base_dir.parent.parent == Path.home() / ".cache"
        assert paths.auth_file.exists()
    finally:
        blocked.chmod(0o700)


def test_transport_imports_without_poll_support(monkeypatch) -> None:
    import loopback_singleton.transport as transport

    monkeypatch.delattr(transport.select, "poll", raising=False)
    monkeypatch.delattr(transport.select, "POLLHUP", raising=False)
    monkeypatch.delattr(transport.select, "POLLERR", raising=False)

    reloaded = importlib.reload(transport)
    assert reloaded._HAS_POLL is False
    assert reloaded._peer_disconnected(object()) is False

    importlib.reload(transport)


def test_recv_message_timeout_sleeps_when_partial_frame_buffered(monkeypatch) -> None:
    import loopback_singleton.transport as transport

    class _Sock:
        def recv(self, _n: int, _flags: int = 0) -> bytes:
            return b"\x00\x01"


    sock = _Sock()
    serializer = get_serializer("pickle")
    sleep_calls: list[float] = []

    def fake_select(_r, _w, _x, _timeout):
        return ([sock], [], [])

    def fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)
        raise RuntimeError("stop")

    monkeypatch.setattr(transport.select, "select", fake_select)
    monkeypatch.setattr(transport.time, "sleep", fake_sleep)
    monkeypatch.setattr(transport, "_peer_disconnected", lambda _sock: False)

    with pytest.raises(RuntimeError, match="stop"):
        transport.recv_message_timeout(sock, serializer, timeout=0.1)

    assert sleep_calls


def test_recv_message_timeout_treats_select_oserror_as_disconnect(monkeypatch) -> None:
    import loopback_singleton.transport as transport

    class _Sock:
        pass

    serializer = get_serializer("pickle")

    def fake_select(_r, _w, _x, _timeout):
        raise OSError("bad file descriptor")

    monkeypatch.setattr(transport.select, "select", fake_select)

    with pytest.raises(ConnectionError, match="Socket closed while receiving"):
        transport.recv_message_timeout(_Sock(), serializer, timeout=0.1)

@pytest.mark.skipif(os.name == "nt", reason="POSIX-only metadata corruption regression")
def test_corrupt_runtime_metadata_is_treated_as_missing_and_recovers(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    name = f"corrupt-runtime-{uuid.uuid4().hex}"
    paths = get_runtime_paths(name)
    paths.base_dir.mkdir(parents=True, exist_ok=True)
    paths.runtime_file.write_bytes(b"this-is-not-pickle")

    assert read_runtime(paths) is None

    test_path = str(Path(__file__).parent)
    src_path = str(Path(__file__).parent.parent / "src")
    monkeypatch.setenv(
        "PYTHONPATH",
        os.pathsep.join([src_path, test_path, os.environ.get("PYTHONPATH", "")]).strip(os.pathsep),
    )

    svc = local_singleton(name=name, factory=FACTORY, idle_ttl=1.0)
    with svc.proxy() as p:
        assert p.ping() == "pong"

    remove_runtime(paths)
