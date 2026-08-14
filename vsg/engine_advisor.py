from __future__ import annotations

from collections import defaultdict
from typing import Any


CATALOG_VERSION = "2026-08-13"

ENGINE_CATALOG: tuple[dict[str, Any], ...] = (
    {
        "id": "llama.cpp",
        "name": "llama.cpp",
        "formats": {"auto", "gguf"},
        "platforms": {"windows", "macos", "linux"},
        "vendors": {"NVIDIA", "AMD", "Apple", "Intel", "CPU", "Unknown"},
        "strengths": ["低依赖、跨平台", "GGUF 量化覆盖广", "CPU/CUDA/HIP/Metal/Vulkan 后端"],
        "strengths_en": ["Low-dependency and cross-platform", "Broad GGUF quantization coverage", "CPU/CUDA/HIP/Metal/Vulkan backends"],
        "source_url": "https://github.com/ggml-org/llama.cpp/blob/master/docs/build.md",
        "support_tier": "supported",
    },
    {
        "id": "ollama",
        "name": "Ollama",
        "formats": {"auto", "gguf", "ollama"},
        "platforms": {"windows", "macos", "linux"},
        "vendors": {"NVIDIA", "AMD", "Apple", "Intel", "CPU", "Unknown"},
        "strengths": ["安装与模型管理简单", "本机 API 开箱即用", "支持 CUDA、ROCm、Metal 与实验性 Vulkan"],
        "strengths_en": ["Simple setup and model management", "Ready-to-use local API", "CUDA, ROCm, Metal and experimental Vulkan paths"],
        "source_url": "https://docs.ollama.com/gpu",
        "support_tier": "supported",
    },
    {
        "id": "mlx-lm",
        "name": "MLX-LM",
        "formats": {"auto", "mlx", "safetensors"},
        "platforms": {"macos"},
        "vendors": {"Apple"},
        "strengths": ["Apple Silicon 统一内存路径", "适合 macOS 本机推理与微调"],
        "strengths_en": ["Apple Silicon unified-memory path", "Native macOS inference and fine-tuning"],
        "source_url": "https://github.com/ml-explore/mlx-lm",
        "support_tier": "supported",
    },
    {
        "id": "vllm",
        "name": "vLLM",
        "formats": {"auto", "safetensors", "gptq_awq"},
        "platforms": {"linux"},
        "vendors": {"NVIDIA", "AMD"},
        "strengths": ["高吞吐连续批处理", "面向多并发 OpenAI 兼容服务"],
        "strengths_en": ["High-throughput continuous batching", "OpenAI-compatible multi-user serving"],
        "source_url": "https://docs.vllm.ai/en/stable/getting_started/installation/gpu/",
        "support_tier": "supported_linux",
    },
    {
        "id": "sglang",
        "name": "SGLang",
        "formats": {"auto", "safetensors", "gptq_awq"},
        "platforms": {"linux"},
        "vendors": {"NVIDIA", "AMD"},
        "strengths": ["高吞吐服务与结构化生成", "适合复杂 Agent 推理工作负载"],
        "strengths_en": ["High-throughput serving and structured generation", "Suited to complex agent inference workloads"],
        "source_url": "https://docs.sglang.io/docs/get-started/install",
        "support_tier": "supported_linux",
    },
    {
        "id": "tensorrt-llm",
        "name": "TensorRT-LLM",
        "formats": {"auto", "safetensors"},
        "platforms": {"linux"},
        "vendors": {"NVIDIA"},
        "strengths": ["NVIDIA 平台深度优化", "适合固定模型的高性能生产部署"],
        "strengths_en": ["Deep NVIDIA platform optimization", "High-performance production deployment for fixed models"],
        "source_url": "https://nvidia.github.io/TensorRT-LLM/reference/support-matrix.html",
        "support_tier": "specialized",
    },
    {
        "id": "tabbyapi",
        "name": "TabbyAPI / ExLlamaV2",
        "formats": {"auto", "exl2", "gptq_awq"},
        "platforms": {"windows", "linux"},
        "vendors": {"NVIDIA"},
        "strengths": ["NVIDIA 消费卡上的 EXL2 路径", "偏向单机低延迟与量化模型"],
        "strengths_en": ["EXL2 path for NVIDIA consumer GPUs", "Single-node low latency and quantized models"],
        "source_url": "https://github.com/theroyallab/tabbyAPI",
        "support_tier": "community",
    },
)


FORMATS = {"auto", "gguf", "ollama", "safetensors", "mlx", "gptq_awq", "exl2"}
PRIORITIES = {"balanced", "ease", "throughput", "latency", "memory", "power"}
FEATURES = {"tools", "vision", "audio", "lora", "structured"}


def validate_engine_request(raw: dict[str, Any]) -> dict[str, Any]:
    model_format = str(raw.get("model_format") or "auto").lower()
    priority = str(raw.get("priority") or "balanced").lower()
    if model_format not in FORMATS:
        raise ValueError("model_format 无效")
    if priority not in PRIORITIES:
        raise ValueError("priority 无效")
    concurrency = int(raw.get("concurrency") or 1)
    context_tokens = int(raw.get("context_tokens") or 4096)
    if not 1 <= concurrency <= 256:
        raise ValueError("concurrency 必须在 1 到 256 之间")
    if not 512 <= context_tokens <= 1_000_000:
        raise ValueError("context_tokens 必须在 512 到 1000000 之间")
    raw_features = raw.get("features") or []
    if not isinstance(raw_features, list) or not all(isinstance(item, str) for item in raw_features):
        raise ValueError("features 必须是字符串数组")
    features = sorted({item.lower() for item in raw_features if item.lower() in FEATURES})
    return {
        "model_format": model_format,
        "priority": priority,
        "concurrency": concurrency,
        "context_tokens": context_tokens,
        "features": features,
        "allow_wsl": bool(raw.get("allow_wsl", False)),
        "allow_docker": bool(raw.get("allow_docker", False)),
    }


def _hardware_context(hardware: dict[str, Any]) -> tuple[str, set[str], list[dict[str, Any]]]:
    platform_key = str(hardware.get("platform", {}).get("key") or "unknown").lower()
    gpus = [item for item in hardware.get("gpus", []) if isinstance(item, dict)]
    vendors = {str(item.get("vendor") or "Unknown") for item in gpus}
    if not vendors:
        vendors = {"CPU"}
    return platform_key, vendors, gpus


def _runtime_map(runtimes: list[dict[str, Any]]) -> dict[str, bool]:
    aliases = {"mlx": "mlx-lm"}
    return {
        aliases.get(str(item.get("id")), str(item.get("id"))): bool(item.get("installed"))
        for item in runtimes
    }


def _runtime_details(runtimes: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    aliases = {"mlx": "mlx-lm"}
    return {
        aliases.get(str(item.get("id")), str(item.get("id"))): item
        for item in runtimes
    }


def recommend_engines(
    hardware: dict[str, Any], runtimes: list[dict[str, Any]], raw: dict[str, Any]
) -> dict[str, Any]:
    request = validate_engine_request(raw)
    platform_key, vendors, gpus = _hardware_context(hardware)
    installed = _runtime_map(runtimes)
    runtime_details = _runtime_details(runtimes)
    wsl_available = installed.get("wsl-runtime", False)
    candidates: list[dict[str, Any]] = []

    for engine in ENGINE_CATALOG:
        score = 50
        reasons: list[str] = []
        reasons_en: list[str] = []
        blockers: list[str] = []
        blockers_en: list[str] = []
        cautions: list[str] = []
        cautions_en: list[str] = []
        state = "compatible"
        checks: list[dict[str, Any]] = []
        unblock_steps: list[str] = []
        unblock_steps_en: list[str] = []

        if request["model_format"] not in engine["formats"]:
            blockers.append(f"不直接支持 {request['model_format']} 格式")
            blockers_en.append(f"No direct support for {request['model_format']} format")
            checks.append({"id": "format", "state": "failed", "detected": request["model_format"], "required": sorted(engine["formats"])})
            unblock_steps.append("选择该引擎直接支持的权重格式，或在离线副本上完成格式转换并校验精度")
            unblock_steps_en.append("Use a directly supported weight format, or convert an offline copy and validate accuracy")
        else:
            score += 16
            reasons.append("模型格式直接匹配")
            reasons_en.append("Direct model-format match")
            checks.append({"id": "format", "state": "passed", "detected": request["model_format"], "required": sorted(engine["formats"])})

        if platform_key not in engine["platforms"]:
            if (
                platform_key == "windows"
                and engine["id"] in {"vllm", "sglang", "tensorrt-llm"}
                and (
                    (request["allow_wsl"] and wsl_available)
                    or (request["allow_docker"] and "NVIDIA" in vendors)
                )
            ):
                state = "preview"
                score -= 8
                bridge = "WSL2" if request["allow_wsl"] and wsl_available else "Docker Desktop / WSL2 GPU"
                cautions.append(f"Windows 需经 {bridge}；VSG 未验证桥接环境内驱动、容器和包版本")
                cautions_en.append(f"Requires {bridge}; VSG has not verified drivers, containers, or package versions inside the bridge")
            else:
                blockers.append(f"当前平台 {platform_key} 不在该引擎的首选支持路径")
                blockers_en.append(f"{platform_key} is outside the engine's primary supported path")
                checks.append({"id": "platform", "state": "failed", "detected": platform_key, "required": sorted(engine["platforms"])})
                unblock_steps.append("改用该引擎官方支持的操作系统路径，或选择当前平台原生兼容的候选")
                unblock_steps_en.append("Use an officially supported OS path, or choose a candidate native to this platform")
        else:
            score += 12
            reasons.append("操作系统路径匹配")
            reasons_en.append("Operating-system path matches")
            checks.append({"id": "platform", "state": "passed", "detected": platform_key, "required": sorted(engine["platforms"])})

        if not any(item.get("id") == "platform" for item in checks):
            checks.append({"id": "platform", "state": "preview", "detected": platform_key, "required": sorted(engine["platforms"])})

        if not (vendors & engine["vendors"]):
            blockers.append(f"硬件厂商 {', '.join(sorted(vendors))} 与该引擎不匹配")
            blockers_en.append(f"Hardware vendor {', '.join(sorted(vendors))} does not match")
            checks.append({"id": "accelerator", "state": "failed", "detected": sorted(vendors), "required": sorted(engine["vendors"])})
            unblock_steps.append("选择支持当前加速器厂商的引擎；不要把未知显卡路径视为已兼容")
            unblock_steps_en.append("Choose an engine supporting the detected accelerator vendor; do not treat an unknown GPU path as compatible")
        else:
            score += 12
            reasons.append("加速器厂商路径匹配")
            reasons_en.append("Accelerator vendor path matches")
            checks.append({"id": "accelerator", "state": "passed", "detected": sorted(vendors), "required": sorted(engine["vendors"])})

        if engine["id"] == "tensorrt-llm":
            capabilities = [item.get("compute_capability") for item in gpus if item.get("vendor") == "NVIDIA"]
            known = [float(value) for value in capabilities if value is not None]
            if known and max(known) < 8.0:
                blockers.append("检测到的 NVIDIA Compute Capability 低于 8.0")
                blockers_en.append("Detected NVIDIA compute capability is below 8.0")
                checks.append({"id": "compute_capability", "state": "failed", "detected": max(known), "required": ">= 8.0 (catalog guardrail)"})
            elif known:
                checks.append({"id": "compute_capability", "state": "passed", "detected": max(known), "required": ">= 8.0 (catalog guardrail)"})
            elif not known and "NVIDIA" in vendors:
                cautions.append("未获得 Compute Capability，必须对照官方支持矩阵复核")
                cautions_en.append("Compute capability is unknown; verify against the official support matrix")
                checks.append({"id": "compute_capability", "state": "unknown", "detected": None, "required": "verify official support matrix"})
                unblock_steps.append("从 NVIDIA 工具读取 Compute Capability，并逐项对照 TensorRT-LLM 官方支持矩阵")
                unblock_steps_en.append("Read compute capability from NVIDIA tooling and verify it against the TensorRT-LLM support matrix")

        if request["concurrency"] >= 4:
            if engine["id"] in {"vllm", "sglang", "tensorrt-llm"}:
                score += 15
                reasons.append("目标并发适合批处理服务引擎")
                reasons_en.append("Target concurrency benefits from a batching server")
            elif engine["id"] in {"llama.cpp", "ollama", "mlx-lm"}:
                score -= min(12, request["concurrency"])
                cautions.append("多并发上限必须用同模型、同上下文实测")
                cautions_en.append("Concurrency must be benchmarked with the same model and context")

        priority = request["priority"]
        if priority == "ease" and engine["id"] == "ollama":
            score += 20
            reasons.append("易用性优先")
            reasons_en.append("Best fit for ease of use")
        if priority == "throughput" and engine["id"] in {"vllm", "sglang", "tensorrt-llm"}:
            score += 20
        if priority == "latency" and engine["id"] in {"llama.cpp", "mlx-lm", "tabbyapi", "tensorrt-llm"}:
            score += 14
        if priority in {"memory", "power"} and engine["id"] in {"llama.cpp", "mlx-lm"}:
            score += 14
        if priority == "balanced" and engine["id"] in {"llama.cpp", "ollama", "vllm"}:
            score += 8

        if engine["id"] == "vllm" and request["concurrency"] >= 4:
            score += 4
        if engine["id"] == "sglang" and "structured" in request["features"]:
            score += 5
        if engine["id"] == "tensorrt-llm" and priority not in {"throughput", "latency"}:
            score -= 8

        if installed.get(engine["id"], False):
            score += 12
            reasons.append("本机已检测到该运行时")
            reasons_en.append("Runtime detected locally")
            checks.append({"id": "runtime", "state": "passed", "detected": runtime_details.get(engine["id"], {}).get("version") or "detected", "required": "installed"})
        else:
            checks.append({"id": "runtime", "state": "unknown", "detected": None, "required": "install and validate"})
            unblock_steps.append("按上游文档安装到隔离环境后，重新读取版本并运行同负载短基准")
            unblock_steps_en.append("Install from upstream documentation in an isolated environment, then detect the version and run an identical-load benchmark")

        if request["features"]:
            cautions.append("工具、多模态、LoRA 与结构化输出还取决于模型、模板和具体引擎版本")
            cautions_en.append("Tools, multimodality, LoRA and structured output also depend on the model, template and exact engine version")

        if blockers:
            state = "incompatible"
            score = min(score, 35)
        rank_score = score
        score = max(0, min(100, score))
        candidates.append(
            {
                "id": engine["id"],
                "name": engine["name"],
                "score": score,
                "state": state,
                "installed": installed.get(engine["id"], False),
                "support_tier": engine["support_tier"],
                "reasons": reasons,
                "reasons_en": reasons_en,
                "blockers": blockers,
                "blockers_en": blockers_en,
                "cautions": cautions,
                "cautions_en": cautions_en,
                "strengths": engine["strengths"],
                "strengths_en": engine["strengths_en"],
                "source_url": engine["source_url"],
                "requires_benchmark": True,
                "automatic_install": False,
                "compatibility_checks": checks,
                "detected": {
                    "runtime_version": runtime_details.get(engine["id"], {}).get("version"),
                    "runtime_detection": runtime_details.get(engine["id"], {}).get("detection") or "not_found",
                    "driver_versions": sorted({str(item.get("driver_version")) for item in gpus if item.get("driver_version")}),
                    "compute_capabilities": sorted({str(item.get("compute_capability")) for item in gpus if item.get("compute_capability")}),
                },
                "unblock_steps": list(dict.fromkeys(unblock_steps)),
                "unblock_steps_en": list(dict.fromkeys(unblock_steps_en)),
                "_rank_score": rank_score,
            }
        )

    candidates.sort(
        key=lambda item: (
            item["state"] == "incompatible",
            -item["_rank_score"],
            not item["installed"],
            item["name"],
        )
    )
    for item in candidates:
        item.pop("_rank_score", None)
    eligible = [item for item in candidates if item["state"] != "incompatible"]
    return {
        "catalog_version": CATALOG_VERSION,
        "request": request,
        "hardware_context": {
            "platform": platform_key,
            "vendors": sorted(vendors),
            "gpu_count": len(gpus),
        },
        "top3": eligible[:3],
        "candidates": candidates,
        "conclusion": "建议只代表兼容性与工作负载拟合；最终顺序必须由同模型、同量化、同上下文、同并发的本机基准确认。",
        "conclusion_en": "Recommendations reflect compatibility and workload fit only; confirm the final ranking with same-model, same-quantization, same-context and same-concurrency local benchmarks.",
    }


def compare_service_benchmarks(items: list[dict[str, Any]]) -> dict[str, Any]:
    cohorts: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for item in items:
        model_name = str(item.get("model_name") or "").strip()
        if not model_name:
            continue
        key = (
            model_name,
            int(item.get("concurrency") or 0),
            int(item.get("requested_context_tokens") or 0),
            int(item.get("requested_output_tokens") or 0),
        )
        cohorts[key].append(item)

    result: list[dict[str, Any]] = []
    for key, rows in cohorts.items():
        latest_by_runtime: dict[str, dict[str, Any]] = {}
        for row in sorted(rows, key=lambda value: float(value.get("created_at") or 0), reverse=True):
            latest_by_runtime.setdefault(str(row.get("runtime") or "unknown"), row)
        values = list(latest_by_runtime.values())
        comparable = len(values) >= 2
        ranked = sorted(
            values,
            key=lambda item: (
                int(item.get("successful_requests") or 0) <= 0,
                -(float(item.get("generation_tps") or 0)),
                float(item.get("ttft_seconds") or 10**9),
            ),
        )
        result.append(
            {
                "model_name": key[0],
                "concurrency": key[1],
                "context_tokens": key[2],
                "output_tokens": key[3],
                "comparable": comparable,
                "reason": "同模型、同并发、同上下文和同输出长度" if comparable else "只有一个运行时样本，不能横向排名",
                "rows": ranked,
            }
        )
    result.sort(
        key=lambda item: max((float(row.get("created_at") or 0) for row in item["rows"]), default=0),
        reverse=True,
    )
    return {
        "cohorts": result[:20],
        "comparable_cohorts": sum(bool(item["comparable"]) for item in result),
        "method": "只有模型名、并发、上下文和输出长度全部一致的不同运行时结果才可比较",
    }
