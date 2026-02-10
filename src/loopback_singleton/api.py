"""Public API and service coordination."""

from __future__ import annotations

import socket
import subprocess
import sys
import time
from dataclasses import dataclass, field
from typing import Any

from .errors import ConnectionFailedError, DaemonConnectionError, FactoryMismatchError, HandshakeError
from .factory import compute_factory_id, normalize_factory
from .locking import FileLock
from .proxy import Proxy
from .runtime import (
    ensure_auth_token,
    get_runtime_paths,
    read_runtime,
    remove_runtime,
    write_factory_payload,
)
from .serialization import get_serializer
from .transport import recv_message, send_message
from .version import PROTOCOL_VERSION


@dataclass
class LocalSingletonService:
    name: str
    factory: str
    idle_ttl: float
    serializer: str = "pickle"
    scope: str = "user"
    connect_timeout: float = 0.5
    start_timeout: float = 3.0
    factory_args: tuple[Any, ...] = field(default_factory=tuple)
    factory_kwargs: dict[str, Any] = field(default_factory=dict)

    @property
    def factory_id(self) -> str:
        return compute_factory_id(self.factory, self.factory_args, self.factory_kwargs)

    def _connect_once(self) -> socket.socket:
        paths = get_runtime_paths(name=self.name, scope=self.scope)
        runtime = read_runtime(paths)
        if runtime is None:
            raise ConnectionFailedError("No runtime metadata")
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(self.connect_timeout)
        try:
            sock.connect((runtime["host"], runtime["port"]))
        except OSError as exc:
            sock.close()
            raise ConnectionFailedError(str(exc)) from exc
        try:
            token = ensure_auth_token(paths)
            serializer = get_serializer(self.serializer)
            send_message(sock, ("HELLO", PROTOCOL_VERSION, token), serializer)
            resp = recv_message(sock, serializer)
            if resp[0] != "OK":
                raise HandshakeError(str(resp))
            self._assert_runtime_factory_match(runtime)
            return sock
        except Exception:
            sock.close()
            raise

    def _assert_runtime_factory_match(self, runtime: dict[str, Any]) -> None:
        running_factory_id = runtime.get("factory_id")
        if running_factory_id is None:
            return
        if running_factory_id != self.factory_id:
            raise FactoryMismatchError(
                "Factory configuration mismatch for running daemon. "
                "Start a new service name or use matching factory/factory_args/factory_kwargs."
            )

    def _spawn_daemon(self) -> None:
        paths = get_runtime_paths(name=self.name, scope=self.scope)
        payload = {
            "protocol_version": PROTOCOL_VERSION,
            "factory_import": self.factory,
            "factory_args": self.factory_args,
            "factory_kwargs": self.factory_kwargs,
        }
        write_factory_payload(paths, payload)
        args = [
            sys.executable,
            "-m",
            "loopback_singleton.daemon",
            "--name",
            self.name,
            "--factory-file",
            str(paths.factory_file),
            "--idle-ttl",
            str(self.idle_ttl),
            "--serializer",
            self.serializer,
            "--scope",
            self.scope,
        ]
        kwargs: dict[str, Any] = {
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            "close_fds": True,
        }
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
        else:
            kwargs["start_new_session"] = True
        subprocess.Popen(args, **kwargs)

    def _connect_or_spawn(self) -> socket.socket:
        paths = get_runtime_paths(name=self.name, scope=self.scope)
        ensure_auth_token(paths)
        try:
            return self._connect_once()
        except FactoryMismatchError:
            raise
        except Exception:
            pass

        with FileLock(paths.lock_file):
            try:
                return self._connect_once()
            except FactoryMismatchError:
                raise
            except Exception:
                pass
            remove_runtime(paths)
            self._spawn_daemon()
            deadline = time.time() + self.start_timeout
            last_exc: Exception | None = None
            while time.time() < deadline:
                try:
                    return self._connect_once()
                except Exception as exc:
                    last_exc = exc
                    time.sleep(0.05)
            if last_exc is None:
                raise DaemonConnectionError("Failed to start/connect daemon: no error details")
            raise DaemonConnectionError(f"Failed to start/connect daemon: {last_exc}") from last_exc

    def ensure_started(self) -> None:
        sock = self._connect_or_spawn()
        sock.close()

    def ping(self) -> dict[str, Any]:
        serializer = get_serializer(self.serializer)
        sock = self._connect_or_spawn()
        try:
            send_message(sock, ("PING",), serializer)
            resp = recv_message(sock, serializer)
        finally:
            sock.close()
        if resp[0] != "OK" or not isinstance(resp[1], dict):
            raise DaemonConnectionError(f"Bad ping response: {resp!r}")
        return resp[1]

    def shutdown(self, force: bool = False) -> None:
        serializer = get_serializer(self.serializer)
        try:
            sock = self._connect_once()
        except DaemonConnectionError:
            return
        try:
            send_message(sock, ("SHUTDOWN", force), serializer)
            resp = recv_message(sock, serializer)
        finally:
            sock.close()
        if resp[0] != "OK":
            raise DaemonConnectionError(f"Bad shutdown response: {resp!r}")

        paths = get_runtime_paths(name=self.name, scope=self.scope)
        deadline = time.time() + max(self.start_timeout, 0.2)
        while time.time() < deadline:
            if read_runtime(paths) is None:
                return
            time.sleep(0.05)
        remove_runtime(paths)

    def proxy(self) -> Proxy:
        sock = self._connect_or_spawn()
        return Proxy(sock=sock, serializer_name=self.serializer, service_name=self.name)


def local_singleton(
    name: str,
    factory: str | Any,
    *,
    factory_args: tuple[Any, ...] = (),
    factory_kwargs: dict[str, Any] | None = None,
    scope: str = "user",
    idle_ttl: float = 2.0,
    serializer: str = "pickle",
    connect_timeout: float = 0.5,
    start_timeout: float = 3.0,
) -> LocalSingletonService:
    normalized_factory = normalize_factory(factory)
    normalized_kwargs = {} if factory_kwargs is None else dict(factory_kwargs)
    get_serializer(serializer)
    return LocalSingletonService(
        name=name,
        factory=normalized_factory,
        factory_args=tuple(factory_args),
        factory_kwargs=normalized_kwargs,
        idle_ttl=idle_ttl,
        serializer=serializer,
        scope=scope,
        connect_timeout=connect_timeout,
        start_timeout=start_timeout,
    )
