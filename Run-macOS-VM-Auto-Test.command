#!/bin/bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd -P)"
cd "$SCRIPT_DIR"

STATE_ROOT="${SCRIPT_DIR}.local-state"
RESULT_DIR="$STATE_ROOT/acceptance-results"
mkdir -p "$RESULT_DIR"
STAMP="$(date '+%Y%m%d-%H%M%S')"
LOG_PATH="$RESULT_DIR/01-automatic-validation-$STAMP.log"

PYTHON_BOOTSTRAP_VERSION="3.12.10"
BUILD_KIT_REVISION="r9"
PYTHON_BOOTSTRAP_NAME="python-$PYTHON_BOOTSTRAP_VERSION-macos11.pkg"
PYTHON_BOOTSTRAP_URL="https://www.python.org/ftp/python/$PYTHON_BOOTSTRAP_VERSION/$PYTHON_BOOTSTRAP_NAME"
PYTHON_BOOTSTRAP_SHA256="8373e58da4ea146b3eb1c1f9834f19a319440b6b679b06050b1f9ee3237aa8e4"
PYTHON_BOOTSTRAP_SIZE="45720356"
PYTHON_BOOTSTRAP_BIN="/Library/Frameworks/Python.framework/Versions/3.12/bin/python3"

exec > >(tee -a "$LOG_PATH") 2>&1

finish() {
  exit_code=$?
  trap - EXIT
  printf '\n%s\n' "日志：$LOG_PATH"
  if [ "${exit_code:-1}" -eq 0 ]; then
    printf '%s\n' "自动构建与验收已通过。下一步请双击 Start-macOS-Manual-Test.command。"
  else
    printf '%s\n' "自动构建或验收失败（退出码 ${exit_code:-1}）。请把本日志发给维护者。"
  fi
  read -r -p "按回车键关闭窗口……" _ || true
  exit "${exit_code:-1}"
}
trap finish EXIT

fail() {
  printf '错误：%s\n' "$1" >&2
  exit 2
}

section() {
  printf '\n===== %s =====\n' "$1"
}

migrate_legacy_runtime_state() {
  local legacy_name legacy_path migration_root evidence_path
  migration_root="$STATE_ROOT/migrated-from-build-tree-$STAMP"
  for legacy_name in acceptance-results .vsg-bootstrap-cache macos-vm-test-project; do
    legacy_path="$SCRIPT_DIR/$legacy_name"
    if [ -e "$legacy_path" ]; then
      mkdir -p "$migration_root"
      mv "$legacy_path" "$migration_root/$legacy_name"
      printf '已将旧运行数据移出构建源码目录：%s\n' "$legacy_name"
    fi
  done
  for evidence_path in "$SCRIPT_DIR"/VSG-macOS-*-VM-evidence-*.zip*; do
    [ -e "$evidence_path" ] || continue
    mkdir -p "$migration_root"
    mv "$evidence_path" "$migration_root/"
    printf '已将旧证据包移出构建源码目录：%s\n' "$(basename "$evidence_path")"
  done
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

install_official_python() {
  local download_dir package_path partial_path actual_hash actual_size signature_output

  for tool in /usr/bin/curl /usr/bin/shasum /usr/sbin/pkgutil /usr/bin/sudo /usr/sbin/installer; do
    [ -x "$tool" ] || fail "安装 Python 需要系统命令：$tool"
  done

  download_dir="$STATE_ROOT/python-bootstrap"
  package_path="$download_dir/$PYTHON_BOOTSTRAP_NAME"
  partial_path="$package_path.part"
  mkdir -p "$download_dir"

  section "安装 Python $PYTHON_BOOTSTRAP_VERSION"
  printf '%s\n' "没有找到受支持的 Python 3.10–3.12。"
  printf '官方来源：%s\n' "$PYTHON_BOOTSTRAP_URL"
  printf '固定 SHA256：%s\n' "$PYTHON_BOOTSTRAP_SHA256"
  printf '%s\n' "安装范围：Python Software Foundation 官方框架，系统级 /Library/Frameworks。"
  printf '%s\n' "安装过程中 macOS 会要求输入当前管理员密码；脚本不会读取或记录密码。"
  read -r -p "输入 INSTALL PYTHON $PYTHON_BOOTSTRAP_VERSION 确认下载安装：" install_confirmation
  [ "$install_confirmation" = "INSTALL PYTHON $PYTHON_BOOTSTRAP_VERSION" ] \
    || fail "确认语不匹配，未下载、未安装 Python。"

  if [ -f "$package_path" ]; then
    actual_hash="$(/usr/bin/shasum -a 256 "$package_path" | /usr/bin/awk '{print $1}')"
    if [ "$actual_hash" != "$PYTHON_BOOTSTRAP_SHA256" ]; then
      mv "$package_path" "$package_path.invalid-$STAMP"
    fi
  fi

  if [ ! -f "$package_path" ]; then
    printf '%s\n' "正在从 python.org 下载约 43.6 MB 的官方 Universal2 安装包……"
    /usr/bin/curl \
      --fail \
      --location \
      --proto '=https' \
      --tlsv1.2 \
      --retry 3 \
      --connect-timeout 20 \
      --output "$partial_path" \
      "$PYTHON_BOOTSTRAP_URL"
    mv "$partial_path" "$package_path"
  fi

  actual_size="$(/usr/bin/stat -f '%z' "$package_path")"
  [ "$actual_size" = "$PYTHON_BOOTSTRAP_SIZE" ] \
    || fail "Python 安装包大小不匹配：${actual_size}，预期 ${PYTHON_BOOTSTRAP_SIZE}。"

  actual_hash="$(/usr/bin/shasum -a 256 "$package_path" | /usr/bin/awk '{print $1}')"
  [ "$actual_hash" = "$PYTHON_BOOTSTRAP_SHA256" ] \
    || fail "Python 安装包 SHA256 不匹配；拒绝安装。"
  printf '%s\n' "SHA256 校验通过。"

  signature_output="$(/usr/sbin/pkgutil --check-signature "$package_path" 2>&1)" \
    || fail "无法验证 Python 安装包签名。"
  printf '%s\n' "$signature_output"
  printf '%s\n' "$signature_output" | /usr/bin/grep -F "Python Software Foundation" >/dev/null \
    || fail "安装包签名不是 Python Software Foundation；拒绝安装。"
  printf '%s\n' "Python Software Foundation 安装包签名校验通过。"

  /usr/bin/sudo /usr/sbin/installer -pkg "$package_path" -target /
  [ -x "$PYTHON_BOOTSTRAP_BIN" ] || fail "安装完成后未找到 $PYTHON_BOOTSTRAP_BIN"
  [ "$($PYTHON_BOOTSTRAP_BIN -c 'import platform; print(platform.machine())')" = "$ARCH" ] \
    || fail "安装后的 Python 架构与当前 macOS 架构不一致。"
  [ "$($PYTHON_BOOTSTRAP_BIN -c 'import platform; print(platform.python_version())')" = "$PYTHON_BOOTSTRAP_VERSION" ] \
    || fail "安装后的 Python 版本与固定版本不一致。"
  printf '%s\n' "Python $PYTHON_BOOTSTRAP_VERSION 安装与运行验证通过。"
}

section "环境预检"
[ "$(uname -s)" = "Darwin" ] || fail "该脚本只能在 macOS 中运行。"
migrate_legacy_runtime_state

MACOS_VERSION="$(sw_vers -productVersion)"
MACOS_MAJOR="${MACOS_VERSION%%.*}"
case "$MACOS_MAJOR" in
  ''|*[!0-9]*) fail "无法识别 macOS 版本：$MACOS_VERSION" ;;
esac
[ "$MACOS_MAJOR" -ge 13 ] || fail "需要 macOS 13 或更高版本，当前为 ${MACOS_VERSION}。"

ARCH="$(uname -m)"
case "$ARCH" in
  x86_64) printf '%s\n' "架构：x86_64（符合 AMD/VMware 验证环境预期）" ;;
  arm64) printf '%s\n' "架构：arm64（可构建，但不属于 AMD/VMware x86_64 验证环境）" ;;
  *) fail "不支持的架构：$ARCH" ;;
esac

for tool in lsof codesign lipo ditto shasum; do
  command -v "$tool" >/dev/null 2>&1 || fail "缺少系统命令：$tool"
done

PYTHON_SELECTED="$(select_python || true)"
if [ -z "$PYTHON_SELECTED" ]; then
  install_official_python
  PYTHON_SELECTED="$(select_python || true)"
fi
[ -n "$PYTHON_SELECTED" ] || fail "Python 自动安装后仍未找到可用的 3.10、3.11 或 3.12。"
export PYTHON_BIN="$PYTHON_SELECTED"

VERSION="$($PYTHON_SELECTED -c 'from vsg import __version__; print(__version__)')"
printf 'macOS：%s\n' "$MACOS_VERSION"
printf '架构：%s\n' "$ARCH"
printf 'Python：%s\n' "$($PYTHON_SELECTED --version 2>&1)"
printf 'Python 路径：%s\n' "$(command -v "$PYTHON_SELECTED")"
printf 'VSG 版本：%s\n' "$VERSION"
printf '构建包修订：%s\n' "$BUILD_KIT_REVISION"

{
  printf 'captured_at=%s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
  printf 'macos_version=%s\n' "$MACOS_VERSION"
  printf 'architecture=%s\n' "$ARCH"
  printf 'python_version=%s\n' "$($PYTHON_SELECTED --version 2>&1)"
  printf 'python_machine=%s\n' "$($PYTHON_SELECTED -c 'import platform; print(platform.machine())')"
  printf 'vsg_version=%s\n' "$VERSION"
  printf 'build_kit_revision=%s\n' "$BUILD_KIT_REVISION"
  printf 'virtualization=VMware_AMD_user_reported\n'
} > "$RESULT_DIR/environment.txt"

chmod +x "$SCRIPT_DIR"/*.command
chmod +x "$SCRIPT_DIR/scripts/Build-Portable-macOS.sh"
chmod +x "$SCRIPT_DIR/scripts/Validate-macOS.sh"

section "构建原生 macOS 包"
printf '%s\n' "该步骤会联网安装哈希锁定的构建依赖，但不会上传本机数据。"
"$SCRIPT_DIR/scripts/Build-Portable-macOS.sh"

PORTABLE_ROOT="$SCRIPT_DIR/release/Vibe-Service-Guardian-macOS-$ARCH-$VERSION"
ZIP_PATH="$PORTABLE_ROOT.zip"
CHECKSUM_PATH="$ZIP_PATH.sha256"
[ -x "$PORTABLE_ROOT/VibeServiceGuardian" ] || fail "未生成原生程序：$PORTABLE_ROOT/VibeServiceGuardian"
[ -f "$ZIP_PATH" ] || fail "未生成便携 ZIP：$ZIP_PATH"
[ -f "$CHECKSUM_PATH" ] || fail "未生成 SHA256 文件：$CHECKSUM_PATH"

section "自动原生验收"
(
  cd "$PORTABLE_ROOT"
  chmod +x ./VibeServiceGuardian ./*.command ./scripts/Validate-macOS.sh
  ./scripts/Validate-macOS.sh
)

section "归档校验"
shasum -a 256 "$ZIP_PATH"
cat "$CHECKSUM_PATH"

{
  printf 'status=PASS\n'
  printf 'completed_at=%s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
  printf 'portable_root=%s\n' "$PORTABLE_ROOT"
  printf 'package=%s\n' "$ZIP_PATH"
  printf 'checksum_file=%s\n' "$CHECKSUM_PATH"
} > "$RESULT_DIR/AUTOMATIC-VALIDATION-PASS.txt"

printf '\n%s\n' "MACOS_VM_AUTOMATIC_ACCEPTANCE_OK"
