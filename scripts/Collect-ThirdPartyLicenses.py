#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-")


def _distribution_license(distribution_name: str) -> tuple[str, str, bytes, str]:
    distribution = importlib.metadata.distribution(distribution_name)
    candidates = []
    for item in distribution.files or ():
        lowered = str(item).lower()
        if "license" in lowered or "copying" in lowered:
            candidates.append(item)
    if not candidates:
        raise RuntimeError(f"{distribution_name} installation does not contain a license file")
    candidates.sort(key=lambda item: (len(Path(str(item)).parts), len(str(item))))
    selected = candidates[0]
    source = Path(distribution.locate_file(selected))
    if not source.is_file():
        raise RuntimeError(f"license file is missing: {source}")
    name = distribution.metadata.get("Name") or distribution_name
    return name, distribution.version, source.read_bytes(), source.name


def _cpython_license_candidates(
    *,
    override: str | None = None,
    base_prefix: Path | None = None,
    prefix: Path | None = None,
    executable: Path | None = None,
    platform_name: str | None = None,
    major_minor: str | None = None,
    applications_root: Path | None = None,
) -> list[Path]:
    """Return bounded, deterministic license locations for this interpreter.

    The python.org macOS installer deliberately keeps its composite license at
    ``/Applications/Python X.Y/License.rtf`` instead of beside the framework
    executable.  Keep that provider-specific location explicit rather than
    recursively searching the host filesystem.
    """

    if override is None:
        override = os.environ.get("VSG_PYTHON_LICENSE")
    if base_prefix is None:
        base_prefix = Path(sys.base_prefix)
    if prefix is None:
        prefix = Path(sys.prefix)
    if executable is None:
        executable = Path(sys.executable).resolve()
    if platform_name is None:
        platform_name = sys.platform
    if major_minor is None:
        major_minor = f"{sys.version_info.major}.{sys.version_info.minor}"
    if applications_root is None:
        applications_root = Path("/Applications")

    candidates: list[Path] = []
    if override:
        candidates.append(Path(override).expanduser())

    runtime_roots = (base_prefix, prefix, executable.parent)
    for runtime_root in runtime_roots:
        candidates.extend((runtime_root / "LICENSE.txt", runtime_root / "LICENSE"))

    if platform_name == "darwin":
        candidates.append(applications_root / f"Python {major_minor}" / "License.rtf")

    unique: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = os.path.normcase(os.path.abspath(candidate))
        if key not in seen:
            seen.add(key)
            unique.append(candidate)
    return unique


def _looks_like_cpython_license(content: bytes) -> bool:
    normalized = content.lower()
    return (
        len(content) >= 1_000
        and b"python software foundation license" in normalized
        and b"license agreement" in normalized
    )


def _select_cpython_license(
    candidates: list[Path], version: str
) -> tuple[str, str, bytes, str]:
    for candidate in candidates:
        if not candidate.is_file():
            continue
        try:
            content = candidate.read_bytes()
        except OSError:
            continue
        if _looks_like_cpython_license(content):
            return "CPython", version, content, candidate.name
    raise RuntimeError(
        "CPython license was not found in the selected interpreter installation; "
        "checked LICENSE.txt, LICENSE, and the official macOS "
        "/Applications/Python X.Y/License.rtf location. Set VSG_PYTHON_LICENSE "
        "to the exact local license path."
    )


def _cpython_license() -> tuple[str, str, bytes, str]:
    return _select_cpython_license(_cpython_license_candidates(), sys.version.split()[0])


def _timestamp() -> str:
    source_date_epoch = os.environ.get("SOURCE_DATE_EPOCH")
    if source_date_epoch:
        moment = datetime.fromtimestamp(int(source_date_epoch), tz=timezone.utc)
    else:
        moment = datetime.now(timezone.utc)
    return moment.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _components() -> list[tuple[str, str, bytes, str, str, str]]:
    return [
        (*_cpython_license(), "NOASSERTION", "https://www.python.org/"),
        (*_distribution_license("psutil"), "BSD-3-Clause", "https://github.com/giampaolo/psutil"),
        (
            *_distribution_license("pyinstaller"),
            "GPL-2.0-or-later WITH Bootloader-exception",
            "https://github.com/pyinstaller/pyinstaller",
        ),
    ]


def verify_components() -> dict[str, object]:
    components = _components()
    return {
        "ok": True,
        "components": [
            {
                "component": name,
                "version": version,
                "license_file": original_name,
                "sha256": hashlib.sha256(content).hexdigest(),
            }
            for name, version, content, original_name, _license_id, _project_url in components
        ],
    }


def collect(output: Path, sbom_path: Path, app_version: str) -> dict[str, object]:
    output.mkdir(parents=True, exist_ok=True)
    components = _components()

    manifest_entries: list[dict[str, str]] = []
    packages: list[dict[str, object]] = []
    relationships: list[dict[str, str]] = [
        {
            "spdxElementId": "SPDXRef-DOCUMENT",
            "relationshipType": "DESCRIBES",
            "relatedSpdxElement": "SPDXRef-Package-VSG",
        }
    ]

    for name, version, content, original_name, license_id, project_url in components:
        filename = f"{_safe_name(name)}-{_safe_name(version)}-{_safe_name(original_name)}"
        destination = output / filename
        destination.write_bytes(content)
        digest = _sha256(destination)
        manifest_entries.append(
            {
                "component": name,
                "version": version,
                "file": filename,
                "sha256": digest,
            }
        )
        spdx_id = f"SPDXRef-Package-{_safe_name(name)}"
        packages.append(
            {
                "SPDXID": spdx_id,
                "name": name,
                "versionInfo": version,
                "downloadLocation": project_url,
                "filesAnalyzed": False,
                "licenseConcluded": license_id,
                "licenseDeclared": license_id,
                "copyrightText": "See the collected upstream license text.",
                "externalRefs": [
                    {
                        "referenceCategory": "PACKAGE-MANAGER",
                        "referenceType": "purl",
                        "referenceLocator": (
                            f"pkg:pypi/{name.lower()}@{version}"
                            if name != "CPython"
                            else f"pkg:generic/cpython@{version}"
                        ),
                    }
                ],
            }
        )
        if name in {"CPython", "psutil"}:
            relationships.append(
                {
                    "spdxElementId": "SPDXRef-Package-VSG",
                    "relationshipType": "DEPENDS_ON",
                    "relatedSpdxElement": spdx_id,
                }
            )
        else:
            relationships.append(
                {
                    "spdxElementId": spdx_id,
                    "relationshipType": "BUILD_DEPENDENCY_OF",
                    "relatedSpdxElement": "SPDXRef-Package-VSG",
                }
            )

    manifest = {
        "schema_version": 1,
        "application": "Vibe Service Guardian",
        "application_version": app_version,
        "generated_at": _timestamp(),
        "files": manifest_entries,
    }
    (output / "MANIFEST.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )

    namespace_seed = "|".join(
        [app_version, *[f"{item['component']}:{item['version']}:{item['sha256']}" for item in manifest_entries]]
    )
    sbom = {
        "spdxVersion": "SPDX-2.3",
        "dataLicense": "CC0-1.0",
        "SPDXID": "SPDXRef-DOCUMENT",
        "name": f"Vibe-Service-Guardian-{app_version}",
        "documentNamespace": f"urn:uuid:{uuid5(NAMESPACE_URL, namespace_seed)}",
        "creationInfo": {
            "created": manifest["generated_at"],
            "creators": ["Tool: scripts/Collect-ThirdPartyLicenses.py"],
        },
        "packages": [
            {
                "SPDXID": "SPDXRef-Package-VSG",
                "name": "Vibe Service Guardian",
                "versionInfo": app_version,
                "downloadLocation": "NOASSERTION",
                "filesAnalyzed": False,
                "licenseConcluded": "MIT",
                "licenseDeclared": "MIT",
                "copyrightText": "NOASSERTION",
            },
            *packages,
        ],
        "relationships": relationships,
    }
    sbom_path.parent.mkdir(parents=True, exist_ok=True)
    sbom_path.write_text(
        json.dumps(sbom, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Collect exact installed third-party license texts and generate an SPDX SBOM."
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--sbom", type=Path)
    parser.add_argument("--app-version")
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="Locate and validate every required license without writing package files.",
    )
    args = parser.parse_args()
    if args.verify_only:
        print(json.dumps(verify_components(), ensure_ascii=False, indent=2))
        return 0
    if args.output is None or args.sbom is None or not args.app_version:
        parser.error("--output, --sbom, and --app-version are required unless --verify-only is used")
    manifest = collect(args.output.resolve(), args.sbom.resolve(), args.app_version)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
