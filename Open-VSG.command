#!/bin/bash
set -u

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd -P)" || exit 1
cd "$SCRIPT_DIR" || exit 1

if [ -x "./VibeServiceGuardian" ]; then
  exec ./VibeServiceGuardian --open-existing
fi
if [ -x "./.venv/bin/python3" ]; then
  exec ./.venv/bin/python3 -m vsg --open-existing
fi
printf '%s\n' "未找到可用运行时。"
exit 2
