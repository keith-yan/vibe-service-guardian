#!/usr/bin/env python3
"""Generate or verify hash-locked, wheel-only dependency files.

Generation intentionally contacts only the public PyPI index and strips pip
configuration variables from the resolver subprocess.  Runtime/build locks use
``--no-deps`` because every required build dependency is explicitly pinned in
the source requirement files.  The audit-tool lock resolves and pins its full
dependency graph for the Linux CI job.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


SCHEMA_VERSION = "1.0"
SUPPORTED_PYTHONS = ("3.10", "3.11", "3.12")
PLATFORMS = {
    "windows": ("win_amd64",),
    "macos": ("macosx_11_0_x86_64", "macosx_11_0_arm64"),
    "linux": ("manylinux2014_x86_64", "manylinux2014_aarch64"),
}
PIN_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9_.-]*)==([^\s;]+)$")


@dataclass(frozen=True)
class LockTarget:
    name: str
    python_version: str
    platforms: tuple[str, ...]
    sources: tuple[str, ...]
    resolve_dependencies: bool = False


def _py_tag(version: str) -> str:
    return "py" + version.replace(".", "")


def targets() -> list[LockTarget]:
    result = [
        LockTarget(
            "bootstrap-py3.txt",
            "3.10",
            ("any",),
            ("requirements-bootstrap.txt",),
        )
    ]
    for platform_name, platform_tags in PLATFORMS.items():
        build_source = {
            "windows": "requirements-build.txt",
            "macos": "requirements-build-macos.txt",
            "linux": "requirements-build-linux.txt",
        }[platform_name]
        for version in SUPPORTED_PYTHONS:
            tag = _py_tag(version)
            result.append(
                LockTarget(
                    f"runtime-{platform_name}-{tag}.txt",
                    version,
                    platform_tags,
                    ("requirements.txt",),
                )
            )
            result.append(
                LockTarget(
                    f"build-{platform_name}-{tag}.txt",
                    version,
                    platform_tags,
                    ("requirements.txt", build_source),
                )
            )
    result.append(
        LockTarget(
            "audit-linux-py312.txt",
            "3.12",
            PLATFORMS["linux"],
            ("requirements-audit.txt",),
            resolve_dependencies=True,
        )
    )
    return result


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _canonical_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def _validate_source_pins(root: Path, source_names: tuple[str, ...]) -> None:
    for source_name in source_names:
        path = root / source_name
        for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            value = raw.strip()
            if not value or value.startswith("#"):
                continue
            if not PIN_RE.fullmatch(value):
                raise ValueError(
                    f"{source_name}:{line_number} must be an exact name==version pin"
                )


def _resolver_environment() -> dict[str, str]:
    environment = dict(os.environ)
    for key in list(environment):
        if key.upper().startswith("PIP_"):
            environment.pop(key, None)
    environment.update(
        {
            "PIP_CONFIG_FILE": os.devnull,
            "PIP_DISABLE_PIP_VERSION_CHECK": "1",
            "PIP_NO_INPUT": "1",
        }
    )
    return environment


def _archive_hash(item: dict[str, Any]) -> str:
    archive = (item.get("download_info") or {}).get("archive_info") or {}
    hashes = archive.get("hashes") or {}
    value = hashes.get("sha256")
    if isinstance(value, str) and re.fullmatch(r"[0-9a-fA-F]{64}", value):
        return value.lower()
    legacy = archive.get("hash")
    if isinstance(legacy, str) and legacy.startswith("sha256="):
        value = legacy.removeprefix("sha256=")
        if re.fullmatch(r"[0-9a-fA-F]{64}", value):
            return value.lower()
    raise ValueError("pip report item has no SHA-256 archive hash")


def _validate_download_url(item: dict[str, Any]) -> None:
    url = str((item.get("download_info") or {}).get("url") or "")
    parsed = urlsplit(url)
    hostname = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not (
        hostname == "pythonhosted.org" or hostname.endswith(".pythonhosted.org")
    ):
        raise ValueError(f"resolver returned a non-PyPI artifact URL for {hostname or 'unknown'}")


def _resolve(
    root: Path,
    target: LockTarget,
    platform_tag: str,
) -> dict[str, tuple[str, set[str]]]:
    with tempfile.TemporaryDirectory(prefix="vsg-lock-") as directory:
        report = Path(directory) / "report.json"
        command = [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--dry-run",
            "--ignore-installed",
            "--no-input",
            "--disable-pip-version-check",
            "--only-binary=:all:",
            "--index-url",
            "https://pypi.org/simple",
            "--report",
            str(report),
            "--python-version",
            target.python_version,
        ]
        if platform_tag == "any":
            command.extend(["--platform", "any", "--implementation", "py", "--abi", "none"])
        else:
            abi = "cp" + target.python_version.replace(".", "")
            command.extend(
                [
                    "--platform",
                    platform_tag,
                    "--implementation",
                    "cp",
                    "--abi",
                    abi,
                ]
            )
        if not target.resolve_dependencies:
            command.append("--no-deps")
        for source_name in target.sources:
            command.extend(["--requirement", str(root / source_name)])
        completed = subprocess.run(
            command,
            cwd=root,
            env=_resolver_environment(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=240,
            check=False,
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout)[-2000:]
            raise RuntimeError(
                f"pip lock resolution failed for {target.name}/{platform_tag}: {detail}"
            )
        payload = json.loads(report.read_text(encoding="utf-8"))

    packages: dict[str, tuple[str, set[str]]] = {}
    for item in payload.get("install") or []:
        if not isinstance(item, dict):
            continue
        _validate_download_url(item)
        metadata = item.get("metadata") or {}
        name = _canonical_name(str(metadata.get("name") or ""))
        version = str(metadata.get("version") or "")
        if not name or not version:
            raise ValueError("pip report item is missing normalized package metadata")
        archive_hash = _archive_hash(item)
        existing = packages.get(name)
        if existing and existing[0] != version:
            raise ValueError(f"conflicting versions for {name}: {existing[0]} and {version}")
        hashes = existing[1] if existing else set()
        hashes.add(archive_hash)
        packages[name] = (version, hashes)
    if not packages:
        raise ValueError(f"resolver produced no packages for {target.name}/{platform_tag}")
    return packages


def _merge_packages(
    destination: dict[str, tuple[str, set[str]]],
    incoming: dict[str, tuple[str, set[str]]],
) -> None:
    for name, (version, hashes) in incoming.items():
        existing = destination.get(name)
        if existing and existing[0] != version:
            raise ValueError(
                f"architecture resolution changed {name} from {existing[0]} to {version}"
            )
        merged_hashes = existing[1] if existing else set()
        merged_hashes.update(hashes)
        destination[name] = (version, merged_hashes)


def _render_lock(target: LockTarget, packages: dict[str, tuple[str, set[str]]]) -> str:
    lines = [
        "# Generated by scripts/Requirement-Locks.py; do not edit by hand.",
        f"# Python: {target.python_version}",
        f"# Platforms: {', '.join(target.platforms)}",
        f"# Sources: {', '.join(target.sources)}",
        "--only-binary=:all:",
        "",
    ]
    for name in sorted(packages):
        version, hashes = packages[name]
        ordered_hashes = sorted(hashes)
        lines.append(f"{name}=={version} \\")
        for index, archive_hash in enumerate(ordered_hashes):
            continuation = " \\" if index < len(ordered_hashes) - 1 else ""
            lines.append(f"    --hash=sha256:{archive_hash}{continuation}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def generate(root: Path, output_dir: Path) -> None:
    root = root.resolve(strict=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    generated_targets = targets()
    source_names = sorted({name for target in generated_targets for name in target.sources})
    source_hashes = {name: _sha256(root / name) for name in source_names}
    lock_hashes: dict[str, str] = {}
    for target in generated_targets:
        _validate_source_pins(root, target.sources)
        packages: dict[str, tuple[str, set[str]]] = {}
        for platform_tag in target.platforms:
            _merge_packages(packages, _resolve(root, target, platform_tag))
        lock_path = output_dir / target.name
        lock_path.write_text(_render_lock(target, packages), encoding="utf-8", newline="\n")
        lock_hashes[target.name] = _sha256(lock_path)
        print(f"generated {target.name}: {len(packages)} packages")
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "generator": "scripts/Requirement-Locks.py",
        "policy": {
            "index": "https://pypi.org/simple",
            "wheel_only": True,
            "hash_algorithm": "sha256",
            "runtime_build_no_deps": True,
        },
        "source_hashes": source_hashes,
        "lock_hashes": lock_hashes,
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _logical_requirements(text: str) -> list[str]:
    values: list[str] = []
    current = ""
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        current = f"{current} {stripped}".strip()
        if current.endswith("\\"):
            current = current[:-1].rstrip()
            continue
        values.append(current)
        current = ""
    if current:
        values.append(current)
    return values


def verify(root: Path, output_dir: Path) -> None:
    root = root.resolve(strict=True)
    manifest_path = output_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("requirement-lock manifest schema version is unsupported")
    expected_targets = {target.name: target for target in targets()}
    recorded_hashes = manifest.get("lock_hashes") or {}
    if set(recorded_hashes) != set(expected_targets):
        raise ValueError("requirement-lock manifest target set is incomplete or unexpected")
    for source_name, expected_hash in (manifest.get("source_hashes") or {}).items():
        if _sha256(root / source_name) != expected_hash:
            raise ValueError(f"source requirement changed without lock refresh: {source_name}")
    for name, target in expected_targets.items():
        path = output_dir / name
        if _sha256(path) != recorded_hashes[name]:
            raise ValueError(f"lock digest mismatch: {name}")
        values = _logical_requirements(path.read_text(encoding="utf-8"))
        package_values = [value for value in values if not value.startswith("--")]
        if not package_values:
            raise ValueError(f"lock has no packages: {name}")
        for value in package_values:
            requirement, separator, hashes = value.partition(" ")
            if not PIN_RE.fullmatch(requirement):
                raise ValueError(f"lock contains a non-exact pin: {name}: {requirement}")
            if not separator or "--hash=sha256:" not in hashes:
                raise ValueError(f"lock contains an unhashed pin: {name}: {requirement}")
        _validate_source_pins(root, target.sources)
    actual_text_locks = {path.name for path in output_dir.glob("*.txt")}
    if actual_text_locks != set(expected_targets):
        raise ValueError("requirements-lock directory contains stale or missing text locks")
    print(f"verified {len(expected_targets)} hash-locked requirement files")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--generate", action="store_true")
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    if args.generate == args.verify:
        parser.error("choose exactly one of --generate or --verify")
    root = args.root.resolve(strict=True)
    output_dir = (args.output_dir or (root / "requirements-lock")).resolve()
    if args.generate:
        generate(root, output_dir)
    else:
        verify(root, output_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
