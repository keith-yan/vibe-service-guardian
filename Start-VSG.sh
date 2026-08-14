#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
cd "$SCRIPT_DIR"

if [ -x "./VibeServiceGuardian" ]; then
  nohup ./VibeServiceGuardian --open >/dev/null 2>&1 &
  exit 0
fi
if [ -x "./.venv/bin/python3" ]; then
  nohup ./.venv/bin/python3 -m vsg --open >/dev/null 2>&1 &
  exit 0
fi
printf '%s\n' "VibeServiceGuardian binary or local .venv was not found."
printf '%s\n' "Portable users: keep this script beside the binary. Source users: run Setup-Linux.sh first."
exit 2
