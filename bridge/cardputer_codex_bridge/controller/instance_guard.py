from __future__ import annotations

import importlib
import os
import tempfile
from contextlib import suppress
from pathlib import Path
from typing import BinaryIO, Protocol, cast


class ControllerAlreadyRunningError(RuntimeError):
    """別processがcontroller runtimeを所有している。"""


class _FcntlModule(Protocol):
    LOCK_EX: int
    LOCK_NB: int
    LOCK_UN: int

    def flock(self, file_descriptor: int, operation: int) -> None: ...


class ControllerInstanceGuard:
    """process終了時にOSが回収するcontroller単一起動lock。"""

    def __init__(self, lock_path: Path | None = None) -> None:
        self._lock_path = lock_path or _default_lock_path()
        self._stream: BinaryIO | None = None

    def acquire(self) -> None:
        if self._stream is not None:
            raise RuntimeError("controller instance guard is already acquired")
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        stream = self._lock_path.open("a+b")
        try:
            stream.seek(0, os.SEEK_END)
            if stream.tell() == 0:
                stream.write(b"\0")
                stream.flush()
            stream.seek(0)
            try:
                _lock(stream)
            except OSError as error:
                raise ControllerAlreadyRunningError(
                    "controller is already running"
                ) from error
        except BaseException:
            stream.close()
            raise
        self._stream = stream

    def close(self) -> None:
        stream = self._stream
        if stream is None:
            return
        self._stream = None
        try:
            stream.seek(0)
            with suppress(OSError):
                _unlock(stream)
        finally:
            stream.close()

    def __enter__(self) -> ControllerInstanceGuard:
        self.acquire()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()


def _default_lock_path() -> Path:
    return Path(tempfile.gettempdir()) / "cardputer-codex-controller" / "controller.lock"


def _lock(stream: BinaryIO) -> None:
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        return

    fcntl_module = cast(_FcntlModule, importlib.import_module("fcntl"))
    fcntl_module.flock(
        stream.fileno(),
        fcntl_module.LOCK_EX | fcntl_module.LOCK_NB,
    )


def _unlock(stream: BinaryIO) -> None:
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        return

    fcntl_module = cast(_FcntlModule, importlib.import_module("fcntl"))
    fcntl_module.flock(stream.fileno(), fcntl_module.LOCK_UN)


__all__ = ["ControllerAlreadyRunningError", "ControllerInstanceGuard"]
