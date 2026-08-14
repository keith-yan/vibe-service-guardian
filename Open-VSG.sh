#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
cd "$SCRIPT_DIR"
if [ -x "./VibeServiceGuardian" ]; then
  exec ./VibeServiceGuardian --open-existing
fi
if [ -x "./.venv/bin/python3" ]; then
  exec ./.venv/bin/python3 -m vsg --open-existing
fi
printf '%s\n' "No VSG runtime was found."
exit 2
