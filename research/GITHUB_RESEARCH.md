# GitHub 同类开源项目调研

调研日期：2026-08-10；跨平台、新增 Agent、本地模型容量与 AI 运行体检补充核验：2026-08-11；Linux、双语界面与通用 GPU 厂商识别补充核验：2026-08-13。范围：Windows/macOS/Linux 进程与端口关联、开发服务生命周期、Agent/项目归属、遗留进程判断、本地控制面安全、本机模型容量、运行时健康、硬件遥测与实测校准。

## 结论

没有发现一个成熟项目能同时完成“Windows/macOS/Linux 端口检测 + 项目归类 + 多 Agent 会话归属 + 遗留评分 + Docker/WSL 分组”。现有项目分别覆盖底层进程网络可见性、受管开发服务、单一 Agent 运行控制面或网络深度监控。直接 Fork 任一项目都会带来明显错配，因此本项目采用原创控制面，只集成一个小型 BSD 许可运行时依赖 `psutil`；macOS 端调用系统自带 `lsof`，Linux 端结合 procfs/PCI sysfs 与可用的厂商 CLI，其余项目仅作为设计证据，不复制源码。

## 核验候选

| 项目 | 许可证 | 可借鉴内容 | 不直接采用的原因 | 本项目处理 |
|---|---|---|---|---|
| [giampaolo/psutil](https://github.com/giampaolo/psutil) | BSD-3-Clause | Windows/macOS 进程与 CPU/内存；Windows TCP/UDP 连接和服务 API | macOS 全局 `net_connections()` 在非 root 场景存在权限限制；是库而非成品控制台 | 固定 `psutil==7.2.2`；macOS 端口表改用非提权 `lsof` 并明确标注部分可见 |
| [winsiderss/systeminformer](https://github.com/winsiderss/systeminformer) | MIT | Windows 进程树、网络连接、服务管理、便携交付 | C/C++ 大型系统工具，范围远超需求；缺少项目和 Agent 语义 | 借鉴“进程证据优先”和便携模式，不复制源码 |
| [Uninen/devserver-mcp](https://github.com/Uninen/devserver-mcp) | MIT | 开发服务器名称、命令、工作目录、端口、启停和日志的统一模型 | 只管理预先配置/受管服务；项目自述为 Alpha；不做全机发现与 Windows 溯源 | 借鉴服务详情和受控启停概念，不运行其 MCP/Playwright |
| [domcyrus/rustnet](https://github.com/domcyrus/rustnet) | Apache-2.0 | Windows IP Helper/ETW 进程网络归属、连接可见性 | 深度包检测需要更高权限/Npcap，采集范围超过需求 | 只采用“端口必须绑定进程证据”的原则，不做抓包/DPI |
| [paperclipai/paperclip 的 runtime-processes 构想](https://github.com/paperclipai/paperclip/blob/master/doc/plugins/ideas-from-opencode.md) | 仅作为公开设计文档核验 | `owner agent + project workspace + command + port + uptime + health` 的领域模型 | 文档明确是插件构想，不是可直接复用的实现 | 将项目、Agent、服务和证据做成独立字段；不复制实现 |
| [laurentiu021/SystemManager](https://github.com/laurentiu021/SystemManager) | MIT | 本地优先、无遥测、普通权限下读取 Windows 连接表、单文件便携交付 | 通用系统维护工具，不处理 Vibe Coding 归属 | 采用普通权限降级和本地优先原则 |
| [CursorTouch/Windows-MCP](https://github.com/CursorTouch/Windows-MCP) | 调研时仅核验公开 README，未纳入依赖 | localhost 控制面的 Host 校验、默认无 CORS、状态变更鉴权 | MCP/桌面自动化范围与本项目不同 | 独立实现随机令牌、Host/Origin 校验和 CSP |

## Codex 会话边界

OpenAI 官方文档确认 Codex CLI 的 `codex resume` 可以按 session ID 恢复会话，并且 `--last` 默认按当前工作目录选择最近会话。这支持“会话与工作目录相关”的设计，但官方文档没有承诺一个供外部工具枚举实时桌面会话的稳定本地 API。因此，本项目只把 Codex 本地 `session_meta` 用作启发式证据；如果项目和时间窗口不能同时匹配，就不展示会话 ID。

- [OpenAI Codex CLI developer commands](https://learn.chatgpt.com/docs/developer-commands?surface=cli)
- [OpenAI Codex CLI overview](https://learn.chatgpt.com/docs/codex/cli)

## macOS 采集决策

`psutil` 官方文档明确列出 macOS 支持，但同时说明 macOS 上系统范围的 `net_connections()` 需要 root。工具的安全目标是不静默提权，因此 0.2.x 在 macOS 使用 `/usr/sbin/lsof` 或 PATH 中的 `lsof`，通过机器可解析的 `-F` 字段读取 TCP/UDP 端点，再用 `psutil` 补充当前用户可见的进程树和资源指标。

- [psutil 官方文档：net_connections](https://psutil.readthedocs.io/en/latest/#psutil.net_connections)
- [psutil macOS 权限问题记录](https://github.com/giampaolo/psutil/issues/1219)
- [lsof 官方仓库](https://github.com/lsof-org/lsof)

该方案的代价是：不使用 `sudo` 时不能承诺覆盖其他用户和所有系统进程。因此 API 返回采集方法与可见性，中文控制台固定显示“当前用户可见、部分覆盖”，而不是把结果包装成完整全机清单。

## 本地模型容量与推理运行时调研

| 项目 | 许可证 | 已核验能力 | 本项目吸收方式 | 未直接采用的原因 |
|---|---|---|---|---|
| [AlexsJones/llmfit](https://github.com/AlexsJones/llmfit) | MIT | 自动识别 RAM/CPU/GPU，区分内存适配、速度、质量、上下文，支持 MoE、动态量化、多个本地运行时和本机基准 | 吸收“估算必须展示输入和假设、实测覆盖估算”的产品闭环；本项目独立实现三层上限、并发/KV 预算和隐私边界 | 它是通用模型选型工具，不处理本项目已有的服务/端口/Agent 归属；直接嵌入会引入第二套目录、评分和发布链 |
| [gpustack/gpustack](https://github.com/gpustack/gpustack) | Apache-2.0 | 面向多机/多集群的 GPU 调度，编排 vLLM、SGLang、TensorRT-LLM，提供监控、负载均衡和模型服务治理 | 借鉴把“模型能装下”和“目标并发/SLA 能满足”分开的决策层次；把 vLLM/SGLang 列为可选模板和服务识别对象 | 范围是集群级控制平面，不适合单机、便携、无账号的本地 Vibe Coding 工具 |
| [ggml-org/llama.cpp / llama-bench](https://github.com/ggml-org/llama.cpp/blob/master/tools/llama-bench/README.md) | MIT | 本地 GGUF 推理与基准；`llama-bench` 可分别测提示处理和生成速度并输出 JSON | 可选调用用户已安装的 `llama-bench`，固定参数、无 shell、无下载；只保存安全数值和文件名，不保存模型绝对路径 | 不把 llama.cpp 二进制打包进本项目，也不自动下载模型；后端和模型版本兼容性必须由用户复核 |
| [ml-explore/mlx](https://github.com/ml-explore/mlx) | MIT | 面向 Apple Silicon 的统一内存数组框架和 Metal 加速基础 | macOS Apple Silicon 容量预算按统一内存只计算一次；MLX-LM 作为首选候选运行时之一 | Windows 不适用；当前没有 macOS 真机，首版只能完成代码适配和静态/合成测试 |

### 容量功能的整合边界

1. 模型目录是 2026-08-11 的离线、非穷尽快照；参数量、架构、上下文和许可证指向发布方页面，KV 系数和吞吐区间是本项目的保守工程估算。
2. “总参数”决定权重是否装得下；MoE 的“激活参数”只参与吞吐近似，不能把激活参数当成权重占用。
3. 输出分为理论物理上限、当前可用上限、满足用户输入 SLA 的上限。任何未经基准校准的 tok/s 和 TTFT 都是范围预测，不是性能承诺。
4. 运行方案只生成固定回环地址命令模板，绝不自动执行、联网拉取模型或修改推理运行时配置。
5. Ollama、llama.cpp、vLLM、SGLang、MLX-LM、LM Studio、KTransformers、KoboldCpp、Hugging Face TGI 的真实监听进程会单独标记为“模型推理”；桌面型 LM Studio 不提供直接结束按钮。

## AI 运行体检与运行时接口核验

| 上游 | 官方证据 | 本项目只读使用 | 边界 |
|---|---|---|---|
| [Ollama API](https://docs.ollama.com/api/ps) | `/api/ps` 返回当前加载模型、大小、显存大小、上下文和量化；生成接口返回加载/提示/生成计数与耗时 | 被动读取 `/api/ps`、`/api/version`；显式短基准使用本机 `/api/generate` | 不读取或注入 API Key，不自动拉模型 |
| [llama.cpp server](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md) | 官方 server 文档列出 `/health`、`/props`、`/slots`、`/metrics` 和 OpenAI 兼容端点 | 无提示词读取健康、配置、slot/metrics；显式短基准才调用补全接口 | 健康端点可能公开，认证姿态须以受保护模型端点响应共同判断 |
| [vLLM production metrics](https://docs.vllm.ai/en/latest/design/metrics/) | 官方文档列出运行/等待请求、KV cache、首 token/迭代 token 等指标 | 聚合允许名单中的 Prometheus 数值并用相邻快照计算速率 | 指标名随版本可能变化；无匹配指标就保持未知 |
| [ComfyUI](https://github.com/Comfy-Org/ComfyUI) | 官方项目提供工作流队列与系统状态接口 | 只读 `/system_stats` 与 `/queue`，展示设备/队列状态 | 不自动构造或提交工作流，不把图像生成服务当作文本补全基准 |
| [Docker restart policy](https://docs.docker.com/engine/containers/start-containers-automatically/) | Docker 官方定义 `no`、`on-failure`、`always`、`unless-stopped` 等重启策略 | `docker inspect` 只读 RestartPolicy 与 RestartCount | 容器 PID/端口仍按 Docker 生命周期单独分组，不套用宿主进程停止规则 |

TensorRT-LLM、Text Generation WebUI/ExLlama 和 TabbyAPI 首版可由进程/命令识别，并按 OpenAI 兼容健康/模型接口保守探测；若服务版本没有兼容接口，结果显示不可达/未知，不伪造后端能力。工具调用、视觉和语音支持仅在命令/配置出现明确标志时显示为“configured”，不把模型家族名称当作已启用功能。

## GPU、温度、风扇与功耗采集决策

Windows Task Manager GPU 数据由 WDDM 的 VidSch/VidMm 计数器支撑，Microsoft 说明它跨 DirectX、OpenGL、Vulkan、OpenCL 和 CUDA，GPU 汇总利用率应选取最忙引擎，专用显存面板显示跨进程当前分配字节。因此本项目读取 `GPU Adapter Memory` 的 Dedicated/Shared Usage 和 `GPU Engine` 的最忙引擎，而不是扫描进程私有内存。计数器只暴露 LUID，不直接给营销型号，本项目按独显优先做启发式配对并明确返回低置信映射。

- [Microsoft DirectX：GPUs in the Task Manager](https://devblogs.microsoft.com/directx/gpus-in-the-task-manager/)
- [Microsoft：per-process GPU memory counter known issue](https://learn.microsoft.com/en-us/troubleshoot/windows-client/performance/gpu-process-memory-counters-report-wrong-value)

Microsoft 还记录了部分 Windows 版本中“GPU Process Memory”/任务管理器 Details 页的进程级专用显存计数错误，因此本项目不用该进程级计数推导 VRAM，而用 Performance 页同类的适配器聚合计数。NVIDIA 存在 `nvidia-smi` 时优先使用厂商显式字段，以获得温度、风扇和功耗。

[AMD SMI 官方文档](https://rocm.docs.amd.com/projects/amdsmi/en/latest/how-to/amdsmi-cli-tool.html)提供 `metric/monitor --json` 的温度、功耗、风扇、VRAM 和利用率，但官方安装文档将支持平台限定为 ROCm 支持的 Linux 环境。它不是 Windows RX 显卡的可携依赖，因此 Windows AMD 首版使用 WDDM 读取显存/利用率，温度、风扇和功耗没有可信普通用户接口时显示不可用，不自动安装第三方内核驱动或硬件监控服务。

Apple 的官方 Metal 计数器面向应用命令缓冲区、Xcode/Metal Debugger 或应用内 HUD，而不是一个供普通后台进程无权限读取全机 GPU 温度/风扇/功耗的稳定接口。首版不请求 `sudo`，也不把统一内存余量伪装成 GPU 专用 VRAM；真实 Mac 无传感器接口时显示不可用。

- [Apple：GPU counters and counter sample buffers](https://developer.apple.com/documentation/metal/gpu-counters-and-counter-sample-buffers)
- [Apple：Analyzing Apple GPU performance using counter statistics](https://developer.apple.com/documentation/xcode/analyzing-apple-gpu-performance-using-counter-statistics)

## 新增 Agent 官方资料核验

| Agent | 官方证据 | 可安全利用的本地证据 | 0.2.x 实现边界 |
|---|---|---|---|
| [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent) | [Sessions 文档](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/sessions.md)说明 `hermes`、`hermes gateway`、恢复参数及 `~/.hermes/state.db` | 进程名/包装命令；只读 `sessions` 表中的 ID、可用目录和时间列 | 不读取 `messages`；没有目录证据时不强行关联会话 ID |
| [anomalyco/opencode](https://github.com/anomalyco/opencode) | [CLI 文档](https://github.com/anomalyco/opencode/blob/dev/packages/web/src/content/docs/cli.mdx)与[官方 session schema](https://github.com/anomalyco/opencode/blob/dev/packages/opencode/src/session/session.sql.ts) | `opencode` 进程；只读本地 `session` 表的 `id`、`directory`、`time_created` | 兼容搜索 `opencode*.db`，先检查表/列再查询，不依赖未核验的固定数据库文件名 |
| [Aider-AI/aider](https://github.com/Aider-AI/aider) | [官方配置样例](https://github.com/Aider-AI/aider/blob/main/aider/website/assets/sample.aider.conf.yml)列出聊天历史文件 | `aider` 进程、Python `-m aider` 包装命令、项目工作目录 | 历史 Markdown 不是稳定会话 API；不读取其正文，不展示伪造会话 ID |
| [google-gemini/gemini-cli](https://github.com/google-gemini/gemini-cli) | [命令文档](https://github.com/google-gemini/gemini-cli/blob/main/docs/reference/commands.md)与[配置文档](https://github.com/google-gemini/gemini-cli/blob/main/docs/reference/configuration.md)说明 `/resume`、`--resume`、`--list-sessions` 与项目范围会话 | Node 包路径/`gemini` 进程；显式恢复 ID；会话文件名和修改时间 | 不打开 `session-*.json*` 聊天内容；仅凭文件名不跨项目强行匹配 |
| [aaif-goose/goose](https://github.com/aaif-goose/goose) | [环境变量文档](https://github.com/block/goose/blob/main/documentation/docs/guides/environment-variables.md)说明 macOS/Windows 状态根目录与 `AGENT=goose`、`AGENT_SESSION_ID`；[配置文档](https://github.com/aaif-goose/goose/blob/main/documentation/docs/guides/config-files.md)给出配置路径 | `goose`/`goosed` 进程和父进程链 | 本工具不枚举其他进程的环境变量，也未确认稳定只读 session schema，因此首版只做产品/项目归属 |

### 第一性边界

“某个 Agent 有会话文件”不等于“外部工具可以稳定、无隐私副作用地读取并实时关联”。本项目只采用三类证据：进程/父进程签名、运行命令显式会话参数、官方 schema 中的会话元数据字段。聊天内容、环境变量值和未经核验的内部结构不进入采集面。

## 许可证和供应链决策

1. 没有执行候选仓库的安装脚本，也没有复制候选实现；GitHub 页面和官方源码 schema 只用于核验公开接口与字段。
2. `psutil` 固定版本并在构建环境内安装；其 BSD-3-Clause 许可适合便携分发。
3. PyInstaller 仅用于本地构建，其 bootloader 例外允许分发生成的可执行文件；不把 PyInstaller 当作运行时服务。
4. Docker/WSL/lsof 通过系统已有 CLI 只读查询；缺少命令或 daemon 时明确显示不可用，不自动安装。
5. [PyInstaller 官方 macOS 说明](https://pyinstaller.org/en/stable/usage.html)确认未提供 Developer ID 时会执行 ad-hoc 签名；Apple Silicon 至少需要 ad-hoc 签名。因此首版“未签名”指无开发者身份签名、无公证，而非破坏 Mach-O 可运行性的绝对零签名。

## 未采用的做法

- 不用 `netstat | findstr | taskkill /F` 直接按端口杀进程：该模式缺少创建时间和进程树校验，容易误杀复用 PID。
- 不抓包、不读取 DNS/URL 或协议正文、不持久化远端地址；0.4.0 仅在内存快照显示已识别模型进程的当前远端 IP/端口与作用域，用于发现意外联网。
- 不把低 CPU 等同于遗留：开发服务器正常情况下长期空闲，低负载只能作为弱证据。
- 不将 Windows 服务、容器或 WSL 进程与普通 Agent 子进程使用同一遗留模型。
