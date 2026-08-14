# Vibe Service Guardian 0.8.3 上线准备度审查

## 结论

**Windows 11 当前主机上的未签名单用户 Alpha 候选：有条件通过。跨平台稳定版或生产级发布：不通过。**

0.8.3 已把高风险控制路径、真实 HTTP 矩阵、历史库升级、依赖哈希锁和容器/WSL 归属模型变成可重复的自动化证据。这显著降低了“按钮能点但闭环未验证”“升级损坏历史”“依赖解析漂移”和“把虚拟化 PID 当宿主 PID”的风险，但不能替代目标系统实机、代码签名、公有仓库 CI 和公开漏洞响应流程。

## 准入门槛

| 门槛 | 0.8.3 状态 | 证据或限制 |
|---|---|---|
| 默认只读、本地回环、无遥测 | 通过 | 既有控制平面与公开目录审计；主动动作仍需精确确认短语 |
| 关停后的 PID/子进程/端口/拉起验证 | 通过（隔离夹具） | 真实测试进程与真实回环端口；没有对用户服务执行 STOP |
| 固定矩阵、可中止、正文不持久化 | 通过（假运行时） | 35 次真实 HTTP；不代表真实模型性能 |
| 旧数据库迁移、备份、回滚、损坏恢复 | 通过 | schema version 4、v3→v4 成效表升级与迁移/恢复专项测试 |
| 依赖固定与安全门禁 | 通过（本地） | 20 份 SHA-256 wheel 锁、Ruff、Bandit、pip-audit；GitHub workflow 尚未实际运行 |
| Docker/WSL PID 与端口语义 | 通过（契约/解析夹具） | 未在本机 Docker daemon/多种 WSL 网络模式做完整实测 |
| Windows 11 源码与便携 EXE | 有条件通过 | 隔离回环验收、许可证/SBOM 与归档契约通过；仍为未签名包 |
| Windows 10 干净机/受限用户 | 未通过 | 尚无独立目标机证据 |
| macOS arm64/x86_64 原生包 | 未通过 | 只有构建包、哈希锁与 CI 定义，尚无真实 Mac 运行证据 |
| Ubuntu/Linux x86_64/aarch64 原生包 | 未通过 | 只有构建包、哈希锁与 CI 定义，尚无目标 Linux 运行证据 |
| Developer ID/Authenticode、公证、稳定更新 | 未通过 | 首版按用户要求保持未签名便携包 |
| 公有仓库 required checks 与私密漏洞报告 | 未通过 | 必须等用户公开仓库后人工配置 |

## 已关闭的 0.8.2 阻断项

1. **主动关停只有模拟结果**：改为真实、一次性夹具进程树和端口。
2. **工作负载矩阵没有真实协议闭环**：改为真实回环 HTTP 35 请求、SSE/NDJSON、取消、认证与 OOM 边界。
3. **SQLite 只有增量加列，没有版本/备份/损坏恢复**：改为版本化事务迁移和证据保留。
4. **依赖只有版本 pin，没有归档哈希**：改为跨平台、跨 Python ABI 的 wheel SHA-256 锁。
5. **Docker/WSL 的 PID/端口归属容易误读**：改为明确的 namespace 与生命周期契约。

## 仍然阻断稳定发布的事项

### P0：发布前必须人工完成

1. 在用户创建的公开 GitHub 仓库实际运行全部 Actions；将测试、安全和原生构建 job 设为 required checks。
2. 在 Windows 10/11 干净普通用户环境验证首次启动、重复启动、端口冲突、升级旧库、SmartScreen 提示和完整卸载。
3. 在 Apple Silicon、Intel Mac、Ubuntu x86_64；若宣称支持 aarch64，再增加 Linux aarch64，构建并运行原生验收脚本。
4. 决定公开安全联系人并启用 GitHub Private Vulnerability Reporting；当前 `SECURITY.md` 不能代替这个人工动作。
5. 为每个平台保留可追溯构建日志、sidecar SHA-256、SBOM、许可证清单和目标机验收记录。

### P1：稳定版前需要，但不阻断小范围未签名 Alpha

1. Windows Authenticode、macOS Developer ID 与 notarization；Linux 可增加发行签名或可复现构建证明。
2. Docker Desktop、原生 Linux Docker、WSL2 mirrored/NAT 网络模式的真实归属样本。
3. 真实 Ollama、llama.cpp、vLLM 等低风险测试模型的协议兼容矩阵；测试必须由维护者明确选择隔离环境，不能对用户常驻模型自动压测。
4. 数据库备份/隔离文件的 UI 管理与人工恢复向导；当前只保留证据并展示文件名，不自动合并损坏历史。

## 公开操作边界

本地源码、文档、测试、锁和便携包可以继续完善；本轮不得初始化 Git、提交、推送、创建仓库、发布 Release 或提交任何外部申请。公开仓库和 OpenAI 相关申请由用户后续亲自执行。
