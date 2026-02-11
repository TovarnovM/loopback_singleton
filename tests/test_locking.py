from __future__ import annotations

import os
from pathlib import Path

import pytest

from loopback_singleton.locking import FileLock


def test_file_lock_creates_parent_dirs_and_lock_file(tmp_path: Path) -> None:
    path = tmp_path / "nested/lockfile.lock"

    assert not path.parent.exists()
    assert not path.exists()

    with FileLock(path):
        assert path.parent.exists()
        assert path.exists()


def test_file_lock_releases_lock_after_context_exit(tmp_path: Path) -> None:
    path = tmp_path / "nested/lockfile.lock"

    with FileLock(path):
        assert path.exists()

    with FileLock(path):
        assert path.exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX-only chmod path")
def test_file_lock_ignores_chmod_errors(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    path = tmp_path / "nested/lockfile.lock"

    def _raise_chmod_error(_path: os.PathLike[str] | str, _mode: int) -> None:
        raise OSError("chmod failed")

    monkeypatch.setattr(os, "chmod", _raise_chmod_error)

    with FileLock(path):
        assert path.exists()
