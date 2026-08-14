from __future__ import annotations

import os
import tempfile
from pathlib import Path


def ensure_private_directory(path: Path) -> Path:
    """Create a local data directory and restrict it to the current user on POSIX."""
    path.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        try:
            path.chmod(0o700)
        except OSError:
            # Some removable/shared filesystems do not implement POSIX modes.
            pass
    return path


def harden_private_file(path: Path) -> None:
    """Apply owner-only POSIX permissions when the filesystem supports them."""
    if os.name != "nt":
        try:
            path.chmod(0o600)
        except OSError:
            pass


def atomic_write_private_text(path: Path, content: str, encoding: str = "utf-8") -> None:
    """Atomically replace a text file without briefly creating a world-readable file."""
    ensure_private_directory(path.parent)
    descriptor: int | None = None
    temporary: Path | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
            text=True,
        )
        temporary = Path(temporary_name)
        if os.name != "nt":
            os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding=encoding, newline="\n") as handle:
            descriptor = None
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
        harden_private_file(path)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if temporary is not None:
            try:
                temporary.unlink()
            except OSError:
                pass
