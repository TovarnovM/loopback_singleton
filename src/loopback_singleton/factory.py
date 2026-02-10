"""Factory normalization and identity helpers."""

from __future__ import annotations

import hashlib
import importlib
import pickle
from typing import Any

FACTORY_IMPORT_ERROR = (
    "Factory must be importable (module-level). "
    "Pass 'pkg.mod:callable' string instead."
)


def normalize_factory(factory: str | Any) -> str:
    """Normalize a factory input into an import string.

    Accepts an existing import string (``module:qualname``) or a module-level
    callable/class object and returns a daemon-safe import string.
    """
    if isinstance(factory, str):
        if ":" not in factory:
            raise TypeError("Factory import string must be in format 'module:callable_or_class'")
        return factory

    if not callable(factory):
        raise TypeError(FACTORY_IMPORT_ERROR)

    module_name = getattr(factory, "__module__", None)
    qualname = getattr(factory, "__qualname__", None)
    if not module_name or not qualname or "<locals>" in qualname:
        raise TypeError(FACTORY_IMPORT_ERROR)

    try:
        module = importlib.import_module(module_name)
        resolved = module
        for attr in qualname.split("."):
            resolved = getattr(resolved, attr)
    except Exception as exc:  # pragma: no cover - exact import failures vary by platform/python
        raise TypeError(FACTORY_IMPORT_ERROR) from exc

    if resolved is not factory:
        raise TypeError(FACTORY_IMPORT_ERROR)

    return f"{module_name}:{qualname}"


def compute_factory_id(factory_import: str, factory_args: tuple[Any, ...], factory_kwargs: dict[str, Any]) -> str:
    payload = pickle.dumps((factory_import, factory_args, factory_kwargs), protocol=pickle.HIGHEST_PROTOCOL)
    return hashlib.blake2b(payload, digest_size=8).hexdigest()
