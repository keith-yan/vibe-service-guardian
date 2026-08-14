# 本机开放权重模型容量规划方法

本模块解决的不是“哪个模型最好”，而是三个可验证、不能混为一谈的问题：

1. **装得下吗**：权重、KV 缓存、运行时工作区和系统保留相加后是否超过可用内存；
2. **跑得动吗**：装得下之后，预计生成速度和首字延迟是否还具备交互价值；
3. **支撑业务吗**：在指定峰值并发、上下文和每用户 SLA 下，哪个模型仍然满足目标。

VSG 从这些基本约束向上推导，不把“模型名称中的 B”“MoE 激活参数”“总用户数”“显存”彼此偷换。结果是规划范围，不是厂商基准或容量承诺。

## 1. 输入与输出

### 硬件输入

- CPU 名称、物理/逻辑核心和内存带宽档位估算；
- 系统总内存、当前可用内存和系统保留；
- 独立 GPU 型号、总/空闲显存、后端与带宽档位；
- Apple Silicon 的统一内存，不与系统内存重复相加；
- 已检测到的 Ollama、llama.cpp、llama-bench、LM Studio CLI、MLX-LM、vLLM、SGLang 和 Windows WSL 桥接。

Windows NVIDIA 显存优先读取 `nvidia-smi`。Windows CIM 的 `AdapterRAM` 经常受 32 位字段影响而把大显存截断到约 4 GiB，因此 AMD/Intel GPU 不直接采用该值：已知型号使用内置硬件档位并标为中置信度，未知型号显示“显存未知”。Windows AMD/Intel 推理路径首版统一标记为实验性。

### 业务输入

- 计划用户数；
- 峰值并发数；
- 平均输入 tokens、平均输出 tokens；
- 每个并发槽位的上下文窗口；
- 目标生成速度（tokens/s/用户）；
- 目标 TTFT（time to first token）；
- 质量/性能/容量偏好、目标运行时和 KV 缓存位宽。

总用户数不直接占用模型内存。峰值并发才决定同时存在的序列数，并线性放大 KV 缓存；总用户数用于显示一次突发需要多少处理波次。

### 决策输出

- **物理装载上限**：在释放其他大进程后的内存预算内可以装载；允许低量化和混合卸载，不保证速度。
- **实际可用上限**：上下文受支持、当前预算可装入、至少 Q3、预计达到最低 2 tokens/s/用户且 TTFT 不超过 30 秒；仍可能不满足用户指定 SLA。
- **目标 SLA 上限**：至少 Q4，并同时满足当前空闲预算、峰值并发、目标生成速度和 TTFT。
- 每个目录模型的一套偏好量化方案，以及权重/KV/工作区拆解、执行模式、吞吐范围、最大并发、风险和命令模板。

## 2. 内存模型

### 2.1 权重

设：

- `P_total` 为总参数量（十亿参数）；
- `bpw` 为量化后的平均 bits per weight；
- `1.06` 为张量容器、元数据和对齐的保守开销。

则：

```text
weights_GiB = P_total × 1,000,000,000 × bpw / 8 / 2^30 × 1.06
```

VSG 当前采用的工程档位为 Q2 2.70 bpw、Q3 3.50、Q4 4.80、Q5 5.70、Q6 6.60、Q8 8.50、FP16/BF16 16.0。实际 GGUF 文件会因张量类型、词表、视觉编码器和量化实现不同而偏离。

关键约束：MoE 的权重能否装入必须使用 `P_total`，不能用 `P_active`。例如 36B-A3B 仍要容纳约 36B 参数的权重；“A3B”只意味着每个 token 主要激活约 3B 参数，不意味着权重文件只有 3B。

### 2.2 KV 缓存

模型目录为每个架构保存 `k`（FP16 条件下每 token 的 KiB 工程系数）和置信度。设：

```text
kv_GiB = k × 1024 × context_tokens × concurrency × (kv_bits / 16) / 2^30
```

这解释了为什么“模型本身能装下”仍可能在长上下文或高并发时 OOM。Ollama 官方 FAQ 也明确说明并行请求会按并行数放大上下文内存；llama.cpp server 的 `-c` 是所有并行槽位共享的总上下文预算。

目录里的 KV 系数是保守容量近似，不是厂商披露值。不同运行时的分页 KV、滑动窗口、混合注意力、KV 量化和前缀缓存会改变真实占用，因此界面必须保留低/中置信度标记。

### 2.3 工作区与系统保留

```text
workspace_GiB = max(0.65,
                    weights_GiB × 0.045
                    + P_active × 0.018
                    + concurrency × 0.08)

required_GiB = weights_GiB + kv_GiB + workspace_GiB
```

系统内存预算会预留 `max(6 GiB, 总内存 × 18%)`。独立 GPU 的理论预算默认取总显存 90%；当前预算优先使用 `nvidia-smi` 空闲显存，否则只能采用保守档位估算。Apple Silicon 使用一份统一内存预算，绝不把“系统内存 + 显存”相加。

独立 GPU 放不下但 GPU 与系统内存合计可容纳时，VSG 可标记为“混合卸载”。它只是物理可行路径：PCIe 传输和 CPU 内存带宽通常会显著降低速度。

## 3. 吞吐与并发模型

自回归生成近似受每 token 需要读取的激活权重和有效内存带宽约束。VSG 使用：

```text
active_weight_GB = P_active × bpw / 8 × 1.03
single_generation_tps ≈ effective_bandwidth_GBps
                        / active_weight_GB
                        × backend_efficiency
```

后端效率按 CUDA、Metal、Vulkan、CPU 和混合卸载分档；MoE/混合架构另加路由与实现折损。并发批处理会提高总吞吐，但不会按并发数线性提高：

```text
batch_gain = 1 + min(0.65, log2(concurrency) × 0.18)
aggregate_tps = single_generation_tps × batch_gain
per_user_tps = aggregate_tps / concurrency
```

TTFT 根据平均输入量、提示词处理速度和并发近似。模型执行图编译、首次加载、前缀缓存命中、采样、工具调用、热降频和排队都可能造成额外延迟。未校准时，VSG 按硬件/KV 置信度返回约 ±22%、±35% 或 ±50% 的范围；实验性路径通常是低置信度。

目标下最大并发同时取两种约束的较小值：

1. 剩余内存还能容纳多少个 KV 槽位；
2. 总吞吐分摊后仍能达到目标 tokens/s/用户的最大槽位数。

## 4. Dense 与 MoE 的业务解释

| 维度 | Dense | MoE |
|---|---|---|
| 权重内存 | 总参数 | **仍是总参数** |
| 每 token 主要计算/读权重 | 总参数 | 激活参数 + 路由/共享层开销 |
| 本地优势 | 实现成熟、行为稳定 | 同等总参数下可能有更高生成吞吐 |
| 本地风险 | 大 Dense 很快受带宽限制 | 权重仍大；专家布局、卸载和运行时支持差异很大 |

因此，MoE 可能出现“装不下但理论吞吐很高”，或“勉强混合卸载后远低于理论吞吐”的情况。VSG 将装载和吞吐分开显示，避免给出一个含义不明的“可运行”标签。

Gemma 4 E2B/E4B 是官方归类的 Dense 模型，`E` 表示 effective parameters：Per-Layer Embeddings 使快速查表的总权重高于有效计算参数。VSG 对它们仍按含嵌入的总参数计算内存、按有效参数近似吞吐，并明确标成 Dense，而不是误标为 MoE。

## 5. 本地校准

当检测到 `llama-bench` 时，用户可选择目录模型、量化和本地 GGUF 路径，并输入确认短语 `BENCHMARK`。VSG 固定运行短测试：128 个提示 tokens、64 个生成 tokens、1 次重复、JSON 输出，最长 300 秒。

安全与隐私约束：

- 使用参数数组启动，不经过 shell；
- 只接受存在的绝对 `.gguf` 普通文件；
- 不联网、不下载、不启动长期服务；
- SQLite 只保存硬件指纹、目录模型 ID、量化、**文件名**、文件大小和速度，不保存绝对路径；
- 解析结果删除上游可能输出的模型路径等字符串，只保留数值字段；
- 同一硬件指纹、模型和量化的最近生成速度会替换带宽公式的单路生成基线，置信度显示为 `calibrated`。

一次短基准仍不等于生产验收。正式部署应继续做真实上下文、真实并发、持续 15–30 分钟的热稳定性、P50/P95 TTFT、错误率与内存峰值测试。

0.8.1 还允许对已就绪、无需凭据的回环模型服务运行固定工作负载矩阵。用户先预览单请求 5 次、双并发 10 次、四并发 20 次，再输入 `BENCHMARK PLAN <端口>`；每步开始前重查服务身份及 RAM、VRAM、温度、磁盘护栏。矩阵不会自动增加并发/上下文，也不以 OOM 为目标，可协作式中止尚未发出的请求波次。

若用户把运行时模型映射到目录模型和量化，系统会保存**未使用本次样本**的公式预测，并展示：

```text
带符号误差 = (实测 - 预测) / 预测 × 100%
绝对误差   = |实测 - 预测| / 预测 × 100%
```

同负载校准必须同时匹配硬件指纹、目录模型、量化、并发、上下文和输出长度。单请求服务样本或 `llama-bench` 只校准基础生成速度；目标并发下的批处理收益仍属工程外推，因此界面标为 `calibrated_base`，不会冒充同负载实测。矩阵的 P95 至少需要 20 个成功样本才标记为统计充分，资源峰值是周期采样而非驱动级连续追踪。

## 6. 运行方案边界

VSG 可生成以下命令模板：

- llama.cpp：总上下文 `context × concurrency`、并行槽位、GPU layers、flash attention、回环地址；
- Ollama：`OLLAMA_NUM_PARALLEL` 与 `OLLAMA_CONTEXT_LENGTH`；
- macOS MLX-LM：本地模型和回环服务；
- vLLM：`max-model-len`、`max-num-seqs` 和 GPU 内存利用率；
- SGLang：首版预览模板。

所有模板固定绑定 `127.0.0.1`，带模型占位符，只复制、不执行。VSG 不自动下载权重，不调用 Hugging Face Token，不启动推理服务。用户启动后，现有“服务监控”页会像其他本机服务一样发现其 PID 和监听端口，从而闭合“规划—启动—观察—遗留复核”链路。

## 7. 离线目录与来源

内置文件 `vsg/catalog/models-2026-08-11.json` 是日期固定、非穷尽的首批目录。发布方事实主要来自：

- [OpenAI gpt-oss-20b](https://developers.openai.com/api/docs/models/gpt-oss-20b) 与 [gpt-oss-120b](https://developers.openai.com/api/docs/models/gpt-oss-120b)；
- [Qwen3.5 发布方集合](https://huggingface.co/collections/Qwen/qwen35) 与 [Qwen3.5-27B 模型卡](https://huggingface.co/Qwen/Qwen3.5-27B)；
- [Google Gemma 4 模型卡](https://ai.google.dev/gemma/docs/core/model_card_4)；
- [Mistral Small 4 模型卡](https://docs.mistral.ai/models/model-cards/mistral-small-4-0-26-03)；
- [DeepSeek-V4-Flash 发布方模型卡](https://huggingface.co/deepseek-ai/DeepSeek-V4-Flash)。

运行时方法参考：

- [Ollama FAQ：并发与上下文内存](https://docs.ollama.com/faq)；
- [llama.cpp server：并行解码与连续批处理](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md)；
- [llama-bench JSON 基准](https://github.com/ggml-org/llama.cpp/blob/master/tools/llama-bench/README.md)；
- [vLLM 配置：KV cache 与 GPU memory utilization](https://docs.vllm.ai/en/latest/api/vllm/config/)；
- [Apple MLX 统一内存框架](https://github.com/ml-explore/mlx)。

研究中还审阅了 [llmfit](https://github.com/AlexsJones/llmfit) 的硬件画像/模型匹配思路与 [GPUStack](https://github.com/gpustack/gpustack) 的大规模部署方向。VSG 没有复制这些项目的源代码：首版保持单机、本地、可解释和无账号依赖，并额外把 KV×并发、MoE 总/激活参数、当前空闲预算、SLA 与本地校准纳入同一决策模型。

## 8. 已知限制

- 模型目录会过期，新增模型或运行时支持不会自动出现；
- KV 工程系数和 GPU 型号档位不是官方基准；
- Windows AMD/Intel GPU、Intel Mac GPU、WSL vLLM/SGLang 尚未完成代表性实机矩阵；
- 多 GPU 拓扑、NVLink/PCIe 代际、张量并行通信、CPU NUMA、视觉编码器内存和 speculative decoding 尚未建模；
- 不自动扫描用户已有模型目录；只有用户明确选择有界目录并输入 `SCAN MODELS` 才盘点；
- “license” 是目录元数据，不构成法律意见，使用前仍需阅读对应模型条款。

这些限制意味着：VSG 可以缩小选择空间、暴露瓶颈并给出可复核起点，但最终生产结论必须由目标模型、目标量化、目标运行时和真实请求压测完成。
