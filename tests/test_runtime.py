from __future__ import annotations

import pytest

from loopback_singleton.runtime import get_runtime_dir


def test_get_runtime_dir_system_scope_not_implemented() -> None:
    with pytest.raises(NotImplementedError, match="Only scope='user'"):
        get_runtime_dir(name="x", scope="system")
