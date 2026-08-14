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


def _cpython_license() -> tuple[str, str, bytes, str]:
    override = os.environ.get("VSG_PYTHON_LICENSE")
    candidates = [Path(override).expanduser()] if override else []
    candidates.extend(
        [
            Path(sys.base_prefix) / "LICENSE.txt",
            Path(sys.prefix) / "LICENSE.txt",
            Path(sys.executable).resolve().parent / "LICENSE.txt",
        ]
    )
    for candidate in candidates:
        if candidate.is_file():
            return "CPython", sys.version.split()[0], candidate.read_bytes(), candidate.name
    raise RuntimeError(
        "CPython LICENSE.txt was not found; set VSG_PYTHON_LICENSE to the local license path"
    )


def _timestamp() -> str:
    source_date_epoch = os.environ.get("SOURCE_DATE_EPOCH")
    if source_date_epoch:
        moment = datetime.fromtimestamp(int(source_date_epoch), tz=timezone.utc)
    else:
        moment = datetime.now(timezone.utc)
    return moment.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def collect(output: Path, sbom_path: Path, app_version: str) -> dict[str, object]:
    output.mkdir(parents=True, exist_ok=True)
    components = [
        (*_cpython_license(), "NOASSERTION", "https://www.python.org/"),
        (*_distribution_license("psutil"), "BSD-3-Clause", "https://github.com/giampaolo/psutil"),
        (
            *_distribution_license("pyinstaller"),
            "GPL-2.0-or-later WITH Bootloader-exception",
            "https://github.com/pyinstaller/pyinstaller",
        ),
    ]

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
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--sbom", required=True, type=Path)
    parser.add_argument("--app-version", required=True)
    args = parser.parse_args()
    manifest = collect(args.output.resolve(), args.sbom.resolve(), args.app_version)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
