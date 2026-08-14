(() => {
  "use strict";

  const STORAGE_KEY = "vsg.locale";
  const HAN = /[\u3400-\u9fff]/;
  const pairs = [
    ["预览和导出均不包含 PID、路径、IP、命令、会话 ID、日志或模型响应。不会自动上传；导出文件仍需人工复核后再对外分享。", "Preview and export exclude PIDs, paths, IP addresses, commands, session IDs, logs, and model responses. Nothing is uploaded automatically; manually review the exported file before sharing it."],
    ["该报告是单机自报证据，不能证明独立用户数、公开仓库采用量、下载量或外部项目使用情况。", "This is self-reported evidence from one machine. It does not prove independent users, public-repository adoption, downloads, or external-project usage."],
    ["请按当前实际情况确认；“暂不确定”不会被计入明确结论。该记录只留在本机，并用于评估判断规则是否命中。", "Confirm the current real-world outcome. Not sure is excluded from decisive results. The record stays local and is used to evaluate whether the assessment rule matched."],
    ["尚未记录人工结论。每个服务指纹只保留一条可更新结果。", "No human outcome has been recorded. Each service fingerprint keeps one updateable result."],
    ["如需下载脱敏 JSON，请输入确认短语 EXPORT REPORT", "To download redacted JSON, enter the confirmation phrase EXPORT REPORT"],
    ["单机、自报、受历史保留期限制；不代表独立用户、公开采用、下载量或社区影响力。", "Single-instance, self-reported, and retention-bounded. It does not represent independent users, public adoption, downloads, or community impact."],
    ["脱敏成效报告已下载；对外分享前仍需人工复核", "Redacted impact report downloaded; manually review it before external sharing"],
    ["本机判断结果已更新；重复确认不会增加样本数", "Local outcome updated; repeated confirmation does not increase the sample count"],
    ["正在汇总保留期内的本机证据…", "Aggregating retained local evidence…"],
    ["只生成本机聚合证据", "Local aggregate evidence only"],
    ["遗留判断结果确认", "Confirm stale-service outcome"],
    ["本机成效报告", "Local Impact Report"],
    ["导出脱敏 JSON", "Export redacted JSON"],
    ["成效确认", "Impact confirmations"],
    ["确属遗留", "Confirmed stale"],
    ["不是遗留", "Not stale"],
    ["暂不确定", "Not sure"],
    ["人工结果", "Human outcomes"],
    ["遗留候选", "Stale candidates"],
    ["TPS 预测误差", "TPS prediction error"],
    ["当前服务", "Current services"],
    ["Agent 归属", "Agent-attributed"],
    ["停止成功", "Stopped"],
    ["检测重启", "Relaunch detected"],
    ["成功记录", "Successful records"],
    ["服务数", "Services"],
    ["实测样本", "Measured samples"],
    ["暂无实测", "No measurement"],
    ["不确定", "Not sure"],
    ["服务快照已变化，请刷新后重试", "The service snapshot changed; refresh and try again"],
    ["重复确认不会增加样本数", "Repeated confirmation does not increase the sample count"],
    ["当前结论", "Current outcome"],
    ["尚未记录人工结论", "No human outcome recorded"],
    ["仅统计绝对百分比误差", "Absolute percentage error only"],
    ["本机成效", "Local Impact"],
    ["证据边界", "Evidence boundary"],
    ["生成于", "Generated"],
    ["优先使用同模型、量化、硬件、并发和上下文的服务矩阵；否则只用单并发样本校准基础生成速度", "Prefer a service matrix with the same model, quantization, hardware, concurrency, and context; otherwise calibrate only the base generation speed with a single-request sample"],
    ["计划固定为单请求、双并发和四并发三步。开始前完整预览；每步重查 RAM、VRAM、温度和磁盘护栏。中止采用协作式取消，已经发出的本机请求可能正常结束。", "The plan is fixed to three stages: single request, concurrency 2, and concurrency 4. It is fully previewed before start, and RAM, VRAM, temperature, and disk guards are rechecked before every stage. Cancellation is cooperative, so in-flight local requests may finish normally."],
    ["系统将在执行前再次校验 PID、启动时间、保护名单和目标来源。无法撤销。", "The PID, start time, protection list, and target source are revalidated immediately before execution. This cannot be undone."],
    ["固定 3 步、共 35 个合成请求；不会自动扩档，不会故意试探 OOM。预览 5 分钟内有效。", "Fixed at three stages and 35 synthetic requests. It never expands automatically or deliberately probes for OOM. The preview is valid for five minutes."],
    ["协作式中止不会强杀已经发出的请求；资源采样可能遗漏短于刷新间隔的瞬时峰值。", "Cooperative cancellation does not force-kill in-flight requests. Resource sampling may miss spikes shorter than the refresh interval."],
    ["可在模型服务页运行固定负载矩阵；系统会按同硬件、模型、量化、并发、上下文和输出长度反向校准。", "Run the fixed workload matrix from the model-services view. Calibration matches hardware, model, quantization, concurrency, context, and output length."],
    ["当前权限无法读取本机连接依赖，关停评估将明确标记证据不足。", "Current permissions cannot read local connection dependencies; the stop assessment will explicitly mark insufficient evidence."],
    ["当前没有检测到服务之间的本机 TCP 依赖。", "No local TCP dependency between services is currently detected."],
    ["正在关联项目、Agent、客户端与监听服务…", "Linking projects, agents, clients, and listening services…"],
    ["服务关系与关停判断", "Service relationships and stop decisions"],
    ["正在生成关停评估…", "Generating stop assessment…"],
    ["查看关停影响与恢复证据", "Review stop impact and recovery evidence"],
    ["关停评估失败", "Stop assessment failed"],
    ["未执行任何停止操作。", "No stop action was performed."],
    ["该服务由独立生命周期管理器托管，当前版本仅提供只读评估", "This service is managed by an independent lifecycle manager; this version provides read-only assessment only"],
    ["该服务或其归属规则已标记为受保护", "This service or its attribution rule is marked as protected"],
    ["该进程未进入普通宿主机开发/模型运行时安全停止白名单", "This process is not in the safe-stop allowlist for ordinary host development or model runtimes"],
    ["检测到自动重启策略，停止后服务可能被重新拉起", "An automatic restart policy was detected; the service may be relaunched after stop"],
    ["先确认项目目录和配置快照仍可用", "First confirm that the project directory and configuration snapshot remain available"],
    ["在原项目终端中复核运行时与脱敏命令后手动重启", "Review the runtime and redacted command in the original project terminal, then restart manually"],
    ["重启后确认原端口、绑定地址、认证和模型加载状态", "After restart, verify the original port, bind address, authentication, and model load state"],
    ["未识别额外阻断；仍需人工确认当前请求是否可中断。", "No additional blocker identified; manually confirm whether current requests may be interrupted."],
    ["停止前需复核", "Review before stopping"],
    ["当前只读阻断", "Read-only block"],
    ["可确认停止", "Eligible for confirmed stop"],
    ["阻断与警告", "Blockers and warnings"],
    ["当前影响", "Current impact"],
    ["重启与恢复", "Restart and recovery"],
    ["重新拉起风险", "Relaunch risk"],
    ["未检测到本机 TCP 客户端依赖。", "No local TCP client dependency detected."],
    ["回到项目目录复核原运行方式后手工恢复。", "Return to the project directory, verify the original launch method, and recover manually."],
    ["已停止并验证端口关闭", "Stopped and ports verified closed"],
    ["检测到替代 PID 或端口重新监听", "Replacement PID or relistening port detected"],
    ["仍有原进程树成员存活", "Original process-tree members are still alive"],
    ["停止已请求，但端口证据不完整", "Stop requested, but port evidence is incomplete"],
    ["不会自动结束重新拉起的进程；更晚的重启由生命周期时间线继续记录。", "A relaunched process is never stopped automatically; later restarts continue to be recorded in the lifecycle timeline."],
    ["分级工作负载矩阵", "Tiered workload matrix"],
    ["容量目录映射（可选）", "Capacity catalog mapping (optional)"],
    ["不映射，仅记录运行时性能", "No mapping; record runtime performance only"],
    ["量化版本", "Quantization"],
    ["生成或刷新预览", "Generate or refresh preview"],
    ["请选择映射后生成预览。", "Generate a preview; catalog mapping is optional."],
    ["中止剩余负载", "Cancel remaining workload"],
    ["确认运行矩阵", "Confirm and run matrix"],
    ["输入确认短语", "Enter confirmation phrase"],
    ["正在读取目录与当前任务…", "Reading the catalog and current job…"],
    ["正在生成固定计划…", "Generating fixed plan…"],
    ["未映射容量目录，不计算预测误差", "Capacity catalog is not mapped; prediction error is not calculated"],
    ["护栏通过", "Guards passed"],
    ["护栏阻断", "Guard blocked"],
    ["当前没有可用资源读数", "No current resource readings are available"],
    ["单请求基线", "Single-request baseline"],
    ["双并发交互", "Two-request interactive"],
    ["四并发持续", "Four-request sustained"],
    ["排队中", "Queued"],
    ["运行中", "Running"],
    ["正在中止", "Cancelling"],
    ["全部完成", "Completed"],
    ["已中止剩余负载", "Remaining workload cancelled"],
    ["资源护栏停止", "Stopped by resource guard"],
    ["服务身份已变化", "Service identity changed"],
    ["尚无完成步骤。", "No completed stage yet."],
    ["工作负载矩阵完成，容量预测已获得校准样本", "Workload matrix completed; capacity prediction received a calibration sample"],
    ["已请求中止；正在等待当前请求波次安全返回", "Cancellation requested; waiting for the current request wave to return safely"],
    ["固定工作负载矩阵已启动；可随时中止剩余负载", "The fixed workload matrix has started; remaining workload can be cancelled at any time"],
    ["请先生成固定计划预览", "Generate the fixed plan preview first"],
    ["预测误差与校准", "Prediction error and calibration"],
    ["尚无匹配实测样本。", "No matching measured sample yet."],
    ["尚无当前候选的匹配实测", "No matching measurement for the current candidate"],
    ["已按同负载实测校准", "Calibrated with the same measured workload"],
    ["已按单请求基线校准", "Calibrated with a single-request baseline"],
    ["当前可用样本", "Available samples"],
    ["已校准候选", "Calibrated candidates"],
    ["同负载实测校准", "Same-workload measured calibration"],
    ["单请求基线校准", "Single-request baseline calibration"],
    ["尚无匹配实测校准", "No matching measured calibration"],
    ["服务工作负载矩阵", "Service workload matrix"],
    ["服务矩阵", "Service matrix"],
    ["理论预测", "Model prediction"],
    ["实测/校准", "Measured/calibrated"],
    ["绝对误差", "absolute error"],
    ["预测误差", "prediction error"],
    ["本机依赖", "Local dependencies"],
    ["可直接确认", "Directly confirmable"],
    ["需先复核", "Review required"],
    ["只读阻断", "Read-only blocked"],
    ["停止验证", "Stop verification"],
    ["生命周期归属", "Lifecycle owner"],
    ["未检测到", "Not detected"],
    ["未识别", "Unidentified"],
    ["客户端", "Clients"],
    ["端点", "Endpoints"],
    ["策略", "Policy"],
    ["管理器", "Manager"],
    ["项目", "Project"],
    ["事件与模型资产", "Events & Model Assets"],
    ["按服务 CPU、RAM 与进程级显存", "Per-service CPU, RAM, and process VRAM"],
    ["进程级显存", "Process VRAM"],
    ["证据状态", "Evidence status"],
    ["Docker/WSL PID 命名空间不同；无法可靠归属时保持未知。", "Docker and WSL use different PID namespaces; attribution remains unknown when it cannot be established reliably."],
    ["全部候选", "All candidates"],
    ["同时展示兼容、预览与被阻断的引擎", "Show compatible, preview, and blocked engines together"],
    ["版本、驱动或 Compute Capability 未读到时明确标记未知。", "Version, driver, and compute capability remain explicitly unknown when unavailable."],
    ["格式 / 平台 / 加速器", "Format / Platform / Accelerator"],
    ["本机检测", "Local detection"],
    ["阻断与解除步骤", "Blockers and remediation"],
    ["严重度", "Severity"],
    ["事件代码", "Event code"],
    ["筛选事件", "Filter events"],
    ["从“发生了什么”回到“哪个项目、哪个 Agent、哪个模型”", "Trace what happened back to the project, agent, and model"],
    ["时间线、网络拓扑、归属纠正和模型盘点全部留在本机。远端 IP 仅显示当前连接，不写入历史。", "Timeline, network topology, attribution corrections, and model inventory stay local. Remote IPs appear only for live connections and are never stored in history."],
    ["刷新事件与资产", "Refresh events and assets"],
    ["正在读取", "Reading"],
    ["尚未显式扫描", "No explicit scan yet"],
    ["本机规则与项目 .vsg.yaml", "Local rules and project .vsg.yaml"],
    ["盘点明确选择的模型目录", "Inventory an explicitly selected model directory"],
    ["只扫描你输入的目录；跳过符号链接，最多 5000 个文件、6 层。不会下载、删除、上传或修改权重。", "Scans only the directory you enter, skips symbolic links, and stops at 5,000 files or six levels. It never downloads, deletes, uploads, or modifies weights."],
    ["模型目录绝对路径", "Absolute model-directory path"],
    ["输入确认短语 SCAN MODELS", "Enter confirmation phrase SCAN MODELS"],
    ["开始本地盘点", "Start local inventory"],
    ["最近一次盘点", "Latest inventory"],
    ["尚未扫描模型目录。", "No model directory has been scanned."],
    ["已盘点模型", "Inventoried models"],
    ["格式、量化、Dense / MoE、权重装载提示与引擎入口", "Format, quantization, Dense / MoE, weight-fit signal, and engine handoff"],
    ["“权重可装下”不含 KV 缓存、并发和性能承诺。", "Weight fit excludes KV cache, concurrency, and any performance promise."],
    ["模型 / 位置", "Model / Location"],
    ["格式 / 量化", "Format / Quantization"],
    ["架构 / 类型", "Architecture / Type"],
    ["权重", "Weights"],
    ["当前装载提示", "Current fit signal"],
    ["下一步", "Next step"],
    ["暂无模型资产", "No model assets"],
    ["选择具体模型目录并输入 SCAN MODELS。", "Choose a specific model directory and enter SCAN MODELS."],
    ["监听与当前远端连接", "Listeners and current remote connections"],
    ["用户纠正规则", "User correction rules"],
    ["关联时间线", "Correlated timeline"],
    ["服务生命周期、暴露变化、网络连接、资源与日志事件", "Service lifecycle, exposure changes, connections, resources, and log events"],
    ["时间范围", "Time range"],
    ["小时", "hours"],
    ["天", "days"],
    ["类别", "Category"],
    ["筛选时间线", "Filter timeline"],
    ["本机历史数据管理", "Local history management"],
    ["选择清除范围", "Select data to clear"],
    ["时间线", "Timeline"],
    ["遥测样本", "Telemetry samples"],
    ["模型盘点历史", "Model-inventory history"],
    ["模型基准", "Model benchmarks"],
    ["服务基准", "Service benchmarks"],
    ["服务观察历史", "Service observation history"],
    ["输入确认短语 CLEAR HISTORY", "Enter confirmation phrase CLEAR HISTORY"],
    ["清除所选本机历史", "Clear selected local history"],
    ["纠正服务归属", "Correct service attribution"],
    ["将为当前服务指纹创建一条本机规则。", "Creates a local rule for the current service fingerprint."],
    ["显示名称", "Display name"],
    ["项目名称", "Project name"],
    ["项目绝对路径", "Absolute project path"],
    ["必须位于设置中的项目根目录", "Must be inside a configured project root"],
    ["这是预期服务", "This is an expected service"],
    ["保护，禁止从控制台停止", "Protect from console stop actions"],
    ["保存本机纠正规则", "Save local correction rule"],
    ["确认本机操作", "Confirm local action"],
    ["确认执行", "Confirm action"],
    ["读取本机硬件、运行时、脱敏日志事件和基准历史；生成建议但不自动安装引擎、不改驱动、不改模型配置。", "Reads local hardware, runtimes, redacted log events, and benchmark history. It recommends but never installs engines or changes drivers and model configuration automatically."],
    ["只读取你明确选择的本机日志；拒绝 .env、密钥和凭据文件。原始日志不入库，界面只展示脱敏短事件。", "Reads only the local log you explicitly select. .env, key, and credential files are rejected. Raw logs are not stored; the UI shows only short redacted events."],
    ["启用监控后，VSG 从日志尾部开始解析并在本机保存脱敏事件。", "After monitoring is enabled, VSG parses from the log tail and stores redacted events locally."],
    ["从故障证据到引擎选型，再用同负载实测回验", "From failure evidence to engine selection, validated with an identical workload"],
    ["每条建议都带证据、动作、代价与回验方式", "Every recommendation includes evidence, action, trade-off, and validation"],
    ["只比较同模型、同并发、同上下文与同输出长度", "Compare only the same model, concurrency, context, and output length"],
    ["持续观察 OOM、加速器错误、加载、超时与崩溃", "Continuously watch OOMs, accelerator errors, loading, timeouts, and crashes"],
    ["Windows 可接受 WSL2 路径", "Allow a WSL2 path on Windows"],
    ["可接受 Docker 路径", "Allow a Docker path"],
    ["查看缺失证据与方法边界", "View missing evidence and method boundaries"],
    ["优化与引擎建议", "Optimization & Engine Advice"],
    ["本机优化闭环", "Local optimization loop"],
    ["重新生成建议", "Regenerate advice"],
    ["优化闭环", "Optimization loop"],
    ["检测", "Detect"],
    ["诊断", "Diagnose"],
    ["建议", "Recommend"],
    ["基准", "Benchmark"],
    ["监控", "Monitor"],
    ["回滚", "Rollback"],
    ["引擎选择条件", "Engine selection inputs"],
    ["模型权重格式", "Model weight format"],
    ["自动 / 尚未确定", "Auto / undecided"],
    ["Ollama 包", "Ollama package"],
    ["首要目标", "Primary goal"],
    ["综合平衡", "Balanced"],
    ["易用性优先", "Ease of use"],
    ["低延迟优先", "Low latency"],
    ["内存效率优先", "Memory efficiency"],
    ["功耗优先", "Power efficiency"],
    ["目标并发", "Target concurrency"],
    ["目标上下文 tokens", "Target context tokens"],
    ["需要的能力", "Required capabilities"],
    ["工具调用", "Tool calling"],
    ["视觉", "Vision"],
    ["语音", "Audio"],
    ["结构化输出", "Structured output"],
    ["计算兼容矩阵", "Calculate compatibility matrix"],
    ["引擎", "Engine"],
    ["状态 / 分数", "Status / Score"],
    ["兼容性与工作负载拟合", "Compatibility and workload fit"],
    ["正在读取本机环境…", "Reading the local environment…"],
    ["硬件优化", "Hardware optimization"],
    ["正在计算", "Calculating"],
    ["可比基准", "Comparable benchmarks"],
    ["暂无可比样本。", "No comparable cohort yet."],
    ["脱敏日志事件", "Redacted log events"],
    ["模型服务", "Model service"],
    ["日志绝对路径", "Absolute log path"],
    ["开始监控", "Start monitoring"],
    ["监控游标", "Watch cursors"],
    ["时间 / 严重度", "Time / Severity"],
    ["运行时 / 类别", "Runtime / Category"],
    ["事件", "Event"],
    ["次数", "Occurrences"],
    ["暂无结构化日志事件", "No structured log events"],
    ["脱敏日志事件保留天数", "Redacted log-event retention days"],
    ["追踪本机监听端口、项目与 Agent 会话，识别疑似遗留开发服务", "Attribute local listening ports to projects and agent sessions, and detect potentially stale development services"],
    ["同时给出物理装载上限、实际可用上限与目标 SLA 上限；Dense 与 MoE 分开计算，结果是范围预测而不是性能承诺。", "Calculate physical, usable, and target-SLA ceilings separately. Dense and MoE models use different equations; results are ranges, not performance guarantees."],
    ["默认只读、被动采集。主动推理短基准、日志/配置检查、快照与回滚均需你逐次选择目标并输入确认短语。", "Collection is read-only and passive by default. Active inference benchmarks, log/config inspection, snapshots, and restores always require a selected target and confirmation phrase."],
    ["控制台仅监听 127.0.0.1。命令中的 Key、Token、密码、Cookie 等常见敏感参数在进入界面前即被脱敏。", "The console listens on 127.0.0.1 only. Common sensitive command arguments such as keys, tokens, passwords, and cookies are redacted before reaching the UI."],
    ["该操作会短时占用 CPU/GPU 和内存。VSG 不会下载模型，也不会把模型路径写入数据库；开始后最长 300 秒超时。", "This action briefly uses CPU/GPU and memory. VSG never downloads a model or stores its path in the database; the hard timeout is 300 seconds."],
    ["会向所选本机模型发送不含用户数据的合成提示词，短时占用 CPU/GPU/内存。首版硬上限：并发 4、上下文 4096、输出 64；系统内存超过 85% 时拒绝运行，不会主动试探到 OOM。", "Sends synthetic prompts containing no user data to the selected local model and briefly uses CPU/GPU/memory. Hard limits: concurrency 4, context 4096, output 64. It refuses to run above 85% system-memory use and never probes until OOM."],
    ["仅在你明确选择文件后读取；拒绝 .env、私钥和凭据文件。返回内容会脱敏，不保存原始文件、绝对路径或密钥值。", "Reads only a file you explicitly select; .env, private-key, and credential files are rejected. Returned content is redacted and original data, absolute paths, and secret values are not stored."],
    ["系统将在执行前再次校验 PID、启动时间、保护名单和目标来源。无法撤销。", "Before execution, the PID, start time, protection list, and target source are verified again. This cannot be undone."],
    ["小于 2 MiB 的 JSON/YAML/TOML/INI/CONF/Modelfile 会保存本地私有副本并支持显式回滚；模型权重只记录名称、大小、时间与可选哈希。", "JSON/YAML/TOML/INI/CONF/Modelfile files under 2 MiB receive a private local copy with explicit restore support. Model weights record only name, size, time, and an optional hash."],
    ["不自动复制几十 GB 权重；超过 512 MiB 默认跳过完整 SHA-256，避免长时间占满磁盘 I/O。", "Large weight files are never copied automatically. Full SHA-256 is skipped above 512 MiB by default to avoid prolonged disk I/O."],
    ["被动指标来自运行时只读 API/metrics；没有指标时不推断 TPS 或 TTFT。", "Passive metrics come from read-only runtime APIs/metrics. TPS and TTFT are not inferred when evidence is absent."],
    ["“不可用”表示系统/厂商未向当前非提权进程暴露传感器，不以估算代替实测。", "Unavailable means the OS or vendor does not expose that sensor to the current unprivileged process; estimates never replace measurements."],
    ["低置信度结果必须实测；“可装载”不等于“跑得动”。", "Low-confidence results require local measurement. Fits in memory does not mean it performs acceptably."],
    ["离线估算；不扫描模型文件，不读取 Key，不自动下载，不执行推荐命令。", "Offline estimate: no model-file scan, no key access, no automatic download, and no execution of suggested commands."],
    ["总用户数影响排队波次；峰值并发才直接放大 KV 缓存和吞吐压力。", "Total users affect queue waves; peak concurrency directly increases KV-cache and throughput pressure."],
    ["只说明权重、KV 与工作区理论上能否装下，可能很慢或需要混合卸载。", "Indicates only whether weights, KV cache, and workspace can theoretically fit; it may be slow or require hybrid offload."],
    ["当前可装入、至少 Q3、最低 2 tokens/s/用户且 TTFT 不超过 30 秒；不等于满足你的业务 SLA。", "Fits now, uses at least Q3, reaches at least 2 tokens/s/user and TTFT no more than 30 seconds; this does not imply your business SLA is met."],
    ["Q4 以上，并同时满足当前空闲内存、并发、速度与首字延迟目标。", "Q4 or better while satisfying current free-memory, concurrency, speed, and first-token-latency targets."],
    ["可选操作。只接受本地 GGUF 绝对路径，必须输入确认短语；不联网、不下载、不保存绝对路径。", "Optional. Accepts only an absolute path to a local GGUF and requires a confirmation phrase; no network, downloads, or persisted absolute path."],
    ["覆盖前会在该快照目录再保存一份当前目标文件；VSG 不自动重启相关服务。", "Before overwrite, the current target file is copied into the snapshot directory. VSG does not restart related services automatically."],
    ["手工受信节点（每行 http://私网主机:端口；最多 8 项）", "Trusted nodes (one http://private-host:port per line; maximum 8)"],
    ["项目根目录（每行一个绝对路径）", "Project roots (one absolute path per line)"],
    ["文件绝对路径（每行一个；最多 100 项）", "Absolute file paths (one per line; maximum 100)"],
    ["电价（每 kWh，仅用于 GPU 实测功耗积分）", "Electricity price per kWh (measured GPU power only)"],
    ["模型服务公网对端", "Public model-service peers"],
    ["模型服务联网", "Model-service network activity"],
    ["模型运行时只读证据", "Read-only model-runtime evidence"],
    ["监听、认证、防火墙与反向代理", "Listeners, authentication, firewall, and reverse proxy"],
    ["加载、性能、并发、上下文与暴露面", "Loading, performance, concurrency, context, and exposure"],
    ["机器健康、模型性能、服务安全，一页闭环", "Machine health, model performance, and service security in one view"],
    ["本机硬件状态与运行成本", "Local hardware status and operating cost"],
    ["证据驱动的风险清单", "Evidence-driven risk findings"],
    ["配置留副本，大模型只建清单", "Back up configuration; inventory large models"],
    ["查看本次体检的证据边界与未知项", "View evidence boundaries and unknowns for this checkup"],
    ["启动 Ollama、llama.cpp、vLLM、SGLang、LM Studio、TGI、KoboldCpp、MLX-LM 或 ComfyUI 后刷新。", "Start Ollama, llama.cpp, vLLM, SGLang, LM Studio, TGI, KoboldCpp, MLX-LM, or ComfyUI, then refresh."],
    ["正在读取本机端口和进程信息…", "Reading local ports and processes…"],
    ["调整搜索词或过滤条件后重试。", "Adjust the search or filters and try again."],
    ["搜索服务、端口、项目、Agent 或命令…", "Search service, port, project, agent, or command…"],
    ["先算清楚，再下载权重", "Size first, download weights later"],
    ["本机服务归属", "Local service attribution"],
    ["服务清单", "Service inventory"],
    ["服务监控", "Service Monitor"],
    ["模型容量规划", "Model Capacity"],
    ["AI 运行体检", "AI Runtime Checkup"],
    ["服务守望", "Service Guardian"],
    ["跳到服务列表", "Skip to service list"],
    ["正在识别平台", "Detecting platform"],
    ["切换到英文", "Switch to English"],
    ["切换到中文", "Switch to Chinese"],
    ["搜索服务", "Search services"],
    ["主功能", "Primary features"],
    ["服务摘要", "Service summary"],
    ["采集器状态", "Collector status"],
    ["服务过滤器", "Service filters"],
    ["服务进程", "Service processes"],
    ["监听端口", "Listening ports"],
    ["关联项目", "Attributed projects"],
    ["Agent 来源", "Agent sources"],
    ["需要复核", "Needs review"],
    ["系统负载", "System load"],
    ["最后更新", "Last updated"],
    ["含分组来源", "Grouped by source"],
    ["已识别目录", "Detected directories"],
    ["活跃归属", "Active attribution"],
    ["规则评分提示", "Rule-score signal"],
    ["等待采集", "Waiting for collection"],
    ["操作记录", "Audit trail"],
    ["刷新状态", "Refresh"],
    ["立即体检", "Run checkup"],
    ["重新读取硬件", "Refresh hardware"],
    ["全部", "All"],
    ["宿主机", "Host"],
    ["Agent 进程", "Agent processes"],
    ["模型推理", "Model runtimes"],
    ["Windows 服务", "Windows services"],
    ["仅看需要复核", "Review only"],
    ["建议复核", "Review"],
    ["疑似遗留", "Potentially stale"],
    ["正常", "Healthy"],
    ["没有匹配的服务", "No matching services"],
    ["项目 / Agent", "Project / Agent"],
    ["PID / 端口", "PID / Port"],
    ["时长 / 判断", "Uptime / Assessment"],
    ["负载", "Load"],
    ["操作", "Actions"],
    ["你的服务目标", "Your service target"],
    ["计划用户数", "Planned users"],
    ["峰值并发数", "Peak concurrency"],
    ["平均输入 tokens", "Average input tokens"],
    ["每会话上下文窗口", "Context window per session"],
    ["平均输出 tokens", "Average output tokens"],
    ["目标速度 tokens/s/用户", "Target tokens/s/user"],
    ["目标首字延迟（秒）", "Target TTFT (seconds)"],
    ["选择倾向", "Preference"],
    ["质量/速度平衡", "Balanced quality/speed"],
    ["吞吐优先", "Throughput first"],
    ["质量优先", "Quality first"],
    ["最大容量优先", "Maximum capacity"],
    ["运行时", "Runtime"],
    ["自动推荐", "Automatic recommendation"],
    ["KV 缓存位宽", "KV-cache precision"],
    ["保守默认", "conservative default"],
    ["需运行时支持", "runtime support required"],
    ["实验性", "experimental"],
    ["计算可运行上限", "Calculate capacity"],
    ["三层容量上限", "Three capacity ceilings"],
    ["物理装载上限", "Physical fit ceiling"],
    ["实际可用上限", "Usable ceiling"],
    ["目标 SLA 上限", "Target-SLA ceiling"],
    ["瓶颈与判断", "Bottlenecks and assessment"],
    ["推荐运行方案", "Recommended runtime plan"],
    ["复制命令模板", "Copy command template"],
    ["候选方案", "Candidate options"],
    ["按当前偏好选择的量化方案", "Quantization options ranked by current preference"],
    ["模型 / 架构", "Model / Architecture"],
    ["参数 / 量化", "Parameters / Quantization"],
    ["内存拆解", "Memory breakdown"],
    ["并发预测", "Concurrency forecast"],
    ["执行路径", "Execution path"],
    ["结论", "Result"],
    ["用本机 llama-bench 校准", "Calibrate with local llama-bench"],
    ["正在检测", "Detecting"],
    ["运行短基准", "Run short benchmark"],
    ["查看公式边界与全部假设", "View formula boundaries and all assumptions"],
    ["总体结论", "Overall conclusion"],
    ["证据采集中", "Collecting evidence"],
    ["正在读取资源与模型服务状态…", "Reading resources and model-service status…"],
    ["加速器", "Accelerators"],
    ["模型磁盘余量", "Model-disk free space"],
    ["温度与风扇", "Temperature and fans"],
    ["服务 / 模型", "Service / Model"],
    ["加载 / 配置", "Load / Configuration"],
    ["实际性能", "Measured performance"],
    ["并发 / 上下文", "Concurrency / Context"],
    ["安全 / 网络", "Security / Network"],
    ["稳定性", "Stability"],
    ["显式操作", "Explicit actions"],
    ["未发现可探测的模型服务", "No detectable model services"],
    ["联网与手工受信节点", "Network activity and trusted nodes"],
    ["处置建议", "Recommended actions"],
    ["版本与回滚", "Versions and restore"],
    ["输入确认短语 SNAPSHOT", "Enter confirmation phrase SNAPSHOT"],
    ["创建本地清单", "Create local manifest"],
    ["快照历史", "Snapshot history"],
    ["服务详情", "Service details"],
    ["停止进程树", "Stop process tree"],
    ["请输入确认短语", "Enter confirmation phrase"],
    ["确认停止", "Confirm stop"],
    ["本机扫描设置", "Local scan settings"],
    ["刷新间隔（秒）", "Refresh interval (seconds)"],
    ["运行时长关注阈值（小时）", "Uptime attention threshold (hours)"],
    ["建议复核分数", "Review score"],
    ["疑似遗留分数", "Stale score"],
    ["采集 UDP 绑定", "Collect UDP bindings"],
    ["映射 Windows 服务", "Map Windows services"],
    ["展示 Docker 容器", "Show Docker containers"],
    ["展示运行中的 WSL", "Show running WSL instances"],
    ["启用模型运行时只读探测", "Enable read-only model-runtime probes"],
    ["低磁盘空间阈值（GiB）", "Low-disk threshold (GiB)"],
    ["隐私边界", "Privacy boundary"],
    ["保存并重新扫描", "Save and rescan"],
    ["运行 llama-bench 短基准", "Run llama-bench short benchmark"],
    ["目录模型", "Catalog model"],
    ["GGUF 量化", "GGUF quantization"],
    ["本地 GGUF 绝对路径", "Absolute path to local GGUF"],
    ["输入确认短语 BENCHMARK", "Enter confirmation phrase BENCHMARK"],
    ["确认运行短基准", "Confirm short benchmark"],
    ["模型服务短基准", "Model-service short benchmark"],
    ["已加载模型", "Loaded model"],
    ["并发请求数", "Concurrent requests"],
    ["请求上下文 tokens", "Request context tokens"],
    ["输出 tokens", "Output tokens"],
    ["确认运行", "Confirm run"],
    ["日志 / 配置检查", "Log / Configuration inspection"],
    ["检查类型", "Inspection type"],
    ["日志错误 / OOM / CUDA", "Log errors / OOM / CUDA"],
    ["配置语法 / 关键项", "Configuration syntax / key fields"],
    ["本地文件绝对路径", "Absolute local file path"],
    ["脱敏检查", "Redacted inspection"],
    ["回滚配置副本", "Restore configuration copy"],
    ["确认回滚", "Confirm restore"],
    ["取消", "Cancel"],
    ["关闭", "Close"],
    ["设置", "Settings"],
    ["公网判断", "Public exposure assessment"],
    ["仅匹配非回环模型端口", "Non-loopback model ports only"],
    ["不执行外网回连", "No outbound validation request"],
    ["0.0.0.0/:: 仅代表潜在远程可达，不证明已穿透 NAT", "0.0.0.0/:: indicates potential remote reachability, not proven NAT traversal"],
    ["当前证据不足", "Insufficient current evidence"],
    ["暂无高优先级发现", "No high-priority findings"],
    ["这不等于所有未知项都安全。", "This does not mean every unknown is safe."],
    ["展开下方证据边界继续核实。", "Expand the evidence boundaries below for verification."],
    ["暂无快照", "No snapshots"],
    ["选择模型或配置文件后创建第一份清单", "Select model or configuration files to create the first manifest"],
    ["未获得稳定会话 ID", "No stable session ID"],
    ["未归类项目", "Unattributed project"],
    ["路径不可见", "Path unavailable"],
    ["可执行路径不可见", "Executable path unavailable"],
    ["父进程链", "Parent process chain"],
    ["监听端点", "Listening endpoints"],
    ["判断证据", "Assessment evidence"],
    ["项目归属", "Project attribution"],
    ["Agent / 会话", "Agent / Session"],
    ["已脱敏命令", "Redacted command"],
    ["查看归属证据", "View attribution evidence"],
    ["打开项目目录", "Open project directory"],
    ["打开本地 URL", "Open local URL"],
    ["标记为预期服务", "Mark as expected"],
    ["取消预期标记", "Remove expected mark"],
    ["短基准", "Benchmark"],
    ["日志/配置", "Logs/config"],
    ["未获得父进程链", "Parent process chain unavailable"],
    ["该服务没有模型运行时探测结果", "No model-runtime probe result for this service"],
    ["未归类", "Unattributed"],
    ["启动", "Started"],
    ["未从工作目录、命令和父进程链命中已配置项目根目录", "No configured project root matched the working directory, command, or parent-process chain"],
    ["检测到项目标记文件", "Project marker file detected"],
    ["命中已配置项目根目录的一级子目录", "Matched a first-level child of a configured project root"],
    ["命中已配置项目根目录", "Matched a configured project root"],
    ["服务进程工作目录", "Service-process working directory"],
    ["服务命令中的本地路径", "Local path in the service command"],
    ["当前进程链中未发现受支持的 Agent、IDE 或终端签名", "No supported agent, IDE, or terminal signature was found in the current process chain"],
    ["桌面应用可执行路径", "Desktop-application executable path"],
    ["进程名精确匹配", "Exact process-name match"],
    ["可执行文件名精确匹配", "Exact executable-name match"],
    ["包装运行时命令令牌", "Wrapped-runtime command token"],
    ["包装运行时包路径", "Wrapped-runtime package path"],
    ["运行命令显式携带会话恢复标识", "The command explicitly carries a session-resume identifier"],
    ["该产品未提供已核验的稳定本地会话标识接口，仅确认进程归属", "This product has no verified stable local-session identifier interface; only process attribution is confirmed"],
    ["已由用户标记为预期服务", "Marked by the user as an expected service"],
    ["Docker/WSL 使用独立生命周期模型，第一版仅展示，不自动判定遗留", "Docker/WSL use separate lifecycle models; this release displays them without automatic stale classification"],
    ["Agent 本体只展示运行状态和项目/会话证据，不按开发服务规则判定遗留", "Agent processes show runtime and project/session evidence only; development-service stale rules do not apply"],
    ["Windows 服务由服务控制管理器托管，第一版不判定遗留", "Windows services are managed by the Service Control Manager and are not classified as stale"],
    ["已连续运行超过 72 小时", "Running continuously for more than 72 hours"],
    ["已连续运行超过 24 小时", "Running continuously for more than 24 hours"],
    ["关联项目目录已不存在", "The attributed project directory no longer exists"],
    ["开发运行时未能归入任何已配置项目", "The development runtime could not be attributed to a configured project"],
    ["长时间运行且当前低负载、无已建立连接", "Long-running with low current load and no established connections"],
    ["未命中疑似遗留规则", "No potentially-stale rule matched"],
    ["正在计算…", "Calculating…"],
    ["正在读取…", "Reading…"],
    ["正在创建…", "Creating…"],
    ["正在脱敏检查…", "Inspecting with redaction…"],
    ["基准运行中…", "Benchmark running…"],
    ["未检测到 llama-bench", "llama-bench not detected"],
    ["llama-bench 可用", "llama-bench available"],
    ["当前硬件指纹下暂无校准结果。", "No calibration result for the current hardware fingerprint."],
    ["未获得可用于容量计算的 GPU，将使用 CPU / 系统内存。", "No GPU with capacity evidence; using CPU/system memory."],
    ["未知 CPU", "Unknown CPU"],
    ["总内存", "total memory"],
    ["当前可用", "currently available"],
    ["Apple 统一内存", "Apple unified memory"],
    ["显存未知", "VRAM unknown"],
    ["统一内存", "unified memory"],
    ["显存", "VRAM"],
    ["置信度", "confidence"],
    ["已检测", "detected"],
    ["未检测", "not detected"],
    ["全 GPU", "all-GPU"],
    ["混合卸载", "hybrid offload"],
    ["当前可装入", "fits now"],
    ["释放资源后理论可装入", "theoretically fits after freeing resources"],
    ["未识别额外瓶颈", "No additional bottleneck identified"],
    ["当前没有可执行方案", "No executable plan currently"],
    ["已检测到运行时", "runtime detected"],
    ["尚未检测到运行时", "runtime not detected"],
    ["仅本机", "loopback only"],
    ["命令只生成不执行", "command generated but not executed"],
    ["满足目标", "Meets target"],
    ["性能不达标", "Performance below target"],
    ["需释放资源", "Resources must be freed"],
    ["超出上下文上限", "Context limit exceeded"],
    ["无法装入", "Does not fit"],
    ["预算不足", "Insufficient budget"],
    ["证据不足", "Insufficient evidence"],
    ["来源未知", "Source unknown"],
    ["未报告", "not reported"],
    ["小时", "h"],
    ["分钟", "m"],
    ["秒", "s"],
    ["天", "d"],
    ["核", " cores"],
    ["线程", " threads"],
    ["带宽估算", "estimated bandwidth"],
    ["频率不可用", "frequency unavailable"],
    ["已用", "used"],
    ["剩余", "free"],
    ["不可用", "Unavailable"],
    ["未配置", "Not configured"],
    ["未知", "Unknown"],
    ["来源", "Source"],
    ["健康", "Health"],
    ["配置", "Configuration"],
    ["认证", "Authentication"],
    ["进程", "Process"],
    ["平台", "Platform"],
    ["防火墙", "Firewall"],
    ["反向代理", "Reverse proxy"],
    ["受信节点", "Trusted nodes"],
    ["温度 / 风扇", "Temperature / fans"],
    ["磁盘", "Disk"],
    ["系统卷", "System volume"],
    ["项目卷", "Project volume"],
    ["总流量", "Total traffic"],
    ["所有网卡", "All interfaces"],
    ["无可见远端连接", "No visible remote connections"],
    ["入站允许", "Inbound allow"],
    ["范围", "Range"],
    ["量化未知", "Quantization unknown"],
    ["加速路径未知", "Acceleration path unknown"],
    ["后端未知", "Backend unknown"],
    ["命令不可见", "Command unavailable"],
    ["工具", "Tools"],
    ["视觉", "Vision"],
    ["语音", "Audio"],
    ["自动重启", "Auto-restart"],
    ["历史重启", "Restart history"],
    ["未获得实测功耗", "Measured power unavailable"],
    ["厂商遥测不可用", "Vendor telemetry unavailable"],
    ["传感器不可用", "Sensors unavailable"],
    ["当前系统未暴露传感器", "The current system does not expose sensors"],
    ["暂无操作记录。", "No audit records."],
    ["设置已保存，正在重新扫描", "Settings saved; rescanning"],
    ["已请求重新扫描", "Rescan requested"],
    ["控制台初始化失败", "Console initialization failed"],
    ["状态读取失败", "Status read failed"],
    ["模型容量模块初始化失败", "Model-capacity module initialization failed"],
    ["快照读取失败", "Snapshot read failed"],
    ["通过 psutil 读取系统端口表", "Read the system port table through psutil"],
    ["无法读取 WSL 运行状态", "Unable to read WSL runtime status"],
    ["未检测到 docker 命令", "Docker command not detected"],
    ["连接设备平台服务", "Connected Devices Platform Service"],
    ["受支持 Agent 进程", "supported agent processes"],
    ["含无监听端口进程", "including processes without listeners"],
    ["个监听 PID", " listening PIDs"],
    ["个祖先进程", " ancestor processes"],
    ["采集耗时", "Collection time"],
    ["无监听端口", "No listening ports"],
    ["未评分", "Not scored"],
    ["证据", "evidence"],
    ["服务", "Service"],
    ["命令", "command"],
    ["检测到", "Detected"],
    ["祖先", "ancestor"],
    ["读取", "Read"],
    ["以及", "and"],
    ["本机模型容量", "Local model capacity"],
    ["WSL 推理运行时桥接", "WSL inference runtime bridge"],
    ["并发会同时放大 KV 缓存，并分摊总生成吞吐；总用户数本身不直接消耗推理内存", "Concurrency increases KV cache and shares aggregate generation throughput; total users alone do not consume inference memory"],
    ["主加速器后端为实验性支持，范围预测不能替代本机 llama-bench 实测", "The primary accelerator backend is experimental; range estimates cannot replace a local llama-bench measurement"],
    ["当前加速后端为实验性支持，必须以本机实测为准", "The current acceleration backend is experimental and requires local measurement"],
    ["需要 CPU/GPU 混合卸载，吞吐对 PCIe、内存带宽和后端实现高度敏感", "CPU/GPU hybrid offload is required; throughput is highly sensitive to PCIe, memory bandwidth, and backend implementation"],
    ["理论可装载，但当前空闲内存不足；需先释放其他进程", "The model can theoretically load, but current free memory is insufficient; free other processes first"],
    ["即使释放可用资源，估算内存仍不足", "Estimated memory remains insufficient even after reclaiming available resources"],
    ["目标下最大并发", "Maximum concurrency at target"],
    ["当前内存紧张", "Current memory pressure is high"],
    ["独立显存", "dedicated VRAM"],
    ["系统内存", "system memory"],
    ["范围预测", "range estimate"],
    ["本机实测", "local measurement"],
    ["推理内存", "inference memory"],
    ["生成吞吐", "generation throughput"],
    ["工作区", "Workspace"],
    ["权重", "Weights"],
    ["合计", "Total"],
    ["余量", "headroom"],
    ["预计", "Estimated"],
    ["推荐", "Recommended"],
    ["激活", "active"],
    ["有效", "effective"],
    ["用户", "user"],
    ["内存", "memory"],
    ["总", "total"],
    ["已知项未发现高优先级异常；仍有", "No high-priority anomaly was found in known evidence;"],
    ["个领域证据不足", "evidence domains remain unknown"],
    ["机器健康", "Machine health"],
    ["模型性能", "Model performance"],
    ["未发现可探测的本地模型服务", "No detectable local model service"],
    ["服务安全", "Service security"],
    ["规则匹配只覆盖 Windows 当前活动策略中的启用入站允许规则；仍不能判断路由器/NAT 的公网可达性", "Rule matching covers enabled inbound-allow rules in the active Windows policy only; router/NAT public reachability remains unknown"],
    ["服务稳定", "Service stability"],
    ["历史重启计数", "Historical restart count"],
    ["资源容量", "Resource capacity"],
    ["个运行时报告模型已加载", "runtimes report a loaded model"],
    ["个显示/计算适配器；可独立容量计算", "display/compute adapters; capacity evidence for"],
    ["个 GPU", "GPUs"],
    ["实时资源", "Live resources"],
    ["仅按厂商实测 GPU 功耗积分；不把 CPU/整机功耗估算冒充实测", "Energy is integrated from measured vendor GPU power only; CPU/system power estimates are not presented as measurements"],
    ["不抓包、不读 URL/内容", "no packet capture or URL/content inspection"],
    ["操作系统未向当前非提权进程暴露温度或风扇传感器；VSG 未使用估算值", "The OS did not expose temperature or fan sensors to the current unprivileged process; VSG used no estimates"],
    ["模型服务", "Model services"],
    ["宽泛 Any 规则", "Broad Any rules"],
    ["未从进程表识别", "Not identified from the process table"],
    ["公网对端", "Public peers"],
    ["累计发送", "Total sent"],
    ["接收", "received"],
    ["非多 GPU 容量", "No multi-GPU capacity evidence"],
    ["不会扫描局域网", "LAN scanning is disabled"],
    ["总量未知", "total unknown"],
    ["温度", "Temperature"],
    ["风扇", "Fan"],
    ["共享", "shared"],
    ["启用", "Enabled"],
    ["未启用", "Disabled"],
    ["可用", "available"],
    ["多卡", "Multiple GPUs"],
    ["检测", "Detected"],
    ["无", "None"]
  ];

  const translations = new Map(pairs);
  const replacements = [...pairs].sort((a, b) => b[0].length - a[0].length);
  const textOriginals = new WeakMap();
  const attributeOriginals = new WeakMap();
  let locale = "zh-CN";
  let observer;

  function normalizeLocale(value) {
    return String(value || "").toLowerCase().startsWith("zh") ? "zh-CN" : "en";
  }

  function translateText(value) {
    const source = String(value ?? "");
    if (!HAN.test(source)) return source;
    if (translations.has(source)) return translations.get(source);
    let result = source;
    result = result
      .replace(/(\d+)天(\d+)小时/g, "$1d $2h")
      .replace(/(\d+)小时(\d+)分/g, "$1h $2m")
      .replace(/(\d+)小时/g, "$1h")
      .replace(/(\d+)分钟/g, "$1m")
      .replace(/(\d+)秒/g, "$1s")
      .replace(/检测到\s*(\d+)\s*个受支持 Agent 进程（含无监听端口进程）/g, "Detected $1 supported agent processes, including processes without listeners")
      .replace(/通过 psutil 读取系统端口表；读取\s*(\d+)\s*个监听 PID，以及\s*(\d+)\s*个祖先进程/g, "Read the system port table through psutil; $1 listening PIDs and $2 ancestor processes")
      .replace(/已知项未发现高优先级异常；仍有\s*(\d+)\s*个领域证据不足/g, "No high-priority anomaly was found in known evidence; $1 evidence domains remain unknown")
      .replace(/检测到\s*(\d+)\s*个 GPU，\s*(\d+)\s*个运行时报告模型已加载/g, "Detected $1 GPUs; $2 runtimes report a loaded model")
      .replace(/检测\s*(\d+)\s*个显示\/计算适配器；可独立容量计算\s*(\d+)\s*个/g, "Detected $1 display/compute adapters; $2 have independent capacity evidence")
      .replace(/已加载\s*(\d+)\s*条本地规则，命中\s*(\d+)\s*个服务；\s*(\d+)\s*个项目清单生效/g, "Loaded $1 local rules, matched $2 services; $3 project manifests active")
      .replace(/关停会同时影响\s*(\d+)\s*个端点和\s*(\d+)\s*个当前本机客户端/g, "Stopping affects $1 endpoints and $2 current local clients")
      .replace(/当前检测到\s*(\d+)\s*个本机客户端依赖，停止可能中断正在进行的请求/g, "Detected $1 local client dependencies; stopping may interrupt in-flight requests")
      .replace(/该进程同时拥有\s*(\d+)\s*个监听端点，停止会一并关闭/g, "This process owns $1 listening endpoints; stopping closes them together")
      .replace(/服务与\s*(.+?)\s*进程链相关联，Agent 仍活动时可能再次启动服务/g, "The service is associated with the $1 process chain; an active agent may start it again")
      .replace(/优先通过\s*(.+?)\s*的原生方式恢复，而不是直接重放命令/g, "Prefer the native $1 recovery path instead of replaying the command directly")
      .replace(/PID\s*(\d+)\s*(.+?)\s*正在连接端口\s*(\d+)/g, "PID $1 $2 is connected to port $3")
      .replace(/历史重启计数\s*(\d+)/g, "Historical restart count $1")
      .replace(/服务进程本身命中\s*(.+?)\s*签名/g, "The service process matched the $1 signature")
      .replace(/第\s*(\d+)\s*级父进程命中\s*(.+?)\s*签名/g, "Ancestor process level $1 matched the $2 signature")
      .replace(/第\s*(\d+)\s*级父进程工作目录/g, "Ancestor process level $1 working directory")
      .replace(/同项目和时间窗口匹配\s*(.+?)(?=$|；|，)/g, "Matched $1 in the same project and time window")
      .replace(/已超过设定的\s*(\d+(?:\.\d+)?)\s*小时关注阈值/g, "Exceeded the configured $1-hour attention threshold")
      .replace(/历史上由\s*(.+?)\s*关联启动，但当前 Agent 链已消失/g, "Historically started through $1 attribution, but the current agent chain is gone")
      .replace(/关联的\s*(.+?)\s*会话当前不活跃/g, "The attributed $1 session is currently inactive")
      .replace(/同项目检测到\s*(\d+)\s*个相似服务实例/g, "Detected $1 similar service instances in the same project")
      .replace(/(\d+)%\s*证据/g, "$1% evidence");
    for (const [zh, en] of replacements) if (result.includes(zh)) result = result.split(zh).join(en);
    result = result
      .replaceAll("：", ": ")
      .replaceAll("；", "; ")
      .replaceAll("，", ", ")
      .replaceAll("（", " (")
      .replaceAll("）", ")")
      .replaceAll("。", ".")
      .replace(/(\d+)\s*个(?=\s|$)/g, "$1");
    return result;
  }

  function processText(node) {
    const current = node.nodeValue || "";
    const oldOriginal = textOriginals.get(node);
    if (locale === "zh-CN") {
      if (oldOriginal != null) {
        if (current !== oldOriginal && current === translateText(oldOriginal)) node.nodeValue = oldOriginal;
        else if (current !== oldOriginal) textOriginals.set(node, current);
      }
      return;
    }
    if (oldOriginal == null || current !== translateText(oldOriginal)) textOriginals.set(node, current);
    const translated = translateText(textOriginals.get(node));
    if (translated !== current) node.nodeValue = translated;
  }

  function processAttributes(element) {
    const names = ["placeholder", "title", "aria-label"];
    let originals = attributeOriginals.get(element);
    if (!originals) {
      originals = new Map();
      attributeOriginals.set(element, originals);
    }
    for (const name of names) {
      if (!element.hasAttribute?.(name)) continue;
      const current = element.getAttribute(name) || "";
      const oldOriginal = originals.get(name);
      if (locale === "zh-CN") {
        if (oldOriginal != null) {
          if (current !== oldOriginal && current === translateText(oldOriginal)) element.setAttribute(name, oldOriginal);
          else if (current !== oldOriginal) originals.set(name, current);
        }
        continue;
      }
      if (oldOriginal == null || current !== translateText(oldOriginal)) originals.set(name, current);
      const translated = translateText(originals.get(name));
      if (translated !== current) element.setAttribute(name, translated);
    }
  }

  function processRoot(root) {
    if (!root) return;
    if (root.nodeType === Node.TEXT_NODE) return processText(root);
    if (![Node.ELEMENT_NODE, Node.DOCUMENT_NODE, Node.DOCUMENT_FRAGMENT_NODE].includes(root.nodeType)) return;
    if (root.nodeType === Node.ELEMENT_NODE) processAttributes(root);
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT);
    let node;
    while ((node = walker.nextNode())) node.nodeType === Node.TEXT_NODE ? processText(node) : processAttributes(node);
  }

  function observe() {
    observer?.disconnect();
    observer = new MutationObserver((mutations) => {
      observer.disconnect();
      for (const mutation of mutations) {
        if (mutation.type === "characterData") processText(mutation.target);
        if (mutation.type === "attributes") processAttributes(mutation.target);
        for (const node of mutation.addedNodes || []) processRoot(node);
      }
      observe();
    });
    observer.observe(document.documentElement, { childList: true, subtree: true, characterData: true, attributes: true, attributeFilter: ["placeholder", "title", "aria-label"] });
  }

  function updateToggle() {
    const button = document.getElementById("language-toggle");
    if (!button) return;
    const english = locale === "en";
    button.textContent = english ? "ZH" : "EN";
    const label = english ? "Switch to Chinese" : "切换到英文";
    button.setAttribute("aria-label", label);
    button.setAttribute("title", label);
  }

  function setLocale(next, persist = true) {
    observer?.disconnect();
    locale = normalizeLocale(next);
    document.documentElement.lang = locale === "en" ? "en" : "zh-CN";
    if (persist) localStorage.setItem(STORAGE_KEY, locale);
    processRoot(document.documentElement);
    updateToggle();
    observe();
    document.dispatchEvent(new CustomEvent("vsg:localechange", { detail: { locale } }));
  }

  function initialLocale() {
    const saved = localStorage.getItem(STORAGE_KEY);
    return normalizeLocale(saved || navigator.languages?.[0] || navigator.language || "zh-CN");
  }

  window.VSG_I18N = {
    get locale() { return locale; },
    setLocale,
    translate: (value) => locale === "en" ? translateText(value) : String(value ?? ""),
    translateRoot: processRoot,
    untranslated(root = document.body) {
      const values = [];
      const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
      let node;
      while ((node = walker.nextNode())) if (HAN.test(node.nodeValue || "") && node.parentElement?.offsetParent !== null) values.push(node.nodeValue.trim());
      return [...new Set(values.filter(Boolean))];
    }
  };

  document.addEventListener("DOMContentLoaded", () => {
    setLocale(initialLocale(), false);
    document.getElementById("language-toggle")?.addEventListener("click", () => setLocale(locale === "en" ? "zh-CN" : "en"));
  });
})();
