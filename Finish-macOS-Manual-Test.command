#!/bin/bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd -P)"
cd "$SCRIPT_DIR"
STATE_ROOT="${SCRIPT_DIR}.local-state"
RESULT_DIR="$STATE_ROOT/acceptance-results"
mkdir -p "$RESULT_DIR"
STAMP="$(date '+%Y%m%d-%H%M%S')"
LOG_PATH="$RESULT_DIR/03-manual-test-finish-$STAMP.log"
PID_FILE="$RESULT_DIR/test-http.pid"
PORT_FILE="$RESULT_DIR/test-http.port"
TEST_STATE_FILE="$RESULT_DIR/test-http.state"
STATE_TMP=""
EVIDENCE_STAGE=""
TEE_PID=""
LOGGING_ACTIVE=0

exec 3>&1 4>&2
exec > >(tee -a "$LOG_PATH" >&3) 2>&1
TEE_PID=$!
LOGGING_ACTIVE=1

cleanup_evidence_stage() {
  [ -n "${EVIDENCE_STAGE:-}" ] || return 0
  case "$EVIDENCE_STAGE" in
    "$STATE_ROOT"/.evidence-stage-*) rm -rf -- "$EVIDENCE_STAGE" ;;
    *) printf '拒绝清理状态目录之外的证据暂存路径：%s\n' "$EVIDENCE_STAGE" >&2 ;;
  esac
  EVIDENCE_STAGE=""
}

close_log_stream() {
  [ "${LOGGING_ACTIVE:-0}" -eq 1 ] || return 0
  exec 1>&3 2>&4
  LOGGING_ACTIVE=0
  if [ -n "${TEE_PID:-}" ]; then
    wait "$TEE_PID" 2>/dev/null || true
  fi
}

finish() {
  exit_code=$?
  trap - EXIT
  cleanup_evidence_stage
  if [ "${LOGGING_ACTIVE:-0}" -eq 1 ]; then
    printf '\n%s\n' "日志：$LOG_PATH"
    close_log_stream
  else
    printf '\n%s\n' "日志：$LOG_PATH"
  fi
  read -r -p "按回车键关闭窗口……" _ || true
  exit "${exit_code:-1}"
}
trap finish EXIT

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

read_state_field() {
  local key
  key="$1"
  [ -f "$TEST_STATE_FILE" ] || return 0
  sed -n "s/^${key}=//p" "$TEST_STATE_FILE" | head -n 1
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

CURRENT_PID="$(read_state_value "$PID_FILE")"
CURRENT_PORT="$(read_state_value "$PORT_FILE")"
LAST_STATUS="$(read_state_field status)"
LAST_PID="$(read_state_field pid)"
LAST_PORT="$(read_state_field port)"
TEST_PID="$CURRENT_PID"
TEST_PORT="$CURRENT_PORT"
TEST_SERVICE_STATUS="${LAST_STATUS:-NOT_RECORDED}"

if [ -f "$PID_FILE" ] || [ -f "$PORT_FILE" ]; then
  if safe_test_pid "$TEST_PID" "$TEST_PORT"; then
    TEST_SERVICE_STATUS="RUNNING"
    printf '检测到当前构建包创建的测试服务：PID=%s，端口=%s。\n' "$TEST_PID" "$TEST_PORT"
    CONFIRMATION=""
    if ! read -r -p "输入 CLEANUP $TEST_PID 确认停止该测试服务：" CONFIRMATION; then
      TEST_SERVICE_STATUS="NO_CLEANUP_CONFIRMATION"
      write_test_state "$TEST_SERVICE_STATUS" "$TEST_PID" "$TEST_PORT"
      printf '%s\n' "未收到清理确认，测试服务保持运行。"
    elif [ "$CONFIRMATION" = "CLEANUP $TEST_PID" ]; then
      kill "$TEST_PID" >/dev/null 2>&1 || true
      for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do
        kill -0 "$TEST_PID" >/dev/null 2>&1 || break
        sleep 0.1
      done
      if kill -0 "$TEST_PID" >/dev/null 2>&1; then
        TEST_SERVICE_STATUS="NORMAL_STOP_TIMEOUT"
        write_test_state "$TEST_SERVICE_STATUS" "$TEST_PID" "$TEST_PORT"
        printf '%s\n' "测试进程未在正常终止等待期内退出；脚本不会强制杀死。"
      else
        TEST_SERVICE_STATUS="STOPPED"
        write_test_state "$TEST_SERVICE_STATUS" "$TEST_PID" "$TEST_PORT"
        printf '%s\n' "测试服务已停止。"
        rm -f -- "$PID_FILE" "$PORT_FILE"
      fi
    else
      TEST_SERVICE_STATUS="KEPT_RUNNING"
      write_test_state "$TEST_SERVICE_STATUS" "$TEST_PID" "$TEST_PORT"
      printf '%s\n' "确认语不匹配，测试服务保持运行。"
    fi
  elif [ -n "$TEST_PID" ] && kill -0 "$TEST_PID" >/dev/null 2>&1; then
    TEST_SERVICE_STATUS="IDENTITY_MISMATCH"
    write_test_state "$TEST_SERVICE_STATUS" "$TEST_PID" "$TEST_PORT"
    printf 'PID %s 仍存在但命令或端口证据不匹配；为防 PID 复用，拒绝停止。\n' "$TEST_PID"
  else
    TEST_SERVICE_STATUS="ALREADY_STOPPED"
    write_test_state "$TEST_SERVICE_STATUS" "$TEST_PID" "$TEST_PORT"
    printf '%s\n' "测试服务已由 VSG 或用户停止；正在清除当前构建包的失效状态。"
    rm -f -- "$PID_FILE" "$PORT_FILE"
  fi
else
  TEST_PID="${LAST_PID:-}"
  TEST_PORT="${LAST_PORT:-}"
  printf '未找到活动测试 PID/端口；沿用最近收尾状态：%s。\n' "$TEST_SERVICE_STATUS"
fi

ARCH="$(uname -m)"
VERSION="$(sed -n 's/^version = "\([^"]*\)"$/\1/p' "$SCRIPT_DIR/pyproject.toml" | head -n 1)"
PORTABLE_ROOT="$SCRIPT_DIR/release/Vibe-Service-Guardian-macOS-$ARCH-$VERSION"
VSG_RUNTIME="$PORTABLE_ROOT/data/runtime.json"
VSG_SHUTDOWN_STATUS="STOP_SCRIPT_UNAVAILABLE"
if [ -x "$PORTABLE_ROOT/Stop-VSG.command" ]; then
  if [ ! -f "$VSG_RUNTIME" ]; then
    VSG_SHUTDOWN_STATUS="ALREADY_STOPPED"
    printf '%s\n' "VSG runtime.json 已不存在，控制台已停止或尚未启动。"
  else
    EXIT_CONFIRMATION=""
    if ! read -r -p "输入 EXIT VSG 确认关闭 VSG：" EXIT_CONFIRMATION; then
      VSG_SHUTDOWN_STATUS="NO_EXIT_CONFIRMATION"
      printf '%s\n' "未收到退出确认，VSG 保持运行。"
    elif [ "$EXIT_CONFIRMATION" = "EXIT VSG" ]; then
      if (cd "$PORTABLE_ROOT" && ./Stop-VSG.command); then
        VSG_SHUTDOWN_STATUS="REQUEST_ACCEPTED"
        for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 \
                 21 22 23 24 25 26 27 28 29 30 31 32 33 34 35 36 37 38 39 40 \
                 41 42 43 44 45 46 47 48 49 50 51 52 53 54 55 56 57 58 59 60 \
                 61 62 63 64 65 66 67 68 69 70 71 72 73 74 75 76 77 78 79 80 \
                 81 82 83 84 85 86 87 88 89 90 91 92 93 94 95 96 97 98 99 100; do
          [ ! -f "$VSG_RUNTIME" ] && break
          sleep 0.1
        done
        if [ -f "$VSG_RUNTIME" ]; then
          VSG_SHUTDOWN_STATUS="RUNTIME_REMOVAL_TIMEOUT"
          printf '%s\n' "VSG 已接受退出请求，但 runtime.json 未在 10 秒内消失；请人工复核。"
        else
          VSG_SHUTDOWN_STATUS="CONFIRMED"
          printf '%s\n' "VSG 已退出，runtime.json 已清除。"
        fi
      else
        VSG_SHUTDOWN_STATUS="REQUEST_FAILED"
        printf '%s\n' "VSG 退出请求失败；请查看其 data/vsg.log。"
      fi
    else
      VSG_SHUTDOWN_STATUS="KEPT_RUNNING"
      printf '%s\n' "确认语不匹配，VSG 保持运行。"
    fi
  fi
fi

SUMMARY_PID="${TEST_PID:-NOT_RECORDED}"
SUMMARY_PORT="${TEST_PORT:-NOT_RECORDED}"
SUMMARY="$RESULT_DIR/FINAL-SUMMARY-$STAMP.txt"
{
  printf 'collected_at=%s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
  printf 'macos_version=%s\n' "$(sw_vers -productVersion)"
  printf 'architecture=%s\n' "$ARCH"
  printf 'vsg_version=%s\n' "$VERSION"
  printf 'virtualization=VMware_AMD_user_reported\n'
  if [ -f "$RESULT_DIR/AUTOMATIC-VALIDATION-PASS.txt" ]; then
    printf 'automatic_validation=PASS\n'
  else
    printf 'automatic_validation=NOT_RECORDED\n'
  fi
  printf 'manual_checklist=USER_REVIEW_REQUIRED\n'
  printf 'test_service_status=%s\n' "$TEST_SERVICE_STATUS"
  printf 'test_service_pid=%s\n' "$SUMMARY_PID"
  printf 'test_service_port=%s\n' "$SUMMARY_PORT"
  printf 'vsg_shutdown_status=%s\n' "$VSG_SHUTDOWN_STATUS"
  printf 'hardware_sensor_scope=NOT_VERIFIABLE_IN_VM\n'
} > "$SUMMARY"

printf '测试服务最终状态：%s（PID=%s，端口=%s）。\n' \
  "$TEST_SERVICE_STATUS" "$SUMMARY_PID" "$SUMMARY_PORT"
printf 'VSG 最终退出状态：%s。\n' "$VSG_SHUTDOWN_STATUS"
printf '%s\n' "操作日志已完成，开始封装只读证据快照。"
close_log_stream

EVIDENCE_STAGE="$STATE_ROOT/.evidence-stage-$STAMP"
cleanup_evidence_stage
EVIDENCE_STAGE="$STATE_ROOT/.evidence-stage-$STAMP"
mkdir -p "$EVIDENCE_STAGE/acceptance-results"
for evidence_file in "$RESULT_DIR"/*; do
  [ -f "$evidence_file" ] || continue
  cp -p "$evidence_file" "$EVIDENCE_STAGE/acceptance-results/"
done

EVIDENCE_ZIP="$STATE_ROOT/VSG-macOS-$ARCH-$VERSION-VM-evidence-$STAMP.zip"
ditto --norsrc -c -k --keepParent "$EVIDENCE_STAGE/acceptance-results" "$EVIDENCE_ZIP"
cleanup_evidence_stage
EVIDENCE_HASH="$(shasum -a 256 "$EVIDENCE_ZIP" | awk '{print $1}')"
printf '%s  %s\n' "$EVIDENCE_HASH" "$(basename "$EVIDENCE_ZIP")" \
  > "$EVIDENCE_ZIP.sha256"

printf '\n%s\n' "验收证据包：$EVIDENCE_ZIP"
printf '%s\n' "SHA256：$EVIDENCE_HASH"
printf '%s\n' "请将证据 ZIP、自动验收日志及三张界面截图发给维护者。"
