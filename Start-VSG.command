#!/bin/bash
set -u

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd -P)" || exit 1
cd "$SCRIPT_DIR" || exit 1

if [ -x "./VibeServiceGuardian" ]; then
  nohup ./VibeServiceGuardian --open >/dev/null 2>&1 &
  exit 0
fi

if [ -x "./.venv/bin/python3" ]; then
  nohup ./.venv/bin/python3 -m vsg --open >/dev/null 2>&1 &
  exit 0
fi

printf '%s\n' "未找到 VibeServiceGuardian 原生程序或本地 .venv。"
printf '%s\n' "构建包用户请确认原生程序与本脚本同目录；源码用户请先运行 Setup-macOS.command。"
read -r -p "按回车键关闭…" _
exit 2
