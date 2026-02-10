"""Daemon process implementation."""

from __future__ import annotations

import argparse
import importlib
import queue
import socket
import threading
import time
import traceback
from dataclasses import dataclass
from typing import Any

from .runtime import get_runtime_paths, remove_runtime, write_runtime
from .serialization import get_serializer
from .transport import recv_message, send_message
from .version import PROTOCOL_VERSION


@dataclass
class ExecItem:
    method_name: str
    args: tuple[Any, ...]
    kwargs: dict[str, Any]
    result_q: queue.Queue[tuple[str, Any]]


def _resolve_factory(factory_import: str):
    if ":" not in factory_import:
        raise ValueError("Factory must be import string 'module:callable_or_class'")
    module_name, attr = factory_import.split(":", 1)
    module = importlib.import_module(module_name)
    target = getattr(module, attr)
    return target


def _build_instance(factory_import: str) -> Any:
    target = _resolve_factory(factory_import)
    return target()


def run_daemon(name: str, factory: str, idle_ttl: float, serializer_name: str, scope: str) -> None:
    serializer = get_serializer(serializer_name)
    paths = get_runtime_paths(name=name, scope=scope)
    auth_token = paths.auth_file.read_bytes().decode("utf-8").strip()

    obj = _build_instance(factory)

    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_sock.bind(("127.0.0.1", 0))
    server_sock.listen()
    server_sock.settimeout(0.2)
    host, port = server_sock.getsockname()

    runtime_info = {
        "protocol_version": PROTOCOL_VERSION,
        "host": host,
        "port": port,
        "pid": __import__("os").getpid(),
        "serializer": serializer_name,
        "started_at": time.time(),
    }
    write_runtime(paths, runtime_info)

    exec_q: queue.Queue[ExecItem] = queue.Queue()
    active_lock = threading.Lock()
    active_connections = 0
    last_zero_at = time.time()
    ever_connected = False
    shutting_down = threading.Event()

    def mark_connected(delta: int) -> None:
        nonlocal active_connections, last_zero_at
        with active_lock:
            active_connections += delta
            if active_connections == 0:
                last_zero_at = time.time()

    def executor() -> None:
        while not shutting_down.is_set():
            try:
                item = exec_q.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                method = getattr(obj, item.method_name)
                result = method(*item.args, **item.kwargs)
                item.result_q.put(("OK", result))
            except Exception:
                item.result_q.put(("ERR", traceback.format_exc()))

    exec_thread = threading.Thread(target=executor, daemon=True)
    exec_thread.start()

    def watchdog() -> None:
        while not shutting_down.is_set():
            time.sleep(0.2)
            with active_lock:
                if ever_connected and active_connections == 0 and (time.time() - last_zero_at) >= idle_ttl:
                    shutting_down.set()
                    break

    wd = threading.Thread(target=watchdog, daemon=True)
    wd.start()

    def handle_client(conn: socket.socket) -> None:
        nonlocal ever_connected
        mark_connected(+1)
        try:
            hello = recv_message(conn, serializer)
            if hello[0] != "HELLO" or hello[1] != PROTOCOL_VERSION or hello[2] != auth_token:
                send_message(conn, ("ERR", "handshake failed"), serializer)
                return
            with active_lock:
                ever_connected = True
            send_message(conn, ("OK", runtime_info["pid"], {"serializer": serializer_name}), serializer)
            while not shutting_down.is_set():
                msg = recv_message(conn, serializer)
                kind = msg[0]
                if kind == "PING":
                    with active_lock:
                        active_count = active_connections
                    send_message(conn, ("OK", {"pid": runtime_info["pid"], "active": active_count}), serializer)
                    continue
                if kind == "CALL":
                    _, method_name, args, kwargs = msg
                    result_q: queue.Queue[tuple[str, Any]] = queue.Queue(maxsize=1)
                    exec_q.put(ExecItem(method_name=method_name, args=args, kwargs=kwargs, result_q=result_q))
                    status, payload = result_q.get()
                    send_message(conn, (status, payload), serializer)
                    continue
                if kind == "SHUTDOWN":
                    shutting_down.set()
                    send_message(conn, ("OK", {"shutdown": True}), serializer)
                    return
                send_message(conn, ("ERR", f"unknown message type: {kind}"), serializer)
        except (ConnectionError, OSError):
            return
        finally:
            mark_connected(-1)
            try:
                conn.close()
            except OSError:
                pass

    client_threads: list[threading.Thread] = []
    try:
        while not shutting_down.is_set():
            try:
                conn, _ = server_sock.accept()
            except socket.timeout:
                continue
            t = threading.Thread(target=handle_client, args=(conn,), daemon=True)
            t.start()
            client_threads.append(t)
    finally:
        shutting_down.set()
        try:
            server_sock.close()
        except OSError:
            pass
        remove_runtime(paths)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", required=True)
    parser.add_argument("--factory", required=True)
    parser.add_argument("--idle-ttl", required=True, type=float)
    parser.add_argument("--serializer", default="pickle")
    parser.add_argument("--scope", default="user")
    args = parser.parse_args()
    run_daemon(
        name=args.name,
        factory=args.factory,
        idle_ttl=args.idle_ttl,
        serializer_name=args.serializer,
        scope=args.scope,
    )


if __name__ == "__main__":
    main()
