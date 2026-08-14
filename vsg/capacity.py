from __future__ import annotations

import math
from typing import Any, Iterable


GIB = 1024**3

QUANTIZATIONS: dict[str, dict[str, Any]] = {
    "Q2_K": {"bits_per_weight": 2.70, "quality_rank": 1, "label": "Q2（仅装载上限）"},
    "Q3_K_M": {"bits_per_weight": 3.50, "quality_rank": 2, "label": "Q3（容量优先）"},
    "Q4_K_M": {"bits_per_weight": 4.80, "quality_rank": 3, "label": "Q4（推荐起点）"},
    "Q5_K_M": {"bits_per_weight": 5.70, "quality_rank": 4, "label": "Q5（质量平衡）"},
    "Q6_K": {"bits_per_weight": 6.60, "quality_rank": 5, "label": "Q6（质量优先）"},
    "Q8_0": {"bits_per_weight": 8.50, "quality_rank": 6, "label": "Q8（高精度）"},
    "FP16": {"bits_per_weight": 16.0, "quality_rank": 7, "label": "FP16/BF16"},
}

PREFERENCES = {"balanced", "performance", "quality", "capacity"}
RUNTIMES = {"auto", "llama.cpp", "ollama", "mlx", "vllm", "sglang"}


class CapacityError(ValueError):
    """Invalid workload or an impossible planner request."""


def _number(
    raw: dict[str, Any],
    key: str,
    default: float,
    minimum: float,
    maximum: float,
    integer: bool = False,
) -> float | int:
    value = raw.get(key, default)
    if isinstance(value, bool):
        raise CapacityError(f"{key} 必须是数字")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise CapacityError(f"{key} 必须是数字") from exc
    if not math.isfinite(number) or number < minimum or number > maximum:
        raise CapacityError(f"{key} 必须在 {minimum:g}–{maximum:g} 之间")
    if integer:
        if not number.is_integer():
            raise CapacityError(f"{key} 必须是整数")
        return int(number)
    return number


def validate_workload(raw: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise CapacityError("工作负载必须是对象")
    total_users = _number(raw, "total_users", 10, 1, 100_000, True)
    concurrency = _number(raw, "concurrency", 2, 1, 128, True)
    prompt_tokens = _number(raw, "prompt_tokens", 1024, 16, 262_144, True)
    context_tokens = _number(raw, "context_tokens", 8192, 512, 1_048_576, True)
    output_tokens = _number(raw, "output_tokens", 512, 1, 65_536, True)
    target_tps = _number(raw, "target_tps_per_user", 8, 0.5, 200)
    target_ttft = _number(raw, "target_ttft_seconds", 5, 0.2, 300)
    kv_bits = _number(raw, "kv_cache_bits", 16, 4, 16, True)
    preference = str(raw.get("preference") or "balanced")
    runtime = str(raw.get("runtime") or "auto")
    if concurrency > total_users:
        raise CapacityError("peak concurrency 不能大于 total_users")
    if prompt_tokens + output_tokens > context_tokens:
        raise CapacityError("prompt_tokens + output_tokens 不能大于 context_tokens")
    if kv_bits not in {4, 8, 16}:
        raise CapacityError("kv_cache_bits 仅支持 4、8 或 16")
    if preference not in PREFERENCES:
        raise CapacityError("preference 无效")
    if runtime not in RUNTIMES:
        raise CapacityError("runtime 无效")
    return {
        "total_users": total_users,
        "concurrency": concurrency,
        "prompt_tokens": prompt_tokens,
        "context_tokens": context_tokens,
        "output_tokens": output_tokens,
        "target_tps_per_user": round(float(target_tps), 2),
        "target_ttft_seconds": round(float(target_ttft), 2),
        "kv_cache_bits": kv_bits,
        "preference": preference,
        "runtime": runtime,
    }


def _memory_targets(hardware: dict[str, Any]) -> dict[str, Any]:
    memory = hardware.get("memory", {})
    total_ram = float(memory.get("total_gib") or 0)
    available_ram = float(memory.get("available_gib") or 0)
    system_reserve = max(6.0, total_ram * 0.18)
    ram_clean = max(0.0, total_ram - system_reserve)
    ram_current = max(0.0, min(ram_clean, available_ram - 2.0))
    cpu_bandwidth = float(hardware.get("cpu", {}).get("memory_bandwidth_gbps_estimate") or 45)
    gpus = [item for item in hardware.get("gpus", []) if not item.get("integrated") or item.get("unified_memory")]
    unified = bool(memory.get("unified"))

    if unified:
        apple_gpu = next((item for item in gpus if item.get("vendor") == "Apple"), None)
        bandwidth = float((apple_gpu or {}).get("bandwidth_gbps") or cpu_bandwidth)
        return {
            "primary": {
                "mode": "unified",
                "label": "Apple 统一内存",
                "clean_budget_gib": ram_clean,
                "current_budget_gib": ram_current,
                "bandwidth_gbps": bandwidth,
                "backend": "metal",
                "support_tier": "supported",
                "confidence": (apple_gpu or {}).get("confidence", "medium"),
            },
            "cpu": {
                "mode": "cpu",
                "label": "CPU / 系统内存",
                "clean_budget_gib": ram_clean,
                "current_budget_gib": ram_current,
                "bandwidth_gbps": cpu_bandwidth,
                "backend": "cpu",
                "support_tier": "supported",
                "confidence": "medium",
            },
            "hybrid": None,
            "system_reserve_gib": system_reserve,
        }

    discrete = [item for item in gpus if item.get("memory_total_gib")]
    primary_gpu = max(discrete, key=lambda item: float(item.get("memory_total_gib") or 0), default=None)
    cpu_target = {
        "mode": "cpu",
        "label": "CPU / 系统内存",
        "clean_budget_gib": ram_clean,
        "current_budget_gib": ram_current,
        "bandwidth_gbps": cpu_bandwidth,
        "backend": "cpu",
        "support_tier": "supported",
        "confidence": "medium",
    }
    if not primary_gpu:
        return {"primary": cpu_target, "cpu": cpu_target, "hybrid": None, "system_reserve_gib": system_reserve}

    gpu_total = float(primary_gpu["memory_total_gib"])
    gpu_free = primary_gpu.get("memory_free_gib")
    clean_gpu = gpu_total * 0.90
    current_gpu = min(clean_gpu, float(gpu_free) * 0.92) if gpu_free is not None else gpu_total * 0.82
    gpu_bandwidth = float(primary_gpu.get("bandwidth_gbps") or max(180, gpu_total * 28))
    accelerator = {
        "mode": "accelerator",
        "label": f"{primary_gpu.get('name')} 独立显存",
        "clean_budget_gib": clean_gpu,
        "current_budget_gib": current_gpu,
        "bandwidth_gbps": gpu_bandwidth,
        "backend": primary_gpu.get("backend") or "unknown",
        "support_tier": primary_gpu.get("support_tier") or "preview",
        "confidence": primary_gpu.get("confidence") or "low",
        "device_name": primary_gpu.get("name"),
    }
    pcie_limited_bandwidth = max(cpu_bandwidth, min(96.0, gpu_bandwidth * 0.20))
    hybrid = {
        "mode": "hybrid",
        "label": f"{primary_gpu.get('name')} + 系统内存混合卸载",
        "clean_budget_gib": clean_gpu + ram_clean,
        "current_budget_gib": current_gpu + ram_current,
        "accelerator_budget_gib": clean_gpu,
        "bandwidth_gbps": pcie_limited_bandwidth,
        "backend": primary_gpu.get("backend") or "unknown",
        "support_tier": "experimental" if primary_gpu.get("support_tier") == "experimental" else "preview",
        "confidence": "low",
        "device_name": primary_gpu.get("name"),
    }
    return {
        "primary": accelerator,
        "cpu": cpu_target,
        "hybrid": hybrid,
        "system_reserve_gib": system_reserve,
    }


def memory_breakdown(
    model: dict[str, Any],
    quantization: str,
    context_tokens: int,
    concurrency: int,
    kv_cache_bits: int,
) -> dict[str, float]:
    quant = QUANTIZATIONS[quantization]
    total_params = float(model["total_params_b"])
    active_params = float(model["active_params_b"])
    bpw = float(quant["bits_per_weight"])
    weights = total_params * 1_000_000_000 * bpw / 8 / GIB * 1.06
    kv_per_sequence = (
        float(model["kv_cache_kib_per_token_fp16"])
        * 1024
        * context_tokens
        * (kv_cache_bits / 16)
        / GIB
    )
    kv_total = kv_per_sequence * concurrency
    workspace = max(0.65, weights * 0.045 + active_params * 0.018 + concurrency * 0.08)
    return {
        "weights_gib": round(weights, 3),
        "kv_cache_per_sequence_gib": round(kv_per_sequence, 3),
        "kv_cache_gib": round(kv_total, 3),
        "workspace_gib": round(workspace, 3),
        "required_gib": round(weights + kv_total + workspace, 3),
    }


def _select_execution(memory: dict[str, float], targets: dict[str, Any]) -> dict[str, Any]:
    required = memory["required_gib"]
    primary = targets["primary"]
    cpu = targets["cpu"]
    hybrid = targets.get("hybrid")
    options: list[dict[str, Any]] = []
    if required <= primary["clean_budget_gib"]:
        options.append(primary)
    if primary["mode"] != "unified" and required <= cpu["clean_budget_gib"]:
        options.append(cpu)
    if hybrid and required <= hybrid["clean_budget_gib"]:
        options.append(hybrid)
    if not options:
        return {
            **primary,
            "clean_fit": False,
            "current_fit": False,
            "headroom_gib": round(primary["clean_budget_gib"] - required, 2),
            "current_headroom_gib": round(primary["current_budget_gib"] - required, 2),
        }
    order = {"accelerator": 0, "unified": 0, "hybrid": 1, "cpu": 2}
    selected = sorted(options, key=lambda item: order[item["mode"]])[0]
    return {
        **selected,
        "clean_fit": True,
        "current_fit": required <= selected["current_budget_gib"],
        "headroom_gib": round(selected["clean_budget_gib"] - required, 2),
        "current_headroom_gib": round(selected["current_budget_gib"] - required, 2),
    }


def _confidence_factor(model: dict[str, Any], execution: dict[str, Any], calibration_scope: str | None) -> tuple[str, float]:
    if calibration_scope == "workload_exact":
        return "calibrated_workload", 0.12
    if calibration_scope == "single_request_base":
        return "calibrated_base", 0.18
    model_confidence = model.get("kv_estimate_confidence", "low")
    hardware_confidence = execution.get("confidence", "low")
    if model_confidence == "high" and hardware_confidence == "high":
        return "high", 0.22
    if model_confidence in {"high", "medium"} and hardware_confidence in {"high", "medium"}:
        return "medium", 0.35
    return "low", 0.50


def _matching_benchmark(
    benchmarks: Iterable[dict[str, Any]],
    model_id: str,
    quantization: str,
    fingerprint: str,
    workload: dict[str, Any],
) -> dict[str, Any] | None:
    matches: list[dict[str, Any]] = []
    for item in benchmarks:
        if (
            item.get("model_id") == model_id
            and item.get("quantization") == quantization
            and item.get("hardware_fingerprint") == fingerprint
            and float(item.get("generation_tps") or 0) > 0
        ):
            matches.append(item)
    if not matches:
        return None
    matches.sort(key=lambda item: float(item.get("created_at") or item.get("id") or 0), reverse=True)
    exact = next(
        (
            item
            for item in matches
            if item.get("calibration_source") == "service_matrix"
            and int(item.get("concurrency") or 0) == int(workload["concurrency"])
            and int(item.get("requested_context_tokens") or 0) == int(workload["context_tokens"])
            and int(item.get("requested_output_tokens") or 0) == int(workload["output_tokens"])
        ),
        None,
    )
    if exact:
        return exact
    base = next(
        (
            item
            for item in matches
            if item.get("calibration_source") != "service_matrix"
            or int(item.get("concurrency") or 1) == 1
        ),
        None,
    )
    return base


def _throughput(
    model: dict[str, Any],
    quantization: str,
    execution: dict[str, Any],
    workload: dict[str, Any],
    benchmark: dict[str, Any] | None,
) -> dict[str, Any]:
    bpw = float(QUANTIZATIONS[quantization]["bits_per_weight"])
    active_weight_gb = max(0.08, float(model["active_params_b"]) * bpw / 8 * 1.03)
    backend = execution.get("backend")
    mode = execution.get("mode")
    efficiency = {
        "cuda": 0.50,
        "metal": 0.60,
        "vulkan": 0.36,
        "cpu": 0.40,
    }.get(str(backend), 0.32)
    if mode == "hybrid":
        efficiency *= 0.62
    if model.get("architecture") == "moe":
        efficiency *= 0.62
    if model.get("architecture") == "hybrid":
        efficiency *= 0.78
    predicted_single_generation = float(execution["bandwidth_gbps"]) / active_weight_gb * efficiency
    concurrency = int(workload["concurrency"])
    batch_gain = 1 + min(0.65, math.log2(max(1, concurrency)) * 0.18)
    predicted_aggregate_generation = predicted_single_generation * batch_gain
    predicted_per_user = predicted_aggregate_generation / concurrency
    prompt_multiplier = 5.0 if backend in {"cuda", "metal", "vulkan"} else 3.0
    predicted_prompt_tps = max(predicted_single_generation * prompt_multiplier, predicted_aggregate_generation * 2.4)
    predicted_ttft = 0.22 + float(workload["prompt_tokens"]) * concurrency / predicted_prompt_tps
    aggregate_generation = predicted_aggregate_generation
    per_user = predicted_per_user
    prompt_tps = predicted_prompt_tps
    ttft = predicted_ttft
    calibration_scope: str | None = None
    if benchmark:
        benchmark_concurrency = int(benchmark.get("concurrency") or 1)
        exact_workload = (
            benchmark.get("calibration_source") == "service_matrix"
            and benchmark_concurrency == concurrency
            and int(benchmark.get("requested_context_tokens") or 0) == int(workload["context_tokens"])
            and int(benchmark.get("requested_output_tokens") or 0) == int(workload["output_tokens"])
        )
        if exact_workload:
            calibration_scope = "workload_exact"
            per_user = float(benchmark["generation_tps"])
            aggregate_generation = float(
                benchmark.get("aggregate_generation_tps") or per_user * concurrency
            )
            if benchmark.get("prompt_tps") is not None:
                prompt_tps = float(benchmark["prompt_tps"])
            if benchmark.get("ttft_seconds") is not None:
                ttft = float(benchmark["ttft_seconds"])
        elif benchmark.get("calibration_source") != "service_matrix" or benchmark_concurrency == 1:
            calibration_scope = "single_request_base"
            measured_single = float(benchmark["generation_tps"])
            aggregate_generation = measured_single * batch_gain
            per_user = aggregate_generation / concurrency
            if benchmark.get("prompt_tps") is not None:
                prompt_tps = max(float(benchmark["prompt_tps"]), aggregate_generation * 2.4)
            else:
                prompt_tps = max(measured_single * prompt_multiplier, aggregate_generation * 2.4)
            ttft = 0.22 + float(workload["prompt_tokens"]) * concurrency / prompt_tps
    confidence, spread = _confidence_factor(model, execution, calibration_scope)
    low = max(0.05, per_user * (1 - spread))
    high = per_user * (1 + spread)
    ttft_low = max(0.05, ttft * (1 - spread * 0.45))
    ttft_high = ttft * (1 + spread)
    return {
        "aggregate_generation_tps": round(aggregate_generation, 2),
        "per_user_generation_tps": {
            "low": round(low, 2),
            "expected": round(per_user, 2),
            "high": round(high, 2),
        },
        "prompt_processing_tps": round(prompt_tps, 2),
        "ttft_seconds": {
            "low": round(ttft_low, 2),
            "expected": round(ttft, 2),
            "high": round(ttft_high, 2),
        },
        "confidence": confidence,
        "calibrated": calibration_scope is not None,
        "calibration_id": benchmark.get("id") if benchmark and calibration_scope else None,
        "calibration": (
            {
                "scope": calibration_scope,
                "source": benchmark.get("calibration_source") or "llama_bench",
                "sample_id": benchmark.get("id"),
                "sample_count": int(benchmark.get("sample_count") or 1),
                "predicted_generation_tps": round(predicted_per_user, 3),
                "measured_generation_tps": round(per_user, 3),
                "signed_error_percent": round((per_user - predicted_per_user) / predicted_per_user * 100, 2)
                if predicted_per_user > 0
                else None,
                "absolute_error_percent": round(abs(per_user - predicted_per_user) / predicted_per_user * 100, 2)
                if predicted_per_user > 0
                else None,
                "predicted_ttft_seconds": round(predicted_ttft, 3),
                "measured_or_calibrated_ttft_seconds": round(ttft, 3),
                "workload_match": calibration_scope == "workload_exact",
            }
            if calibration_scope
            else None
        ),
    }


def _max_concurrency(
    model: dict[str, Any],
    quantization: str,
    execution: dict[str, Any],
    workload: dict[str, Any],
    benchmark: dict[str, Any] | None,
) -> dict[str, int]:
    base = memory_breakdown(
        model,
        quantization,
        int(workload["context_tokens"]),
        1,
        int(workload["kv_cache_bits"]),
    )
    per_slot = base["kv_cache_per_sequence_gib"] + 0.08
    fixed = base["weights_gib"] + max(0.65, base["workspace_gib"] - 0.08)
    available = float(execution["clean_budget_gib"])
    memory_limit = max(0, min(128, math.floor((available - fixed) / max(0.001, per_slot))))
    performance_limit = 0
    for candidate_concurrency in range(1, 129):
        candidate_workload = {**workload, "concurrency": candidate_concurrency}
        perf = _throughput(model, quantization, execution, candidate_workload, benchmark)
        if perf["per_user_generation_tps"]["expected"] >= float(workload["target_tps_per_user"]):
            performance_limit = candidate_concurrency
    effective = min(memory_limit, performance_limit)
    return {"memory": memory_limit, "performance": performance_limit, "effective": effective}


def _variant(
    model: dict[str, Any],
    quantization: str,
    hardware: dict[str, Any],
    targets: dict[str, Any],
    workload: dict[str, Any],
    benchmarks: list[dict[str, Any]],
) -> dict[str, Any]:
    memory = memory_breakdown(
        model,
        quantization,
        int(workload["context_tokens"]),
        int(workload["concurrency"]),
        int(workload["kv_cache_bits"]),
    )
    execution = _select_execution(memory, targets)
    context_supported = int(workload["context_tokens"]) <= int(model["native_context_tokens"])
    benchmark = _matching_benchmark(
        benchmarks,
        str(model["id"]),
        quantization,
        str(hardware.get("hardware_fingerprint") or ""),
        workload,
    )
    performance = _throughput(model, quantization, execution, workload, benchmark)
    limits = _max_concurrency(model, quantization, execution, workload, benchmark) if execution["clean_fit"] else {"memory": 0, "performance": 0, "effective": 0}
    tps_ok = performance["per_user_generation_tps"]["expected"] >= workload["target_tps_per_user"]
    ttft_ok = performance["ttft_seconds"]["expected"] <= workload["target_ttft_seconds"]
    current_fit = bool(execution["current_fit"])
    meets_sla = bool(execution["clean_fit"] and current_fit and context_supported and tps_ok and ttft_ok)
    risks: list[str] = []
    if not context_supported:
        risks.append("请求上下文超过模型发布方标称上限")
    if not execution["clean_fit"]:
        risks.append("即使释放可用资源，估算内存仍不足")
    elif not current_fit:
        risks.append("理论可装载，但当前空闲内存不足；需先释放其他进程")
    if execution.get("mode") == "hybrid":
        risks.append("需要 CPU/GPU 混合卸载，吞吐对 PCIe、内存带宽和后端实现高度敏感")
    if execution.get("support_tier") == "experimental":
        risks.append("当前加速后端为实验性支持，必须以本机实测为准")
    if not tps_ok:
        risks.append("预计单用户生成速度低于目标")
    if not ttft_ok:
        risks.append("预计首字延迟高于目标")
    if int(workload["kv_cache_bits"]) < 16:
        risks.append("KV 缓存量化可能降低质量或受运行时支持限制")
    status = "meets_sla" if meets_sla else "current_pressure" if execution["clean_fit"] and not current_fit else "performance_risk" if execution["clean_fit"] and context_supported else "does_not_fit"
    if not context_supported:
        status = "context_unsupported"
    completion = float(workload["output_tokens"]) / max(0.05, performance["per_user_generation_tps"]["expected"])
    max_users = max(0, limits["effective"])
    burst_waves = math.ceil(int(workload["total_users"]) / max_users) if max_users else None
    return {
        "model_id": model["id"],
        "name": model["name"],
        "publisher": model["publisher"],
        "architecture": model["architecture"],
        "total_params_b": model["total_params_b"],
        "active_params_b": model["active_params_b"],
        "native_context_tokens": model["native_context_tokens"],
        "license": model["license"],
        "source_url": model["source_url"],
        "model_url": model.get("model_url"),
        "strengths": model.get("strengths", []),
        "runtimes": model.get("runtimes", []),
        "quantization": quantization,
        "quantization_label": QUANTIZATIONS[quantization]["label"],
        "bits_per_weight": QUANTIZATIONS[quantization]["bits_per_weight"],
        "memory": memory,
        "execution": execution,
        "performance": performance,
        "max_concurrency": limits,
        "completion_seconds_per_request": round(completion, 1),
        "burst_waves_for_total_users": burst_waves,
        "context_supported": context_supported,
        "meets_sla": meets_sla,
        "status": status,
        "risks": risks,
        "notes": model.get("notes"),
        "kv_estimate_confidence": model.get("kv_estimate_confidence"),
    }


def _pick_preferred(variants: list[dict[str, Any]], preference: str) -> dict[str, Any]:
    fits = [item for item in variants if item["execution"]["clean_fit"] and item["context_supported"]]
    pool = fits or variants
    by_quant = {item["quantization"]: item for item in pool}
    if preference == "quality":
        order = ["FP16", "Q8_0", "Q6_K", "Q5_K_M", "Q4_K_M", "Q3_K_M", "Q2_K"]
    elif preference == "capacity":
        order = ["Q3_K_M", "Q4_K_M", "Q5_K_M", "Q6_K", "Q8_0", "FP16", "Q2_K"]
    elif preference == "performance":
        order = ["Q4_K_M", "Q3_K_M", "Q5_K_M", "Q6_K", "Q8_0", "FP16", "Q2_K"]
    else:
        order = ["Q5_K_M", "Q4_K_M", "Q6_K", "Q3_K_M", "Q8_0", "FP16", "Q2_K"]
    for quantization in order:
        if quantization in by_quant:
            return by_quant[quantization]
    return variants[0]


def _ceiling(
    variants: Iterable[dict[str, Any]],
    predicate,
    level: str,
    empty_reason: str,
) -> dict[str, Any]:
    matches = [item for item in variants if predicate(item)]
    if not matches:
        return {"level": level, "available": False, "reason": empty_reason}
    selected = max(
        matches,
        key=lambda item: (
            float(item["total_params_b"]),
            QUANTIZATIONS[item["quantization"]]["quality_rank"],
        ),
    )
    return {
        "level": level,
        "available": True,
        "model_id": selected["model_id"],
        "name": selected["name"],
        "architecture": selected["architecture"],
        "total_params_b": selected["total_params_b"],
        "active_params_b": selected["active_params_b"],
        "quantization": selected["quantization"],
        "required_gib": selected["memory"]["required_gib"],
        "execution_mode": selected["execution"]["mode"],
        "current_fit": selected["execution"]["current_fit"],
        "per_user_tps": selected["performance"]["per_user_generation_tps"],
        "ttft_seconds": selected["performance"]["ttft_seconds"],
        "confidence": selected["performance"]["confidence"],
        "risks": selected["risks"],
    }


def _select_runtime(
    candidate: dict[str, Any] | None,
    hardware: dict[str, Any],
    workload: dict[str, Any],
    runtimes: list[dict[str, Any]],
) -> str:
    requested = workload["runtime"]
    if requested != "auto":
        return requested
    compatible = set(candidate.get("runtimes", [])) if candidate else set()
    installed = {item["id"] for item in runtimes if item.get("installed")}
    platform_key = hardware.get("platform", {}).get("key")
    backend = candidate.get("execution", {}).get("backend") if candidate else "cpu"
    concurrency = int(workload["concurrency"])
    if platform_key == "macos" and "mlx" in compatible:
        return "mlx" if "mlx" in installed else "llama.cpp"
    if backend == "cuda" and concurrency >= 4 and "vllm" in compatible and "vllm" in installed:
        return "vllm"
    if "llama.cpp" in compatible:
        return "llama.cpp"
    if "ollama" in compatible:
        return "ollama"
    if "vllm" in compatible:
        return "vllm"
    return "llama.cpp"


def _command_plan(
    candidate: dict[str, Any] | None,
    runtime: str,
    hardware: dict[str, Any],
    workload: dict[str, Any],
    runtimes: list[dict[str, Any]],
) -> dict[str, Any]:
    if not candidate:
        return {
            "runtime": runtime,
            "available": False,
            "installed": False,
            "support_tier": "unavailable",
            "template_only": True,
            "will_execute": False,
            "command": [],
            "display": "",
            "binding": "127.0.0.1:8080",
            "reason": "没有满足 SLA 的模型，先降低并发/上下文/速度目标",
        }
    installed = next((item for item in runtimes if item.get("id") == runtime), None)
    context = int(workload["context_tokens"])
    concurrency = int(workload["concurrency"])
    platform_key = hardware.get("platform", {}).get("key")
    mode = candidate["execution"]["mode"]
    if runtime == "llama.cpp":
        gpu_layers = "999" if mode in {"accelerator", "unified"} else "<按显存实测调整>" if mode == "hybrid" else "0"
        command = [
            "llama-server",
            "-m", "<本地 GGUF 绝对路径>",
            "-c", str(context * concurrency),
            "-np", str(concurrency),
            "-ngl", gpu_layers,
            "--host", "127.0.0.1",
            "--port", "8080",
            "--flash-attn", "on",
        ]
        display = " ".join(f'\"{item}\"' if " " in item or "<" in item else item for item in command)
    elif runtime == "ollama":
        if platform_key == "windows":
            display = f"$env:OLLAMA_NUM_PARALLEL='{concurrency}'\n$env:OLLAMA_CONTEXT_LENGTH='{context}'\nollama serve\nollama run <本地模型名>"
        else:
            display = f"OLLAMA_NUM_PARALLEL={concurrency} OLLAMA_CONTEXT_LENGTH={context} ollama serve\nollama run <本地模型名>"
        command = []
    elif runtime == "mlx":
        command = ["mlx_lm.server", "--model", "<本地或已确认的 MLX 模型>", "--host", "127.0.0.1", "--port", "8080"]
        display = " ".join(f'\"{item}\"' if " " in item or "<" in item else item for item in command)
    elif runtime == "vllm":
        command = [
            "vllm", "serve", "<本地模型目录>", "--host", "127.0.0.1",
            "--max-model-len", str(context), "--max-num-seqs", str(concurrency),
            "--gpu-memory-utilization", "0.90",
        ]
        display = " ".join(f'\"{item}\"' if " " in item or "<" in item else item for item in command)
    else:
        command = [
            "python", "-m", "sglang.launch_server", "--model-path", "<本地模型目录>",
            "--host", "127.0.0.1", "--context-length", str(context),
        ]
        display = " ".join(f'\"{item}\"' if " " in item or "<" in item else item for item in command)
    return {
        "runtime": runtime,
        "available": True,
        "installed": bool(installed and installed.get("installed")),
        "support_tier": (installed or {}).get("support_tier", "recommended_not_detected"),
        "template_only": True,
        "will_execute": False,
        "command": command,
        "display": display,
        "binding": "127.0.0.1:8080",
        "reason": "命令只生成不执行；模型路径、量化文件和运行时版本需由用户复核。",
    }


def estimate_capacity(
    hardware: dict[str, Any],
    catalog: dict[str, Any],
    raw_workload: dict[str, Any],
    runtimes: list[dict[str, Any]] | None = None,
    benchmarks: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    workload = validate_workload(raw_workload)
    runtime_items = runtimes or []
    benchmark_items = benchmarks or []
    targets = _memory_targets(hardware)
    all_variants: list[dict[str, Any]] = []
    preferred_candidates: list[dict[str, Any]] = []
    for model in catalog.get("models", []):
        variants = [
            _variant(model, quantization, hardware, targets, workload, benchmark_items)
            for quantization in QUANTIZATIONS
        ]
        all_variants.extend(variants)
        preferred_candidates.append(_pick_preferred(variants, workload["preference"]))

    physical = _ceiling(
        all_variants,
        lambda item: item["execution"]["clean_fit"] and item["context_supported"],
        "physical",
        "没有目录内模型能在请求上下文下装入当前内存预算",
    )
    usable = _ceiling(
        all_variants,
        lambda item: (
            item["execution"]["clean_fit"]
            and item["execution"]["current_fit"]
            and item["context_supported"]
            and QUANTIZATIONS[item["quantization"]]["quality_rank"] >= 2
            and item["performance"]["per_user_generation_tps"]["expected"] >= 2.0
            and item["performance"]["ttft_seconds"]["expected"] <= 30.0
        ),
        "usable",
        "没有同时达到当前可装入、Q3 以上、最低 2 token/s/用户和 30 秒 TTFT 的目录内模型",
    )
    sla = _ceiling(
        all_variants,
        lambda item: item["meets_sla"] and QUANTIZATIONS[item["quantization"]]["quality_rank"] >= 3,
        "sla",
        "没有目录内模型在 Q4 以上同时满足当前内存、并发、速度和 TTFT 目标",
    )

    candidates = sorted(
        preferred_candidates,
        key=lambda item: (
            item["meets_sla"],
            item["execution"]["current_fit"],
            item["execution"]["clean_fit"],
            float(item["total_params_b"]),
        ),
        reverse=True,
    )
    selected = next(
        (
            item
            for item in all_variants
            if item["model_id"] == sla.get("model_id")
            and item["quantization"] == sla.get("quantization")
            and item["meets_sla"]
        ),
        None,
    )
    if selected:
        candidates = [
            selected if item["model_id"] == selected["model_id"] else item for item in candidates
        ]
        candidates = sorted(
            candidates,
            key=lambda item: (
                item["meets_sla"],
                item["execution"]["current_fit"],
                item["execution"]["clean_fit"],
                float(item["total_params_b"]),
            ),
            reverse=True,
        )
    if selected is None:
        selected = next((item for item in candidates if item["meets_sla"]), None)
    if selected is None:
        selected = next((item for item in candidates if item["execution"]["current_fit"] and item["context_supported"]), None)
    runtime = _select_runtime(selected, hardware, workload, runtime_items)
    command = _command_plan(selected, runtime, hardware, workload, runtime_items)
    bottlenecks: list[str] = []
    if not sla["available"]:
        bottlenecks.append("当前目标没有 Q4 以上 SLA 解；候选表会说明是内存、吞吐、TTFT 还是上下文限制")
    if workload["concurrency"] > 1:
        bottlenecks.append("并发会同时放大 KV 缓存，并分摊总生成吞吐；总用户数本身不直接消耗推理内存")
    if targets["primary"].get("support_tier") == "experimental":
        bottlenecks.append("主加速器后端为实验性支持，范围预测不能替代本机 llama-bench 实测")
    if int(workload["context_tokens"]) >= 32768:
        bottlenecks.append("长上下文预留按每个并发槽位计算，实际平均上下文更短时可提高吞吐和并发")
    calibrated_candidates = [item for item in candidates if (item.get("performance") or {}).get("calibrated")]
    calibration_errors = [
        float(item["performance"]["calibration"]["absolute_error_percent"])
        for item in calibrated_candidates
        if (((item.get("performance") or {}).get("calibration") or {}).get("absolute_error_percent")) is not None
    ]
    return {
        "schema_version": "1.1",
        "workload": workload,
        "hardware_fingerprint": hardware.get("hardware_fingerprint"),
        "budgets": {
            "primary": targets["primary"],
            "cpu": targets["cpu"],
            "hybrid": targets.get("hybrid"),
            "system_reserve_gib": round(float(targets["system_reserve_gib"]), 2),
        },
        "ceilings": {"physical": physical, "usable": usable, "sla": sla},
        "selected_model_id": selected.get("model_id") if selected else None,
        "candidates": candidates,
        "calibration_summary": {
            "calibrated_candidates": len(calibrated_candidates),
            "available_samples": len(benchmark_items),
            "mean_absolute_prediction_error_percent": round(sum(calibration_errors) / len(calibration_errors), 2)
            if calibration_errors
            else None,
            "selected": (selected.get("performance") or {}).get("calibration") if selected else None,
            "method": "优先使用同模型、量化、硬件、并发和上下文的服务矩阵；否则只用单并发样本校准基础生成速度",
        },
        "runtime_plan": command,
        "bottlenecks": bottlenecks,
        "assumptions": [
            "权重内存按总参数 × 量化 bits/weight × 6% 容器/张量开销估算；MoE 不能用激活参数替代总权重。",
            "KV 缓存按目录中的工程系数 × 上下文窗口 × 并发 × KV 位宽估算，并非厂商实测。",
            "吞吐以可用内存带宽、激活权重和后端效率估算，返回范围而非承诺值；提示词处理、采样、驱动和热限制会改变结果。",
            "理论上限按释放其他大进程后的预算计算；SLA 上限还要求当前空闲预算可容纳。",
            "目录是带日期的离线快照，不联网、不自动下载模型、不代表质量排行榜。",
        ],
    }


def predict_model_variant(
    hardware: dict[str, Any],
    catalog: dict[str, Any],
    model_id: str,
    quantization: str,
    raw_workload: dict[str, Any],
) -> dict[str, Any] | None:
    """Return an uncalibrated prediction for one exact model/quantization.

    Workload-matrix previews use this baseline so the subsequent measured
    result can report prediction error without feeding the sample into its own
    prediction.
    """

    model = next(
        (item for item in catalog.get("models", []) if str(item.get("id")) == str(model_id)),
        None,
    )
    if not model or quantization not in QUANTIZATIONS:
        return None
    concurrency = int(raw_workload.get("concurrency") or 1)
    context_tokens = int(raw_workload.get("context_tokens") or 512)
    output_tokens = int(raw_workload.get("output_tokens") or 32)
    prompt_tokens = int(raw_workload.get("prompt_tokens") or context_tokens)
    workload = validate_workload(
        {
            "total_users": concurrency,
            "concurrency": concurrency,
            "prompt_tokens": min(prompt_tokens, max(1, context_tokens - output_tokens)),
            "context_tokens": context_tokens,
            "output_tokens": output_tokens,
            "target_tps_per_user": 1,
            "target_ttft_seconds": 60,
            "preference": "balanced",
            "runtime": "auto",
            "kv_cache_bits": 16,
        }
    )
    variant = _variant(
        model,
        quantization,
        hardware,
        _memory_targets(hardware),
        workload,
        [],
    )
    return {
        "model_id": model_id,
        "quantization": quantization,
        "concurrency": concurrency,
        "context_tokens": context_tokens,
        "aggregate_generation_tps": variant["performance"]["aggregate_generation_tps"],
        "per_user_generation_tps": variant["performance"]["per_user_generation_tps"],
        "ttft_seconds": variant["performance"]["ttft_seconds"],
        "confidence": variant["performance"]["confidence"],
        "source": "uncalibrated capacity formula",
    }
