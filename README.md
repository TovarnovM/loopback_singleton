# loopback-singleton

`loopback-singleton` provides a process-external singleton object shared by all local processes through a loopback daemon (`127.0.0.1`).

## Installation

```bash
pip install loopback-singleton
```

## Quickstart

```python
from loopback_singleton import local_singleton

svc = local_singleton(
    "myname",
    factory="mypkg.mymod:MyObj",
    idle_ttl=2.0,
    serializer="pickle",
)

with svc.proxy() as obj:
    obj.method(...)
```

Prefer `with` or explicit `close()` for deterministic disconnect. `__del__` cleanup is best-effort only.

## MVP limitations

- **Security warning:** this MVP uses `pickle` for transport; pickle is trusted-local-only and can execute arbitrary code if input is untrusted.
- Factory must be an import string: `"pkg.module:callable_or_class"`.
- Proxy is not identity-transparent (`isinstance(proxy, MyObj)` is not preserved).
- No magic method proxying in MVP (`__len__`, `__iter__`, operators, etc.).

## Troubleshooting

If startup keeps trying stale endpoints, stop clients and remove the runtime directory for your service name:

- Windows: `%LOCALAPPDATA%/loopback-singleton/<name>/`
- Linux/macOS: `$XDG_RUNTIME_DIR/loopback-singleton/<name>/` or `~/.cache/loopback-singleton/<name>/`
