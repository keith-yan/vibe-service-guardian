#!/bin/bash
set -euo pipefail

if [ "$(uname -s)" != "Darwin" ]; then
  printf '%s\n' "PyInstaller 不能从 Windows 交叉编译 macOS Mach-O；请在 macOS 13 或更高版本执行本脚本。"
  exit 2
fi

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd -P)"
cd "$PROJECT_ROOT"
ARCH="$(uname -m)"
case "$ARCH" in
  arm64) PACKAGE_ARCH="arm64" ;;
  x86_64) PACKAGE_ARCH="x86_64" ;;
  *) printf '不支持的 macOS 架构：%s\n' "$ARCH"; exit 2 ;;
esac

REQUESTED_ARCH="${VSG_TARGET_ARCH:-$PACKAGE_ARCH}"
if [ "$REQUESTED_ARCH" != "$PACKAGE_ARCH" ]; then
  printf '%s\n' "当前脚本只生成当前原生架构。请分别在 Apple Silicon 与 Intel Mac 上构建对应包。"
  exit 2
fi

PYTHON_BIN="${PYTHON_BIN:-python3}"
MACOSX_DEPLOYMENT_TARGET="${MACOSX_DEPLOYMENT_TARGET:-13.0}"
export MACOSX_DEPLOYMENT_TARGET
BUILD_VENV="$PROJECT_ROOT/.venv-build-macos-$PACKAGE_ARCH"
"$PYTHON_BIN" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 2)'
"$PYTHON_BIN" -m venv "$BUILD_VENV"
PY_TAG="$("$BUILD_VENV/bin/python3" -c 'import sys; print(f"py{sys.version_info.major}{sys.version_info.minor}")')"
case "$PY_TAG" in py310|py311|py312) ;; *) printf '不支持的 Python 锁版本：%s\n' "$PY_TAG"; exit 2 ;; esac
"$BUILD_VENV/bin/python3" -m pip install --disable-pip-version-check --only-binary=:all: \
  --no-deps --require-hashes --requirement requirements-lock/bootstrap-py3.txt
"$BUILD_VENV/bin/python3" -m pip install --disable-pip-version-check --only-binary=:all: \
  --no-deps --require-hashes --requirement "requirements-lock/build-macos-$PY_TAG.txt"
"$BUILD_VENV/bin/python3" scripts/Requirement-Locks.py --verify
PYTHON_ARCH="$("$BUILD_VENV/bin/python3" -c 'import platform; print(platform.machine())')"
if [ "$PYTHON_ARCH" != "$PACKAGE_ARCH" ]; then
  printf 'Python 架构 %s 与目标架构 %s 不一致。\n' "$PYTHON_ARCH" "$PACKAGE_ARCH"
  exit 2
fi
VERSION="$("$BUILD_VENV/bin/python3" -c 'from vsg import __version__; print(__version__)')"
case "$VERSION" in
  [0-9]*.[0-9]*.[0-9]*) ;;
  *) printf '无效版本号：%s\n' "$VERSION"; exit 2 ;;
esac
"$BUILD_VENV/bin/python3" scripts/Audit-Public-Tree.py --root "$PROJECT_ROOT"
"$BUILD_VENV/bin/python3" -m unittest discover -s tests -v
VSG_TARGET_ARCH="$PACKAGE_ARCH" "$BUILD_VENV/bin/python3" -m PyInstaller \
  --noconfirm --clean VibeServiceGuardian.spec
BUILT_ARCHES="$(lipo -archs dist/VibeServiceGuardian)"
case " $BUILT_ARCHES " in
  *" $PACKAGE_ARCH "*) ;;
  *) printf '构建产物架构不匹配：%s\n' "$BUILT_ARCHES"; exit 3 ;;
esac
codesign --verify --verbose=2 dist/VibeServiceGuardian

RELEASE_ROOT="$PROJECT_ROOT/release"
PORTABLE_ROOT="$RELEASE_ROOT/Vibe-Service-Guardian-macOS-$PACKAGE_ARCH-$VERSION"
mkdir -p "$RELEASE_ROOT"
case "$PORTABLE_ROOT" in
  "$RELEASE_ROOT"/*) ;;
  *) printf '%s\n' "拒绝清理 release 目录之外的路径。"; exit 3 ;;
esac
if [ -e "$PORTABLE_ROOT" ]; then
  rm -rf "$PORTABLE_ROOT"
fi
mkdir -p "$PORTABLE_ROOT/research" "$PORTABLE_ROOT/scripts"

cp dist/VibeServiceGuardian "$PORTABLE_ROOT/VibeServiceGuardian"
cp Start-VSG.command Stop-VSG.command Open-VSG.command \
  README.md README.en.md SECURITY.md PRIVACY.md THIRD_PARTY_NOTICES.md LICENSE \
  CHANGELOG.md SUPPORT.md MACOS-VALIDATION.md LINUX-VALIDATION.md \
  IMPACT.md MAINTAINERS.md ROADMAP.md GOVERNANCE.md "$PORTABLE_ROOT/"
cp research/GITHUB_RESEARCH.md "$PORTABLE_ROOT/research/"
cp scripts/Validate-macOS.sh "$PORTABLE_ROOT/scripts/"
mkdir -p "$PORTABLE_ROOT/docs" "$PORTABLE_ROOT/docs/case-studies" "$PORTABLE_ROOT/docs/assets"
cp docs/AGENT-SUPPORT.md docs/ARCHITECTURE.md docs/MODEL-CAPACITY.md \
  docs/V0.8-FEATURES.md docs/V0.8.1-FEATURES.md docs/V0.8.2-HARDENING.md \
  docs/PRODUCTION-READINESS-0.8.2.md docs/V0.8.3-CONVERGENCE.md \
  docs/PRODUCTION-READINESS-0.8.3.md docs/VALIDATION.md docs/EVIDENCE-REGISTER.md \
  "$PORTABLE_ROOT/docs/"
cp docs/case-studies/README.md docs/case-studies/maintainer-validation.md \
  "$PORTABLE_ROOT/docs/case-studies/"
cp docs/assets/vsg-overview.svg "$PORTABLE_ROOT/docs/assets/"
"$BUILD_VENV/bin/python3" scripts/Collect-ThirdPartyLicenses.py \
  --output "$PORTABLE_ROOT/THIRD_PARTY_LICENSES" \
  --sbom "$PORTABLE_ROOT/SBOM.spdx.json" \
  --app-version "$VERSION"
chmod +x "$PORTABLE_ROOT/VibeServiceGuardian" "$PORTABLE_ROOT"/*.command "$PORTABLE_ROOT/scripts/Validate-macOS.sh"

ZIP_PATH="$RELEASE_ROOT/Vibe-Service-Guardian-macOS-$PACKAGE_ARCH-$VERSION.zip"
rm -f "$ZIP_PATH" "$ZIP_PATH.sha256"
COPYFILE_DISABLE=1 ditto -c -k --keepParent "$PORTABLE_ROOT" "$ZIP_PATH"
HASH="$(shasum -a 256 "$ZIP_PATH" | awk '{print $1}')"
printf '%s  %s\n' "$HASH" "$(basename "$ZIP_PATH")" > "$ZIP_PATH.sha256"
"$BUILD_VENV/bin/python3" scripts/Validate-Archive.py \
  --zip "$ZIP_PATH" \
  --checksum "$ZIP_PATH.sha256" \
  --expected-root "$(basename "$PORTABLE_ROOT")" \
  --version "$VERSION" \
  --platform macos
file "$PORTABLE_ROOT/VibeServiceGuardian"
printf 'Developer-ID-unsigned portable package (ad-hoc signing may be applied): %s\nSHA256: %s\n' "$ZIP_PATH" "$HASH"
