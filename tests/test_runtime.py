from __future__ import annotations

import os
import pickle

import pytest

from loopback_singleton.runtime import (
    RuntimePaths,
    ensure_auth_token,
    get_runtime_dir,
    read_factory_payload,
    read_factory_payload_file,
    read_runtime,
    remove_runtime,
    write_factory_payload,
    write_runtime,
)


def _make_paths(tmp_path) -> RuntimePaths:
    base_dir = tmp_path / "runtime"
    return RuntimePaths(
        base_dir=base_dir,
        runtime_file=base_dir / "runtime.bin",
        auth_file=base_dir / "auth.bin",
        lock_file=base_dir / "lockfile.lock",
        factory_file=base_dir / "factory.bin",
    )


def test_get_runtime_dir_system_scope_not_implemented() -> None:
    with pytest.raises(NotImplementedError, match="Only scope='user'"):
        get_runtime_dir(name="x", scope="system")


def test_runtime_read_write_round_trip(tmp_path) -> None:
    paths = _make_paths(tmp_path)
    runtime_info = {"port": 9911, "factory": "pkg:callable", "ready": True}

    write_runtime(paths, runtime_info)

    assert read_runtime(paths) == runtime_info


def test_remove_runtime_removes_runtime_and_factory_files_and_is_idempotent(tmp_path) -> None:
    paths = _make_paths(tmp_path)
    paths.base_dir.mkdir(parents=True)

    for file_path in (
        paths.runtime_file,
        paths.runtime_file.with_suffix(".tmp"),
        paths.factory_file,
        paths.factory_file.with_suffix(".tmp"),
    ):
        file_path.write_bytes(b"payload")

    remove_runtime(paths)

    assert not paths.runtime_file.exists()
    assert not paths.runtime_file.with_suffix(".tmp").exists()
    assert not paths.factory_file.exists()
    assert not paths.factory_file.with_suffix(".tmp").exists()

    remove_runtime(paths)


def test_factory_payload_round_trip_for_both_readers(tmp_path) -> None:
    paths = _make_paths(tmp_path)
    payload = {"factory": "pkg:make", "args": [1, 2], "kwargs": {"debug": True}}

    write_factory_payload(paths, payload)

    assert read_factory_payload(paths) == payload
    assert read_factory_payload_file(paths.factory_file) == payload


@pytest.mark.parametrize("invalid_payload", [["not", "a", "dict"], "not-a-dict"])
def test_factory_payload_readers_raise_for_non_dict_payload(tmp_path, invalid_payload) -> None:
    paths = _make_paths(tmp_path)
    paths.base_dir.mkdir(parents=True)
    with paths.factory_file.open("wb") as f:
        pickle.dump(invalid_payload, f, protocol=pickle.HIGHEST_PROTOCOL)

    with pytest.raises(ValueError, match="Factory payload must be a dictionary"):
        read_factory_payload(paths)

    with pytest.raises(ValueError, match="Factory payload must be a dictionary"):
        read_factory_payload_file(paths.factory_file)


def test_ensure_auth_token_reads_existing_file_when_open_raises_file_exists(tmp_path, monkeypatch) -> None:
    paths = _make_paths(tmp_path)
    paths.base_dir.mkdir(parents=True)
    expected_token = "already-exists-token"
    paths.auth_file.write_text(expected_token, encoding="utf-8")

    def _raise_file_exists(*_args, **_kwargs):
        raise FileExistsError

    monkeypatch.setattr(os, "open", _raise_file_exists)

    assert ensure_auth_token(paths) == expected_token
