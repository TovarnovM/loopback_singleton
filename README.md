# loopback-singleton

`loopback-singleton` is a lightweight Python package that gives multiple local processes access to a **single shared object instance** hosted in a background daemon on `127.0.0.1`.

It is useful when you want one process-external object (cache, counter, coordinator, adapter, etc.) and you want all local workers to call into that object without standing up a full RPC system.

## Current status (v0.0.1)

This is an MVP release (`0.0.1`) focused on reliability of startup/connect behavior and process coordination.

### What works today

- Local singleton daemon auto-start on first use.
- Safe-ish concurrent startup with file locking to reduce duplicate daemons.
- Authenticated handshake (shared token in runtime dir) between client and daemon.
- Sequential method execution on the singleton object (single executor queue).
- Idle TTL auto-shutdown for daemon cleanup.
- Recovery from stale or corrupted runtime metadata.
- Cross-platform runtime location strategy (Windows + POSIX fallback behavior).

## Installation

```bash
pip install loopback-singleton
```

For local development:

```bash
pip install -e .[dev]
```

## Quickstart

Create a module with a factory target (class or callable):

```python
# mypkg/services.py
class Counter:
    def __init__(self):
        self.value = 0

    def inc(self) -> int:
        self.value += 1
        return self.value

    def ping(self) -> str:
        return "pong"
```

Use `local_singleton` from any process:

```python
from loopback_singleton import local_singleton

svc = local_singleton(
    name="my-counter",
    factory="mypkg.services:Counter",
    idle_ttl=2.0,
    serializer="pickle",
)

with svc.proxy() as obj:
    print(obj.ping())
    print(obj.inc())
```

### API overview

```python
local_singleton(
    name: str,
    factory: str,
    *,
    scope: str = "user",
    idle_ttl: float = 2.0,
    serializer: str = "pickle",
    connect_timeout: float = 0.5,
    start_timeout: float = 3.0,
)
```

- `name`: singleton identity (shared runtime namespace).
- `factory`: import string in form `"module:callable_or_class"`.
- `scope`: currently only `"user"` is implemented.
- `idle_ttl`: daemon stops after this many seconds with zero active connections.
- `serializer`: currently only `"pickle"` is implemented.
- `connect_timeout`, `start_timeout`: socket/startup tuning.

`svc.proxy()` returns a dynamic proxy where method calls are forwarded to the daemon.

## How it works

1. Client computes runtime paths for the singleton name.
2. Client attempts connection using runtime metadata.
3. If missing/failing, it takes a file lock, cleans stale metadata, and spawns daemon.
4. Daemon binds ephemeral loopback TCP port, writes runtime metadata, and serves requests.
5. Each `CALL` request is executed sequentially against one in-memory object instance.

## Error model

Main exception classes exported by the package:

- `LoopbackSingletonError` (base)
- `DaemonConnectionError`
  - `ConnectionFailedError`
  - `HandshakeError`
- `RemoteError` (remote traceback payload)

## Security notes (important)

This MVP uses `pickle` for transport serialization. `pickle` is **not safe for untrusted input** and can execute arbitrary code.

Use this package only in trusted local environments for now.

## Runtime files and cleanup

Runtime files are created under:

- **Windows:** `%LOCALAPPDATA%/loopback-singleton/<name>/`
- **Linux/macOS:** `$XDG_RUNTIME_DIR/loopback-singleton/<name>/`
- **POSIX fallback:** `~/.cache/loopback-singleton/<name>/`

If startup repeatedly fails due to stale metadata, stop clients and remove the directory for that singleton name.

## Known limitations (MVP)

- Factory must be an import string (`"module:callable_or_class"`).
- No identity transparency for proxies (`isinstance(proxy, MyType)` is not preserved).
- No magic-method forwarding (`__len__`, operators, iteration, etc.).
- Only `scope="user"` implemented.
- Only `serializer="pickle"` implemented (`msgpack` placeholder exists but not implemented).
- Transport is loopback TCP only.

## Development

Run tests:

```bash
pytest -q
```

Build package:

```bash
python -m build
```

## Future work

Planned directions for post-MVP releases:

- **Safer serialization options**
  - Implement `msgpack` serializer path and typed payload envelopes.
  - Add optional schema validation for RPC payloads.

- **Richer proxy semantics**
  - Support selected dunder/magic methods.
  - Improve error transport with structured remote exception metadata.

- **Lifecycle and observability**
  - Add daemon health/metrics endpoint(s) and lightweight tracing hooks.
  - Expose explicit client APIs for graceful shutdown and restart policies.

- **Scope and deployment flexibility**
  - Add additional scope modes beyond per-user.
  - Evaluate optional Unix domain socket transport on POSIX.

- **Robustness and compatibility**
  - Protocol version negotiation for rolling upgrades.
  - Expanded stress/regression suite for high-concurrency scenarios.

- **Security hardening**
  - Optional mutual-auth improvements and stricter runtime file hardening.
  - Guidance and tooling for locked-down local deployments.

Contributions and issue reports are welcome at:

- Repository: <https://github.com/TovarnovM/loopback_singleton>
- Issues: <https://github.com/TovarnovM/loopback_singleton/issues>
