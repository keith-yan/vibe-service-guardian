# Vibe Service Guardian v0.8.5.2-alpha.1 发布说明 / Release Notes

- 发布日期 / Release date: 2026-08-23
- 程序版本 / Runtime version: `0.8.5.2`
- 预发布标签 / Prerelease tag: `v0.8.5.2-alpha.1`

## 中文

### 发布定位

这是 Vibe Service Guardian 首个面向公开试用准备的未签名单用户 Alpha。它用于验证本机服务归属、遗留判断、停止影响、停止后复活证据、AI 推理服务健康和本机模型容量规划是否真正帮助 Vibe Coding 用户做决定。

这不是稳定版，也不是完整跨平台支持声明。Windows 当前主机已完成源码、便携 EXE、回环安全、中文/英文界面和归档校验；macOS 13.7.8 x86_64 仅完成一次 AMD 主机 VMware 客体中的 r8 自动原生预览，r9 目标机复验和人工界面证据尚未完成；Apple Silicon、实体 Intel Mac、Windows 10 和 Ubuntu/Linux 真机矩阵仍待验证。

### 主要变化

- 首页显示当前 VSG 版本、PID、回环端口、运行时长和首次使用检查，降低误开旧实例的概率。
- 默认“今日关注”聚合疑似遗留、非回环暴露、运行时异常和受管服务建议路径；完整服务清单仍可查看。
- 按项目生成安全清理预览，但不提供批量停止接口；每个服务仍须重新评估并输入 `STOP <PID>`。
- 停止后支持 5/15/30 分钟有限观察，记录 PID、端口和脱敏命令身份是否复活，不自动二次停止。
- 用户纠正可沉淀为本机归属规则；规则可搜索、启停、审计、保留最近 5 版并回滚，脱敏规则包必须预览冲突后逐条重绑定。
- 增加本机提醒中心、Windows 可选托盘、默认关闭的 `Ctrl+Alt+G` 快捷键和经精确确认的当前用户开机启动。
- 只读识别 Ollama、llama.cpp、vLLM 等推理运行时，并把已加载模型、健康状态、本机实测并发余量与项目归属关联。
- 保留固定、可预览、可中止的工作负载矩阵，并以同硬件/模型/量化/负载实测结果校准容量预测误差。

### 安全与隐私边界

- 控制台只绑定 `127.0.0.1`；无遥测、无云端存储、无自动更新。
- 不读取目标进程或容器环境变量，不保存完整命令行，不上传日志、规则、模型清单或本机成效报告。
- 不自动停止、重启或清理服务；Docker、WSL、Agent/IDE 和系统服务保持受保护或只读建议路径。
- 所有高风险操作继续要求精确确认短语，并在执行前复核 PID、启动时间和保护进程树。
- Windows 包未签名，可能出现 SmartScreen 提示；校验 SHA-256 只能证明下载文件与发布者提供的摘要一致，不能替代代码签名身份链。

### 计划发布资产与完整性

最终资产必须从包含本发布元数据的 `main` 重新构建。只把 GitHub Release 页面中与标签 `v0.8.5.2-alpha.1` 关联、且带同名 `.sha256` 的文件视为本次发布资产：

- `Vibe-Service-Guardian-Windows-x64-0.8.5.2.zip`
- `Vibe-Service-Guardian-macOS-build-kit-0.8.5.2-r9.zip`
- `Vibe-Service-Guardian-macOS-build-kit-0.8.5.2-r9.tar.gz`

本次不承诺 Linux 原生 ELF，也不发布 macOS VM 证据 ZIP、SQLite、日志、运行数据或旧版 r4–r8 构建包。

Windows PowerShell 校验示例：

```powershell
Get-FileHash -Algorithm SHA256 '.\Vibe-Service-Guardian-Windows-x64-0.8.5.2.zip'
Get-Content '.\Vibe-Service-Guardian-Windows-x64-0.8.5.2.zip.sha256'
```

macOS/Linux 校验示例：

```bash
shasum -a 256 Vibe-Service-Guardian-macOS-build-kit-0.8.5.2-r9.tar.gz
cat Vibe-Service-Guardian-macOS-build-kit-0.8.5.2-r9.tar.gz.sha256
```

安装、运行和平台限制见 [中文 README](../README.md)，安全问题报告方式见 [SECURITY.md](../SECURITY.md)，详细验证证据见 [VALIDATION.md](VALIDATION.md) 与 [上线准备度审查](PRODUCTION-READINESS-0.8.5.2.md)。

## English

### Positioning

This is the first unsigned, single-user public Alpha prepared for Vibe Service Guardian. It is intended to test whether local service attribution, stale-service assessment, stop impact, bounded relaunch evidence, AI-runtime health, and local model capacity planning help Vibe Coding users make safer decisions.

It is not a stable release or a complete cross-platform support claim. The current Windows host has source, portable EXE, loopback-security, bilingual UI, and archive-validation evidence. macOS 13.7.8 x86_64 has only one constrained r8 automated native preview in an AMD-hosted VMware guest; the r9 target rerun and manual UI evidence are incomplete. Apple Silicon, physical Intel Mac, Windows 10, and Ubuntu/Linux native matrices remain outstanding.

### Highlights

- Visible VSG version, PID, loopback port, uptime, and first-use readiness on the home page.
- A noise-reduced Today's Focus view plus a complete inventory when needed.
- Project cleanup previews without a batch-stop API; every actionable service still requires fresh evaluation and `STOP <PID>`.
- Bounded 5/15/30-minute post-stop observation without an automatic second termination.
- Versioned, auditable local attribution rules and integrity-checked redacted rule packs with explicit rebinding.
- A local alert center and optional Windows tray, default-off `Ctrl+Alt+G` shortcut, and explicitly confirmed current-user startup.
- Read-only Ollama, llama.cpp, and vLLM evidence connected to project ownership, health, loaded-model identity, and measured concurrency headroom.
- A fixed, previewable, cancellable workload matrix whose comparable measurements calibrate capacity-prediction error.

### Security and privacy boundaries

- The console binds only to `127.0.0.1`; there is no telemetry, cloud storage, or automatic update channel.
- VSG does not read target-process or container environment variables, persist full command lines, or upload logs, rules, model inventory, or impact reports.
- It never automatically stops, restarts, or cleans up services. Docker, WSL, Agent/IDE, and system-managed services remain protected or guidance-only.
- High-risk actions retain exact confirmation phrases plus PID, create-time, and protected-tree revalidation.
- Windows artifacts are unsigned and may trigger SmartScreen. SHA-256 verifies artifact integrity against the published digest; it does not provide a code-signing identity chain.

### Planned assets and integrity

Final assets must be rebuilt from `main` after these release metadata changes. Only files attached to the GitHub Release for `v0.8.5.2-alpha.1` and accompanied by matching `.sha256` files are release assets:

- `Vibe-Service-Guardian-Windows-x64-0.8.5.2.zip`
- `Vibe-Service-Guardian-macOS-build-kit-0.8.5.2-r9.zip`
- `Vibe-Service-Guardian-macOS-build-kit-0.8.5.2-r9.tar.gz`

This prerelease does not promise a native Linux ELF and does not ship macOS VM evidence archives, SQLite databases, logs, runtime data, or obsolete r4-r8 build kits.

See the [English README](../README.en.md) for setup and platform limits, [SECURITY.md](../SECURITY.md) for reporting, and [VALIDATION.md](VALIDATION.md) plus the [production-readiness review](PRODUCTION-READINESS-0.8.5.2.md) for evidence and limitations.
