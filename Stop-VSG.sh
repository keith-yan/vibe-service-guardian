#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
cd "$SCRIPT_DIR"
if [ -x "./VibeServiceGuardian" ]; then
  exec ./VibeServiceGuardian --stop
fi
if [ -x "./.venv/bin/python3" ]; then
  exec ./.venv/bin/python3 -m vsg --stop
fi
printf '%s\n' "No VSG runtime was found."
exit 2
