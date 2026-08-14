from __future__ import annotations

import http.client
import json
import math
import statistics
import threading
import time
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable

from .diagnostics import redact_text


MAX_STREAM_BYTES = 10 * 1024 * 1024
MAX_CONCURRENCY = 4
MAX_CONTEXT_TOKENS = 4096
MAX_OUTPUT_TOKENS = 64
MAX_MATRIX_REQUESTS = 20
MAX_CALIBRATION_REQUESTS = 120


class ServiceBenchmarkError(RuntimeError):
    pass


def _prompt(target_tokens: int, request_index: int) -> str:
    # A repeated neutral token keeps content deterministic and avoids sending
    # local user data.  The server-reported prompt token count is authoritative;
    # target_tokens remains a requested approximation until that count arrives.
    prefix = f"Local synthetic benchmark request {request_index}. Reply with short words only.\n"
    return prefix + (" ping" * max(16, target_tokens - 20))


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(len(ordered) * fraction) - 1))
    return ordered[index]


def _stream_payloads(response: http.client.HTTPResponse) -> list[tuple[float, dict[str, Any]]]:
    captured: list[tuple[float, dict[str, Any]]] = []
    total = 0
    while True:
        # Bound each readline itself.  Checking only after an unbounded
        # readline would still let a broken local runtime allocate an
        # arbitrarily large single line before the 10 MiB guard ran.
        remaining = MAX_STREAM_BYTES - total
        line = response.readline(remaining + 1)
        if not line:
            break
        if len(line) > remaining:
            raise ServiceBenchmarkError("流式响应超过 10 MiB，已中止")
        total += len(line)
        text = line.decode("utf-8", errors="replace").strip()
        if not text:
            continue
        if text.startswith("data:"):
            text = text[5:].strip()
        if text == "[DONE]":
            break
        try:
            value = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            captured.append((time.perf_counter(), value))
    return captured


def _has_generated_content(payload: dict[str, Any], runtime: str) -> bool:
    if runtime == "Ollama":
        return bool(payload.get("response"))
    if runtime == "llama.cpp":
        return bool(payload.get("content") or payload.get("token"))
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return False
    choice = choices[0]
    if choice.get("text"):
        return True
    delta = choice.get("delta")
    return isinstance(delta, dict) and bool(delta.get("content"))


def _usage(payloads: list[tuple[float, dict[str, Any]]], runtime: str) -> dict[str, Any]:
    prompt_tokens = None
    completion_tokens = None
    generation_tps = None
    prompt_tps = None
    for _, payload in payloads:
        if runtime == "Ollama" and payload.get("done"):
            prompt_tokens = payload.get("prompt_eval_count")
            completion_tokens = payload.get("eval_count")
            eval_duration = payload.get("eval_duration")
            prompt_duration = payload.get("prompt_eval_duration")
            if isinstance(completion_tokens, (int, float)) and isinstance(eval_duration, (int, float)) and eval_duration > 0:
                generation_tps = float(completion_tokens) / (float(eval_duration) / 1_000_000_000)
            if isinstance(prompt_tokens, (int, float)) and isinstance(prompt_duration, (int, float)) and prompt_duration > 0:
                prompt_tps = float(prompt_tokens) / (float(prompt_duration) / 1_000_000_000)
        usage = payload.get("usage")
        if isinstance(usage, dict):
            prompt_tokens = usage.get("prompt_tokens", prompt_tokens)
            completion_tokens = usage.get("completion_tokens", completion_tokens)
        timings = payload.get("timings")
        if isinstance(timings, dict):
            completion_tokens = timings.get("predicted_n", completion_tokens)
            prompt_tokens = timings.get("prompt_n", prompt_tokens)
            generation_tps = timings.get("predicted_per_second", generation_tps)
            prompt_tps = timings.get("prompt_per_second", prompt_tps)
    return {
        "prompt_tokens": int(prompt_tokens) if isinstance(prompt_tokens, (int, float)) else None,
        "completion_tokens": int(completion_tokens) if isinstance(completion_tokens, (int, float)) else None,
        "generation_tps": round(float(generation_tps), 3) if isinstance(generation_tps, (int, float)) else None,
        "prompt_tps": round(float(prompt_tps), 3) if isinstance(prompt_tps, (int, float)) else None,
    }


def _request_payload(runtime: str, model: str, prompt: str, context_tokens: int, output_tokens: int) -> tuple[str, dict[str, Any]]:
    if runtime == "Ollama":
        return "/api/generate", {
            "model": model,
            "prompt": prompt,
            "stream": True,
            "options": {"num_predict": output_tokens, "num_ctx": context_tokens, "temperature": 0},
        }
    if runtime == "llama.cpp":
        return "/completion", {
            "prompt": prompt,
            "n_predict": output_tokens,
            "stream": True,
            "temperature": 0,
        }
    return "/v1/completions", {
        "model": model,
        "prompt": prompt,
        "max_tokens": output_tokens,
        "stream": True,
        "stream_options": {"include_usage": True},
        "temperature": 0,
    }


def _one_request(
    port: int,
    runtime: str,
    model: str,
    context_tokens: int,
    output_tokens: int,
    request_index: int,
    start_barrier: threading.Barrier,
    request_timeout: float = 120,
) -> dict[str, Any]:
    prompt = _prompt(context_tokens, request_index)
    path, payload = _request_payload(runtime, model, prompt, context_tokens, output_tokens)
    encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    start_barrier.wait(timeout=10)
    started = time.perf_counter()
    connection = http.client.HTTPConnection(
        "127.0.0.1", port, timeout=max(5.0, min(float(request_timeout), 120.0))
    )
    try:
        connection.request(
            "POST",
            path,
            body=encoded,
            headers={
                "Host": f"localhost:{port}",
                "Content-Type": "application/json",
                "Accept": "application/json,text/event-stream",
                "Content-Length": str(len(encoded)),
            },
        )
        response = connection.getresponse()
        if response.status in {401, 403}:
            response.read(8192)
            raise ServiceBenchmarkError("运行时要求认证；VSG 不读取或代填 API Key")
        if not 200 <= response.status < 300:
            error = response.read(8192).decode("utf-8", errors="replace")
            raise ServiceBenchmarkError(
                f"运行时返回 HTTP {response.status}: {redact_text(error)[:160]}"
            )
        payloads = _stream_payloads(response)
    finally:
        connection.close()
    ended = time.perf_counter()
    first_token_at = next((captured for captured, value in payloads if _has_generated_content(value, runtime)), None)
    usage = _usage(payloads, runtime)
    completion_tokens = usage.get("completion_tokens")
    client_tps = None
    inter_token_latency = None
    if isinstance(completion_tokens, int) and completion_tokens > 0 and first_token_at is not None and ended > first_token_at:
        client_tps = completion_tokens / (ended - first_token_at)
        if completion_tokens > 1:
            inter_token_latency = (ended - first_token_at) / (completion_tokens - 1)
    return {
        "success": first_token_at is not None,
        "ttft_seconds": round(first_token_at - started, 4) if first_token_at is not None else None,
        "wall_seconds": round(ended - started, 4),
        "prompt_tokens": usage.get("prompt_tokens"),
        "completion_tokens": completion_tokens,
        "server_generation_tps": usage.get("generation_tps"),
        "server_prompt_tps": usage.get("prompt_tps"),
        "client_generation_tps": round(client_tps, 3) if client_tps is not None else None,
        "inter_token_latency_seconds": round(inter_token_latency, 5) if inter_token_latency is not None else None,
        "response_content_persisted": False,
    }


def run_service_benchmark(
    service: dict[str, Any],
    probe: dict[str, Any],
    body: dict[str, Any],
    memory_used_percent: float,
    *,
    request_count: int | None = None,
    cancel_event: threading.Event | None = None,
    progress_callback: Callable[[int, int], None] | None = None,
    duration_seconds: int | None = None,
) -> dict[str, Any]:
    port = int(probe.get("port") or 0)
    runtime = str(service.get("runtime") or "")
    if not service.get("metadata", {}).get("model_runtime") or not 1 <= port <= 65535:
        raise ServiceBenchmarkError("目标不是当前已识别的本地模型服务")
    if probe.get("health") not in {"ready", "reachable_auth_required"}:
        raise ServiceBenchmarkError("模型服务尚未就绪，拒绝发起推理基准")
    if probe.get("security", {}).get("auth_posture") == "required":
        raise ServiceBenchmarkError("该服务需要认证；VSG 不读取或代填 API Key")
    if runtime == "ComfyUI":
        raise ServiceBenchmarkError("ComfyUI 工作流不是文本补全接口，首版不自动构造工作流基准")
    if memory_used_percent >= 85:
        raise ServiceBenchmarkError("系统内存已超过 85%，为降低 OOM 风险拒绝基准")
    try:
        concurrency = int(body.get("concurrency", 1))
        context_tokens = int(body.get("context_tokens", 512))
        output_tokens = int(body.get("output_tokens", 32))
    except (TypeError, ValueError) as exc:
        raise ServiceBenchmarkError("基准参数必须是整数") from exc
    if not 1 <= concurrency <= MAX_CONCURRENCY:
        raise ServiceBenchmarkError(f"首版并发只允许 1 到 {MAX_CONCURRENCY}")
    if not 128 <= context_tokens <= MAX_CONTEXT_TOKENS:
        raise ServiceBenchmarkError(f"首版上下文测试只允许 128 到 {MAX_CONTEXT_TOKENS} tokens")
    if not 1 <= output_tokens <= MAX_OUTPUT_TOKENS:
        raise ServiceBenchmarkError(f"首版输出只允许 1 到 {MAX_OUTPUT_TOKENS} tokens")
    if duration_seconds is not None:
        try:
            duration_target = int(duration_seconds)
        except (TypeError, ValueError) as exc:
            raise ServiceBenchmarkError("校准时长必须是整数秒") from exc
        if not 10 <= duration_target <= 60:
            raise ServiceBenchmarkError("校准时长只允许 10 到 60 秒")
        total_requests = MAX_CALIBRATION_REQUESTS
    else:
        duration_target = None
        total_requests = concurrency if request_count is None else int(request_count)
    request_limit = MAX_CALIBRATION_REQUESTS if duration_target is not None else MAX_MATRIX_REQUESTS
    if total_requests < concurrency or total_requests > request_limit:
        raise ServiceBenchmarkError(
            f"请求样本数必须介于并发数和 {request_limit} 之间"
        )
    if str(body.get("confirmation") or "").strip() != f"BENCHMARK {port}":
        raise ServiceBenchmarkError(f"确认短语必须是 BENCHMARK {port}")
    models = probe.get("models") or []
    requested_model = str(body.get("model") or "").strip()
    model = requested_model or (str(models[0].get("name") or "") if models and isinstance(models[0], dict) else "")
    if not model:
        raise ServiceBenchmarkError("运行时未报告已加载模型，请明确选择模型后重试")
    if (
        len(model) > 180
        or any(character in model for character in "\r\n\0\\")
        or model.startswith("/")
        or re.match(r"^[A-Za-z]:[/\\]", model)
        or not re.fullmatch(r"[A-Za-z0-9_.:+/@-]+", model)
    ):
        raise ServiceBenchmarkError("模型标识无效")
    known_models = {
        str(item.get("name") or "")
        for item in models
        if isinstance(item, dict) and item.get("name")
    }
    if known_models and model not in known_models:
        raise ServiceBenchmarkError("模型标识不在运行时报告的已加载模型清单中")

    started = time.perf_counter()
    results: list[dict[str, Any]] = []
    errors: list[str] = []
    launched = 0
    while launched < total_requests:
        if cancel_event is not None and cancel_event.is_set():
            break
        if duration_target is not None and time.perf_counter() - started >= duration_target:
            break
        wave_size = min(concurrency, total_requests - launched)
        barrier = threading.Barrier(wave_size)
        with ThreadPoolExecutor(max_workers=wave_size, thread_name_prefix="vsg-benchmark") as pool:
            futures = [
                pool.submit(
                    _one_request,
                    port,
                    runtime,
                    model,
                    context_tokens,
                    output_tokens,
                    launched + index + 1,
                    barrier,
                    30 if duration_target is not None else 120,
                )
                for index in range(wave_size)
            ]
            for future in as_completed(futures):
                try:
                    results.append(future.result())
                except Exception as exc:
                    errors.append(
                        redact_text(f"{type(exc).__name__}: {str(exc)}")[:200]
                    )
                if progress_callback:
                    progress_callback(min(total_requests, len(results) + len(errors)), total_requests)
        launched += wave_size
    wall = time.perf_counter() - started
    successful = [item for item in results if item.get("success")]
    ttfts = [float(item["ttft_seconds"]) for item in successful if item.get("ttft_seconds") is not None]
    wall_times = [float(item["wall_seconds"]) for item in successful if item.get("wall_seconds") is not None]
    inter_token_latencies = [
        float(item["inter_token_latency_seconds"])
        for item in successful
        if item.get("inter_token_latency_seconds") is not None
    ]
    server_tps = [float(item["server_generation_tps"]) for item in successful if item.get("server_generation_tps") is not None]
    client_tps = [float(item["client_generation_tps"]) for item in successful if item.get("client_generation_tps") is not None]
    prompt_tps = [float(item["server_prompt_tps"]) for item in successful if item.get("server_prompt_tps") is not None]
    completion_tokens = sum(int(item.get("completion_tokens") or 0) for item in successful)
    verified_prompt_tokens = [int(item["prompt_tokens"]) for item in successful if item.get("prompt_tokens") is not None]
    generation_tps = statistics.fmean(server_tps) if server_tps else statistics.fmean(client_tps) if client_tps else None
    aggregate_tps = completion_tokens / wall if completion_tokens and wall > 0 else None
    return {
        "service_fingerprint": service.get("fingerprint"),
        "service_id": service.get("id"),
        "runtime": runtime,
        "port": port,
        "model_name": model,
        "concurrency": concurrency,
        "request_count": len(results) + len(errors) if duration_target is not None else total_requests,
        "request_limit": total_requests if duration_target is not None else None,
        "completed_requests": len(results) + len(errors),
        "requested_context_tokens": context_tokens,
        "requested_output_tokens": output_tokens,
        "verified_prompt_tokens_min": min(verified_prompt_tokens) if verified_prompt_tokens else None,
        "verified_prompt_tokens_max": max(verified_prompt_tokens) if verified_prompt_tokens else None,
        "successful_requests": len(successful),
        "failed_requests": max(0, len(results) + len(errors) - len(successful)),
        "cancelled": bool(cancel_event is not None and cancel_event.is_set() and len(results) + len(errors) < total_requests),
        "ttft_seconds": round(statistics.fmean(ttfts), 4) if ttfts else None,
        "ttft_p50_seconds": round(_percentile(ttfts, 0.50) or 0, 4) if ttfts else None,
        "ttft_p95_seconds": round(_percentile(ttfts, 0.95) or 0, 4) if ttfts else None,
        "ttft_p95_sample_sufficient": len(ttfts) >= 20,
        "sample_count": len(successful),
        "end_to_end_p50_seconds": round(_percentile(wall_times, 0.50) or 0, 4) if wall_times else None,
        "end_to_end_p95_seconds": round(_percentile(wall_times, 0.95) or 0, 4) if wall_times else None,
        "inter_token_latency_seconds": round(statistics.fmean(inter_token_latencies), 5) if inter_token_latencies else None,
        "generation_tps": round(generation_tps, 3) if generation_tps is not None else None,
        "aggregate_generation_tps": round(aggregate_tps, 3) if aggregate_tps is not None else None,
        "prompt_tps": round(statistics.fmean(prompt_tps), 3) if prompt_tps else None,
        "wall_seconds": round(wall, 3),
        "duration_target_seconds": duration_target,
        "duration_limited": duration_target is not None,
        "oom_observed": any("out of memory" in error.lower() or "oom" in error.lower() for error in errors),
        "errors": errors[:8],
        "details": {
            "requests": results,
            "prompt_policy": "synthetic repeated neutral token; server-reported prompt count is authoritative",
            "limits": {
                "max_concurrency": MAX_CONCURRENCY,
                "max_context_tokens": MAX_CONTEXT_TOKENS,
                "max_output_tokens": MAX_OUTPUT_TOKENS,
                "max_matrix_requests": MAX_MATRIX_REQUESTS,
                "max_calibration_requests": MAX_CALIBRATION_REQUESTS,
                "max_calibration_seconds": 60,
            },
            "p95_policy": "P95 is labelled statistically insufficient until at least 20 successful samples exist",
            "cancellation_policy": "cooperative between waves; in-flight local requests may finish",
            "response_content_persisted": False,
        },
    }
