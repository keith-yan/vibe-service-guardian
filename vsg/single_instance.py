from __future__ import annotations

import os
from pathlib import Path
from typing import BinaryIO

from .privacy import ensure_private_directory, harden_private_file


class SingleInstanceGuard:
    """Hold a non-blocking OS lock for one VSG data directory.

    The file may remain after a crash, but the operating-system lock never
    does.  A stale file therefore cannot permanently block a future launch.
    """

    def __init__(self, path: Path):
        self.path = path
        self._stream: BinaryIO | None = None

    @property
    def acquired(self) -> bool:
        return self._stream is not None

    def acquire(self) -> bool:
        if self._stream is not None:
            return True
        ensure_private_directory(self.path.parent)
        stream = self.path.open("a+b")
        harden_private_file(self.path)
        try:
            stream.seek(0, os.SEEK_END)
            if stream.tell() == 0:
                stream.write(b"\0")
                stream.flush()
            stream.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError):
            stream.close()
            return False
        self._stream = stream
        return True

    def release(self) -> None:
        stream = self._stream
        self._stream = None
        if stream is None:
            return
        try:
            stream.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        finally:
            stream.close()

    def __enter__(self) -> SingleInstanceGuard:
        if not self.acquire():
            raise RuntimeError("VSG instance lock is already held")
        return self

    def __exit__(self, _exc_type: object, _exc: object, _traceback: object) -> None:
        self.release()
