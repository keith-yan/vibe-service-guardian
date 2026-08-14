# Changelog

本文件记录面向用户的变化。项目在公开发布前不承诺稳定 API。

## [0.8.4] - Unreleased

### Bounded post-stop observation

- 普通宿主机开发/推理服务仍需逐次输入 `STOP <PID>`；停止后可选择 5/15/30 分钟观察窗口（默认 15 分钟），每 10 秒复核原 PID、原端口和脱敏命令哈希，观察可随时中止。
- 只有先观察到原 PID 身份消失，随后原端口由其他 PID 监听或同一脱敏命令哈希重新出现，才标记“已复活”；否则明确保留为证据不足。复活后不自动二次停止。
- 观察进度、复活证据、父进程变化和最终报告写入本机 SQLite；页面提供高优先级本地提示，可选调用操作系统本机通知，不联网。
- 用户可把同一规范化可执行路径与工作目录标记为“预期服务”或“可安全清理”。数据库只保存组合身份 SHA-256；“可安全清理”只提高复核优先级，绝不绕过保护名单、PID/启动时间复核或 `STOP` 确认。
- Agent/IDE、Docker、WSL、Windows 服务及可见生命周期管理器继续保持只读，新增可复制的人工操作建议；建议只展示参数数组推导的命令文本，服务端不执行。

### Measured local capacity profiles

- 对健康就绪、无需凭据、已报告加载模型的本机推理服务增加固定 60 秒单并发/双并发校准。计划必须完整预览并输入 `BENCHMARK <端口>`，不自动下载或加载模型。
- 校准期间持续采样 RAM、VRAM、GPU 温度和磁盘余量；RAM 或 VRAM 达到 85% 时协作式停止发起新请求，在途请求可完成，不强杀服务或故意制造 OOM。
- SQLite schema version 5 增加本机实测档案与停止观察任务。档案区分理论容量上限、实测可用上限和推荐安全并发，保存预测误差、资源峰值与限制；硬件指纹或 VRAM 总量变化时自动标为“可能失效”。
- 档案可复用、手动标记过期或经 `DELETE PROFILE <ID>` 删除；项目—推理实例视图在运行时提供当前请求数且档案有效时显示实测并发余量。
- 项目—推理实例余量要求唯一已加载模型与档案精确匹配；同时加载多个模型时保持证据不足，RAM/VRAM 达到 85% 时不建议新增并发。

### Boundary

- 0.8.4 P0 不实现托盘、全局快捷键、开机启动、项目批量停止、自动下载、自动调参、跨机同步、远程控制或发布流程。macOS/Linux 仍需后续真机验收，不能用当前 Windows 本地结果替代。

## [0.8.3] - 2026-08-14 local milestone (not published)

### Converged verification

- 增加只操作测试自建进程的真实关停夹具，覆盖父子进程树、端口释放、PID 消失、看门狗自动拉起、替代 PID 识别，以及“只观察、不二次关停”契约。
- 增加回环假模型运行时，固定矩阵通过真实 HTTP 完成 5 + 10 + 20 共 35 个请求；覆盖 Ollama NDJSON、OpenAI 兼容 SSE、协作式中止、认证拒绝、OOM 脱敏和响应正文不持久化。
- Docker Compose 使用受限标签形成项目归属；Docker 与 WSL 都输出版本化 PID、端口、项目和生命周期归属契约，不把引擎/虚拟机 PID 冒充为宿主机 PID。

### Storage and supply chain

- SQLite 历史库升级到 schema version 4：旧库迁移前创建一致性备份，迁移在单事务中执行并可回滚，较新数据库拒绝降级打开，损坏数据库原件隔离后创建新库；新增按服务指纹去重的本机成效确认记录。
- 增加 Windows x64、macOS arm64/x86_64、Linux x86_64/aarch64 与 Python 3.10–3.12 的运行/构建 wheel 哈希锁，以及 Linux CI 审计工具完整依赖锁；构建和安装入口强制 `--require-hashes` 与仅 wheel。
- GitHub Actions 增加六组合测试矩阵、Ruff、Bandit 中高风险、依赖漏洞审计、锁完整性和三平台原生 PyInstaller 冒烟验收。工作流已在本地完成语法与命令级检查，但在公开仓库运行前不能冒充 GitHub 托管执行证据。

### Boundary

- 增加不扩张控制权限的“本机成效”弹窗：用户可在服务详情确认“确属遗留 / 不是遗留 / 暂不确定”，并在精确输入 `EXPORT REPORT` 后下载仅含聚合计数、停止验证和预测误差的脱敏 JSON。报告不含 PID、路径、IP、命令、会话 ID、日志或模型响应，不自动上传，并明确不能证明外部采用。
- 0.8.3 不新增工作区、远程控制、自动停止、自动压测、凭据读取或模型下载权限；所有主动关停/矩阵 E2E 仅针对测试自建回环夹具。

## [0.8.2] - Unreleased

### Security and boundary hardening

- 内部旧实例控制客户端只接受无凭据、无查询串的显式回环 HTTP 地址，限制响应体为 1 MiB；控制服务器增加连接超时、`Transfer-Encoding` 拒绝及资源/权限隔离响应头。
- 停止前再次核验整个进程树的 PID、创建时间和受保护状态；部分终止失败会进入结构化验证结果，不再把已经发生的局部动作错误地报告为完全失败或完全成功。
- 项目清单和本地规则的 `protected` 只能增加保护，不能降低内置/既有保护；JSON 形式 `.vsg.yaml` 必须声明 `version: 1`。
- 受信节点拒绝未指定、多播、保留和公网地址；日志监控最多同时保持 32 个游标，并在进程身份不可验证时关闭监控。
- 快照去重并设置单文件 512 MiB、总计 1 GiB 哈希预算；已知凭据、环境、私钥和证书文件只记录清单而不复制，失败时清理不完整快照，回滚前再次验证目标未被链接重定向。

### Reliability, storage, and parser limits

- 服务基准使用有界流式读取，错误在持久化前脱敏；工作负载矩阵对线程启动、审计失败、取消与终态清理采用可恢复状态机，预览/任务数量有硬上限。
- GGUF 递归元数据、Safetensors 维度/规模、模型字符串和盘点历史均增加大小与深度边界；超大盘点按确定性层级降级，避免 SQLite 请求或页面被任意上游元数据撑大。
- 基准详情以有效、有界 JSON 保存，不再任意截断 JSON 文本；历史保留策略同时清理过期模型基准。
- 采集器关闭改为等待当前扫描/遥测/探测阶段结束，避免 Windows 上退出时与 SQLite 关闭竞态。

### Validation and release engineering

- 新增 0.8.2 边界/上线审查文档和 18 个专门加固测试；完整自动化回归为 147 项（146 通过、1 项按平台跳过）。
- Windows、macOS 与 Linux 构建链固定使用 `pip 26.2.1` 和仅二进制依赖安装；本地依赖审计未发现已知漏洞。
- 增加 Ruff 规则基线，并将 Bandit 中高风险作为本地发布检查；跨平台哈希锁定与原生实机矩阵仍是公开稳定发布前的剩余工作。

## [0.8.1] - Unreleased

### Service relationships and stop assessment

- 增加项目、Agent/会话、服务、进程、监听端点与当前本机 TCP 客户端之间的关系模型；通配监听只把回环或本机网卡地址认作本机依赖，不会因公网主机使用相同端口而误关联。
- 停止入口先显示阻断项、当前客户端/端点影响、自动重启与生命周期管理器线索，以及人工恢复步骤。Agent、IDE、Windows 服务、Docker、WSL、受保护对象和未进入安全白名单的进程仍保持只读。
- 用户输入 `STOP <PID>` 后，VSG 在有限观察窗口内复核原 PID/子进程、原监听端口和替代 PID；区分已停止、重新拉起、停止不完整和证据不完整。重新拉起的进程不会被自动二次结束。

### Fixed workload matrix and capacity calibration

- 增加需完整预览并输入 `BENCHMARK PLAN <端口>` 的固定三步矩阵：单请求 5 次、双并发 10 次、四并发 20 次。每一步重查 RAM、VRAM、GPU/系统温度与磁盘余量；单机只允许一个矩阵，不自动扩档、不故意制造 OOM。
- 支持协作式中止剩余波次；已经发出的本机请求可以正常结束。结果记录样本数、P50/P95 TTFT、端到端延迟、近似 token 间隔、聚合吞吐和采样期资源峰值；P95 少于 20 个成功样本时明确标记统计不足。
- 可选把运行时模型映射到离线容量目录。只有硬件指纹、模型、量化、并发、上下文和输出长度全部匹配的服务矩阵才作为同负载校准；单请求样本只能校准基础生成速度。
- 容量候选与汇总面板显示理论预测、实测/校准结果、带符号误差和绝对误差；矩阵数值与预测误差只保存在本机，不保存合成提示或生成正文。

### Console and storage

- 中英文控制台增加服务关系摘要、关停评估/验证、矩阵预览/进度/中止与容量校准误差界面。
- SQLite 增加停止验证记录和服务矩阵校准字段；历史清除可单独删除停止验证，仍不会触碰用户进程、项目、模型、日志或配置原文件。

## [0.8.0] - Unreleased

### Correctable attribution and stale-service evidence

- 增加按服务指纹、端口、运行时、命令、可执行文件或目录匹配的本地归属规则；服务行可直接纠正项目、服务名与 Agent，删除规则要求 `DELETE RULE <编号>`。
- 增加受限的项目 `.vsg.yaml`：可声明项目名、预期/保护服务和 Agent 线索；不执行 YAML，不支持标签、锚点、别名或变量替换，无效清单不会中断扫描。
- 遗留判断增加服务启动、退出、疑似重启、暴露变化、归属变化、模型联网变化、资源阈值和脱敏日志事件的关联时间线，避免只凭运行时长下结论。

### Network topology and per-service resources

- 增加主机—服务—监听—当前远端连接拓扑，单独标识 Docker、WSL、反向代理、所有网卡监听和远端地址作用域。
- 远端 IP 只存在于当前内存快照；历史仅保存不可逆端点摘要、作用域与端口，不抓包、不读取 URL/正文。
- 增加逐服务 CPU、RSS、RAM 比例与 NVIDIA 进程显存汇总；无法可靠关联的 AMD、macOS、Docker/WSL 项保持未知并披露原因。

### Bounded local model inventory

- 增加需精确输入 `SCAN MODELS` 的显式目录盘点；拒绝根目录、用户主目录、符号链接和 Windows 目录联接，限制 5000 文件、6 层和 120 秒。
- 有界解析 GGUF、Safetensors、Ollama manifest、Modelfile 与常见配置，识别格式、量化、架构、上下文、Dense/MoE、专家、分片和候选重复文件；不下载、删除、移动或修改模型。
- 盘点历史只保存相对路径、根目录名称/摘要和快速指纹，不保存绝对扫描根；资产可带入容量规划和完整推理引擎兼容矩阵。

### Operations workspace and data lifecycle

- 新增中英文“事件与模型资产”工作区，汇总模型盘点、网络拓扑、归属规则、关联事件、日志筛选与按类别历史清除。
- 历史清除要求 `CLEAR HISTORY`，只删除选中的 VSG 数据库记录，不触碰模型、项目、日志或配置原文件。
- 推理引擎建议由仅展示 Top 3 改为完整候选矩阵，返回阻断条件、实测运行时/驱动信息和中英文解除阻断步骤；仍不执行安装。

## [0.6.0] - Unreleased

### Optimization advisor and inference-engine selection

- 增加“优化与引擎建议”工作区，把检测、诊断、建议、基准、监控和回滚串成可回验闭环；建议不会自动安装引擎、修改驱动或改写模型配置。
- 根据操作系统、GPU 厂商、权重格式、并发、上下文和目标倾向，对 llama.cpp、Ollama、MLX-LM、vLLM、SGLang、TensorRT-LLM 与 TabbyAPI/ExLlamaV2 做兼容性筛选并给出 Top 3；Windows 的 WSL/Docker 路径明确标为预览并要求本机实测。
- 增加硬件优化规则：只用本机实测 RAM/VRAM/磁盘/温度、用户负载目标、运行时健康和脱敏日志事件触发建议；每条结果给出证据、动作、代价、置信度和回验方式，缺失传感器不估算。
- 服务基准只在模型名、并发、上下文和输出长度完全一致时形成横向可比组，避免把不同负载的 TPS/TTFT 直接排名。

### Explicit redacted log monitoring

- 增加需要 `WATCH <PID>` 明示确认的持续日志监控。只允许已识别的宿主机模型推理服务和用户明确选择的普通日志文件；拒绝 `.env`、凭据、私钥、符号链接和超过 100 MiB 的日志。
- 从日志尾部增量读取，按 OOM、CUDA/ROCm/Metal/Vulkan、加载、超时、认证、上下文、工具模板、崩溃和 CPU 回退分类；原始日志不入库，接口只返回脱敏短事件和文件名。
- 游标同时记录跨平台文件身份；日志被轮转、原子替换或截断时从新文件开头重新解析，避免沿用旧字节偏移截断首行。
- 监控游标绑定服务指纹、PID 与启动时间；服务退出或 PID 身份变化时记录结构化事件并自动停止该游标，不跟随未经重新确认的新进程。
- 脱敏事件默认保留 7 天，可在设置中调整为 1–90 天。

## [0.5.0] - 2026-08-13 local milestone (not published)

### Cross-platform and bilingual console

- 控制台增加中文/英文完整切换：首次按浏览器首选语言，用户切换结果仅持久化到本地浏览器存储；静态界面、动态采集结果、证据与错误提示使用同一翻译层。
- Linux 从实验性源码兼容升级为受支持目标，首批以 Ubuntu 22.04+ 图形桌面为验收基线；增加无 `sudo` 的可选 freedesktop `.desktop` 启动器、Linux 原生构建/验收脚本和 Windows 侧源码构建包。
- Linux 端口/进程使用 psutil/procfs 可见范围，Docker 继续独立分组；防火墙按当前普通用户可见性读取 ufw、firewalld 或 nftables。

### Generic GPU discovery

- NVIDIA/AMD 识别不再依赖静态型号穷举。Windows 使用 `Win32_VideoController` 的 PNP 厂商/设备 ID，macOS 使用 `system_profiler`，Linux 使用 PCI sysfs 的 class/vendor/device；未知新型号仍能识别厂商并展示证据。
- NVIDIA 继续以 `nvidia-smi` 的驱动可见 GPU 和实测显存/利用率/温度/风扇/功耗为准；AMD 在可用时读取 `amd-smi metric --json`，并容忍版本间 JSON 嵌套差异。
- 静态型号表只作为已知设备的容量/带宽估算补充；厂商接口或可靠显存证据缺失时保持未知，不用 `AdapterRAM`、TDP 或型号猜测冒充实测。

## [0.4.0] - Unreleased

### AI 运行体检

- 增加 CPU、RAM、交换空间、项目/系统卷余量、网络速率、GPU/VRAM、温度、风扇、功耗和启动期电费累计；传感器不可用时返回未知，不用估算值冒充实测。
- NVIDIA 使用显式 `nvidia-smi` 字段；Windows 非 NVIDIA GPU 使用 WDDM 适配器/引擎计数器，并披露 LUID 到营销型号的启发式映射。
- 增加 Ollama、llama.cpp、vLLM、SGLang、TGI、LM Studio、MLX-LM、KoboldCpp、KTransformers、ComfyUI、TensorRT-LLM、Text Generation WebUI/ExLlama 与 TabbyAPI 的本机只读运行时探测。
- 展示模型加载、量化、运行后端与加速路径、工具/视觉/语音能力线索、上下文、并发、KV、被动 TPS/TTFT、远端连接和认证姿态；拿不到的字段保持未知。
- 增加 `BENCHMARK <端口>` 显式确认的合成短基准，限制并发/上下文/输出，不读取 API Key、不持久化生成正文、不故意制造 OOM。
- 增加 Windows/macOS 防火墙摘要、非回环模型端口规则匹配、反向代理进程识别和模型进程远端 IP/端口作用域；不抓包、不读 URL/正文、不扫描 LAN、不做外网回连。
- 增加 `INSPECT <PID>` 日志尾部与配置脱敏检查，拒绝 `.env`、私钥和已知凭据文件；默认不搜索或打开用户日志/配置。
- 增加 `SNAPSHOT` 文件清单、小配置本地副本与 `RESTORE <文件名>` 显式回滚；大模型不自动复制，超大文件不自动做完整哈希。
- 增加手工受信私网节点健康检查；每次连接前重新校验地址，禁止凭据、查询串、公开 IP 与自动发现。
- 综合结论拆成机器、性能、安全、稳定和容量五域；未知域不参与分数，界面以 `*` 明确标记证据不完整。

### Fixed

- Windows 控制端口启用独占绑定，端口被旧实例或其他程序占用时顺延，不再通过地址复用抢占监听。
- 旧 `runtime.json` 只有实例 ID 与版本均匹配当前健康响应时才复用；陈旧/外来服务不会被当作当前实例打开。
- 历史重启计数按指纹组聚合；多个完全相同的并发 Electron/Agent 进程不再被误判为持续重启。
- 首次 CPU 读数改为短采样，避免把 `psutil` 首次调用的 0.0 哨兵值当作真实空闲。

## [0.3.0] - Unreleased

### Model capacity planning

- 增加 Windows/macOS 本机 CPU、内存、磁盘、GPU/统一内存和本地推理运行时识别；Windows AMD/Intel GPU 明确标记为实验性路径。
- 增加带日期、离线且非穷尽的开放权重模型目录，首批覆盖 Qwen3.5、gpt-oss、Gemma 4、Mistral Small 4 与 DeepSeek-V4-Flash。
- 增加物理装载上限、实际可用上限和目标 SLA 上限，显式拆分权重、KV 缓存、工作区、当前空闲预算和系统保留。
- Dense 与 MoE 分开计算：权重按总参数，吞吐按激活参数近似；预测返回范围和置信度，不把估算伪装成基准。
- 增加用户数、峰值并发、上下文、输入/输出、每用户速度、TTFT、运行时偏好和 KV 位宽的交互式中文规划界面。
- 服务监控新增 Ollama、llama.cpp/llamafile、vLLM、SGLang、MLX-LM、LM Studio、KTransformers、KoboldCpp 与 Hugging Face TGI 识别和“模型推理”过滤。
- 生成只绑定 `127.0.0.1` 的 llama.cpp、Ollama、MLX-LM、vLLM/SGLang 命令模板，但不自动执行、不下载模型。
- 增加需要 `BENCHMARK` 明示确认的本地 `llama-bench` 短基准；数据库只保存模型文件名、大小和数值，不保存绝对路径。

### Existing service monitoring

- 保留 0.2.1 的本机服务、项目/Agent 会话归属、Docker/WSL 分组和疑似遗留检测能力。

## [0.2.1] - 2026-08-11

### Security and privacy

- 扩展命令脱敏，覆盖凭据 URI、敏感查询参数和常见供应商 Token。
- 在 POSIX 上对数据目录和本地文件应用当前用户专属权限。
- 旧 `runtime.json` 只有在健康响应与当前 VSG 版本一致时才会复用。

### Fixed

- 修复 WSL `netstat` 回退输出、IPv6 地址和 PID/进程名解析。
- 为 Hermes/OpenCode SQLite 会话元数据增加 48 小时时效限制；无项目目录证据的 Hermes 会话不再归属到项目。
- 修复设置等弹窗的关闭按钮可能提交表单的问题。
- Windows 默认项目目录在保留 `E:\vibe coding` 优先级的同时支持常见项目根目录。

### Supply chain and project readiness

- 构建时收集实际安装版本的完整第三方许可证并生成 SPDX 2.3 SBOM。
- 增加跨平台 CI、依赖审计、Dependabot、公开树敏感文件审计和贡献文档。
- 增加 Agent 支持矩阵、架构、隐私、支持和公开发布检查清单。

## [0.2.0] - 2026-08-11

- 增加 macOS 端口采集与双架构原生构建链。
- 增加 Hermes、OpenCode、Aider、Gemini CLI 与 Goose 识别。
- Agent 本体无端口时也可只读展示。

## [0.1.0] - 2026-08-10

- Windows 本机服务发现、项目/Agent 归属、遗留评分和中文回环控制台原型。
