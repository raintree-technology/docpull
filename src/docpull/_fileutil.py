"""Small filesystem helpers: atomic text writes and advisory file locks."""

from __future__ import annotations

import contextlib
import os
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import IO, Any

try:  # POSIX
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - Windows
    _fcntl = None  # type: ignore[assignment]

try:  # Windows
    import msvcrt as _msvcrt
except ImportError:
    _msvcrt = None  # type: ignore[assignment]


def atomic_write_text(path: Path | str, text: str, *, encoding: str = "utf-8") -> None:
    """Write ``text`` to ``path`` via a unique temp file in the same dir + ``os.replace``.

    Each writer gets its own temp file, so concurrent writers never clobber
    each other's half-written temp; the last ``os.replace`` wins atomically.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding=encoding,
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            tmp_name = handle.name
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, target)
    except BaseException:
        if tmp_name is not None:
            with contextlib.suppress(OSError):
                os.unlink(tmp_name)
        raise


def _lock(handle: IO[Any]) -> None:
    if _fcntl is not None:
        _fcntl.flock(handle.fileno(), _fcntl.LOCK_EX)
    elif _msvcrt is not None:  # pragma: no cover - Windows
        handle.seek(0)
        while True:
            try:
                _msvcrt.locking(handle.fileno(), _msvcrt.LK_LOCK, 1)
                return
            except OSError:
                continue


def _unlock(handle: IO[Any]) -> None:
    if _fcntl is not None:
        _fcntl.flock(handle.fileno(), _fcntl.LOCK_UN)
    elif _msvcrt is not None:  # pragma: no cover - Windows
        handle.seek(0)
        with contextlib.suppress(OSError):
            _msvcrt.locking(handle.fileno(), _msvcrt.LK_UNLCK, 1)


@contextlib.contextmanager
def file_lock(path: Path | str) -> Iterator[None]:
    """Exclusive advisory lock on ``path`` (created if missing), blocking.

    Uses ``fcntl.flock`` on POSIX and ``msvcrt.locking`` on Windows; a no-op
    where neither exists. Advisory only: cooperating writers must all use it.
    """
    lock_path = Path(path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "a+b") as handle:
        _lock(handle)
        try:
            yield
        finally:
            _unlock(handle)
