#!/bin/bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd -P)"
cd "$SCRIPT_DIR"
STATE_ROOT="${SCRIPT_DIR}.local-state"
RESULT_DIR="$STATE_ROOT/acceptance-results"
mkdir -p "$RESULT_DIR"
LOG_PATH="$RESULT_DIR/02-manual-test-start.log"

TEST_PID=""
TEST_PORT=""
TEST_CREATED=0
START_COMPLETED=0
CHECKLIST_TMP=""
PID_TMP=""
PORT_TMP=""
STATE_TMP=""
PID_FILE="$RESULT_DIR/test-http.pid"
PORT_FILE="$RESULT_DIR/test-http.port"
TEST_STATE_FILE="$RESULT_DIR/test-http.state"

exec > >(tee "$LOG_PATH") 2>&1

safe_test_pid() {
  local pid port command_line
  pid="$1"
  port="$2"
  case "$pid" in ''|*[!0-9]*) return 1 ;; esac
  case "$port" in ''|*[!0-9]*) return 1 ;; esac
  [ "$port" -ge 1 ] 2>/dev/null && [ "$port" -le 65535 ] 2>/dev/null || return 1
  kill -0 "$pid" >/dev/null 2>&1 || return 1
  command_line="$(ps -ww -p "$pid" -o command= 2>/dev/null || true)"
  case "$command_line" in
    *"http.server 0"*"--bind 127.0.0.1"*) ;;
    *) return 1 ;;
  esac
  lsof -nP -a -p "$pid" -iTCP:"$port" -sTCP:LISTEN -Fn 2>/dev/null \
    | grep -Fx "n127.0.0.1:$port" >/dev/null 2>&1
}

read_state_value() {
  [ -f "$1" ] || return 0
  sed -n '1{s/\r$//;p;}' "$1"
}

write_test_state() {
  local status pid port
  status="$1"
  pid="$2"
  port="$3"
  STATE_TMP="$TEST_STATE_FILE.$$.tmp"
  {
    printf 'status=%s\n' "$status"
    printf 'pid=%s\n' "$pid"
    printf 'port=%s\n' "$port"
    printf 'updated_at=%s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
  } > "$STATE_TMP"
  mv -f "$STATE_TMP" "$TEST_STATE_FILE"
  STATE_TMP=""
}

rollback_current_test_service() {
  [ "${TEST_CREATED:-0}" -eq 1 ] || return 0
  if safe_test_pid "${TEST_PID:-}" "${TEST_PORT:-}"; then
    printf '启动未完成；正在回收本次脚本创建的测试服务 PID=%s，端口=%s。\n' \
      "$TEST_PID" "$TEST_PORT"
    kill "$TEST_PID" >/dev/null 2>&1 || true
    for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do
      kill -0 "$TEST_PID" >/dev/null 2>&1 || break
      sleep 0.1
    done
    if kill -0 "$TEST_PID" >/dev/null 2>&1; then
      write_test_state "NORMAL_STOP_TIMEOUT" "$TEST_PID" "$TEST_PORT"
      printf '%s\n' "测试服务未在正常终止等待期内退出；脚本不会强制杀死。"
      return 0
    fi
    rm -f -- "$PID_FILE" "$PORT_FILE"
    write_test_state "ROLLED_BACK" "$TEST_PID" "$TEST_PORT"
    printf '%s\n' "本次脚本创建的测试服务已回收。"
  else
    write_test_state "ROLLBACK_REFUSED" "${TEST_PID:-}" "${TEST_PORT:-}"
    printf '%s\n' "本次测试服务的 PID、命令或端口证据已变化；为防 PID 复用，脚本不会停止它。"
  fi
}

finish() {
  exit_code=$?
  trap - EXIT
  for temporary_path in "${CHECKLIST_TMP:-}" "${PID_TMP:-}" "${PORT_TMP:-}" "${STATE_TMP:-}"; do
    if [ -n "$temporary_path" ] && [ -f "$temporary_path" ]; then
      rm -f -- "$temporary_path"
    fi
  done
  if [ "${exit_code:-1}" -ne 0 ] && [ "${START_COMPLETED:-0}" -ne 1 ]; then
    rollback_current_test_service
  fi
  printf '\n%s\n' "日志：$LOG_PATH"
  if [ "${exit_code:-1}" -eq 0 ]; then
    printf '%s\n' "VSG 和测试服务已就绪。请按照自动打开的验收清单操作。"
  else
    printf '%s\n' "启动失败（退出码 ${exit_code:-1}）。请把本日志发给维护者。"
  fi
  if safe_test_pid "${TEST_PID:-}" "${TEST_PORT:-}"; then
    close_prompt="按回车键关闭窗口（测试服务会继续运行）……"
  else
    close_prompt="按回车键关闭窗口……"
  fi
  read -r -p "$close_prompt" _ || true
  exit "${exit_code:-1}"
}
trap finish EXIT

fail() {
  printf '错误：%s\n' "$1" >&2
  exit 2
}

select_python() {
  local candidate version_ok arch_ok
  for candidate in "${PYTHON_BIN:-}" \
    /Library/Frameworks/Python.framework/Versions/3.12/bin/python3 \
    /Library/Frameworks/Python.framework/Versions/3.11/bin/python3 \
    /Library/Frameworks/Python.framework/Versions/3.10/bin/python3 \
    /usr/local/bin/python3.12 /usr/local/bin/python3.11 /usr/local/bin/python3.10 \
    /opt/homebrew/bin/python3.12 /opt/homebrew/bin/python3.11 /opt/homebrew/bin/python3.10 \
    python3.12 python3.11 python3.10 python3; do
    [ -n "$candidate" ] || continue
    command -v "$candidate" >/dev/null 2>&1 || continue
    version_ok="$($candidate -c 'import sys; print("yes" if (3, 10) <= sys.version_info[:2] <= (3, 12) else "no")' 2>/dev/null || true)"
    [ "$version_ok" = "yes" ] || continue
    arch_ok="$($candidate -c 'import platform; print(platform.machine())' 2>/dev/null || true)"
    [ "$arch_ok" = "$ARCH" ] || continue
    printf '%s\n' "$candidate"
    return 0
  done
  return 1
}

wait_for_vsg() {
  "$PYTHON_SELECTED" - "$1" "$2" <<'PY'
import json
import sys
import time
import urllib.request
from pathlib import Path

runtime_path = Path(sys.argv[1])
expected_version = sys.argv[2]
deadline = time.monotonic() + 45.0
last_error = "runtime.json 尚未生成"
while time.monotonic() < deadline:
    try:
        runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
        port = runtime.get("port")
        instance_id = runtime.get("instance_id")
        if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
            raise ValueError("runtime.json 端口无效")
        if not isinstance(instance_id, str) or not instance_id:
            raise ValueError("runtime.json 实例标识无效")
        url = f"http://127.0.0.1:{port}/healthz"
        request = urllib.request.Request(url, headers={"Host": f"127.0.0.1:{port}"})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request, timeout=0.8) as response:
            body = response.read(1024 * 1024 + 1)
        if len(body) > 1024 * 1024:
            raise ValueError("健康接口响应过大")
        health = json.loads(body.decode("utf-8"))
        if health.get("ok") is not True:
            raise ValueError("健康接口未返回 ok=true")
        if health.get("instance_id") != instance_id:
            raise ValueError("健康接口实例标识不匹配")
        if health.get("version") != expected_version:
            raise ValueError(
                f"运行版本 {health.get('version')!r} 与预期版本 {expected_version!r} 不一致"
            )
        print(f"http://127.0.0.1:{port}")
        raise SystemExit(0)
    except Exception as exc:  # noqa: BLE001 - bounded local readiness probe
        last_error = f"{type(exc).__name__}: {exc}"
        time.sleep(0.2)
print(f"VSG 在 45 秒内未就绪：{last_error}", file=sys.stderr)
raise SystemExit(1)
PY
}

[ "$(uname -s)" = "Darwin" ] || fail "该脚本只能在 macOS 中运行。"
command -v lsof >/dev/null 2>&1 || fail "未找到 macOS 系统命令 lsof，无法验证测试服务身份。"
ARCH="$(uname -m)"
PYTHON_SELECTED="$(select_python || true)"
[ -n "$PYTHON_SELECTED" ] || fail "未找到与 ${ARCH} 匹配的 Python 3.10、3.11 或 3.12。"
VERSION="$($PYTHON_SELECTED -c 'from vsg import __version__; print(__version__)')"
PORTABLE_ROOT="$SCRIPT_DIR/release/Vibe-Service-Guardian-macOS-$ARCH-$VERSION"
[ -x "$PORTABLE_ROOT/VibeServiceGuardian" ] || fail "请先运行 Run-macOS-VM-Auto-Test.command。"

TEST_PROJECT="$STATE_ROOT/macos-vm-test-project"
TEST_LOG="$RESULT_DIR/test-http.log"
CHECKLIST="$RESULT_DIR/MANUAL-TEST-CHECKLIST.txt"
CHECKLIST_TMP="$RESULT_DIR/.MANUAL-TEST-CHECKLIST.$$.tmp"
mkdir -p "$TEST_PROJECT"

if [ -f "$PID_FILE" ] || [ -f "$PORT_FILE" ]; then
  OLD_PID="$(read_state_value "$PID_FILE")"
  OLD_PORT="$(read_state_value "$PORT_FILE")"
  if safe_test_pid "$OLD_PID" "$OLD_PORT"; then
    TEST_PID="$OLD_PID"
    TEST_PORT="$OLD_PORT"
    write_test_state "RUNNING" "$TEST_PID" "$TEST_PORT"
    printf '复用当前构建包已启动的测试服务：PID=%s，端口=%s。\n' "$TEST_PID" "$TEST_PORT"
  elif [ -n "$OLD_PID" ] && kill -0 "$OLD_PID" >/dev/null 2>&1; then
    fail "状态文件中的 PID ${OLD_PID} 仍存在，但命令或端口证据不匹配；为防 PID 复用，拒绝继续。"
  else
    rm -f -- "$PID_FILE" "$PORT_FILE"
    write_test_state "STALE_STATE_CLEARED" "$OLD_PID" "$OLD_PORT"
    printf '%s\n' "已清除当前构建包中失效的测试服务状态。"
  fi
fi

if [ -z "$TEST_PID" ]; then
  cd "$TEST_PROJECT"
  nohup "$PYTHON_SELECTED" -u -m http.server 0 --bind 127.0.0.1 \
    > "$TEST_LOG" 2>&1 &
  TEST_PID=$!
  TEST_CREATED=1
  cd "$SCRIPT_DIR"

  for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 \
           21 22 23 24 25 26 27 28 29 30 31 32 33 34 35 36 37 38 39 40 \
           41 42 43 44 45 46 47 48 49 50; do
    kill -0 "$TEST_PID" >/dev/null 2>&1 || break
    TEST_PORT="$(
      lsof -nP -a -p "$TEST_PID" -iTCP -sTCP:LISTEN -Fn 2>/dev/null \
        | sed -n 's/^n127\.0\.0\.1:\([0-9][0-9]*\)$/\1/p' \
        | head -n 1 || true
    )"
    case "$TEST_PORT" in ''|*[!0-9]*) TEST_PORT="" ;; esac
    [ -n "$TEST_PORT" ] && break
    sleep 0.1
  done

  kill -0 "$TEST_PID" >/dev/null 2>&1 \
    || fail "测试 HTTP 服务未能启动，请查看 $TEST_LOG"
  [ -n "$TEST_PORT" ] \
    || fail "测试进程存在，但未能取得其动态回环端口，请查看 $TEST_LOG"
  safe_test_pid "$TEST_PID" "$TEST_PORT" \
    || fail "测试服务的 PID、命令或回环监听端口验证失败。"

  PORT_TMP="$PORT_FILE.$$.tmp"
  PID_TMP="$PID_FILE.$$.tmp"
  printf '%s\n' "$TEST_PORT" > "$PORT_TMP"
  printf '%s\n' "$TEST_PID" > "$PID_TMP"
  mv -f "$PORT_TMP" "$PORT_FILE"
  PORT_TMP=""
  mv -f "$PID_TMP" "$PID_FILE"
  PID_TMP=""
  write_test_state "RUNNING" "$TEST_PID" "$TEST_PORT"
fi

cat > "$CHECKLIST_TMP" <<EOF
Vibe Service Guardian 0.8.5.2 macOS VMware 人工验收
===================================================

环境：macOS $(sw_vers -productVersion) / ${ARCH} / AMD VMware（用户提供）
测试项目：${TEST_PROJECT}
测试 PID：${TEST_PID}
测试端口：127.0.0.1:${TEST_PORT}

请在 VSG 界面依次检查：

[ ] 1. 版本显示为 ${VERSION}，平台为 macOS，架构为 ${ARCH}。
[ ] 2. 实例信息显示 PID、仅回环监听端口和运行时长。
[ ] 3. 在设置中添加项目根目录：${TEST_PROJECT}
[ ] 4. 刷新后识别到 PID ${TEST_PID}、端口 ${TEST_PORT}、绑定地址 127.0.0.1。
[ ] 5. 将该服务纠正归属到 macos-vm-test-project，并标记为可安全清理。
[ ] 6. 打开“项目安全清理推荐”，确认只展示预览，不自动批量停止。
[ ] 7. 只对 PID ${TEST_PID} 打开停止评估，检查影响、证据和恢复建议。
[ ] 8. 如需测试停止，请按界面要求输入 STOP ${TEST_PID}，并选择观察 5 分钟。
[ ] 9. 检查通知中心能显示停止/观察事件，并可标记已读。
[ ] 10. launchd 或系统进程应显示受保护/建议路径，禁止直接停止。
[ ] 11. Windows 托盘、全局快捷键和 Windows 开机启动显示不支持，这是预期结果。
[ ] 12. VSG 自身控制台只能绑定 127.0.0.1，不应暴露到虚拟机外部网卡。

虚拟机不作为以下能力的通过证据：真实 GPU/Metal、温度、风扇、功耗、
Intel Mac 真机和 Apple Silicon arm64。

完成界面检查后，请双击 Finish-macOS-Manual-Test.command。
EOF
mv -f "$CHECKLIST_TMP" "$CHECKLIST"
CHECKLIST_TMP=""

(
  cd "$PORTABLE_ROOT"
  chmod +x ./VibeServiceGuardian ./*.command
  ./Start-VSG.command
)

VSG_RUNTIME="$PORTABLE_ROOT/data/runtime.json"
if ! VSG_URL="$(wait_for_vsg "$VSG_RUNTIME" "$VERSION")"; then
  fail "VSG 启动命令已返回，但本机健康验证未通过；请查看 $PORTABLE_ROOT/data/vsg.log"
fi

open -a TextEdit "$CHECKLIST" >/dev/null 2>&1 || true
START_COMPLETED=1
printf '测试服务已就绪：PID=%s，地址=http://127.0.0.1:%s\n' "$TEST_PID" "$TEST_PORT"
printf 'VSG 控制台已验证：%s\n' "$VSG_URL"
printf '项目根目录：%s\n' "$TEST_PROJECT"
printf '验收清单：%s\n' "$CHECKLIST"
