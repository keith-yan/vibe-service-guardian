#!/usr/bin/env python3
"""Create a macOS build-kit tarball while preserving executable script modes."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import tarfile
from pathlib import Path, PurePosixPath


EXECUTABLE_SUFFIXES = {".command", ".sh"}
REQUIRED_EXECUTABLES = {
    "Run-macOS-VM-Auto-Test.command",
    "Start-macOS-Manual-Test.command",
    "Finish-macOS-Manual-Test.command",
    "scripts/Build-Portable-macOS.sh",
    "scripts/Validate-macOS.sh",
}


def _mode_for(path: Path, *, is_directory: bool) -> int:
    if is_directory or path.suffix in EXECUTABLE_SUFFIXES:
        return 0o755
    return 0o644


def create_archive(root: Path, output: Path) -> None:
    if not root.is_dir():
        raise ValueError(f"build-kit root does not exist: {root}")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.unlink(missing_ok=True)

    paths = [root, *sorted(root.rglob("*"), key=lambda item: item.as_posix())]
    with output.open("wb") as raw_output:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw_output, mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT) as archive:
                for path in paths:
                    relative = path.relative_to(root.parent)
                    archive_name = relative.as_posix()
                    info = archive.gettarinfo(str(path), arcname=archive_name)
                    info.uid = 0
                    info.gid = 0
                    info.uname = ""
                    info.gname = ""
                    info.mtime = 0
                    info.mode = _mode_for(path, is_directory=path.is_dir())
                    if path.is_file():
                        with path.open("rb") as source:
                            archive.addfile(info, source)
                    else:
                        archive.addfile(info)


def validate_archive(root_name: str, output: Path) -> dict[str, int]:
    file_count = 0
    uncompressed_bytes = 0
    executable_modes: dict[str, int] = {}
    with tarfile.open(output, mode="r:gz") as archive:
        for member in archive.getmembers():
            member_path = PurePosixPath(member.name)
            if member_path.is_absolute() or ".." in member_path.parts:
                raise ValueError(f"unsafe tar entry: {member.name}")
            if not member_path.parts or member_path.parts[0] != root_name:
                raise ValueError(f"unexpected archive root: {member.name}")
            if member.isfile():
                file_count += 1
                uncompressed_bytes += member.size
            relative = PurePosixPath(*member_path.parts[1:]).as_posix()
            if relative in REQUIRED_EXECUTABLES:
                executable_modes[relative] = member.mode

    missing = sorted(REQUIRED_EXECUTABLES - executable_modes.keys())
    if missing:
        raise ValueError(f"required executable entries missing: {missing}")
    not_executable = sorted(
        path for path, mode in executable_modes.items() if mode & 0o111 == 0
    )
    if not_executable:
        raise ValueError(f"executable mode missing: {not_executable}")
    return {"entries": file_count, "uncompressed_bytes": uncompressed_bytes}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    root = args.root.resolve()
    output = args.output.resolve()
    create_archive(root, output)
    result = validate_archive(root.name, output)
    digest = hashlib.sha256(output.read_bytes()).hexdigest().upper()
    checksum_path = Path(f"{output}.sha256")
    checksum_path.write_text(f"{digest}  {output.name}\n", encoding="ascii")
    print(
        json.dumps(
            {
                "ok": True,
                "archive": str(output),
                "sha256": digest,
                **result,
                "executable_modes_preserved": True,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
