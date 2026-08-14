#!/bin/sh
set -eu

if [ "$(uname -s)" != "Linux" ]; then
  printf '%s\n' "PyInstaller cannot cross-compile a Linux ELF binary. Run this script on Ubuntu 22.04+ or another compatible Linux host."
  exit 2
fi

PROJECT_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)
cd "$PROJECT_ROOT"
ARCH=$(uname -m)
case "$ARCH" in
  x86_64|aarch64) PACKAGE_ARCH=$ARCH ;;
  *) printf 'Unsupported Linux architecture: %s\n' "$ARCH"; exit 2 ;;
esac

PYTHON_BIN=${PYTHON_BIN:-python3}
BUILD_VENV="$PROJECT_ROOT/.venv-build-linux-$PACKAGE_ARCH"
"$PYTHON_BIN" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 2)'
"$PYTHON_BIN" -m venv "$BUILD_VENV"
PY_TAG=$("$BUILD_VENV/bin/python3" -c 'import sys; print(f"py{sys.version_info.major}{sys.version_info.minor}")')
case "$PY_TAG" in py310|py311|py312) ;; *) printf 'Unsupported Python lock version: %s\n' "$PY_TAG"; exit 2 ;; esac
"$BUILD_VENV/bin/python3" -m pip install --disable-pip-version-check --only-binary=:all: \
  --no-deps --require-hashes --requirement requirements-lock/bootstrap-py3.txt
"$BUILD_VENV/bin/python3" -m pip install --disable-pip-version-check --only-binary=:all: \
  --no-deps --require-hashes --requirement "requirements-lock/build-linux-$PY_TAG.txt"
"$BUILD_VENV/bin/python3" scripts/Requirement-Locks.py --verify
VERSION=$("$BUILD_VENV/bin/python3" -c 'from vsg import __version__; print(__version__)')
"$BUILD_VENV/bin/python3" scripts/Audit-Public-Tree.py --root "$PROJECT_ROOT"
"$BUILD_VENV/bin/python3" -m unittest discover -s tests -v
"$BUILD_VENV/bin/python3" -m PyInstaller --noconfirm --clean VibeServiceGuardian.spec

file dist/VibeServiceGuardian | grep -q 'ELF' || { printf '%s\n' "Built artifact is not an ELF executable."; exit 3; }
RELEASE_ROOT="$PROJECT_ROOT/release"
PORTABLE_ROOT="$RELEASE_ROOT/Vibe-Service-Guardian-Linux-$PACKAGE_ARCH-$VERSION"
mkdir -p "$RELEASE_ROOT"
case "$PORTABLE_ROOT" in "$RELEASE_ROOT"/*) ;; *) printf '%s\n' "Unsafe release path."; exit 3 ;; esac
[ ! -e "$PORTABLE_ROOT" ] || rm -rf "$PORTABLE_ROOT"
mkdir -p "$PORTABLE_ROOT/research" "$PORTABLE_ROOT/scripts" "$PORTABLE_ROOT/docs"

cp dist/VibeServiceGuardian "$PORTABLE_ROOT/VibeServiceGuardian"
cp Start-VSG.sh Stop-VSG.sh Open-VSG.sh Setup-Linux.sh Vibe-Service-Guardian.desktop.in \
  README.md README.en.md SECURITY.md PRIVACY.md THIRD_PARTY_NOTICES.md LICENSE \
  CHANGELOG.md SUPPORT.md LINUX-VALIDATION.md IMPACT.md MAINTAINERS.md ROADMAP.md \
  GOVERNANCE.md "$PORTABLE_ROOT/"
cp research/GITHUB_RESEARCH.md "$PORTABLE_ROOT/research/"
cp scripts/Validate-Linux.sh "$PORTABLE_ROOT/scripts/"
cp docs/AGENT-SUPPORT.md docs/ARCHITECTURE.md docs/MODEL-CAPACITY.md \
  docs/V0.8-FEATURES.md docs/V0.8.1-FEATURES.md docs/V0.8.2-HARDENING.md \
  docs/PRODUCTION-READINESS-0.8.2.md docs/V0.8.3-CONVERGENCE.md \
  docs/PRODUCTION-READINESS-0.8.3.md docs/V0.8.4-P0-CLOSURE.md \
  docs/PRODUCTION-READINESS-0.8.4.md docs/VALIDATION.md docs/EVIDENCE-REGISTER.md \
  "$PORTABLE_ROOT/docs/"
mkdir -p "$PORTABLE_ROOT/docs/case-studies" "$PORTABLE_ROOT/docs/assets"
cp docs/case-studies/README.md docs/case-studies/maintainer-validation.md \
  "$PORTABLE_ROOT/docs/case-studies/"
cp docs/assets/vsg-overview.svg "$PORTABLE_ROOT/docs/assets/"
"$BUILD_VENV/bin/python3" scripts/Collect-ThirdPartyLicenses.py \
  --output "$PORTABLE_ROOT/THIRD_PARTY_LICENSES" \
  --sbom "$PORTABLE_ROOT/SBOM.spdx.json" \
  --app-version "$VERSION"
chmod +x "$PORTABLE_ROOT/VibeServiceGuardian" "$PORTABLE_ROOT"/*.sh "$PORTABLE_ROOT/scripts/Validate-Linux.sh"

ZIP_PATH="$RELEASE_ROOT/Vibe-Service-Guardian-Linux-$PACKAGE_ARCH-$VERSION.zip"
rm -f "$ZIP_PATH" "$ZIP_PATH.sha256"
"$BUILD_VENV/bin/python3" - "$PORTABLE_ROOT" "$ZIP_PATH" <<'PY'
import sys, zipfile
from pathlib import Path
root, target = Path(sys.argv[1]), Path(sys.argv[2])
with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
    for path in sorted(root.rglob("*")):
        if path.is_file():
            archive.write(path, Path(root.name) / path.relative_to(root))
PY
HASH=$(sha256sum "$ZIP_PATH" | awk '{print $1}')
printf '%s  %s\n' "$HASH" "$(basename "$ZIP_PATH")" > "$ZIP_PATH.sha256"
"$BUILD_VENV/bin/python3" scripts/Validate-Archive.py \
  --zip "$ZIP_PATH" --checksum "$ZIP_PATH.sha256" \
  --expected-root "$(basename "$PORTABLE_ROOT")" --version "$VERSION" --platform linux
printf 'Unsigned Linux portable package: %s\nSHA256: %s\n' "$ZIP_PATH" "$HASH"
