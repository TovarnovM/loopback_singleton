from __future__ import annotations

import os
import pickle
import socket
import struct
import sys
import time
import uuid

import pytest
from multiprocessing import get_context
from pathlib import Path

from loopback_singleton import RemoteError, local_singleton
from loopback_singleton.runtime import ensure_auth_token, get_runtime_paths, remove_runtime
from loopback_singleton.serialization import get_serializer
from loopback_singleton.transport import MAX_FRAME_BYTES, recv_message, send_message
from loopback_singleton.version import PROTOCOL_VERSION

TESTS_DIR = Path(__file__).parent
os.environ["PYTHONPATH"] = (
    f"{TESTS_DIR}{os.pathsep}{os.environ.get('PYTHONPATH', '')}".rstrip(os.pathsep)
)
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

FACTORY = "fixtures_pkg.services:TestCounter"


def _worker_ping(name: str, queue) -> None:
    svc = local_singleton(name=name, factory=FACTORY, idle_ttl=1.5)
    with svc.proxy() as p:
        result = p.ping()
    runtime = pickle.loads(get_runtime_paths(name).runtime_file.read_bytes())
    queue.put((result, runtime["pid"]))


def _worker_inc(name: str, n: int, queue) -> None:
    svc = local_singleton(name=name, factory=FACTORY, idle_ttl=2.0)
    vals = []
    with svc.proxy() as p:
        for _ in range(n):
            vals.append(p.inc())
    queue.put(vals)


def test_race_start_multi_process() -> None:
    name = f"race-{uuid.uuid4().hex}"
    ctx = get_context("spawn")
    q = ctx.Queue()
    procs = [ctx.Process(target=_worker_ping, args=(name, q)) for _ in range(12)]
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=20)
        assert p.exitcode == 0

    results = [q.get(timeout=5) for _ in procs]
    assert all(item[0] == "pong" for item in results)
    pids = {item[1] for item in results}
    assert len(pids) == 1


def test_strict_sequential_counter() -> None:
    name = f"seq-{uuid.uuid4().hex}"
    ctx = get_context("spawn")
    q = ctx.Queue()
    workers = 8
    per_worker = 8
    procs = [ctx.Process(target=_worker_inc, args=(name, per_worker, q)) for _ in range(workers)]
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=30)
        assert p.exitcode == 0

    values = []
    for _ in procs:
        values.extend(q.get(timeout=5))

    assert len(values) == workers * per_worker
    assert sorted(values) == list(range(1, workers * per_worker + 1))


def test_idle_shutdown_restarts_daemon() -> None:
    name = f"idle-{uuid.uuid4().hex}"
    svc = local_singleton(name=name, factory=FACTORY, idle_ttl=0.8)

    with svc.proxy() as p:
        assert p.ping() == "pong"
    runtime_path = get_runtime_paths(name).runtime_file
    first_runtime = pickle.loads(runtime_path.read_bytes())
    first_pid = first_runtime["pid"]

    time.sleep(1.5)

    with svc.proxy() as p:
        assert p.ping() == "pong"
    second_runtime = pickle.loads(runtime_path.read_bytes())
    second_pid = second_runtime["pid"]

    assert second_pid != first_pid


def test_stale_runtime_is_replaced() -> None:
    name = f"stale-{uuid.uuid4().hex}"
    paths = get_runtime_paths(name)
    paths.base_dir.mkdir(parents=True, exist_ok=True)
    paths.auth_file.write_bytes("deadbeef".encode("utf-8"))
    with paths.runtime_file.open("wb") as f:
        pickle.dump(
            {
                "protocol_version": 1,
                "host": "127.0.0.1",
                "port": 65000,
                "pid": 999999,
                "serializer": "pickle",
                "started_at": time.time(),
            },
            f,
        )

    svc = local_singleton(name=name, factory=FACTORY, idle_ttl=1.0)
    with svc.proxy() as p:
        assert p.ping() == "pong"
    runtime = pickle.loads(paths.runtime_file.read_bytes())
    assert runtime["port"] != 65000
    assert runtime["pid"] != 999999

    remove_runtime(paths)


def test_remote_error_traceback_contains_runtime_error() -> None:
    name = f"fail-{uuid.uuid4().hex}"
    svc = local_singleton(name=name, factory=FACTORY, idle_ttl=1.0)

    try:
        with pytest.raises(RemoteError) as exc_info:
            with svc.proxy() as p:
                p.fail()

        message = str(exc_info.value)
        assert "RuntimeError" in message
        assert "boom" in message
    finally:
        remove_runtime(get_runtime_paths(name))


def test_oversized_frame_rejected_and_daemon_stays_healthy() -> None:
    name = f"frame-limit-{uuid.uuid4().hex}"
    svc = local_singleton(name=name, factory=FACTORY, idle_ttl=2.0)

    svc.ensure_started()
    runtime = pickle.loads(get_runtime_paths(name).runtime_file.read_bytes())
    token = ensure_auth_token(get_runtime_paths(name))
    serializer = get_serializer("pickle")

    with socket.create_connection((runtime["host"], runtime["port"]), timeout=2.0) as sock:
        send_message(sock, ("HELLO", PROTOCOL_VERSION, token), serializer)
        assert recv_message(sock, serializer)[0] == "OK"

        sock.sendall(struct.pack("!I", MAX_FRAME_BYTES + 1))
        deadline = time.time() + 2.0
        while time.time() < deadline:
            try:
                probe = sock.recv(1)
            except (ConnectionResetError, OSError):
                break
            if probe == b"":
                break
        else:
            raise AssertionError("daemon did not close oversized-frame connection")

    info = svc.ping()
    assert info["pid"] == runtime["pid"]
    remove_runtime(get_runtime_paths(name))


def test_idle_shutdown_with_stuck_client_connection() -> None:
    name = f"stuck-client-{uuid.uuid4().hex}"
    svc = local_singleton(name=name, factory=FACTORY, idle_ttl=0.5)

    svc.ensure_started()
    runtime_path = get_runtime_paths(name).runtime_file
    first_runtime = pickle.loads(runtime_path.read_bytes())
    token = ensure_auth_token(get_runtime_paths(name))
    serializer = get_serializer("pickle")

    sock = socket.create_connection((first_runtime["host"], first_runtime["port"]), timeout=2.0)
    send_message(sock, ("HELLO", PROTOCOL_VERSION, token), serializer)
    assert recv_message(sock, serializer)[0] == "OK"

    time.sleep(0.8)
    assert runtime_path.exists()

    sock.close()

    deadline = time.time() + 4.0
    while time.time() < deadline and runtime_path.exists():
        time.sleep(0.05)
    assert not runtime_path.exists()
    remove_runtime(get_runtime_paths(name))


def test_private_method_call_denied_server_side() -> None:
    name = f"private-denied-{uuid.uuid4().hex}"
    svc = local_singleton(name=name, factory=FACTORY, idle_ttl=2.0)

    svc.ensure_started()
    runtime = pickle.loads(get_runtime_paths(name).runtime_file.read_bytes())
    token = ensure_auth_token(get_runtime_paths(name))
    serializer = get_serializer("pickle")

    with socket.create_connection((runtime["host"], runtime["port"]), timeout=2.0) as sock:
        send_message(sock, ("HELLO", PROTOCOL_VERSION, token), serializer)
        assert recv_message(sock, serializer)[0] == "OK"

        send_message(sock, ("CALL", "_reset_state", (), {}), serializer)
        status, payload = recv_message(sock, serializer)
        assert status == "ERR"
        assert "private methods are not allowed" in payload

    remove_runtime(get_runtime_paths(name))


def test_service_ensure_started_ping_shutdown_lifecycle() -> None:
    name = f"service-api-{uuid.uuid4().hex}"
    svc = local_singleton(name=name, factory=FACTORY, idle_ttl=2.0)

    svc.ensure_started()
    first_info = svc.ping()
    assert isinstance(first_info["pid"], int)
    assert isinstance(first_info["active"], int)

    first_pid = first_info["pid"]
    svc.shutdown()

    runtime_path = get_runtime_paths(name).runtime_file
    deadline = time.time() + 4.0
    while time.time() < deadline and runtime_path.exists():
        time.sleep(0.05)
    assert not runtime_path.exists()

    second_info = svc.ping()
    assert second_info["pid"] != first_pid
    remove_runtime(get_runtime_paths(name))
