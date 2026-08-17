# macOS 构建与验收边界

当前仓库已适配 macOS 13+，目标架构为 Apple Silicon `arm64` 与 Intel `x86_64`。PyInstaller 不支持从 Windows 直接交叉编译 macOS Mach-O，因此 Windows 侧交付的是可复现构建包，不是伪造的 macOS 可执行文件。

## 在真实 Mac 上生成未签名便携包

1. 解压与当前版本匹配的 `Vibe-Service-Guardian-macOS-build-kit-<version>-r9.tar.gz`；macOS 上优先使用 `.tar.gz`，以保留可执行位。
2. 在终端进入解压目录。
3. 执行 `chmod +x ./*.command ./scripts/*.sh`。
4. 执行 `./scripts/Build-Portable-macOS.sh`。
5. Apple Silicon Mac 生成 `Vibe-Service-Guardian-macOS-arm64-<version>.zip`；x86_64 Mac 生成 `Vibe-Service-Guardian-macOS-x86_64-<version>.zip`。

构建脚本会创建独立构建虚拟环境、安装固定版本依赖、执行完整单元测试、调用 PyInstaller、保留 `.command` 启停脚本，并输出 SHA-256。它不会使用 Apple Developer ID 证书、不会 notarize 或上传文件。PyInstaller 在未提供证书身份时会执行 ad-hoc 签名；Apple Silicon 要求至少具备此类签名，因此这里的“未签名包”准确含义是“无开发者身份签名、无公证”。

## 原生验收

进入构建生成的 `release/Vibe-Service-Guardian-macOS-<架构>-<version>` 目录后执行：

```bash
chmod +x ./scripts/Validate-macOS.sh
./scripts/Validate-macOS.sh
```

成功标志为 `MACOS_NATIVE_VALIDATION_OK`。脚本会校验：

- API 报告平台为 `macos`，架构为 `arm64` 或 `x86_64`；
- 快照结构版本为 `2.0`；
- 宿主机端口采集方法为 `lsof`；
- 模型规划器读取 macOS 硬件、加载离线目录并返回三层容量结论；
- 运行命令仍为只生成、不执行，并固定绑定 `127.0.0.1`；
- 本地 HTTP 服务可启动和停止；
- Mach-O 架构与当前 Mac 一致，且 ad-hoc 代码签名可通过系统校验；
- `data` 目录和主要持久化文件使用当前用户专属 POSIX 权限。

## 当前验收状态

- 2026-08-16：0.8.5.2 r8 在 macOS 13.7.8 x86_64 AMD/VMware 客体完成一次自动原生预览验收，出现 `MACOS_NATIVE_VALIDATION_OK`；这不是实体 Intel Mac 或 Apple Silicon 证据。
- r8 的人工清单未形成勾选与截图证据，且证据 ZIP 在最终日志完全落盘前封装。r9 已在本地回归中修复证据生成顺序、停止状态与 AppleDouble 排除，但仍须在 macOS 目标环境重新执行。
- 只有自动原生验收、人工清单、界面截图、清理状态和证据包完整性均通过，才能把对应环境标为“完整验收”；其余情况统一标为“预览”或“待验证”。

## 无开发者身份签名的运行提示

首次运行可能被 Gatekeeper 拦截。应先核对 ZIP 的 SHA-256，再通过 Finder 右键“打开”，或在“系统设置 → 隐私与安全性”中确认打开。不要全局关闭 Gatekeeper。只有在确认文件来源和哈希后，才可针对解压目录移除下载隔离属性。

## 可见性限制

工具默认不请求 `sudo`。macOS 端通过系统 `lsof` 读取当前用户可见的 TCP/UDP 端口，跨用户进程和部分系统进程可能不可见。控制台会把该状态明确标为“部分可见”，不会静默提权，也不会把不完整结果宣称为全机完整清单。
