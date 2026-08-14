# Vibe Service Guardian

[GitHub](https://github.com/keith-yan/vibe-service-guardian) · [English](README.en.md) · [项目影响与证据](IMPACT.md) · [路线图](ROADMAP.md) · [维护者](MAINTAINERS.md) · [0.8.4 P0 边界](docs/V0.8.4-P0-CLOSURE.md) · [隐私](PRIVACY.md) · [安全](SECURITY.md) · [上线审查](docs/PRODUCTION-READINESS-0.8.4.md) · [本地验证记录](docs/VALIDATION.md)

Vibe Service Guardian（服务守望）是面向 Windows、macOS 与 Linux Vibe Coding 场景的本机服务溯源、模型容量规划和推理优化工具。它一方面把“哪些进程正在监听端口”关联到“哪个项目、哪个 Agent/IDE/终端、是否可能已经遗留”；另一方面读取本机硬件，回答“最大能装什么开放权重模型、指定并发能否支撑、哪个推理引擎更匹配，以及出现 OOM/加速器错误后如何回验”。全部结果通过只监听回环地址、可切换中英文的本地 Web 图形控制台展示。

当前源码版本为 **0.8.4（未发布）**。公开仓库已创建，首个 GitHub Release 尚未发布；本轮只在本地功能分支开发，没有提交、推送或发布。现有 Windows 本机测试和构建记录不等同于 macOS/Linux 真机验收。

0.8.4 P0 在既有证据闭环上增加两项日常能力：停止后 5/15/30 分钟持续观察与用户历史生命周期标签；针对已加载本机模型的固定 60 秒单/双并发校准和可复用实测档案。它仍不自动清理、不自动下载模型、不读取目标进程环境变量，也不把理论容量上限称为实验证实的物理极限。完整边界见 [0.8.4 P0 说明](docs/V0.8.4-P0-CLOSURE.md)；当前仍是“本机单用户 Alpha”，不是跨平台生产就绪版本，原因见 [上线审查](docs/PRODUCTION-READINESS-0.8.4.md)。

## 30 秒看懂

VSG 的核心不是“列出端口”，而是把一个本机服务沿着证据链回答完整：**谁开的 → 属于哪个项目/Agent → 是否疑似遗留 → 关停会影响谁 → 停止后端口/PID 是否真的消失、会不会自动拉起 → 这台机器能否支撑目标模型与并发**。

![Vibe Service Guardian 本机证据链](docs/assets/vsg-overview.svg)

最短体验路径：从源码运行 `python -m vsg --open`，在“服务监控”中打开一条服务详情；先核对归属和判断证据，再记录“确属遗留 / 不是遗留 / 暂不确定”。顶部“本机成效”只预览聚合结果；只有精确输入 `EXPORT REPORT` 才下载脱敏 JSON，且不会自动上传。运行方法见[源码运行](#源码运行)，证据边界见 [IMPACT.md](IMPACT.md)。

## 0.8.4 平台状态

| 平台 | 交付状态 | 端口采集 | 说明 |
|---|---|---|---|
| Windows 10/11 x64 | 当前 Windows 主机已执行源码、自动化和便携 EXE 路径验证；Windows 10 仍需独立实机验收 | `psutil` 系统连接表 | Windows 服务与 WSL 单独分组 |
| macOS 13+ Apple Silicon `arm64` | 代码与构建链已适配 | 系统 `lsof` | 需在真实 Apple Silicon Mac 上构建和原生验收 |
| macOS 13+ Intel `x86_64` | 代码与构建链已适配 | 系统 `lsof` | 需在真实 Intel Mac 上构建和原生验收 |
| Ubuntu 22.04+ / 图形 Linux `x86_64`、`aarch64` | 代码、图形启动入口、原生构建与验收链已适配 | `psutil` / procfs 可见范围 | 仍需在对应 Linux 真机生成 ELF 并执行原生验收 |

Windows 无法直接生成可信的 macOS Mach-O 或 Linux ELF。`scripts/Build-macOS-Build-Kit.ps1` 与 `scripts/Build-Linux-Build-Kit.ps1` 可准备源码构建包，再在目标系统生成未签名原生便携包。详见 [MACOS-VALIDATION.md](MACOS-VALIDATION.md) 和 [LINUX-VALIDATION.md](LINUX-VALIDATION.md)。

## 核心能力

- 展示 TCP 监听、UDP 绑定、PID、进程路径、已脱敏命令、工作目录、父进程链、CPU、内存和启动时间。
- Agent 主进程即使没有监听端口也会进入“Agent 进程”分组；Agent 本体只展示，不按开发服务规则评分或停止。
- 根据工作目录、项目标记文件、命令路径和父进程工作目录归类项目。Windows 默认根目录优先使用 `E:\vibe coding`；macOS 默认使用已有的 `~/Developer`、`~/Projects`；Linux 默认检查已有的 `~/Projects`、`~/Developer`、`~/src`、`~/workspace`。三类系统都可在设置中添加其他绝对路径。
- Windows 服务、Docker、WSL 使用独立分组。macOS 与 Linux 隐藏不适用的 Windows 服务/WSL 设置；Docker 仍单独展示。
- 基于运行时长、项目目录、空闲状态、历史 Agent 归属和重复实例给出“正常 / 建议复核 / 疑似遗留”，每条判断均显示证据。
- 支持打开本地 URL、在资源管理器/Finder 中打开项目、标记预期服务、查看本地操作记录，以及二次确认后停止普通宿主机开发进程树。
- 识别正在监听的 Ollama、llama.cpp/llamafile、vLLM、SGLang、MLX-LM、LM Studio、KTransformers、KoboldCpp、Hugging Face TGI、ComfyUI、TensorRT-LLM、Text Generation WebUI/ExLlama 与 TabbyAPI，并提供“模型推理”快速过滤；LM Studio 主程序保持只读。
- 控制台仅绑定 `127.0.0.1`，无云端遥测、无云端存储、无外部字体或 CDN；中英文首次按浏览器首选语言，切换值只保存到浏览器本地存储。

## 0.8.1 关系、关停与实测校准闭环

- **服务关系**：把项目、Agent/会话、服务、进程、监听端点和当前本机 TCP 客户端放入同一关系模型。关系只使用当前可见的本机证据；无法读取连接表时明确标记未知，通配监听不会把同端口公网连接误判为本机依赖。
- **关停评估**：停止前先展示只读阻断、客户端/端点影响、生命周期管理器、自动重启风险和人工恢复步骤。只有普通宿主机开发/推理进程可进入 `STOP <PID>` 链路；Agent、IDE、Windows 服务、Docker、WSL 和受保护对象仍只读。
- **停止后验证**：操作后在有限窗口内复核原 PID、子进程、端口和替代 PID，区分“已停止”“重新拉起”“停止不完整”“证据不完整”。工具不会自动二次结束被管理器重新拉起的进程；更晚的重启由生命周期时间线继续记录。
- **固定工作负载矩阵**：预览固定的单请求 5 次、双并发 10 次、四并发 20 次三步计划，再输入 `BENCHMARK PLAN <端口>`。每一步重查 RAM、VRAM、温度和磁盘护栏；只允许一个活动矩阵，不自动增加并发/上下文，不故意制造 OOM，并可协作式中止尚未发出的波次。
- **反向校准**：矩阵展示 TPS、聚合吞吐、TTFT P50/P95、近似 token 间隔与采样期资源峰值。可选映射离线目录模型与量化；只有同硬件、模型、量化、并发、上下文和输出长度的样本才作为同负载校准，并在容量页显示理论预测、实测值、带符号误差和绝对误差。

完整规则与统计边界见 [0.8.1 功能边界](docs/V0.8.1-FEATURES.md)。

## 0.8.0 事件与模型资产闭环

- **可纠正归属**：服务行可人工纠正项目、服务名和 Agent；也可建立按指纹、端口、运行时、命令或目录匹配的本地规则。项目根目录中的可选 `.vsg.yaml` 可声明项目名、预期服务与保护状态；所有覆盖都会显示本地证据，不会修改目标项目的启动命令。
- **遗留判断可追溯**：扫描器把项目规则、会话证据、启动/停止/疑似重启、暴露范围变化、联网变化和资源阈值放入同一时间线。事件视图按服务指纹关联日志与遥测样本，让用户看到“谁启动、何时异常、现在是否仍在监听”，而不是只看一个静态分数。
- **网络拓扑**：以主机、服务、监听端点和当前远端连接构建本地图谱，Docker、WSL、反向代理和非回环监听单独标识。远端 IP 只存在于当前内存快照，历史仅保留不可逆端点摘要、作用域与端口；不抓包、不读取流量正文。
- **本地模型资产盘点**：只有用户选择一个绝对目录并精确输入 `SCAN MODELS` 才开始。扫描最多 5000 个文件、6 层、120 秒，拒绝根目录、用户主目录、符号链接和目录联接；只读取有界 GGUF/Safetensors/Ollama/Modelfile/config 元数据，持久化相对路径和快速指纹，不删除、不移动、不下载模型。
- **容量衔接**：盘点结果区分 GGUF、Safetensors、量化、架构、Dense/MoE、专家数、上下文、分片与疑似重复；“可装载提示”只比较实测文件体积、工作区和当前可用内存，明确不替代 KV、并发、吞吐与 TTFT 的完整规划。选中资产后可带入模型容量规划和完整引擎兼容矩阵。
- **数据生命周期**：归属规则、模型盘点历史、时间线、脱敏日志事件和遥测样本可按类别显式清除，清除前必须输入 `CLEAR HISTORY`。该动作不会删除模型、项目、原始日志或配置文件。

规则格式、接口和限制见 [0.8 功能边界](docs/V0.8-FEATURES.md)。

## AI 运行体检闭环

“AI 运行体检”不是把进程存在等同于服务健康，而是分层展示证据与未知项：

1. **实时资源**：CPU、RAM、交换空间、系统卷与项目卷余量、网络速率；NVIDIA 优先读取 `nvidia-smi`，AMD 在可用时读取 `amd-smi`，Windows 无厂商遥测的 GPU 使用 WDDM 性能计数器读取实际分配显存与最忙引擎利用率。温度、风扇和功耗只展示操作系统或厂商工具真实返回的数据，拿不到就明确显示“不可用”。
2. **运行时健康**：对已识别的本机模型端口执行无提示词、无凭据的只读健康探测，读取模型加载状态、量化、上下文、并发/KV 指标和后端能力。Ollama、llama.cpp、vLLM/SGLang/TGI/LM Studio 等按各自只读接口适配；不会读取进程环境变量里的 API Key。
3. **实际性能**：运行时已公开的 Prometheus 指标可形成被动 TPS、TTFT、运行/排队请求和 KV 使用率；否则保持未知。需要实测时，用户必须输入 `BENCHMARK <端口>`，短基准只向本机已识别模型服务发送固定合成提示，限制并发 1–4、上下文 128–4096、输出 1–64，不通过制造 OOM 探测极限。
4. **安全与联网**：区分回环、指定网卡与所有网卡监听；只读判断匿名端点/认证要求、Windows/macOS/Linux 防火墙状态、反向代理进程，以及模型服务当前远端 IP/端口和作用域。它不抓包、不读 URL/正文、不扫描 LAN，也不把 `0.0.0.0` 直接宣称为已暴露公网。
5. **稳定性**：展示启动时间、运行时长、历史重启计数和 Docker 重启策略。日志和配置检查必须由用户选择普通文件并输入 `INSPECT <PID>`；只返回脱敏片段/结构，不自动搜索日志、`.env` 或凭据文件。
6. **备份与回滚**：输入 `SNAPSHOT` 后为指定文件创建本地清单；大模型权重不自动复制，超过 512 MiB 不自动计算完整 SHA-256。小于 2 MiB 的常见配置保存原样本地副本；回滚还需输入 `RESTORE <文件名>`，并先保存回滚前副本，不自动重启服务。
7. **多机边界**：只探测设置中人工加入的回环/私网/链路本地 HTTP 节点，连接前重新解析地址；不自动发现节点、不携带凭据、不做集群编排。

综合结论分为机器健康、模型性能、服务安全、服务稳定和资源容量五域。总分只计算已有证据的领域；若仍有未知域，分数后显示 `*`，不能把“已知项正常”解释为“所有项目均已验证”。

## 优化、引擎选型与日志监控闭环

“优化与引擎建议”页按“检测 → 诊断 → 建议 → 基准 → 监控 → 回滚”组织：

1. 用户选择权重格式、目标并发/上下文、易用性/吞吐/延迟/内存/功耗倾向和工具调用、多模态等需求；系统结合操作系统、GPU 厂商、驱动/计算能力与已安装运行时，为 llama.cpp、Ollama、MLX-LM、vLLM、SGLang、TensorRT-LLM 和 TabbyAPI/ExLlamaV2 展示完整兼容矩阵，并把推荐候选排在前面。
2. Windows 上的 vLLM/SGLang/TensorRT-LLM 不伪装成原生支持；只有用户允许且检测到 WSL2，或明确接受 NVIDIA Docker/WSL GPU 路径时，才作为“预览”候选。所有结果仍需对照页面中的官方/上游依据并在本机复测。
3. 硬件优化只由实测 RAM/VRAM/磁盘/温度、当前运行时健康、用户目标和脱敏日志事件触发。每条建议列出证据、动作、代价、置信度与验证方法；缺失传感器保持未知。
4. 横向基准只比较模型名、并发、上下文和输出长度完全相同的记录；不同负载不会被错误排名。
5. 持续日志监控必须为已识别的宿主机模型服务选择普通日志文件，并准确输入 `WATCH <PID>`。系统从末尾增量读取，识别 OOM、CUDA/ROCm/Metal/Vulkan、加载、超时、认证、上下文、工具模板、崩溃和 CPU 回退事件；原始日志不入库，Web 接口不返回绝对路径。
6. 游标绑定服务指纹、PID 与启动时间。服务退出或 PID 被复用时自动停止该游标，必须对新进程重新确认；脱敏事件默认保留 7 天，可在设置中改为 1–90 天。

VSG 不自动安装建议引擎、不改驱动、不改功耗/风扇固件、不改模型参数；建议的最终成立条件始终是同模型、同量化、同上下文、同并发的本机复测。

## 模型容量规划闭环

模型容量页把“硬件读取—负载设定—方案筛选—运行参数—本地校准—服务监控”连成一条链：

1. 读取 CPU、物理/逻辑核心、系统内存与当前可用内存、系统盘空间、GPU/统一内存、后端和本地推理运行时。不会采集序列号、MAC、主机名、用户名、模型文件列表或环境变量。
2. 用户输入计划用户数、峰值并发、平均输入/输出 tokens、每会话上下文、目标 tokens/s/用户和目标 TTFT。总用户数用于估算排队波次；峰值并发才直接放大 KV 缓存和吞吐压力。
3. 同时给出三种不同结论：
   - **物理装载上限**：释放资源后，权重、KV 和工作区理论上能装下；可能非常慢或需要混合卸载。
   - **实际可用上限**：当前可装入、至少 Q3、预估达到最低 2 tokens/s/用户且 TTFT 不超过 30 秒；不等于满足业务 SLA。
   - **目标 SLA 上限**：Q4 以上，并同时满足当前空闲内存、指定并发、目标生成速度和 TTFT。
4. Dense 权重和计算均按总参数近似；MoE 权重内存仍按**总参数**，生成吞吐才按**激活参数**近似，避免把 A3B 错当成只需 3B 权重。
5. 每个候选都拆出权重、KV、工作区、执行路径、当前/理论余量、每用户吞吐范围、TTFT 范围、目标下最大并发、置信度和风险原因。
6. 首批运行方案覆盖 Ollama、llama.cpp、macOS MLX-LM；检测 LM Studio；vLLM/SGLang 与 WSL 桥接为预览。Windows AMD/Intel GPU 路径明确标为实验性。
7. 推荐命令只生成模板，固定使用 `127.0.0.1`，不会自动执行。模型只提供发布方页面信息，不自动下载大文件。
8. 可选 `llama-bench` 短基准必须输入 `BENCHMARK`；校准结果只保存模型文件名、大小和数值，不保存绝对路径。
9. 对已就绪且无需凭据的回环模型服务，可预览固定三步负载矩阵并输入 `BENCHMARK PLAN <端口>`。同负载实测会反向校准候选并显示预测误差；样本条件不完全一致时不会冒充精确校准。

内置目录是带日期的离线快照，首批覆盖 Qwen3.5、OpenAI gpt-oss、Gemma 4、Mistral Small 4 和 DeepSeek-V4-Flash。它不是穷尽列表，也不是质量排名。公式、来源和误差边界见 [docs/MODEL-CAPACITY.md](docs/MODEL-CAPACITY.md)。

## Agent 与开发入口识别

0.8.1 的进程/父链识别覆盖：

- Codex Desktop/CLI、Claude Code、WorkBuddy/CodeBuddy；
- Hermes Agent、OpenCode、Aider、Gemini CLI、Goose；
- Cursor、Windsurf、VS Code；
- Windows Terminal、PowerShell、CMD；
- macOS Terminal、iTerm2、Warp、Ghostty、WezTerm、Zsh、Bash、Fish。

会话关联采用“有证据才显示”的保守策略：

- Codex 与 Claude Code读取近期本地 JSONL 元数据；
- Hermes 只读 `~/.hermes/state.db` 的 `sessions` 表，不查询消息表；
- OpenCode 只读本地数据库的 `session` 表，仅取 ID、项目目录和时间字段；
- Gemini CLI 只使用 `~/.gemini/tmp/.../chats/session-*.json*` 的文件名和修改时间，不打开聊天内容；
- Aider 与 Goose 首版确认产品/进程和项目归属，不伪造稳定会话 ID；
- Hermes、OpenCode、Gemini CLI 等运行命令显式携带恢复 ID 时，可直接作为高置信度证据。

完整的“进程识别 / 项目归属 / 会话归属 / 真实验证等级”拆分见 [docs/AGENT-SUPPORT.md](docs/AGENT-SUPPORT.md)。尤其是 Hermes：当前官方 `sessions` schema 没有项目目录时，数据库记录不会单独建立项目会话归属。

## Windows 便携包

先在源码目录执行 `Build-Portable.cmd`，脚本会生成 `Vibe-Service-Guardian-Windows-x64-0.8.4.zip`。然后：

1. 解压 ZIP 到仅当前用户可访问的可写目录。
2. 双击 `Start-VSG.cmd`。
3. 若浏览器没有自动打开，双击 `Open-VSG.cmd`。
4. 退出时双击 `Stop-VSG.cmd`。

运行数据保存在便携目录的 `data` 文件夹。当前 EXE 未签名，Windows SmartScreen 可能显示“未知发布者”；运行前请核对随包 `.sha256`。

## macOS 未签名便携包

当前修订尚无真实 macOS 验收证据，因此提供的是固定依赖的原生构建包，不是声称已经实机验证的二进制：

1. 在目标 Mac 解压本地生成的 `Vibe-Service-Guardian-macOS-build-kit-0.8.4.zip`。
2. 执行 `chmod +x ./*.command ./scripts/*.sh`。
3. 执行 `./scripts/Build-Portable-macOS.sh`。
4. 进入构建生成的 `release/Vibe-Service-Guardian-macOS-<架构>-0.8.4` 目录，运行 `./scripts/Validate-macOS.sh`，以 `MACOS_NATIVE_VALIDATION_OK` 为通过标志。

构建脚本只生成当前 Mac 的原生架构：Apple Silicon 生成 `arm64`，Intel Mac 生成 `x86_64`。首版不使用 Apple Developer ID 证书、不公证、不上传；PyInstaller 会按 macOS 要求执行无身份的 ad-hoc 签名，这不等于可信发布者签名。Gatekeeper 操作与验收项见 [MACOS-VALIDATION.md](MACOS-VALIDATION.md)。

macOS 默认不请求 `sudo`。`lsof` 只能保证当前用户可见端口，跨用户和部分系统进程可能缺失；控制台会将该采集器标为“部分可见”。

## Ubuntu / Linux 图形便携包

Windows 侧先运行 `powershell -File .\scripts\Build-Linux-Build-Kit.ps1` 生成源码构建包；在 Ubuntu/Linux 目标机解压后：

```bash
chmod +x ./*.sh ./scripts/*.sh
./scripts/Build-Portable-Linux.sh
./release/Vibe-Service-Guardian-Linux-$(uname -m)-0.8.4/scripts/Validate-Linux.sh
```

便携包运行 `./Start-VSG.sh` 后由默认浏览器打开图形控制台。源码运行可先执行 `./Setup-Linux.sh`；若希望加入 GNOME/KDE 等桌面应用菜单，显式运行 `VSG_INSTALL_DESKTOP_LAUNCHER=1 ./Setup-Linux.sh`，只写入当前用户的 XDG applications 目录，不使用 `sudo`。

## 安全停止规则

停止按钮只有同时满足以下条件时启用：

1. 来源是已识别的普通宿主机开发进程或模型推理服务，而非 Agent 本体、LM Studio 主程序、Windows 服务、Docker 或 WSL；
2. 目标不在系统与 Agent 保护名单；
3. 用户输入 `STOP <PID>`；
4. 服务端重新校验 PID、启动时间和进程树；
5. 进程树中不存在受保护子进程。

工具先请求温和终止，超时后才强制结束仍存活的目标。不存在按进程名批量结束或自动清理。

停止前还会显示关系模型生成的影响评估。停止完成后，系统在有限时间窗内检查原 PID/子进程、原端口和替代 PID；验证只观察、不自动对重新拉起的进程执行第二次停止。

## 源码运行

Python 3.10–3.12（下面示例为 Python 3.12；其他版本选择同名 `py310`/`py311` 锁）：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --only-binary=:all: --no-deps --require-hashes -r requirements-lock\bootstrap-py3.txt
.\.venv\Scripts\python.exe -m pip install --only-binary=:all: --no-deps --require-hashes -r requirements-lock\runtime-windows-py312.txt
.\.venv\Scripts\python.exe -m vsg --open
```

macOS：

```bash
chmod +x Setup-macOS.command
./Setup-macOS.command
./Start-VSG.command
```

Ubuntu / Linux：

```bash
chmod +x Setup-Linux.sh
./Setup-Linux.sh
./Start-VSG.sh
```

只读扫描与测试：

```text
python -m vsg --once
python -m unittest discover -s tests -v
```

Windows 构建执行 `Build-Portable.cmd`；macOS 原生构建执行 `./scripts/Build-Portable-macOS.sh`；Linux 原生构建执行 `./scripts/Build-Portable-Linux.sh`。

## 隐私与判断边界

- 不枚举目标进程环境变量；程序自身只读取用于定位本地目录的路径变量。完整字段清单见 [PRIVACY.md](PRIVACY.md)。
- 命令中的常见 Key、Token、密码、Cookie、Bearer、凭据 URI 与敏感查询参数会在进入数据模型前替换为 `[REDACTED]`。
- SQLite 历史不保存完整命令，只保存命令哈希、项目和归属结果。
- 模型规划硬件指纹不含设备序列号；本地基准不保存模型绝对路径。
- 会话元数据只用于本地匹配，不上传；不读取 Hermes/OpenCode 消息表，不打开 Gemini 聊天 JSON。
- “疑似遗留”只是可解释的复核线索，不是自动关停依据。

详细威胁模型见 [SECURITY.md](SECURITY.md)，架构见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)，开源调研与取舍见 [research/GITHUB_RESEARCH.md](research/GITHUB_RESEARCH.md)，本轮通过项与未验证边界见 [docs/VALIDATION.md](docs/VALIDATION.md)。构建产物会包含完整第三方许可证、校验清单和 `SBOM.spdx.json`。

本项目独立开发，与 OpenAI、Anthropic、Cursor、Windsurf、Microsoft、Hermes、OpenCode 或其他被识别产品不存在隶属或背书关系。
