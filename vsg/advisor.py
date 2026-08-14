from __future__ import annotations

from typing import Any


SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}


def _item(
    rule_id: str,
    domain: str,
    severity: str,
    title: str,
    title_en: str,
    evidence: str,
    evidence_en: str,
    action: str,
    action_en: str,
    validation: str,
    validation_en: str,
    *,
    confidence: str = "high",
    tradeoff: str = "变更前后应使用同一负载复测",
    tradeoff_en: str = "Re-test with an identical workload before and after the change",
) -> dict[str, Any]:
    return {
        "id": rule_id,
        "domain": domain,
        "severity": severity,
        "title": title,
        "title_en": title_en,
        "evidence": evidence,
        "evidence_en": evidence_en,
        "action": action,
        "action_en": action_en,
        "validation": validation,
        "validation_en": validation_en,
        "tradeoff": tradeoff,
        "tradeoff_en": tradeoff_en,
        "confidence": confidence,
        "automatic": False,
    }


def generate_hardware_advice(
    hardware: dict[str, Any],
    telemetry: dict[str, Any],
    workload: dict[str, Any] | None = None,
    runtime_probes: list[dict[str, Any]] | None = None,
    log_events: list[dict[str, Any]] | None = None,
    low_disk_free_gib: int = 50,
) -> dict[str, Any]:
    workload = workload or {}
    runtime_probes = runtime_probes or []
    log_events = log_events or []
    recommendations: list[dict[str, Any]] = []
    unknowns: list[str] = []

    memory = telemetry.get("memory") or hardware.get("memory") or {}
    memory_percent = memory.get("used_percent")
    if isinstance(memory_percent, (int, float)) and memory_percent >= 85:
        severity = "critical" if memory_percent >= 93 else "high"
        recommendations.append(
            _item(
                "ram-pressure",
                "capacity",
                severity,
                "系统内存压力过高",
                "System memory pressure is high",
                f"RAM 已使用 {float(memory_percent):.1f}%",
                f"RAM usage is {float(memory_percent):.1f}%",
                "先降低并发或上下文，再考虑更低量化；不要以增加交换文件代替容量验证。",
                "Reduce concurrency or context first, then test a lower quantization; do not treat swap growth as capacity proof.",
                "复测峰值 RAM、TTFT、tokens/s，并确认无交换抖动和 OOM。",
                "Re-test peak RAM, TTFT and tokens/s; confirm there is no swap thrashing or OOM.",
            )
        )
    elif memory_percent is None:
        unknowns.append("未获得系统内存占用率")

    disks = telemetry.get("disks") or []
    low_disks = [
        item
        for item in disks
        if isinstance(item.get("free_gib"), (int, float))
        and float(item["free_gib"]) < float(low_disk_free_gib)
    ]
    if low_disks:
        detail = ", ".join(
            f"{item.get('root') or item.get('mountpoint') or item.get('device')}: {float(item['free_gib']):.1f} GiB"
            for item in low_disks[:4]
        )
        recommendations.append(
            _item(
                "disk-headroom",
                "storage",
                "high",
                "模型磁盘余量不足",
                "Model storage headroom is low",
                f"低于 {low_disk_free_gib} GiB 阈值：{detail}",
                f"Below the {low_disk_free_gib} GiB threshold: {detail}",
                "清理可再下载的重复权重和过期缓存，保留配置快照；下载或转换新模型前预留权重大小至少 1.5 倍空间。",
                "Remove re-downloadable duplicate weights and stale caches while keeping config snapshots; reserve at least 1.5x model size before download or conversion.",
                "检查目标卷剩余空间，并做一次小文件写入与模型加载验证。",
                "Check free space on the target volume, then verify a small write and model load.",
            )
        )

    gpus = telemetry.get("gpus") or hardware.get("gpus") or []
    measured_vram = [
        item for item in gpus if isinstance(item.get("memory_util_percent"), (int, float))
    ]
    hot_vram = [item for item in measured_vram if float(item["memory_util_percent"]) >= 88]
    if hot_vram:
        peak = max(float(item["memory_util_percent"]) for item in hot_vram)
        recommendations.append(
            _item(
                "vram-pressure",
                "capacity",
                "critical" if peak >= 96 else "high",
                "显存余量过小",
                "VRAM headroom is too small",
                f"实测最高显存占用率 {peak:.1f}%",
                f"Measured peak VRAM utilization is {peak:.1f}%",
                "按优先顺序降低并发、上下文/KV 缓存、GPU offload 层数或量化位宽；每次只改一个变量。",
                "Reduce concurrency, context/KV cache, GPU-offload layers or quantization precision in that order; change one variable at a time.",
                "运行相同并发短基准并观察峰值 VRAM 与 CUDA/ROCm/Metal 日志。",
                "Run the same-concurrency short benchmark and observe peak VRAM plus CUDA/ROCm/Metal logs.",
            )
        )
    elif gpus and not measured_vram:
        unknowns.append("GPU 已识别，但当前接口没有提供可置信的实时显存占用率")

    temperatures = [
        float(item["temperature_c"])
        for item in gpus
        if isinstance(item.get("temperature_c"), (int, float))
    ]
    sensor_state = telemetry.get("sensors") or {}
    temperatures.extend(
        float(item["current_c"])
        for item in sensor_state.get("temperatures", [])
        if isinstance(item.get("current_c"), (int, float))
    )
    if temperatures and max(temperatures) >= 85:
        peak_temp = max(temperatures)
        recommendations.append(
            _item(
                "thermal-headroom",
                "thermal",
                "high",
                "长时间推理存在热降频风险",
                "Sustained inference may thermal-throttle",
                f"实测最高温度 {peak_temp:.1f} °C",
                f"Measured peak temperature is {peak_temp:.1f} °C",
                "先改善风道、清灰或降低功耗上限，再用长于 15 分钟的固定负载验证；不要仅凭瞬时温度更改风扇固件。",
                "Improve airflow, clean cooling paths or reduce the power limit, then validate with a fixed workload longer than 15 minutes; do not alter fan firmware from a single sample.",
                "比较稳态温度、频率、tokens/s 与错误率。",
                "Compare steady-state temperature, clocks, tokens/s and error rate.",
                tradeoff="降低功耗可能减少峰值 tokens/s，但通常能改善持续性能与噪声",
                tradeoff_en="Lower power may reduce peak tokens/s but can improve sustained performance and acoustics",
            )
        )
    elif not temperatures:
        unknowns.append("温度传感器未向当前进程提供数据")

    concurrency = int(workload.get("concurrency") or 1)
    context_tokens = int(workload.get("context_tokens") or 4096)
    if concurrency * context_tokens >= 131_072:
        recommendations.append(
            _item(
                "kv-budget",
                "capacity",
                "medium",
                "并发与上下文的 KV 缓存预算偏高",
                "Concurrency and context create a high KV-cache budget",
                f"并发 × 上下文 = {concurrency} × {context_tokens:,} = {concurrency * context_tokens:,} token-slots",
                f"Concurrency × context = {concurrency} × {context_tokens:,} = {concurrency * context_tokens:,} token-slots",
                "将最大上下文与常用上下文分离设置，按真实 P95 提示长度配置，并用阶梯并发而非一次打满。",
                "Separate maximum from typical context, size for actual P95 prompt length, and ramp concurrency in steps.",
                "依次测试 1、2、4…并发，记录成功率、TTFT、tokens/s、RAM/VRAM 峰值和 OOM。",
                "Test concurrency at 1, 2, 4… and record success rate, TTFT, tokens/s, RAM/VRAM peaks and OOMs.",
                confidence="medium",
            )
        )

    vendors = {str(item.get("vendor") or "Unknown") for item in hardware.get("gpus", [])}
    platform_key = str(hardware.get("platform", {}).get("key") or "unknown")
    if "AMD" in vendors and platform_key == "windows":
        recommendations.append(
            _item(
                "amd-windows-backend",
                "engine",
                "medium",
                "Windows AMD 后端应先做 Vulkan/可用 HIP 路径 A/B 测试",
                "Benchmark Vulkan and any available HIP path on Windows AMD",
                "检测到 Windows + AMD；驱动、型号和引擎版本会显著影响可用路径",
                "Windows + AMD detected; driver, adapter and engine versions materially affect availability",
                "优先比较 llama.cpp Vulkan 与 Ollama 当前可用后端；未被厂商工具实测的 ROCm/HIP 路径只标记为待验证。",
                "Compare llama.cpp Vulkan with the backend currently selected by Ollama; treat unmeasured ROCm/HIP paths as unverified.",
                "使用同一 GGUF、上下文和并发记录 TTFT、tokens/s、显存、错误日志与 15 分钟稳定性。",
                "Use the same GGUF, context and concurrency; record TTFT, tokens/s, VRAM, errors and 15-minute stability.",
                confidence="medium",
            )
        )

    event_codes = {str(item.get("code")) for item in log_events}
    if event_codes & {"CUDA_OOM", "MEMORY_OOM", "CONTEXT_LIMIT"}:
        recommendations.append(
            _item(
                "log-capacity-failure",
                "stability",
                "critical",
                "日志已出现容量失败证据",
                "Logs contain capacity-failure evidence",
                f"事件代码：{', '.join(sorted(event_codes & {'CUDA_OOM', 'MEMORY_OOM', 'CONTEXT_LIMIT'}))}",
                f"Event codes: {', '.join(sorted(event_codes & {'CUDA_OOM', 'MEMORY_OOM', 'CONTEXT_LIMIT'}))}",
                "先回退到最后一次可用配置，再降低并发/上下文或量化；不要继续增加压力寻找极限。",
                "Return to the last known-good configuration, then reduce concurrency/context or quantization; do not keep increasing load to find the limit.",
                "清空异常后以低并发起步，确认连续基准无 OOM 再逐级增加。",
                "After clearing the fault, start at low concurrency and increase only after repeated benchmarks remain OOM-free.",
            )
        )
    if event_codes & {"CUDA_ERROR", "ROCM_HIP_ERROR", "METAL_ERROR", "VULKAN_ERROR", "CPU_FALLBACK"}:
        recommendations.append(
            _item(
                "log-accelerator-failure",
                "engine",
                "high",
                "加速器后端存在错误或回退证据",
                "Accelerator backend has error or fallback evidence",
                f"事件代码：{', '.join(sorted(event_codes & {'CUDA_ERROR', 'ROCM_HIP_ERROR', 'METAL_ERROR', 'VULKAN_ERROR', 'CPU_FALLBACK'}))}",
                f"Event codes: {', '.join(sorted(event_codes & {'CUDA_ERROR', 'ROCM_HIP_ERROR', 'METAL_ERROR', 'VULKAN_ERROR', 'CPU_FALLBACK'}))}",
                "核对驱动、引擎构建后端和实际 offload 配置；先保留旧环境，不原地覆盖可回滚版本。",
                "Verify driver, engine build backend and actual offload settings; retain the old environment instead of overwriting the rollback point.",
                "重启后确认日志出现正确后端与模型已加载证据，再做相同负载基准。",
                "After restart, confirm the intended backend and model-loaded evidence, then repeat the same-load benchmark.",
            )
        )

    unhealthy = [item for item in runtime_probes if item.get("health") not in {"healthy", "reachable"}]
    if unhealthy:
        recommendations.append(
            _item(
                "runtime-probe-health",
                "stability",
                "medium",
                "部分模型服务未通过只读健康探测",
                "Some model services did not pass passive health probes",
                f"异常或未知探测 {len(unhealthy)} / {len(runtime_probes)}",
                f"Unhealthy or unknown probes: {len(unhealthy)} / {len(runtime_probes)}",
                "先检查模型加载状态、监听地址和脱敏日志事件，不要直接用端口存活代替模型就绪。",
                "Check model load state, bind address and redacted log events; do not equate an open port with model readiness.",
                "对健康接口、模型列表和一次合成短请求分别验收。",
                "Validate the health endpoint, model list and one synthetic short request separately.",
            )
        )

    recommendations.sort(
        key=lambda item: (SEVERITY_ORDER.get(item["severity"], 99), item["domain"], item["id"])
    )
    if not recommendations:
        recommendations.append(
            _item(
                "baseline-first",
                "workflow",
                "info",
                "当前没有可由实测证据触发的高优先级优化项",
                "No high-priority optimization is triggered by current evidence",
                "当前已采集指标未越过确定性规则阈值",
                "Current collected metrics do not cross deterministic rule thresholds",
                "保留现状，先建立同模型、同量化、同上下文、同并发的基准线，再决定是否优化。",
                "Keep the current configuration and establish a same-model, same-quantization, same-context and same-concurrency baseline before optimizing.",
                "记录 TTFT、tokens/s、成功率、RAM/VRAM 峰值与 15 分钟稳定性。",
                "Record TTFT, tokens/s, success rate, RAM/VRAM peak and 15-minute stability.",
            )
        )

    return {
        "recommendations": recommendations,
        "summary": {
            "critical": sum(item["severity"] == "critical" for item in recommendations),
            "high": sum(item["severity"] == "high" for item in recommendations),
            "actionable": sum(item["severity"] in {"critical", "high", "medium"} for item in recommendations),
        },
        "unknowns": unknowns,
        "method": "仅由本机实测值、用户目标和脱敏日志事件触发确定性规则；缺失数据不估算。",
        "method_en": "Deterministic rules use only locally measured values, user targets and redacted log events; missing data is not estimated.",
    }
