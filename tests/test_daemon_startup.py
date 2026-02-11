from __future__ import annotations

import os
import pickle
import socket
import sys
import time
import uuid
from multiprocessing import get_context
from pathlib import Path

import pytest

from loopback_singleton.daemon import _load_factory_startup, main, run_daemon
from loopback_singleton.runtime import ensure_auth_token, get_runtime_paths, remove_runtime, write_factory_payload
from loopback_singleton.serialization import get_serializer
from loopback_singleton.transport import recv_message, send_message
from loopback_singleton.version import PROTOCOL_VERSION

TESTS_DIR = Path(__file__).parent
os.environ["PYTHONPATH"] = (
    f"{TESTS_DIR}{os.pathsep}{os.environ.get('PYTHONPATH', '')}".rstrip(os.pathsep)
)
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))


def _write_payload_file(path: Path, payload: dict[object, object]) -> None:
    with path.open("wb") as f:
        pickle.dump(payload, f, protocol=pickle.HIGHEST_PROTOCOL)


def test_load_factory_startup_valid_payload_returns_values(tmp_path: Path) -> None:
    payload_file = tmp_path / "factory.bin"
    payload = {
        "factory_import": "fixtures_pkg.services:make_daemon_smoke_service",
        "factory_args": (1, 2),
        "factory_kwargs": {"x": 3},
    }
    _write_payload_file(payload_file, payload)

    factory_import, args, kwargs, factory_id = _load_factory_startup(str(payload_file))

    assert factory_import == payload["factory_import"]
    assert args == payload["factory_args"]
    assert kwargs == payload["factory_kwargs"]
    assert isinstance(factory_id, str)
    assert factory_id


@pytest.mark.parametrize(
    ("field", "value", "expected_message"),
    [
        ("factory_import", 123, "factory_import must be present in factory payload"),
        ("factory_args", [1], "factory_args must be a tuple"),
        ("factory_kwargs", [("x", 1)], "factory_kwargs must be a dict"),
    ],
)
def test_load_factory_startup_invalid_types_raise_value_error(
    tmp_path: Path,
    field: str,
    value: object,
    expected_message: str,
) -> None:
    payload_file = tmp_path / "factory.bin"
    payload: dict[str, object] = {
        "factory_import": "fixtures_pkg.services:make_daemon_smoke_service",
        "factory_args": (),
        "factory_kwargs": {},
    }
    payload[field] = value
    _write_payload_file(payload_file, payload)

    with pytest.raises(ValueError, match=expected_message):
        _load_factory_startup(str(payload_file))


def test_main_passes_cli_args_to_run_daemon(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, object]] = []

    def fake_run_daemon(**kwargs) -> None:
        calls.append(kwargs)

    monkeypatch.setattr(
        "sys.argv",
        [
            "daemon.py",
            "--name",
            "svc-name",
            "--factory-file",
            "/tmp/factory.bin",
            "--idle-ttl",
            "3.5",
            "--serializer",
            "json",
            "--scope",
            "user",
        ],
    )
    monkeypatch.setattr("loopback_singleton.daemon.run_daemon", fake_run_daemon)

    main()

    assert calls == [
        {
            "name": "svc-name",
            "factory_file": "/tmp/factory.bin",
            "idle_ttl": 3.5,
            "serializer_name": "json",
            "scope": "user",
        }
    ]


def test_run_daemon_unknown_message_type_returns_error() -> None:
    name = f"daemon-smoke-{uuid.uuid4().hex}"
    paths = get_runtime_paths(name)
    token = ensure_auth_token(paths)
    write_factory_payload(
        paths,
        {
            "factory_import": "fixtures_pkg.services:make_daemon_smoke_service",
            "factory_args": (),
            "factory_kwargs": {},
        },
    )

    ctx = get_context("spawn")
    process = ctx.Process(
        target=run_daemon,
        kwargs={
            "name": name,
            "factory_file": str(paths.factory_file),
            "idle_ttl": 5.0,
            "serializer_name": "pickle",
            "scope": "user",
        },
    )
    process.start()

    serializer = get_serializer("pickle")

    try:
        deadline = time.time() + 5.0
        while time.time() < deadline and not paths.runtime_file.exists():
            time.sleep(0.05)
        assert paths.runtime_file.exists(), "runtime file was not created"

        runtime = pickle.loads(paths.runtime_file.read_bytes())

        with socket.create_connection((runtime["host"], runtime["port"]), timeout=2.0) as sock:
            send_message(sock, ("HELLO", PROTOCOL_VERSION, token), serializer)
            assert recv_message(sock, serializer)[0] == "OK"

            send_message(sock, ("UNKNOWN",), serializer)
            status, payload = recv_message(sock, serializer)
            assert status == "ERR"
            assert "unknown message type: UNKNOWN" in payload

            send_message(sock, ("SHUTDOWN", True), serializer)
            assert recv_message(sock, serializer)[0] == "OK"

        process.join(timeout=5)
        assert process.exitcode == 0
    finally:
        if process.is_alive():
            process.kill()
            process.join(timeout=5)
        remove_runtime(paths)
