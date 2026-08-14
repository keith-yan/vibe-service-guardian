# Agent、IDE 与终端支持矩阵

“支持”分为三个互不等价的层级：识别产品进程、把服务归到项目、把服务归到具体会话。界面不会用进程识别冒充会话识别。

| 产品/入口 | 进程或父链识别 | 项目归属 | 会话 ID | 当前验证等级 |
|---|---|---|---|---|
| Codex Desktop | Windows 包路径、macOS App 路径 | 工作目录/父链 | 近期本地 `session_meta`，须项目与时间匹配 | Windows 当前主机 + 自动化；macOS 待实机 |
| Codex CLI | `codex`、包路径/命令 | 工作目录/父链 | `codex resume <id>` 或近期 `session_meta` | 自动化夹具；真实版本组合仍需扩充 |
| Claude Code | `claude`、包路径/命令 | 工作目录/父链 | 显式 `--resume` 或近期 JSONL 元数据 | 自动化夹具；真实端到端待验 |
| Cursor | IDE 进程/父链 | 工作目录/父链 | 不声称稳定会话 ID | 自动化夹具 |
| Windsurf | IDE 进程/父链 | 工作目录/父链 | 不声称稳定会话 ID | 自动化夹具 |
| VS Code | IDE 进程/父链 | 工作目录/父链 | 不声称稳定会话 ID | 自动化夹具 |
| WorkBuddy/CodeBuddy | 精确进程名/父链 | 工作目录/父链 | 不声称稳定会话 ID | 自动化夹具 |
| Hermes Agent | 原生进程或 Python 包装命令 | 工作目录/父链 | 显式 `--resume`；当前官方 `sessions` schema 无项目目录时，数据库记录不会单独建立项目会话归属 | schema/自动化夹具；真实端到端待验 |
| OpenCode | 原生进程或 Node/Bun 包装命令 | 工作目录/父链 | 显式 `--session`，或只读 `session` 表的目录+时间匹配 | 官方 schema/自动化夹具；真实端到端待验 |
| Aider | 原生进程或 Python 包装命令 | 工作目录/父链 | 不读取聊天历史，不声称稳定会话 ID | 自动化夹具 |
| Gemini CLI | 原生进程或 Node 包装命令 | 工作目录/父链 | 显式 `--resume`；文件名仅作候选，不跨项目强配 | 自动化夹具 |
| Goose/goosed | 精确进程名/父链 | 工作目录/父链 | 不枚举环境变量，不声称稳定会话 ID | 自动化夹具 |
| PowerShell/CMD/Windows Terminal | 父进程链 | 工作目录/父链 | 只标终端，不是 Agent 会话 | Windows 自动化与当前主机路径 |
| macOS Terminal/iTerm2/Warp/Ghostty/WezTerm/Shell | 父进程链 | 工作目录/父链 | 只标终端，不是 Agent 会话 | 自动化夹具；待真实 Mac 扩充 |
| Linux GNOME Terminal/Konsole/Kitty/Alacritty/Tilix/XTerm/Shell | 父进程链 | 工作目录/父链 | 只标终端，不是 Agent 会话 | 自动化夹具；待 Ubuntu/Linux 实机扩充 |

## 验证等级解释

- “当前主机”：本版本开发时在实际运行主机观察到相应路径，但不代表所有版本/安装方式。
- “自动化夹具”：单元测试构造公开进程名、父链、命令或 schema，证明解析逻辑，不等于真实产品端到端兼容认证。
- “待实机/端到端”：已实现保守适配，但发布前仍需在真实产品和目标操作系统运行验收。

Docker 和 WSL 是运行环境，不作为 Agent。它们单独分组，并保留容器 ID、发行版和内部 PID 等各自的身份模型。
