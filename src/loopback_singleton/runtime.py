"""Runtime directory and metadata helpers."""

from __future__ import annotations

import os
import pickle
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Any

RUNTIME_SUBDIR = "loopback-singleton"


def _chmod_owner_rw(path: Path) -> None:
    if os.name == "nt":
        return
    try:
        mode = 0o700 if path.is_dir() else 0o600
        os.chmod(path, mode)
    except OSError:
        pass


@dataclass(frozen=True)
class RuntimePaths:
    base_dir: Path
    runtime_file: Path
    auth_file: Path
    lock_file: Path
    factory_file: Path


def get_runtime_dir(name: str, scope: str = "user") -> Path:
    if scope != "user":
        raise NotImplementedError("Only scope='user' is implemented in MVP")
    if os.name == "nt":
        root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        xdg_runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
        root = Path.home() / ".cache"
        if xdg_runtime_dir:
            candidate_root = Path(xdg_runtime_dir)
            probe = candidate_root / RUNTIME_SUBDIR
            try:
                probe.mkdir(parents=True, exist_ok=True)
                if not os.access(probe, os.W_OK | os.X_OK):
                    raise PermissionError("Runtime directory is not writable/traversable")
                root = candidate_root
            except OSError:
                root = Path.home() / ".cache"
    return root / RUNTIME_SUBDIR / name


def get_runtime_paths(name: str, scope: str = "user") -> RuntimePaths:
    base = get_runtime_dir(name=name, scope=scope)
    return RuntimePaths(
        base_dir=base,
        runtime_file=base / "runtime.bin",
        auth_file=base / "auth.bin",
        lock_file=base / "lockfile.lock",
        factory_file=base / "factory.bin",
    )


def ensure_auth_token(paths: RuntimePaths) -> str:
    paths.base_dir.mkdir(parents=True, exist_ok=True)
    _chmod_owner_rw(paths.base_dir)
    try:
        if paths.auth_file.exists():
            _chmod_owner_rw(paths.auth_file)
            return paths.auth_file.read_bytes().decode("utf-8")
    except PermissionError:
        pass

    token = secrets.token_hex(32)
    try:
        fd = os.open(paths.auth_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(token.encode("utf-8"))
    except FileExistsError:
        _chmod_owner_rw(paths.auth_file)
        return paths.auth_file.read_bytes().decode("utf-8")
    _chmod_owner_rw(paths.auth_file)
    return token


def read_runtime(paths: RuntimePaths) -> dict[str, Any] | None:
    try:
        if not paths.runtime_file.exists():
            return None
    except PermissionError:
        return None
    try:
        with paths.runtime_file.open("rb") as f:
            return pickle.load(f)
    except (PermissionError, pickle.UnpicklingError, EOFError, OSError):
        return None


def write_runtime(paths: RuntimePaths, runtime_info: dict[str, Any]) -> None:
    paths.base_dir.mkdir(parents=True, exist_ok=True)
    _chmod_owner_rw(paths.base_dir)
    tmp = paths.runtime_file.with_suffix(".tmp")
    with tmp.open("wb") as f:
        pickle.dump(runtime_info, f, protocol=pickle.HIGHEST_PROTOCOL)
    _chmod_owner_rw(tmp)
    os.replace(tmp, paths.runtime_file)
    _chmod_owner_rw(paths.runtime_file)


def remove_runtime(paths: RuntimePaths) -> None:
    for path in (
        paths.runtime_file,
        paths.runtime_file.with_suffix(".tmp"),
        paths.factory_file,
        paths.factory_file.with_suffix(".tmp"),
    ):
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def write_factory_payload(paths: RuntimePaths, payload: dict[str, Any]) -> None:
    paths.base_dir.mkdir(parents=True, exist_ok=True)
    _chmod_owner_rw(paths.base_dir)
    tmp = paths.factory_file.with_suffix(".tmp")
    with tmp.open("wb") as f:
        pickle.dump(payload, f, protocol=pickle.HIGHEST_PROTOCOL)
    _chmod_owner_rw(tmp)
    os.replace(tmp, paths.factory_file)
    _chmod_owner_rw(paths.factory_file)


def read_factory_payload(paths: RuntimePaths) -> dict[str, Any]:
    with paths.factory_file.open("rb") as f:
        payload = pickle.load(f)
    if not isinstance(payload, dict):
        raise ValueError("Factory payload must be a dictionary")
    return payload


def read_factory_payload_file(factory_file: Path) -> dict[str, Any]:
    with factory_file.open("rb") as f:
        payload = pickle.load(f)
    if not isinstance(payload, dict):
        raise ValueError("Factory payload must be a dictionary")
    return payload
