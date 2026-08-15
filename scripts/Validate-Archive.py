#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
import zipfile
from pathlib import Path, PurePosixPath


FORBIDDEN_NAMES = {".env", "runtime.json"}
FORBIDDEN_SUFFIXES = {".db", ".log", ".pyc", ".sqlite", ".sqlite3"}


def _sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _assert_line_endings(content: bytes, eol: bytes, label: str) -> None:
    if eol == b"\r\n":
        if b"\n" in content.replace(b"\r\n", b""):
            raise ValueError(f"{label} contains non-CRLF line endings")
    elif b"\r\n" in content or b"\r" in content:
        raise ValueError(f"{label} contains non-LF line endings")


def validate(
    zip_path: Path,
    checksum_path: Path,
    expected_root: str,
    version: str,
    platform_name: str,
    package_kind: str = "portable",
) -> dict[str, object]:
    archive_digest = _sha256_file(zip_path)
    checksum_parts = checksum_path.read_text(encoding="ascii").strip().split()
    if not checksum_parts or checksum_parts[0].lower() != archive_digest:
        raise ValueError("SHA-256 sidecar does not match the archive")

    with zipfile.ZipFile(zip_path) as archive:
        infos = archive.infolist()
        if len(infos) > 500:
            raise ValueError("archive contains too many entries")
        names: dict[str, zipfile.ZipInfo] = {}
        total_size = 0
        for info in infos:
            name = info.filename
            if "\\" in name or re.match(r"^[A-Za-z]:", name):
                raise ValueError(f"unsafe archive path: {name}")
            pure = PurePosixPath(name)
            if pure.is_absolute() or ".." in pure.parts or not pure.parts:
                raise ValueError(f"unsafe archive path: {name}")
            if pure.parts[0] != expected_root:
                raise ValueError(f"entry is outside the expected package root: {name}")
            normalized = name.rstrip("/").casefold()
            if normalized in names:
                raise ValueError(f"duplicate archive entry: {name}")
            names[normalized] = info
            unix_mode = (info.external_attr >> 16) & 0o170000
            if unix_mode == 0o120000:
                raise ValueError(f"symlinks are not allowed in portable archives: {name}")
            if info.is_dir():
                continue
            total_size += info.file_size
            if info.file_size > 200_000_000 or total_size > 400_000_000:
                raise ValueError("archive uncompressed size exceeds the safety limit")
            lowered_name = pure.name.lower()
            if (
                "data" in {part.lower() for part in pure.parts[1:-1]}
                or lowered_name in FORBIDDEN_NAMES
                or lowered_name.startswith(".env.")
                or pure.suffix.lower() in FORBIDDEN_SUFFIXES
            ):
                raise ValueError(f"runtime/local data is forbidden in the archive: {name}")

        prefix = f"{expected_root}/"
        if package_kind == "build-kit":
            if platform_name == "windows":
                raise ValueError("Windows does not use a source build kit")
            common_required = {
                prefix + "pyproject.toml",
                prefix + "requirements.txt",
                prefix + "VibeServiceGuardian.spec",
                prefix + "vsg/__init__.py",
                prefix + "tests/test_version.py",
                prefix + "scripts/Audit-Public-Tree.py",
                prefix + "scripts/Collect-ThirdPartyLicenses.py",
                prefix + "scripts/Validate-Archive.py",
                prefix + "docs/V0.8-FEATURES.md",
                prefix + "docs/V0.8.1-FEATURES.md",
                prefix + "docs/V0.8.2-HARDENING.md",
                prefix + "docs/PRODUCTION-READINESS-0.8.2.md",
                prefix + "docs/V0.8.3-CONVERGENCE.md",
                prefix + "docs/PRODUCTION-READINESS-0.8.3.md",
                prefix + "docs/V0.8.4-P0-CLOSURE.md",
                prefix + "docs/PRODUCTION-READINESS-0.8.4.md",
                prefix + "docs/V0.8.5-P2-A.md",
                prefix + "docs/PRODUCTION-READINESS-0.8.5.md",
                prefix + "requirements-bootstrap.txt",
                prefix + "requirements-lock/manifest.json",
                prefix + "requirements-lock/bootstrap-py3.txt",
                prefix + "scripts/Requirement-Locks.py",
            }
            if platform_name == "macos":
                required = common_required | {
                    prefix + "Start-VSG.command",
                    prefix + "Stop-VSG.command",
                    prefix + "Open-VSG.command",
                    prefix + "Setup-macOS.command",
                    prefix + "requirements-build-macos.txt",
                    prefix + "requirements-lock/build-macos-py310.txt",
                    prefix + "requirements-lock/build-macos-py311.txt",
                    prefix + "requirements-lock/build-macos-py312.txt",
                    prefix + "requirements-lock/runtime-macos-py310.txt",
                    prefix + "requirements-lock/runtime-macos-py311.txt",
                    prefix + "requirements-lock/runtime-macos-py312.txt",
                    prefix + "MACOS-VALIDATION.md",
                    prefix + "scripts/Build-Portable-macOS.sh",
                    prefix + "scripts/Validate-macOS.sh",
                }
                scripts = [
                    prefix + "Start-VSG.command",
                    prefix + "Stop-VSG.command",
                    prefix + "Open-VSG.command",
                    prefix + "Setup-macOS.command",
                    prefix + "scripts/Build-Portable-macOS.sh",
                    prefix + "scripts/Validate-macOS.sh",
                ]
            else:
                required = common_required | {
                    prefix + "Start-VSG.sh",
                    prefix + "Stop-VSG.sh",
                    prefix + "Open-VSG.sh",
                    prefix + "Setup-Linux.sh",
                    prefix + "Vibe-Service-Guardian.desktop.in",
                    prefix + "requirements-build-linux.txt",
                    prefix + "requirements-lock/build-linux-py310.txt",
                    prefix + "requirements-lock/build-linux-py311.txt",
                    prefix + "requirements-lock/build-linux-py312.txt",
                    prefix + "requirements-lock/runtime-linux-py310.txt",
                    prefix + "requirements-lock/runtime-linux-py311.txt",
                    prefix + "requirements-lock/runtime-linux-py312.txt",
                    prefix + "LINUX-VALIDATION.md",
                    prefix + "scripts/Build-Portable-Linux.sh",
                    prefix + "scripts/Validate-Linux.sh",
                }
                scripts = [
                    prefix + "Start-VSG.sh",
                    prefix + "Stop-VSG.sh",
                    prefix + "Open-VSG.sh",
                    prefix + "Setup-Linux.sh",
                    prefix + "scripts/Build-Portable-Linux.sh",
                    prefix + "scripts/Validate-Linux.sh",
                ]
            expected_eol = b"\n"
        elif platform_name == "windows":
            required = {
                prefix + "VibeServiceGuardian.exe",
                prefix + "Start-VSG.cmd",
                prefix + "Stop-VSG.cmd",
                prefix + "Open-VSG.cmd",
                prefix + "scripts/Validate-Windows.ps1",
            }
            scripts = [prefix + "Start-VSG.cmd", prefix + "Stop-VSG.cmd", prefix + "Open-VSG.cmd"]
            expected_eol = b"\r\n"
        elif platform_name == "macos":
            required = {
                prefix + "VibeServiceGuardian",
                prefix + "Start-VSG.command",
                prefix + "Stop-VSG.command",
                prefix + "Open-VSG.command",
                prefix + "scripts/Validate-macOS.sh",
            }
            scripts = [
                prefix + "Start-VSG.command",
                prefix + "Stop-VSG.command",
                prefix + "Open-VSG.command",
                prefix + "scripts/Validate-macOS.sh",
            ]
            expected_eol = b"\n"
        else:
            required = {
                prefix + "VibeServiceGuardian",
                prefix + "Start-VSG.sh",
                prefix + "Stop-VSG.sh",
                prefix + "Open-VSG.sh",
                prefix + "Setup-Linux.sh",
                prefix + "Vibe-Service-Guardian.desktop.in",
                prefix + "LINUX-VALIDATION.md",
                prefix + "scripts/Validate-Linux.sh",
            }
            scripts = [
                prefix + "Start-VSG.sh",
                prefix + "Stop-VSG.sh",
                prefix + "Open-VSG.sh",
                prefix + "Setup-Linux.sh",
                prefix + "scripts/Validate-Linux.sh",
            ]
            expected_eol = b"\n"
        required |= {
            prefix + "README.md",
            prefix + "README.en.md",
            prefix + "IMPACT.md",
            prefix + "MAINTAINERS.md",
            prefix + "ROADMAP.md",
            prefix + "GOVERNANCE.md",
            prefix + "LICENSE",
            prefix + "PRIVACY.md",
            prefix + "SECURITY.md",
            prefix + "THIRD_PARTY_NOTICES.md",
            prefix + "docs/AGENT-SUPPORT.md",
            prefix + "docs/ARCHITECTURE.md",
            prefix + "docs/MODEL-CAPACITY.md",
            prefix + "docs/V0.8-FEATURES.md",
            prefix + "docs/V0.8.1-FEATURES.md",
            prefix + "docs/V0.8.2-HARDENING.md",
            prefix + "docs/PRODUCTION-READINESS-0.8.2.md",
            prefix + "docs/V0.8.3-CONVERGENCE.md",
            prefix + "docs/PRODUCTION-READINESS-0.8.3.md",
            prefix + "docs/V0.8.4-P0-CLOSURE.md",
            prefix + "docs/PRODUCTION-READINESS-0.8.4.md",
            prefix + "docs/V0.8.5-P2-A.md",
            prefix + "docs/PRODUCTION-READINESS-0.8.5.md",
            prefix + "docs/VALIDATION.md",
            prefix + "docs/EVIDENCE-REGISTER.md",
            prefix + "docs/case-studies/README.md",
            prefix + "docs/case-studies/maintainer-validation.md",
            prefix + "docs/assets/vsg-overview.svg",
            prefix + "research/GITHUB_RESEARCH.md",
        }
        if package_kind == "portable":
            required |= {
                prefix + "SBOM.spdx.json",
                prefix + "THIRD_PARTY_LICENSES/MANIFEST.json",
            }
        missing = sorted(item for item in required if item.casefold() not in names)
        if missing:
            raise ValueError(f"archive is missing required entries: {missing}")

        for script in scripts:
            content = archive.read(names[script.casefold()])
            _assert_line_endings(content, expected_eol, script)
            if platform_name == "windows":
                content.decode("ascii")
            else:
                content.decode("utf-8")

        components: set[str | None] = set()
        package_versions: dict[str | None, object] = {}
        if package_kind == "portable":
            manifest = json.loads(
                archive.read(names[(prefix + "THIRD_PARTY_LICENSES/MANIFEST.json").casefold()])
            )
            if manifest.get("application_version") != version:
                raise ValueError("license manifest application version is incorrect")
            components = {item.get("component") for item in manifest.get("files", [])}
            if components != {"CPython", "psutil", "pyinstaller"}:
                raise ValueError(f"license manifest components are incomplete: {components}")
            for item in manifest["files"]:
                filename = item.get("file")
                if not isinstance(filename, str) or PurePosixPath(filename).name != filename:
                    raise ValueError("license manifest contains an unsafe filename")
                entry_name = prefix + "THIRD_PARTY_LICENSES/" + filename
                info = names.get(entry_name.casefold())
                if not info:
                    raise ValueError(f"license text is missing: {filename}")
                if _sha256_bytes(archive.read(info)) != item.get("sha256"):
                    raise ValueError(f"license hash mismatch: {filename}")

            sbom = json.loads(archive.read(names[(prefix + "SBOM.spdx.json").casefold()]))
            if sbom.get("spdxVersion") != "SPDX-2.3":
                raise ValueError("SBOM is not SPDX 2.3")
            package_versions = {
                item.get("name"): item.get("versionInfo") for item in sbom.get("packages", [])
            }
            if package_versions.get("Vibe Service Guardian") != version:
                raise ValueError("SBOM application version is incorrect")
            if not {"CPython", "psutil", "pyinstaller"}.issubset(package_versions):
                raise ValueError("SBOM package inventory is incomplete")

    return {
        "ok": True,
        "kind": package_kind,
        "platform": platform_name,
        "version": version,
        "sha256": archive_digest.upper(),
        "entries": len(infos),
        "uncompressed_bytes": total_size,
        "runtime_data_entries": 0,
        "license_components": sorted(item for item in components if isinstance(item, str)),
        "sbom_packages": sorted(item for item in package_versions if isinstance(item, str)),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate a VSG portable or source build-kit ZIP.")
    parser.add_argument("--zip", required=True, type=Path)
    parser.add_argument("--checksum", required=True, type=Path)
    parser.add_argument("--expected-root", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--platform", required=True, choices=("windows", "macos", "linux"))
    parser.add_argument("--kind", choices=("portable", "build-kit"), default="portable")
    args = parser.parse_args()
    result = validate(
        args.zip.resolve(strict=True),
        args.checksum.resolve(strict=True),
        args.expected_root,
        args.version,
        args.platform,
        args.kind,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
