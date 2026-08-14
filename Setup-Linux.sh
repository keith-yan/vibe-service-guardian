#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
cd "$SCRIPT_DIR"
if [ "$(uname -s)" != "Linux" ]; then
  printf '%s\n' "This setup script must run on Linux."
  exit 2
fi

PYTHON_BIN=${PYTHON_BIN:-python3}
command -v "$PYTHON_BIN" >/dev/null 2>&1 || { printf '%s\n' "Python 3.10 or newer is required."; exit 2; }
"$PYTHON_BIN" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 2)'
"$PYTHON_BIN" -m venv .venv
PY_TAG=$(./.venv/bin/python3 -c 'import sys; print(f"py{sys.version_info.major}{sys.version_info.minor}")')
case "$PY_TAG" in py310|py311|py312) ;; *) printf 'Unsupported Python lock version: %s\n' "$PY_TAG"; exit 2 ;; esac
./.venv/bin/python3 -m pip install --disable-pip-version-check --only-binary=:all: \
  --no-deps --require-hashes --requirement requirements-lock/bootstrap-py3.txt
./.venv/bin/python3 -m pip install --disable-pip-version-check --only-binary=:all: \
  --no-deps --require-hashes --requirement "requirements-lock/runtime-linux-$PY_TAG.txt"
./.venv/bin/python3 scripts/Requirement-Locks.py --verify
./.venv/bin/python3 -m unittest discover -s tests -v
chmod +x Start-VSG.sh Stop-VSG.sh Open-VSG.sh Setup-Linux.sh

if [ "${VSG_INSTALL_DESKTOP_LAUNCHER:-0}" = "1" ]; then
  XDG_DATA_HOME_VALUE=${XDG_DATA_HOME:-"$HOME/.local/share"}
  APP_DIR="$XDG_DATA_HOME_VALUE/applications"
  mkdir -p "$APP_DIR"
  DESKTOP_PATH="$APP_DIR/vibe-service-guardian.desktop"
  sed "s|@VSG_EXEC@|$SCRIPT_DIR/Start-VSG.sh|g; s|@VSG_PATH@|$SCRIPT_DIR|g" Vibe-Service-Guardian.desktop.in > "$DESKTOP_PATH"
  chmod 0644 "$DESKTOP_PATH"
  printf 'Desktop launcher installed: %s\n' "$DESKTOP_PATH"
else
  printf '%s\n' "Source environment created. Run ./Start-VSG.sh."
  printf '%s\n' "Optional graphical launcher: VSG_INSTALL_DESKTOP_LAUNCHER=1 ./Setup-Linux.sh"
fi
