#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path


EXCLUDED_DIRECTORY_NAMES = {
    ".git",
    ".codex-tmp",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "__pycache__",
    "build",
    "data",
    "dist",
    "htmlcov",
    "release",
}
FORBIDDEN_FILENAMES = {
    ".env",
    "runtime.json",
    "id_rsa",
    "id_ed25519",
}
FORBIDDEN_SUFFIXES = {
    ".db",
    ".key",
    ".log",
    ".p12",
    ".pem",
    ".pfx",
    ".pyc",
    ".sqlite",
    ".sqlite3",
}
TEXT_SUFFIXES = {
    "",
    ".cmd",
    ".css",
    ".html",
    ".ini",
    ".js",
    ".json",
    ".md",
    ".ps1",
    ".py",
    ".sh",
    ".svg",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}
SECRET_PATTERNS = {
    "private-key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "provider-token": re.compile(
        r"(?:sk-[A-Za-z0-9_-]{16,}|github_pat_[A-Za-z0-9_]{20,}|"
        r"gh[pousr]_[A-Za-z0-9_]{20,}|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{30,}|"
        r"xox[baprs]-[A-Za-z0-9-]{10,})"
    ),
    "credential-uri": re.compile(
        r"\b[a-z][a-z0-9+.-]*://[^\s/@:]+:[^\s/@]+@",
        re.IGNORECASE,
    ),
    "secret-assignment": re.compile(
        r"(?i)\b(?:api[_-]?key|access[_-]?token|auth[_-]?token|password|passwd|"
        r"client[_-]?secret)\s*[:=]\s*['\"]?[A-Za-z0-9._~+/-]{12,}"
    ),
}
IDENTITY_PATTERNS = {
    "windows-user-path": re.compile(r"[A-Za-z]:\\Users\\[^\\/\s]+", re.IGNORECASE),
    "macos-user-path": re.compile(re.escape("/" + "Users" + "/") + r"[^/\s]+"),
}


def _excluded(relative: Path) -> bool:
    return any(
        part in EXCLUDED_DIRECTORY_NAMES or part.startswith(".venv")
        for part in relative.parts[:-1]
    )


def _inside(root: Path, candidate: Path) -> bool:
    try:
        return os.path.commonpath([root, candidate]) == str(root)
    except (OSError, ValueError):
        return False


def audit(root: Path) -> list[dict[str, object]]:
    root = root.resolve(strict=True)
    findings: list[dict[str, object]] = []
    for path in sorted(root.rglob("*")):
        try:
            relative = path.relative_to(root)
        except ValueError:
            continue
        if _excluded(relative) or path.is_dir():
            continue
        relative_name = relative.as_posix()
        if path.is_symlink():
            target = path.resolve(strict=False)
            if not _inside(root, target):
                findings.append(
                    {"kind": "external-symlink", "path": relative_name, "line": None}
                )
            continue
        lowered_name = path.name.lower()
        lowered_suffix = path.suffix.lower()
        if (
            lowered_name in FORBIDDEN_FILENAMES
            or lowered_name.startswith(".env.")
            or lowered_suffix in FORBIDDEN_SUFFIXES
        ):
            findings.append({"kind": "forbidden-file", "path": relative_name, "line": None})
            continue
        if lowered_suffix not in TEXT_SUFFIXES:
            continue
        try:
            if path.stat().st_size > 2_000_000:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            findings.append({"kind": "unreadable-file", "path": relative_name, "line": None})
            continue
        for line_number, line in enumerate(text.splitlines(), start=1):
            for label, pattern in {**SECRET_PATTERNS, **IDENTITY_PATTERNS}.items():
                if pattern.search(line):
                    findings.append(
                        {"kind": label, "path": relative_name, "line": line_number}
                    )
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fail when an intended public source tree contains local data or likely secrets."
    )
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    findings = audit(args.root)
    print(
        json.dumps(
            {"ok": not findings, "root": str(args.root.resolve()), "findings": findings},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
