# 本地验证记录

本文保留 Vibe Service Guardian 0.4.0–0.8.5.1 的既有验证证据，并追加 0.8.5.2 P1 日常工作流及受限 macOS x86_64 VMware 预览的可复核结果。以下 0.8.5.2 验证轮次执行时没有提交、推送、发布 Release 或提交 OpenAI 申请；后续 Git 合并不改变这些目标环境证据的范围或结论。

## 0.8.5.2 P1 日常工作流验收（2026-08-16）

- 首页固定展示实际运行版本、PID、回环端口与运行时长，并提供首次使用检查、提醒中心、项目安全清理和安全退出入口。跨版本重复启动只记录请求/运行版本、PID、端口、时间和动作，不记录数据目录或控制令牌。
- 项目安全清理返回 `automatic_cleanup=false`、`execution_mode=individual_confirmation_only` 和 `requires_fresh_assessment=true`。受管/受保护服务继续显示建议路径但不能进入停止；可操作项点击后仍走现有逐服务快照复核、PID/创建时间防复用、`STOP <PID>` 与停止观察链路，没有批量停止 API。
- SQLite schema version 7 在一致性备份和单事务迁移中增加提醒已读位置。专项测试覆盖 warning/critical 去重、单条已读、再次出现重新未读、v6 到 v7 迁移和 Web 不返回内部去重键。
- Windows 桌面集成默认关闭。打包 EXE 在隔离临时数据目录中完成原生托盘启停：启用后 `running=true` 且无错误，关闭后线程退出；快捷键始终为 `disabled`。验证前后 `HKCU` 的 `VibeServiceGuardian` 开机启动值均不存在，测试未调用启动配置写接口。
- 打包版 0.8.5.2 使用 Microsoft Edge/CDP 分别完成中文和英文交互检查：首页版本正确，首次检查可见，提醒、项目计划、设置和退出弹窗均可打开；项目计划展示 3 个本机分组和 31 个逐服务入口，退出占位符为 `EXIT VSG`，两次浏览器检查的 warning/error/exception 均为空。
- 当前 Windows 源码树完整 unittest 共 243 项：242 通过，1 项因 Windows 不适用 POSIX mode-bit 断言而跳过；P1 专项 10 项和 macOS 验收脚本专项 9 项均通过。Ruff、两个前端 JavaScript 语法检查、20 份哈希锁、公开目录审计、Bandit 中/高风险门禁、`pip check` 和 `git diff --check` 通过。
- 独立源码回环 smoke 确认版本 `0.8.5.2`、端口 `65503`、项目计划仅逐项确认、托盘默认关闭；错误退出短语返回 HTTP 409，`EXIT VSG` 后优雅退出。
- Windows 0.8.5.2 未签名 EXE 在全新临时数据目录完成原生 smoke：仅绑定 `127.0.0.1`，Host/Origin/随机令牌拒绝、CSP、17 个离线模型候选、29 个服务评估、144 个关系节点、123 条边、3 条本机依赖、2 个 GPU、健康状态 `healthy`、0 个采集错误和优雅退出均通过。服务与关系数量是动态本机样本，不作为固定断言。
- Windows ZIP 排除运行数据，并通过单根目录、路径穿越/符号链接/体积限制、许可证、SPDX 2.3 SBOM 和 0.8.5.2 文档契约验证。归档为 `Vibe-Service-Guardian-Windows-x64-0.8.5.2.zip`；最终 SHA-256 只以归档旁同名 `.sha256` 为准，避免文档与归档形成哈希自引用。
- 本轮没有停止用户服务、没有运行真实模型负载、没有读取目标进程环境变量，也没有修改开机启动。macOS/Linux 托盘、完整目标平台真机矩阵和 systemd/launchd/Hermes/OpenCode 原生矩阵仍属于 0.8.6 P2-B。

上线结论与剩余阻断项见 [PRODUCTION-READINESS-0.8.5.2.md](PRODUCTION-READINESS-0.8.5.2.md)。

### macOS x86_64 VMware 预览验收（2026-08-16，r8）

- 环境证据为 macOS 13.7.8、`x86_64`、Python 3.12.10；AMD 宿主机与 VMware 由用户报告，不能由客体日志独立证明。
- 原生构建与校验输出 `MACOS_NATIVE_VALIDATION_OK` 和 `MACOS_VM_AUTOMATIC_ACCEPTANCE_OK`；测试套件整体为 OK，其中 1 项 Windows Git Bash 专用测试按平台跳过。Mach-O 当前架构和 ad-hoc 签名系统检查通过。
- 未签名便携 ZIP 的 SHA-256 为 `E41CE43EB8395F8D5CC03527B4A9C1B090B55235DBE5B004B5C927D367BAAA10`。该散列只对应当次 r8 x86_64 构建，不代表 r9 或其他架构。
- 人工测试辅助服务使用动态回环端口 `50234`，VSG 使用回环端口 `43922`；重复启动复用了同一辅助进程。后续状态文件、PID/端口文件与 `runtime.json` 均显示已完成清理。
- 该次证据不能升级为完整人工验收：清单仍为未勾选模板，没有三张界面截图；VM 不能验证 Metal、真实 GPU/统一内存、温度、风扇和功耗；也不能替代实体 Intel Mac 或 Apple Silicon。
- r8 证据 ZIP 的 SHA-256 为 `8534A062F6CFEBE45DA032C82333D302E7BCEA2646E29C699F6DE28741E03C56`，但封装时最终日志仍在写入，ZIP 内日志短于收尾后的独立日志，因此该 ZIP 只作为缺陷证据，不作为完整验收包。
- r9 已修复日志关闭顺序、最近停止状态、VSG 退出证明和 AppleDouble 排除，并通过 Windows Git Bash 自包含回归；修改后的 r9 尚待 macOS 目标环境重新执行。脱敏审查记录见 [macos-vm-preview-0.8.5.2.md](case-studies/macos-vm-preview-0.8.5.2.md)。

## 0.8.5.1 日常使用收敛验收（2026-08-16）

- 默认首页升级为“今日关注”：聚合疑似遗留、非回环监听、推理运行时异常和重复 VSG 实例；项目/Agent/推理实例保留在关注范围，Windows 服务等系统噪声默认折叠，仍可切回“全部服务”。重复 VSG 只生成一张聚合行动卡，不按实例重复刷屏。
- 增加按数据目录隔离的跨平台单实例锁：Windows 使用 `msvcrt.locking`，macOS/Linux 使用 `fcntl.flock`；崩溃残留锁文件不等于仍被占用。同一数据目录第二次启动会打开已存活控制台而非创建新监听；本机源码 smoke 中两次启动保持同一 PID `49108` 和端口 `55670`。显式使用不同数据目录仍允许隔离实例，因此多实例提示要求核对数据目录意图，不直接断言为遗留。
- VSG 自身、Docker/Compose、Windows Service、WSL、systemd、launchd、Agent/IDE 等受保护或受管服务改为直接显示只读处置路径；弹窗不显示停止提交按钮，所有建议均为 `will_execute=false`，不会执行建议命令。VSG 自身不会由服务清单直接停止。
- Ollama、llama.cpp 和 vLLM 增加原生只读适配标识、加载模型/量化/上下文或 KV cache 等可见证据摘要，并进入首页推理运行时概览。没有读取 API Key、目标进程环境变量或模型文件正文；需要认证或证据缺失时保持未知，不猜测。
- 内置浏览器对隔离源码实例完成中英文 smoke：首页默认关注视图、重复 VSG 聚合卡、推理运行时概览和只读建议弹窗均可见；英文页面未发现残留中文，页面宽度 `1265 == 1265` 无横向溢出，控制台 warning/error 为空。浏览器验收发现并修复了一处建议弹窗混合语言和一处重复行动卡噪声。
- 完整 unittest 共 221 项：220 通过，1 项因 Windows 不适用 POSIX mode-bit 断言而跳过；Ruff、两个前端 JavaScript 语法检查、20 份哈希锁、公开目录审计和 `git diff --check` 通过。新增测试覆盖关注范围、重复 VSG 聚合、源码/便携进程识别、跨平台锁互斥、只读 VSG 引导、三类运行时适配和三/四段发布版本契约。
- Windows 0.8.5.1 未签名 EXE 在全新临时数据目录完成原生 smoke：仅绑定 `127.0.0.1`，Host/Origin/随机令牌拒绝、CSP、17 个离线模型候选、32 个服务评估、153 个关系节点、133 条边、5 条本机依赖、2 个 GPU、健康状态 `healthy`、0 个采集错误和优雅退出均通过。动态服务与关系数量不作为固定断言。
- Windows ZIP 共 44 个条目，排除运行数据并通过单根目录、路径穿越/符号链接/体积限制、许可证与 SPDX 2.3 SBOM 验证。归档为 `Vibe-Service-Guardian-Windows-x64-0.8.5.1.zip`；最终 SHA-256 只以归档旁 `.sha256` 为准，避免文档与归档形成哈希自引用。
- 当前主机没有真实 Ollama、llama.cpp 或 vLLM 服务，三类适配器仅完成单元/回环夹具验证，不能据此宣称具体版本的真实兼容性。macOS/Linux 的锁实现与构建脚本已被自动化覆盖，但原生进程、端口、桌面环境和分发包仍需在目标系统真机验收。

## 0.8.5 P2-A 归属进化验收（2026-08-15）

- SQLite schema version 6 专项覆盖 v5 备份迁移、旧规则范围推断和首版快照；规则按 `instance > strict > standard > custom > legacy` 具体度、最新用户意图、优先级和 ID 确定性决胜。每条规则最多保留最近 5 版，回滚会创建新修订而不是改写历史。
- 服务纠正支持当前实例、标准复用和严格复用三种范围。命中和纠正按来源、服务指纹、启动时间与可见托管实体组成的脱敏 episode 去重；重复刷新不增加命中，同一 episode 多次纠正只计一次规则覆盖，两个不同 episode 覆盖后标记建议复核。
- 规则包覆盖空规则拒绝、256 KiB/500 条上限、canonical SHA-256、篡改拒绝、循环/异常深度拒绝、路径/完整命令/备注字段剥离、候选与冲突预览，以及经当前服务和范围显式重绑定后的真实 API 写入。没有后台同步或自动跨机匹配。
- Agent/IDE 可见父链子进程继承 `managed_child` 归属并保持停止保护，不进入普通遗留评分。Docker 测试断言只调用固定 9 字段 `inspect --format` 模板，不请求 `.Config.Env`、完整标签、挂载或 Compose 文件正文。
- 完整 unittest 共 210 项：209 通过，1 项因 Windows 不适用 POSIX mode-bit 断言而跳过；构建内完整回归用时 29.074 秒。0.8.5 专项 9 项全部通过；Ruff E4/E7/E9/F/B、两个前端 JavaScript 语法检查和 `git diff --check` 通过。
- 本机浏览器只读 UI smoke 验证事件与模型资产工作区、规则统计/搜索/导出/导入入口、导入重绑定弹窗、归属纠正三档复用范围和中英文切换；英文生命周期选项均完整翻译，控制台 warning/error 为空。检查未提交表单或写入规则，隔离临时数据已移入回收站。
- 20 份哈希锁验证通过，公开目录审计无发现，Bandit 中/高风险门禁为 0。`requirements.txt`、Windows、macOS、Linux 四组固定依赖在 2026-08-15 的 `pip-audit` 均返回 `No known vulnerabilities found`；该结论只代表当次漏洞数据库。
- Windows 0.8.5 未签名 EXE 在全新临时数据目录完成原生 smoke：版本 0.8.5、仅绑定 `127.0.0.1`、Host/Origin/随机令牌拒绝、CSP、17 个离线模型候选、33 个服务评估、155 个关系节点、136 条边、5 条本机依赖、2 个 GPU、健康状态 `healthy`、0 个采集错误和优雅退出均通过。服务与关系数量是动态本机样本，不作为固定断言。
- Windows ZIP 通过 sidecar、单根目录、路径穿越/符号链接/体积限制、运行数据排除、43 个条目、CRLF/ASCII 启动脚本、许可证、SPDX 2.3 SBOM 和 0.8.5 文档契约验证。最终 SHA-256 只以归档旁同名 `.sha256` 为准，避免文档与归档形成哈希自引用。
- 本轮没有停止用户服务、压测真实模型、读取目标进程/容器环境变量或执行 Docker/systemd/launchd 控制命令。macOS/Linux、systemd/launchd 原生证据和真实 Hermes/OpenCode 版本矩阵顺延到 0.8.6 P2-B，当前 Windows 结果不能替代。

上线结论与剩余阻断项见 [PRODUCTION-READINESS-0.8.5.md](PRODUCTION-READINESS-0.8.5.md)。

## 0.8.4 P0 收敛验收（2026-08-14）

- 停止后持续观察覆盖 5/15/30 分钟白名单、随时中止、VSG 退出后 `interrupted / evidence_insufficient`、原 PID 身份消失门槛、同端口重监听、同脱敏命令哈希、父进程变化、通知失败、SQLite 写入失败和“绝不二次停止”。端口采集不可用时保持未知，空命令行不生成共享身份哈希，窗口末次快照仍可捕获重监听。
- 历史生命周期标签覆盖“预期 / 可安全清理”、精确可执行路径 + 工作目录摘要、缺失路径拒绝继承、只继承标签、不传播项目/Agent 纠正、撤销标签时保留同一规则的其他覆盖，以及保护状态单调不降低。
- 60 秒本机校准覆盖已加载模型白名单、并发 1/2、最多 120 个请求、`BENCHMARK <端口>`、缺失 RAM/VRAM 证据阻断、运行中 85% 护栏协作式取消、用户中止不形成安全档案、实测档案持久化、预测误差和硬件/VRAM 变化失效。
- 项目—推理实例视图只复用当前硬件有效、实测安全且与唯一已加载模型名称精确匹配的档案；RAM/VRAM 达到 85% 时并发余量归零，同时加载多个模型时保持证据不足，不选择看似更高的单模型档案。
- 完整 unittest 共 201 项：200 通过，1 项因 Windows 不适用 POSIX mode-bit 断言而跳过；本轮一次完整回归用时 30.971 秒。Ruff E4/E7/E9/F/B、Python 编译、两个前端 JavaScript 语法检查、公开目录审计、20 份哈希锁离线校验和 `pip check` 均通过。
- Bandit 中/高风险门禁为 0；`requirements.txt`、Windows、macOS、Linux 四组固定依赖在 2026-08-14 的 `pip-audit` 均返回 `No known vulnerabilities found`。该结果只代表当次漏洞数据库，不是未来无漏洞保证。
- 所有主动停止 E2E 只操作测试自建临时进程；回环模型 E2E 只操作测试假服务。本轮没有停止用户服务、没有连接或压测真实模型、没有读取目标进程环境变量，也没有下载或上传模型。
- Windows 0.8.4 未签名 EXE 在全新临时数据目录完成原生 smoke：版本 0.8.4、仅绑定 `127.0.0.1`、Host/Origin/随机令牌拒绝、CSP、17 个离线模型候选、34 个服务评估、168 个关系节点、151 条边、6 条本机依赖、2 个 GPU、0 个采集错误、空观察/停止验证/校准档案和优雅退出均通过。服务与关系数量是动态本机样本，不作为固定断言。
- Windows ZIP 通过单根目录、路径穿越/符号链接/体积限制、运行数据排除、41 个条目、CRLF/ASCII 启动脚本、许可证、SPDX 2.3 SBOM 和 0.8.4 文档契约验证；最终 SHA-256 只以归档旁同名 `.sha256` 为准。macOS、Linux、真实系统通知、真实运行时 60 秒校准和浏览器逐页中英文仍未由当前 Windows 主机验证。

上线结论与剩余阻断项见 [PRODUCTION-READINESS-0.8.4.md](PRODUCTION-READINESS-0.8.4.md)。

## 0.8.3 收敛验收（2026-08-14）

- 增加两类只操作临时夹具的真实关停 E2E：第一类验证监听父进程、子进程、PID 与端口全部退出；第二类由看门狗重新拉起服务，验证替代 PID 与重新绑定端口被识别为 `relaunched`，且 `second_stop_attempted=false`。没有停止任何用户服务。
- 增加回环假模型服务 E2E：固定工作负载矩阵通过真实 HTTP 完成 5 + 10 + 20 共 35 次请求，并达到四并发；另验证协作式中止、Ollama NDJSON、OpenAI 兼容 SSE、认证拒绝、OOM 脱敏，以及唯一响应正文不出现在 SQLite 记录中。没有连接或压测任何真实模型服务。
- SQLite 升级到 schema version 4；旧库备份/数据保留、较新版本拒绝降级、损坏原件隔离后重建、迁移异常事务回滚、新库不产生多余备份，以及 v3→v4 创建成效确认表共 6 项专项测试通过。
- 新增本机成效证据专项测试：覆盖服务指纹 upsert 去重、`not_scored` 边界、固定枚举拒绝、聚合口径、预测误差统计、规范化报告哈希、错误/正确 `EXPORT REPORT` API 门禁，以及 PID、路径、IP、命令秘密、主机名、会话 ID 不进入导出报告。
- Docker Compose 合法标签项目归属、恶意/相对标签拒绝、Docker container ID/engine PID/host PID 语义，以及 WSL Linux PID/Windows PID/guest port forwarding 未验证语义均有契约测试；当前主机未把解析夹具冒充为真实 Docker/WSL 环境矩阵。
- `requirements-lock/` 共 20 份 SHA-256 wheel 锁，覆盖 Windows x64、macOS arm64/x86_64、Linux x86_64/aarch64 和 Python 3.10–3.12；离线 manifest 完整性与篡改拒绝测试通过。macOS 显式补齐过去由 pip 隐式解析的 `macholib==1.16.4`。
- 完整 unittest 共 168 项：167 通过，1 项因 Windows 不适用 POSIX mode-bit 断言而跳过；其中一次完整构建回归用时 24.492 秒，耗时会随本机负载波动。Ruff E4/E7/E9/F/B 零问题；Bandit 中/高风险为 0；公开目录审计零发现；20 份哈希锁离线校验与 `pip check` 通过。
- 内置浏览器在隔离源码实例验证“本机成效”弹窗、错误确认拒绝、正确导出代码、人工结果即时汇总和中英文切换；英文弹窗无汉字漏译，6 个指标卡均渲染，弹窗位于视口内、页面无横向溢出，浏览器控制台 warning/error 为空。浏览器自动化后端未上报 Blob 下载事件，但页面成功回执与 HTTP API 集成测试均通过。隔离实例退出后，含本机进程快照的临时数据已移入回收站。
- 新构建的 Windows EXE 在隔离实例复核本机成效 API：报告 schema 1、作用域为 `single_local_instance`、`external_adoption_verified=false`；错误大小写确认语返回 409，精确 `EXPORT REPORT` 返回带 SHA-256 完整性摘要的导出包，随后优雅退出。该验证不生成或上传外部采用数据；临时数据已移入回收站。
- `requirements.txt`、Windows、macOS、Linux 四组固定依赖经串行 `pip-audit` 均返回 `No known vulnerabilities found`。该结论是 2026-08-14 当次漏洞数据库结果，不是对未来漏洞或上游可信度的保证。
- Windows 未签名 EXE 在隔离临时数据目录完成回环原生验收：版本 0.8.3、35 个服务评估、168 个关系节点、149 条边、7 条本机依赖、17 个离线模型候选、2 个 GPU、0 个采集错误；Host/Origin/令牌拒绝、CSP、未确认基准/快照拒绝、空闲矩阵和正常退出通过。动态服务/关系数量不作为固定断言。
- Windows ZIP 通过 sidecar、单根目录、路径穿越/符号链接/体积限制、运行数据排除、39 个条目、CRLF/ASCII 启动脚本、许可证和 SPDX 2.3 SBOM 校验。macOS 源码构建包通过 149 个条目；Linux 源码构建包通过 158 个条目；三类归档均强制包含影响说明、维护者、路线图、治理、证据登记、案例模板与概览图，两类构建包同时包含锁文件与目标平台构建脚本。最终 SHA-256 只以各归档旁同名 `.sha256` 为准，避免文档与归档形成哈希自引用。
- `.github/workflows/ci.yml` 的六组合测试、质量/安全和三平台原生构建 job 已完成本地 YAML 解析与脚本级检查，但本轮没有初始化 Git 或上传仓库，因此没有 GitHub 托管 runner 通过证据。macOS/Linux 仍没有 Mach-O/ELF 真机验收。

上线结论与剩余阻断项见 [PRODUCTION-READINESS-0.8.3.md](PRODUCTION-READINESS-0.8.3.md)。

## 0.8.2 加固与上线审查（2026-08-14）

- 针对进程关停、旧实例控制、快照/回滚、日志游标、受信节点、项目保护规则、工作负载状态机、模型元数据和 SQLite 详情实施有界/失败关闭加固；新增 `tests/test_v082_hardening.py` 18 项专项测试，并扩充 actions/app 契约。
- 完整 unittest 共 147 项：146 通过，1 项因 Windows 不适用 POSIX mode-bit 断言而跳过；用时 20.280 秒。Ruff 选定的 E4/E7/E9/F/B 规则零问题；Bandit 高风险 0、中风险 0、低风险 15；Python 编译、`pip check`、公开目录审计和两个前端 JavaScript 语法检查均通过。
- 本地环境与 `requirements.txt` 两种 `pip-audit` 均返回 `No known vulnerabilities found`。构建链固定引导 `pip 26.2.1` 并使用 `--only-binary=:all:`；尚未启用按平台 wheel 哈希和 `--require-hashes`，已列为稳定发布前剩余项。
- Windows 源码验收使用隔离端口 `58577`：版本 0.8.2、33 个服务评估、162 个关系节点、145 条边、9 条本机依赖、17 个离线模型候选、2 个 GPU、0 个采集错误；Host/Origin/令牌拒绝、未确认基准/快照拒绝、空闲矩阵和正常退出通过。
- Windows 未签名 EXE 验收使用隔离端口 `58877`：34 个服务评估、162 个关系节点、144 条边、7 条本机依赖；回环独占、Host/Origin/令牌防护、CSP、离线容量规划、2 个 GPU、0 个采集错误和正常退出通过。服务与关系数量是动态本机样本，不是固定断言。
- 内置浏览器在隔离源码实例 `127.0.0.1:58482` 验证五个主工作区切换；中英文切换后刷新保持英文；1280×720 视口的页面宽度为 1265、无横向溢出；浏览器控制台 warning/error 为空。实例通过本工具停止入口正常退出，未执行用户服务关停或模型基准。
- Windows 0.8.2 ZIP 通过 sidecar 哈希、单根目录、路径穿越/符号链接/体积限制、运行数据排除、29 个归档条目、CRLF/ASCII 启动脚本、许可证、SPDX 2.3 SBOM 和新增上线文档验证。最终 SHA-256 以归档旁同名 `.sha256` 为准，避免归档内文档记录自身哈希造成自引用失效。
- macOS 0.8.2 源码构建包通过 107 个条目、LF 脚本、运行数据排除和归档契约；Linux 0.8.2 源码构建包通过 116 个条目和同类契约。两者最终 SHA-256 同样以各自 sidecar 为准。
- 当前主机没有已就绪的真实模型服务，本轮没有主动运行固定矩阵，也没有生成真实 TPS、TTFT、OOM 极限或预测误差；遵守安全边界，未实际停止任何用户服务。macOS/Linux 仍无 Mach-O/ELF 原生验收；NVIDIA/AMD 全系列、Docker/WSL 与各 Agent 真实版本矩阵也不能由当前 Windows 主机证明。

上线结论与剩余阻断项见 [PRODUCTION-READINESS-0.8.2.md](PRODUCTION-READINESS-0.8.2.md)。

## 0.8.1 增量验证（2026-08-14）

- 新增服务关系模型：项目、Agent 会话、服务、进程、监听端点与当前本机 TCP 客户端形成有向关系；通配监听只接受回环或本机接口地址，不把相同端口的公网连接误判为本机依赖。关系图与服务清单逐项生成关停决策。
- 新增“关停评估”面板：分别展示阻断项、当前客户端/端点/项目/Agent 影响、自动重启风险与人工恢复路径。Agent/IDE、Windows Service、Docker、WSL 和受保护对象保持只读；普通宿主开发/模型运行时仍需输入 `STOP <PID>`。本轮没有实际停止任何用户服务。
- 新增停止后有界验证：在一次停止动作后核对原 PID、子 PID、原端口和替代 PID，并区分 `stopped`、`relaunched`、`stop_incomplete`、`verification_partial`；不会因发现自动重启而二次停止。
- 新增固定、可预览、可中止的三段负载矩阵：并发 1/2/4，请求数 5/10/20，上下文上限 512/1024/2048，输出 32/32/64；启动前与每一步均检查 RAM、VRAM、温度和磁盘护栏。只允许一个活动矩阵，取消为协作式，不自动扩容或故意触发 OOM。预测失败会降级为只测实绩，不阻断预览。
- 新增实测反向校准：仅在硬件指纹、模型目录 ID、量化、并发、上下文和输出长度完全一致时应用样本；显示预测值、实测值、带符号误差和绝对误差。P95 TTFT 只有达到 20 个成功样本才标记样本充分。
- 自动化测试共运行 126 项，其中 125 项执行通过、1 项因 Windows 不适用 POSIX mode-bit 断言而跳过。Python AST、前端 JavaScript 语法、公开树审计均通过。测试清理发现的采集线程/SQLite 关闭竞态已修复，最终完整回归没有再出现该错误日志。
- Windows 源码验收使用隔离端口 `58848`：版本 0.8.1、34 个服务评估、165 个关系节点、146 条边、7 条本机依赖、空闲负载矩阵、空关停验证历史、17 个离线模型候选、2 个 GPU、0 个采集错误和正常退出均通过。
- Windows 未签名 EXE 验收使用隔离端口 `55232`：35 个服务评估、174 个关系节点、155 条边、10 条本机依赖；Host/Origin/控制令牌防护、CSP、容量 schema 1.1、空样本校准状态、端口回环独占和正常退出均通过。服务数量与关系数量是动态本机样本，不是固定基线。
- 内置浏览器在隔离实例 `127.0.0.1:49271` 完成英文界面复核：服务关系摘要和动态关停评估均无汉字漏译；Codex Desktop 显示只读阻断、影响与恢复信息且停止按钮不可用；隐藏负载矩阵对话框无汉字；1265 px 视口无横向溢出；控制台错误和警告为空。
- Windows 0.8.1 ZIP 已通过 sidecar 哈希、单根目录、路径穿越/符号链接/体积限制、运行数据排除、27 个归档条目、CRLF/ASCII 启动脚本、许可证、SPDX 2.3 SBOM 和 0.8.1 功能边界文档验证。最终哈希以归档旁同名 `.sha256` 为准。
- 当前主机没有已就绪的真实模型服务，因此本轮没有发起主动负载矩阵，也没有生成真实 TPS、TTFT、OOM 极限或预测误差结论；相关闭环由合成服务、持久化和容量匹配自动化测试覆盖。macOS/Linux 仅完成跨平台源码、构建脚本和归档契约调整，不构成 Mach-O/ELF 原生验收。

## 0.8.0 增量验证（2026-08-13）

- 增加可纠正归属链：本地规则与项目 `.vsg.yaml` 的解析、优先级、项目根目录限制、无效清单降级和服务指纹纠正均有自动化覆盖；规则删除必须输入 `DELETE RULE <编号>`。
- 增加生命周期/事故关联：服务启动、退出、疑似重启、暴露变化、归属变化、模型远端连接、资源阈值、日志事件和 15 秒遥测样本进入同一时间窗；历史远端地址只保留不可逆摘要，测试确认原始公网 IP 未持久化。
- 增加本机网络拓扑和逐服务 CPU/RSS/RAM/NVIDIA 进程显存映射；AMD/macOS/Docker/WSL 在无法可靠逐进程关联时保持未知，不使用猜测值。
- 增加需精确输入 `SCAN MODELS` 的模型资产盘点：GGUF/Safetensors/Ollama/Modelfile/config 有界解析、5000 文件/6 层/120 秒护栏、根目录/主目录/符号链接拒绝、分片与快速重复候选分组、相对路径持久化均有测试。
- 增加中英文“事件与模型资产”工作区、完整 7 引擎兼容矩阵、日志严重度/代码筛选和 `CLEAR HISTORY` 分类清除；前端静态契约确认不再调用浏览器 `prompt()`。
- 自动化测试 115 项通过，1 项跳过；跳过项是 Windows 不适用的 POSIX mode-bit 断言。Python 字节码编译、JavaScript 语法、公开树审计和固定依赖漏洞审计通过，后者未发现已知漏洞。
- Windows 源码验收使用隔离端口 `51965`，Windows 未签名 EXE 验收使用隔离端口 `49862`；版本均为 0.8.0，Host/Origin/令牌防护、CSP、17 个离线模型候选、2 个 GPU、0 个采集错误和正常退出均通过。
- 内置浏览器在全新隔离实例 `127.0.0.1:60680` 完成：中文第五工作区；显式扫描合成 GGUF + config 得到 1 个 Mixtral/MoE 候选；资产带入 `gguf` 引擎建议并显示 7 个完整候选；英文事件/引擎/硬件页无汉字漏译；34 条逐服务资源记录、2 块 AMD GPU 的 WDDM 显存分配、归属规则创建与显式删除、1265 px 无横向溢出均通过，页面控制台错误/警告为空。
- Windows ZIP 通过哈希、单根目录、路径穿越、运行数据排除、CRLF/ASCII 启动脚本、许可证与 SPDX 2.3 SBOM 验证；最终值以归档旁同名 `.sha256` 为准，避免在归档内部记录自身哈希造成自引用失效。
- macOS/Linux 源码构建包新增独立 `build-kit` 归档类型验证，分别检查 100/109 个条目、LF 脚本、源码/测试/构建链、运行数据排除和 sidecar 哈希。最终值同样以各自 `.sha256` 为准。这不构成 Mach-O/ELF 原生验收。

## 0.6.0 增量验证（2026-08-13）

- 新增持续模型日志监控：仅接受用户手工选择的普通日志文件，要求输入 `WATCH <PID>`，并绑定服务 PID、创建时间、指纹和日志文件身份。日志轮转、原子替换、服务退出、游标续读、单次读取上限和停止监控均有回归测试。
- 新增结构化日志事件：覆盖 CUDA/ROCm/Metal/Vulkan 错误、显存/内存 OOM、超时、上下文限制、认证失败、CPU 回退、工具模板错误、模型加载及进程退出；所有消息在持久化前脱敏，API 不返回日志绝对路径、原始行、文件指纹或消息散列。
- 新增硬件优化建议闭环：只根据实测 RAM/VRAM/磁盘/温度/服务健康/日志证据生成建议；每条包含证据、动作、代价、验证方法和置信度。缺失传感器保持“未知”，不以 TDP、型号表或经验值冒充实测。
- 新增推理引擎 Top 3：按操作系统、GPU 厂商、模型格式、优先目标、并发、上下文、功能要求及 WSL/Docker 明示选择筛选 llama.cpp、Ollama、MLX-LM、vLLM、SGLang、TensorRT-LLM、TabbyAPI/ExLlamaV2；硬不兼容候选被排除，不自动安装或改配置。
- 新增公平基准分组：只有模型标识、并发、上下文和输出长度完全一致的结果才进入同一比较组，避免把不同负载的 TPS/TTFT 直接排序。
- 内置浏览器完成“识别回环模型服务 → 输入确认监控日志 → 注入脱敏测试事件 → 识别 `CUDA_OOM` → 生成优化建议 → 停止监控”闭环验收；原始测试凭据未出现在页面，页面控制台错误/警告为空。
- Windows 源码与 0.6.0 未签名便携 EXE 均通过隔离临时数据目录验收；控制端口自动分配，没有停止、覆盖或复用既有实例。

## 0.5.0 增量验证（2026-08-13）

- 增加中英文界面资源、浏览器语言首选、本地持久化切换和动态 DOM 翻译层；静态页面、动态服务/硬件状态和后端证据/错误字符串共用翻译过程。
- Windows GPU 枚举增加 PNP 厂商/设备 ID；Linux 增加 PCI sysfs `class/vendor/device` 枚举；macOS 增加 `system_profiler` 厂商/设备字段。测试使用不在型号表中的未来 NVIDIA/AMD 设备 ID，仍能正确识别厂商。
- AMD SMI JSON 解析覆盖嵌套指标、带单位 VRAM、利用率、温度、风扇和功耗；NVIDIA 保留驱动枚举与实测字段。厂商遥测缺失时保持未知。
- Linux 目标增加启动/停止/打开脚本、无 sudo 的可选 XDG `.desktop` 入口、ufw/firewalld/nftables 只读适配、原生 PyInstaller 构建脚本、归档验证和运行验收脚本。
- Windows 当前主机可以验证源码、Windows EXE、ZIP 与 Linux/macOS 源码构建包完整性，但不能证明 Linux ELF、macOS Mach-O、桌面环境、驱动或真实传感器兼容性；这些仍必须在目标系统原生验收。

## 已通过

### Windows 当前主机

- 环境：Windows 11（10.0.26200）、CPython 3.12.10、PyInstaller 6.22.0。
- 自动化测试：115 项通过，1 项跳过。跳过项是只适用于 POSIX 的文件权限断言。
- Python 全包字节码编译、前端 JavaScript 语法检查和公共源码树审计：通过；审计未发现凭据、运行时数据库/日志、构建缓存、外部符号链接或本机绝对路径泄漏。
- 源码控制台验证和 Windows x64 未签名便携 EXE 验证：通过。
- 控制面仅监听 `127.0.0.1`；非法 `Host` / `Origin` 返回 HTTP 421，错误控制令牌返回 403，有效刷新返回 202，CSP 与实例 ID/运行时文件一致性通过。
- 快照契约为 schema `2.0`，宿主端口采集器为 `psutil`；CPU/RAM/磁盘、GPU 数组、五域体检、运行时探测数组和受信节点对象均通过 EXE API 验收，采集错误数为 0。
- 端口独占与旧实例保护回归通过：第二个控制服务不能复用同一端口；Windows 0.8.0 源码/EXE 验收分别使用隔离临时数据目录自动分配 `51965`/`49862`，没有停止或抢占既有实例。
- 正常退出和临时 `runtime.json` 清理：源码与 EXE 验收均通过。

### 当前主机硬件与体检页面

以下只是 2026-08-13 单次动态样本，不是性能基线：

- CPU 与 RAM 百分比、GPU 利用率和显存分配均在页面刷新时动态实测；首次 CPU 读数使用 100 ms 短采样，不把 `psutil` 的首次 0.0 哨兵值当作真实空闲。
- Radeon RX 9070 XT：Windows WDDM 适配器计数器可读到 GPU 利用率、专用显存分配（容量 16 GiB）和共享内存。WDDM 只有适配器 LUID，名称按独显优先做低置信启发式配对，界面明确标记部分实测，不能当作厂商级逐进程显存证据。
- 本机未提供 VSG 可用的普通用户温度、风扇和功耗接口，因此三个字段均显示“不可用”；电费不计算，没有用 TDP 或软件猜值替代实测。
- Windows Domain/Private/Public 防火墙配置文件均为启用状态；因为没有运行中的模型服务和非回环模型端口，本轮没有请求宽泛入站规则匹配。路由器/NAT 公网可达性仍未知。
- 当前未发现可探测模型服务，五域中 3 个为已知、模型性能与实际容量 2 个为未知；页面显示 `100*` 和“仍有 2 个领域证据不足”，不把已知项正常解释为全机完全验证。
- 内置浏览器实际打开 0.8.0：服务监控、模型容量、AI 体检、优化与引擎建议、事件与模型资产、设置和日志监控均通过。事件/优化/体检页切换到英文后可见 DOM 中汉字匹配数为 0；1265 px 桌面视口 `scrollWidth == clientWidth`，页面控制台错误/警告为空。

### 运行时、性能、安全与回滚

- Ollama 只读 `/api/ps` 适配器通过本机回环假服务测试，已核实加载模型、Q4_K_M、上下文与匿名读取姿态；llama.cpp、vLLM/SGLang/TGI/LM Studio/MLX-LM/Kobold/KTransformers/ComfyUI 的端点路由和保守降级由静态/解析测试覆盖。
- TensorRT-LLM、Text Generation WebUI/ExLlama 与 TabbyAPI 的进程/命令识别通过；配置结果把“后端”与 CUDA/ROCm/Vulkan/Metal“加速路径”分开。
- Prometheus 标签指标聚合、被动指标增量、服务端 token 计数、TTFT/TPS、并发/上下文限制、模型路径拒绝和无正文持久化测试通过。
- 显式 Ollama 合成短基准在回环假服务上通过；未输入 `BENCHMARK <端口>`、需要认证、内存过高、越界并发/上下文/输出和不匹配模型均由服务端拒绝逻辑覆盖。当前主机没有真实模型服务，因此没有真实 tok/s、TTFT、长上下文或并发结论。
- 日志/配置检查的 `INSPECT <PID>` 确认，以及持续日志监控的 `WATCH <PID>` 确认、类型/大小限制、`.env`/凭据/私钥拒绝、敏感值脱敏、轮转和进程身份绑定测试通过。
- 小配置 `SNAPSHOT`、原样本地副本、修改后 `RESTORE <文件名>`、回滚前副本和原子替换测试通过；大模型不复制与 512 MiB 哈希上限由代码/资产断言覆盖。
- 受信节点只允许手工配置的回环/私网地址；公开 IP/带凭据 URL 拒绝、DNS 解析到公开地址拒绝、连接已校验字面 IP 防 rebinding、回环 `/healthz` 探测均通过测试。
- 多个同指纹并发 Agent/Electron 进程不会形成重启风暴，单进程 PID/创建时间变化只累计一次重启；Docker RestartPolicy/RestartCount 解析通过合成测试。

### 模型容量规划

- EXE 已实际调用 `/api/model-planner/status` 与 `/api/model-planner/estimate`：通过。
- 离线目录加载 17 个候选；Dense/MoE、总参数/激活参数、上下文和量化字段校验：通过。
- 返回“物理装载 / 实际可用 / 目标 SLA”三层结论和 17 个完整候选：通过。
- 运行时模板固定使用 `127.0.0.1` 且 `will_execute=false`：通过。
- 未输入 `BENCHMARK` 且使用相对路径的 `llama-bench` 请求返回 HTTP 409；安全数值解析与不保存模型绝对路径测试通过。

### 依赖、许可证与归档

- 构建产物包含 CPython 3.12.10、psutil 7.2.2、PyInstaller 6.22.0 的许可证文本、SHA-256 清单和 SPDX 2.3 SBOM。
- Windows ZIP 已通过哈希 sidecar、路径穿越、符号链接、体积上限、必需文档、CRLF/ASCII 启动脚本、许可证清单、SBOM 版本和运行时数据排除检查。
- macOS 与 Linux 源码构建包已通过公开树审计、SHA-256 sidecar、单一根目录、必需构建/验收脚本和运行数据/缓存排除检查；Linux ZIP 条目已强制使用 POSIX `/`，避免 Windows `Compress-Archive` 的反斜杠路径兼容问题。
- 归档包含 AI 运行体检、模型容量方法、Agent 支持矩阵、架构、验证记录和 GitHub/官方资料调研。最终 ZIP 的 SHA-256 以归档旁同名 `.sha256` 文件为准。

## 尚未实机验证

以下项目是“证据不足”，不是已经判定不支持：

- Windows 10 x64；当前只在 Windows 11 主机完成源码与 EXE 路径验收。
- macOS 13+ Apple Silicon `arm64` 与 Intel `x86_64` 的原生构建、启动、端口采集、Metal/统一内存、传感器、Application Firewall、Gatekeeper 和便携包行为。Windows 不能生成可验证的原生 Mach-O，本轮只提供源码构建包和跨平台测试。
- Ubuntu 22.04+ / 24.04 等图形 Linux 的 `x86_64` 与 `aarch64` 原生 ELF 构建、浏览器启动、procfs/PCI sysfs 可见性、ufw/firewalld/nftables、NVIDIA/AMD 驱动遥测和 `.desktop` 集成。Windows 不能生成可验证的原生 ELF，本轮只提供源码构建包和跨平台测试。
- 真实 Docker Desktop / Docker Engine 容器、重启策略和端口归属；真实 WSL 发行版中的 `ss`/`netstat`、模型运行时和端口归属。
- 真实 Codex Desktop/CLI、Claude Code、Cursor、Windsurf、VS Code 终端、WorkBuddy、Hermes、OpenCode 等全部版本的端到端会话归属。自动化只替代不了真实产品升级后的验收。
- 真实 Ollama、llama.cpp、vLLM、SGLang、TensorRT-LLM、ExLlama、TGI、LM Studio、MLX-LM、ComfyUI 等服务的认证、模型加载、日志格式和 metrics 兼容性。
- Windows AMD/Vulkan、NVIDIA CUDA、macOS Metal、CPU-only、MoE 混合卸载的真实吞吐、P50/P95 TTFT、持续并发、上下文极限和 OOM 恢复。工具不会主动把机器推到 OOM 来证明上限。
- 温度、风扇、CPU/整机功耗和电费：当前 Windows 主机没有可用传感器，未得到真实数值；多 GPU、多节点、NUMA、视觉/语音编码器额外内存、投机解码与远程 KV cache 未完成实机验证。
- 公网暴露结论：VSG 只分析本机监听、防火墙和当前连接，不执行路由器/NAT 穿透或外网回连测试。
- GitHub Actions：工作流只完成本地文件审查；因为本轮不创建或推送仓库，没有 GitHub 托管运行证据。

## 复核命令

Windows 源码目录：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m ruff check vsg tests scripts
.\.venv\Scripts\python.exe -m bandit -r vsg scripts -x tests,build,dist,release -ll
.\.venv\Scripts\python.exe -m pip_audit --local
.\.venv\Scripts\python.exe -m pip_audit -r requirements.txt
.\.venv\Scripts\python.exe -m compileall -q vsg tests
node --check .\vsg\web\app.js
node --check .\vsg\web\i18n.js
.\.venv\Scripts\python.exe scripts\Audit-Public-Tree.py --root .
.\scripts\Validate-Windows.ps1 -Source -PythonPath .\.venv\Scripts\python.exe -ExpectedVersion 0.8.2
.\scripts\Validate-Windows.ps1 -Executable .\dist\VibeServiceGuardian.exe -ExpectedVersion 0.8.2
.\.venv\Scripts\python.exe scripts\Validate-Archive.py --zip .\release\Vibe-Service-Guardian-Windows-x64-0.8.2.zip --checksum .\release\Vibe-Service-Guardian-Windows-x64-0.8.2.zip.sha256 --expected-root Vibe-Service-Guardian-Windows-x64-0.8.2 --platform windows --version 0.8.2
```

macOS 必须在相应架构真机上执行：

```bash
./scripts/Build-Portable-macOS.sh
./release/Vibe-Service-Guardian-macOS-<arch>-0.8.2/scripts/Validate-macOS.sh
```

只有最后一条输出 `MACOS_NATIVE_VALIDATION_OK`，才应把对应架构标为实机通过。

Linux 必须在相应架构真机上执行：

```bash
chmod +x scripts/Build-Portable-Linux.sh scripts/Validate-Linux.sh
./scripts/Build-Portable-Linux.sh
./release/Vibe-Service-Guardian-Linux-<arch>-0.8.2/scripts/Validate-Linux.sh
```

只有构建脚本的归档验证通过，且最后一条输出 `Linux native validation passed.`，才应把对应架构标为实机通过。
