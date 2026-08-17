# Vibe Service Guardian 0.8.5.2 Alpha 1

> 未签名单用户预发布 / Unsigned single-user prerelease
>
> 不是稳定版，不代表完整跨平台支持 / Not a stable or fully validated cross-platform release

Vibe Service Guardian（服务守望）帮助 Vibe Coding 用户回答：本机哪些服务正在监听、由哪个项目或 Agent 拉起、是否可能遗留、关停会影响谁、停止后是否被重新拉起，以及本机硬件能够支撑哪些开放权重模型和并发负载。

## 下载选择

- **Windows 10/11 x64**：下载 `Vibe-Service-Guardian-Windows-x64-0.8.5.2.zip`。这是未签名便携包，可能触发 SmartScreen；解压后运行 `Start-VSG.cmd`。
- **macOS 13+**：提供的是 `r9` 源码构建包，不是已完成全平台验收的通用二进制。请在目标 Mac 上执行构建与原生验证脚本。
- **Linux**：本次 Alpha 不提供或承诺原生 ELF；源码与构建链保留预览状态。

每个上传资产都必须带有同名 `.sha256`。只信任本 Release 页面实际附带的文件和摘要，不要使用旧版 r4–r8 构建包或 macOS VM 证据 ZIP。

## 本版重点

- 首页显示 VSG 版本、PID、回环端口、运行时长和首次使用检查。
- 默认“今日关注”降低系统噪声，同时保留完整服务清单。
- 按项目预览安全清理候选，但不提供批量停止；每项仍需重新评估并输入 `STOP <PID>`。
- 停止后可观察 5/15/30 分钟，记录 PID、端口或同一脱敏命令身份是否复活，不自动二次停止。
- 用户纠正可沉淀为可审计、可回滚的本机归属规则；脱敏规则包必须预览冲突并逐条重绑定。
- 增加本机提醒中心，以及默认关闭的 Windows 托盘、`Ctrl+Alt+G` 快捷键和当前用户开机启动。
- 只读关联 Ollama、llama.cpp、vLLM 等运行时的模型、健康状态、项目归属和实测并发余量。
- 固定工作负载矩阵可预览、可中止，并用同负载实测结果显示容量预测误差。

## 安全与隐私边界

- Web 控制台只监听 `127.0.0.1`；无遥测、无云端存储、无自动更新。
- 不读取目标进程或容器环境变量，不上传日志、规则、模型清单或本机成效报告。
- 不自动停止、重启或清理服务；Docker、WSL、Agent/IDE 与系统服务保持受保护或只读建议。
- 高风险操作继续要求精确确认短语，并重新校验 PID、启动时间和保护进程树。

## 当前验证范围

- **Windows 当前主机**：源码、243 项 unittest、静态/安全门禁、未签名便携 EXE、回环安全、双语界面和归档校验通过；其中 1 项 Windows 不适用的 POSIX 测试跳过。
- **macOS x86_64**：macOS 13.7.8 AMD/VMware 客体完成一次 `r8` 自动原生预览；人工界面证据和 `r9` 目标机复验未完成，不能替代实体 Intel Mac。
- **尚未完成**：Windows 10 独立实机、Apple Silicon、实体 Intel Mac、Ubuntu/Linux x86_64/aarch64，以及真实 Hermes/OpenCode 版本矩阵。

完整说明：[Release Notes](https://github.com/keith-yan/vibe-service-guardian/blob/v0.8.5.2-alpha.1/docs/RELEASE-NOTES-0.8.5.2-alpha.1.md) · [安装与平台状态](https://github.com/keith-yan/vibe-service-guardian/blob/v0.8.5.2-alpha.1/README.md) · [安全策略](https://github.com/keith-yan/vibe-service-guardian/blob/v0.8.5.2-alpha.1/SECURITY.md) · [验证证据](https://github.com/keith-yan/vibe-service-guardian/blob/v0.8.5.2-alpha.1/docs/VALIDATION.md)

问题与反馈：[GitHub Issues](https://github.com/keith-yan/vibe-service-guardian/issues)。安全问题请按 [SECURITY.md](https://github.com/keith-yan/vibe-service-guardian/blob/v0.8.5.2-alpha.1/SECURITY.md) 的私密报告路径提交，不要公开披露敏感细节。

---

Vibe Service Guardian helps Vibe Coding users understand which local services are listening, which project or Agent started them, whether they may be stale, what a stop could affect, whether they relaunch, and which open-weight model workloads the current hardware can sustain.

This unsigned Alpha adds visible instance identity, a noise-reduced Today's Focus view, per-project cleanup previews without batch termination, bounded post-stop observation, versioned local attribution rules, local alerts, optional Windows desktop entry points, read-only inference-runtime evidence, and measured capacity calibration.

The console binds only to `127.0.0.1`. VSG has no telemetry, cloud storage, automatic update, automatic cleanup, or automatic second termination. It does not read target-process/container environment variables or upload local evidence.

Use the Windows x64 ZIP for the current Windows-first candidate. The macOS `r9` files are source build kits that must be built and validated on the target Mac. No native Linux ELF is promised in this Alpha. Apple Silicon, physical Intel Mac, Linux native matrices, and real Hermes/OpenCode variants remain unverified.

Verify every download against its matching `.sha256` sidecar. Windows artifacts are unsigned; a matching hash verifies integrity against the published digest but does not provide a code-signing identity chain.
