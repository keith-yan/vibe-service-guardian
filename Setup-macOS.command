#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd -P)"
cd "$SCRIPT_DIR"

if [ "$(uname -s)" != "Darwin" ]; then
  printf '%s\n' "此脚本仅用于 macOS。"
  exit 2
fi

PYTHON_BIN="${PYTHON_BIN:-python3}"
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  printf '%s\n' "需要 Python 3.10 或更高版本。"
  exit 2
fi

"$PYTHON_BIN" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 2)'
"$PYTHON_BIN" -m venv .venv
PY_TAG="$(./.venv/bin/python3 -c 'import sys; print(f"py{sys.version_info.major}{sys.version_info.minor}")')"
case "$PY_TAG" in py310|py311|py312) ;; *) printf '不支持的 Python 锁版本：%s\n' "$PY_TAG"; exit 2 ;; esac
./.venv/bin/python3 -m pip install --disable-pip-version-check --only-binary=:all: \
  --no-deps --require-hashes --requirement requirements-lock/bootstrap-py3.txt
./.venv/bin/python3 -m pip install --disable-pip-version-check --only-binary=:all: \
  --no-deps --require-hashes --requirement "requirements-lock/runtime-macos-$PY_TAG.txt"
./.venv/bin/python3 scripts/Requirement-Locks.py --verify
./.venv/bin/python3 -m unittest discover -s tests -v
chmod +x Start-VSG.command Stop-VSG.command Open-VSG.command
printf '%s\n' "本地运行环境已创建。现在可双击 Start-VSG.command。"
