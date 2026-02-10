"""Public API and service coordination."""

from __future__ import annotations

import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Any

from .errors import ConnectionFailedError, DaemonConnectionError, HandshakeError
from .locking import FileLock
from .proxy import Proxy
from .runtime import ensure_auth_token, get_runtime_paths, read_runtime, remove_runtime
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
            return sock
        except Exception:
            sock.close()
            raise

    def _spawn_daemon(self) -> None:
        args = [
            sys.executable,
            "-m",
            "loopback_singleton.daemon",
            "--name",
            self.name,
            "--factory",
            self.factory,
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
        except Exception:
            pass

        with FileLock(paths.lock_file):
            try:
                return self._connect_once()
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

    def proxy(self) -> Proxy:
        sock = self._connect_or_spawn()
        return Proxy(sock=sock, serializer_name=self.serializer)


def local_singleton(
    name: str,
    factory: str,
    *,
    scope: str = "user",
    idle_ttl: float = 2.0,
    serializer: str = "pickle",
    connect_timeout: float = 0.5,
    start_timeout: float = 3.0,
) -> LocalSingletonService:
    if not isinstance(factory, str):
        raise TypeError("MVP requires factory as import string: 'module:callable_or_class'")
    get_serializer(serializer)
    return LocalSingletonService(
        name=name,
        factory=factory,
        idle_ttl=idle_ttl,
        serializer=serializer,
        scope=scope,
        connect_timeout=connect_timeout,
        start_timeout=start_timeout,
    )
