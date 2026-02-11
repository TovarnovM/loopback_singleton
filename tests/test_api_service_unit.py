from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from loopback_singleton.api import LocalSingletonService
from loopback_singleton.errors import DaemonConnectionError, FactoryMismatchError
from loopback_singleton.runtime import RuntimePaths


@pytest.fixture
def service() -> LocalSingletonService:
    return LocalSingletonService(
        name="svc",
        factory="fixtures_pkg.services:make_counter",
        idle_ttl=2.0,
        serializer="pickle",
        scope="user",
        connect_timeout=0.1,
        start_timeout=0.1,
    )


@pytest.fixture
def runtime_paths() -> RuntimePaths:
    base = Path("/tmp/loopback-tests")
    return RuntimePaths(
        base_dir=base,
        runtime_file=base / "runtime.bin",
        auth_file=base / "auth.bin",
        lock_file=base / "lock.lock",
        factory_file=base / "factory.bin",
    )


def test_assert_runtime_factory_match_without_factory_id(service: LocalSingletonService) -> None:
    service._assert_runtime_factory_match({"host": "127.0.0.1", "port": 1111})


def test_assert_runtime_factory_match_same_factory_id(service: LocalSingletonService) -> None:
    service._assert_runtime_factory_match({"factory_id": service.factory_id})


def test_assert_runtime_factory_match_mismatch_raises(service: LocalSingletonService) -> None:
    with pytest.raises(FactoryMismatchError):
        service._assert_runtime_factory_match({"factory_id": "another-id"})


def test_spawn_daemon_builds_expected_args_and_posix_kwargs(
    monkeypatch: pytest.MonkeyPatch,
    service: LocalSingletonService,
    runtime_paths: RuntimePaths,
) -> None:
    calls: dict[str, object] = {}

    def fake_write_factory_payload(paths: RuntimePaths, payload: dict[str, object]) -> None:
        calls["payload_paths"] = paths
        calls["payload"] = payload

    def fake_popen(args: list[str], **kwargs: object) -> None:
        calls["args"] = args
        calls["kwargs"] = kwargs
        return None

    import loopback_singleton.api as api

    monkeypatch.setattr(api, "get_runtime_paths", lambda **_: runtime_paths)
    monkeypatch.setattr(api, "write_factory_payload", fake_write_factory_payload)
    monkeypatch.setattr(api, "subprocess", SimpleNamespace(DEVNULL=object(), Popen=fake_popen))
    monkeypatch.setattr(api.sys, "platform", "linux")

    service._spawn_daemon()

    args = calls["args"]
    kwargs = calls["kwargs"]

    assert isinstance(args, list)
    assert "--factory-file" in args
    assert str(runtime_paths.factory_file) in args
    assert "--scope" in args and "user" in args
    assert "--serializer" in args and "pickle" in args
    assert kwargs["start_new_session"] is True
    assert "creationflags" not in kwargs


def test_spawn_daemon_windows_uses_creationflags(
    monkeypatch: pytest.MonkeyPatch,
    service: LocalSingletonService,
    runtime_paths: RuntimePaths,
) -> None:
    calls: dict[str, object] = {}

    def fake_popen(args: list[str], **kwargs: object) -> None:
        calls["args"] = args
        calls["kwargs"] = kwargs
        return None

    import loopback_singleton.api as api

    monkeypatch.setattr(api, "get_runtime_paths", lambda **_: runtime_paths)
    monkeypatch.setattr(api, "write_factory_payload", lambda *_: None)
    monkeypatch.setattr(api.sys, "platform", "win32")
    monkeypatch.setattr(api.subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200, raising=False)
    monkeypatch.setattr(api.subprocess, "DETACHED_PROCESS", 0x008, raising=False)
    monkeypatch.setattr(api.subprocess, "Popen", fake_popen)

    service._spawn_daemon()

    kwargs = calls["kwargs"]
    assert kwargs["creationflags"] == 0x200 | 0x008
    assert "start_new_session" not in kwargs


def test_connect_or_spawn_primary_connect_success_without_lock_or_spawn(
    monkeypatch: pytest.MonkeyPatch,
    service: LocalSingletonService,
    runtime_paths: RuntimePaths,
) -> None:
    sock = object()

    import loopback_singleton.api as api

    monkeypatch.setattr(api, "get_runtime_paths", lambda **_: runtime_paths)
    monkeypatch.setattr(api, "ensure_auth_token", lambda *_: "token")
    monkeypatch.setattr(service, "_connect_once", lambda: sock)
    monkeypatch.setattr(service, "_spawn_daemon", lambda: (_ for _ in ()).throw(AssertionError("spawn called")))
    monkeypatch.setattr(api, "FileLock", lambda *_: (_ for _ in ()).throw(AssertionError("lock used")))

    assert service._connect_or_spawn() is sock


def test_connect_or_spawn_first_fail_then_success_under_lock(
    monkeypatch: pytest.MonkeyPatch,
    service: LocalSingletonService,
    runtime_paths: RuntimePaths,
) -> None:
    state = {"calls": 0, "entered": False}
    expected_sock = object()

    class DummyLock:
        def __enter__(self) -> None:
            state["entered"] = True
            return None

        def __exit__(self, *_: object) -> None:
            return None

    def fake_connect_once() -> object:
        state["calls"] += 1
        if state["calls"] == 1:
            raise RuntimeError("first connect fail")
        return expected_sock

    import loopback_singleton.api as api

    monkeypatch.setattr(api, "get_runtime_paths", lambda **_: runtime_paths)
    monkeypatch.setattr(api, "ensure_auth_token", lambda *_: "token")
    monkeypatch.setattr(api, "FileLock", lambda *_: DummyLock())
    monkeypatch.setattr(service, "_connect_once", fake_connect_once)
    monkeypatch.setattr(service, "_spawn_daemon", lambda: (_ for _ in ()).throw(AssertionError("unexpected spawn")))
    monkeypatch.setattr(api, "remove_runtime", lambda *_: (_ for _ in ()).throw(AssertionError("unexpected cleanup")))

    assert service._connect_or_spawn() is expected_sock
    assert state == {"calls": 2, "entered": True}


def test_connect_or_spawn_timeout_raises_last_error_text(
    monkeypatch: pytest.MonkeyPatch,
    service: LocalSingletonService,
    runtime_paths: RuntimePaths,
) -> None:
    attempts = {"count": 0}

    class DummyLock:
        def __enter__(self) -> None:
            return None

        def __exit__(self, *_: object) -> None:
            return None

    def fake_connect_once() -> object:
        attempts["count"] += 1
        if attempts["count"] <= 2:
            raise RuntimeError("initial fail")
        raise ValueError("boom-last")

    import loopback_singleton.api as api

    ticks = iter([0.0, 0.01, 0.02, 0.2])
    monkeypatch.setattr(api, "get_runtime_paths", lambda **_: runtime_paths)
    monkeypatch.setattr(api, "ensure_auth_token", lambda *_: "token")
    monkeypatch.setattr(api, "FileLock", lambda *_: DummyLock())
    monkeypatch.setattr(api, "remove_runtime", lambda *_: None)
    monkeypatch.setattr(service, "_spawn_daemon", lambda: None)
    monkeypatch.setattr(service, "_connect_once", fake_connect_once)
    monkeypatch.setattr(api.time, "time", lambda: next(ticks))
    monkeypatch.setattr(api.time, "sleep", lambda *_: None)

    with pytest.raises(DaemonConnectionError, match="boom-last"):
        service._connect_or_spawn()


def test_connect_or_spawn_timeout_without_last_error_details(
    monkeypatch: pytest.MonkeyPatch,
    service: LocalSingletonService,
    runtime_paths: RuntimePaths,
) -> None:
    attempts = {"count": 0}

    class DummyLock:
        def __enter__(self) -> None:
            return None

        def __exit__(self, *_: object) -> None:
            return None

    def fake_connect_once() -> object:
        attempts["count"] += 1
        raise RuntimeError("pre-loop failure")

    import loopback_singleton.api as api

    monkeypatch.setattr(service, "start_timeout", 0.0)
    monkeypatch.setattr(api, "get_runtime_paths", lambda **_: runtime_paths)
    monkeypatch.setattr(api, "ensure_auth_token", lambda *_: "token")
    monkeypatch.setattr(api, "FileLock", lambda *_: DummyLock())
    monkeypatch.setattr(api, "remove_runtime", lambda *_: None)
    monkeypatch.setattr(service, "_spawn_daemon", lambda: None)
    monkeypatch.setattr(service, "_connect_once", fake_connect_once)
    monkeypatch.setattr(api.time, "time", lambda: 10.0)

    with pytest.raises(DaemonConnectionError, match="no error details"):
        service._connect_or_spawn()


def test_shutdown_raises_on_bad_response(
    monkeypatch: pytest.MonkeyPatch,
    service: LocalSingletonService,
) -> None:
    events: list[tuple[object, tuple[object, ...], object]] = []

    class DummySock:
        closed = False

        def close(self) -> None:
            self.closed = True

    sock = DummySock()

    import loopback_singleton.api as api

    monkeypatch.setattr(service, "_connect_once", lambda: sock)
    monkeypatch.setattr(api, "get_serializer", lambda *_: object())
    monkeypatch.setattr(api, "send_message", lambda s, msg, ser: events.append((s, msg, ser)))
    monkeypatch.setattr(api, "recv_message", lambda *_: ("ERR", "fail"))

    with pytest.raises(DaemonConnectionError, match="Bad shutdown response"):
        service.shutdown()

    assert sock.closed is True
    assert events and events[0][1] == ("SHUTDOWN", False)


def test_shutdown_forces_runtime_cleanup_after_deadline(
    monkeypatch: pytest.MonkeyPatch,
    service: LocalSingletonService,
    runtime_paths: RuntimePaths,
) -> None:
    class DummySock:
        def close(self) -> None:
            return None

    import loopback_singleton.api as api

    removed = {"called": False}
    ticks = iter([0.0, 0.01, 0.03, 0.25])

    monkeypatch.setattr(service, "_connect_once", lambda: DummySock())
    monkeypatch.setattr(api, "get_serializer", lambda *_: object())
    monkeypatch.setattr(api, "send_message", lambda *_: None)
    monkeypatch.setattr(api, "recv_message", lambda *_: ("OK", "done"))
    monkeypatch.setattr(api, "get_runtime_paths", lambda **_: runtime_paths)
    monkeypatch.setattr(api, "read_runtime", lambda *_: {"alive": True})
    monkeypatch.setattr(api, "remove_runtime", lambda *_: removed.update(called=True))
    monkeypatch.setattr(api.time, "time", lambda: next(ticks))
    monkeypatch.setattr(api.time, "sleep", lambda *_: None)

    service.shutdown()

    assert removed["called"] is True


@pytest.mark.parametrize("resp", [("ERR", {}), ("OK", "not-a-dict")])
def test_ping_invalid_response_raises_daemon_connection_error(
    monkeypatch: pytest.MonkeyPatch,
    service: LocalSingletonService,
    resp: tuple[object, object],
) -> None:
    class DummySock:
        closed = False

        def close(self) -> None:
            self.closed = True

    sock = DummySock()

    import loopback_singleton.api as api

    monkeypatch.setattr(service, "_connect_or_spawn", lambda: sock)
    monkeypatch.setattr(api, "get_serializer", lambda *_: object())
    monkeypatch.setattr(api, "send_message", lambda *_: None)
    monkeypatch.setattr(api, "recv_message", lambda *_: resp)

    with pytest.raises(DaemonConnectionError, match="Bad ping response"):
        service.ping()

    assert sock.closed is True
