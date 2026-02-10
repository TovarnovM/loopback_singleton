from __future__ import annotations

import os
import pickle
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path

from loopback_singleton.api import LocalSingletonService
from loopback_singleton.errors import (
    ConnectionFailedError,
    DaemonConnectionError,
    HandshakeError,
)
from loopback_singleton.runtime import ensure_auth_token, get_runtime_paths, remove_runtime
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
