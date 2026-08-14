from __future__ import annotations

import http.client
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import PurePath, PureWindowsPath
from typing import Any


MAX_RESPONSE_BYTES = 512 * 1024
SAFE_TIMEOUT_SECONDS = 0.9
MODEL_FLAG_NAMES = {
    "-m",
    "--model",
    "--model-path",
    "--served-model-name",
    "--model-name",
    "--model-id",
}
CONTEXT_FLAG_NAMES = {"-c", "--ctx-size", "--context-size", "--max-model-len", "--max-seq-len"}
CONCURRENCY_FLAG_NAMES = {"-np", "--parallel", "--max-num-seqs", "--max-concurrent-requests"}
HOST_FLAG_NAMES = {"--host", "--listen", "--address"}
AUTH_MARKERS = ("api-key", "apikey", "auth-token", "access-token", "password", "basic-auth")
QUANT_RE = re.compile(r"(?i)\b(?:Q[2-8](?:_[A-Z0-9]+)+|IQ[1-4]_[A-Z0-9]+|FP16|BF16|FP8|INT8|INT4)\b")
PROMETHEUS_RE = re.compile(
    r"^(?P<name>[a-zA-Z_:][a-zA-Z0-9_:]*)(?:\{[^}]*\})?\s+(?P<value>[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)"
)


def _basename(value: str) -> str:
    cleaned = value.strip().strip('"\'')
    if not cleaned:
        return ""
    if re.match(r"^[A-Za-z]:[\\/]", cleaned) or "\\" in cleaned:
        return PureWindowsPath(cleaned).name[:180]
    if cleaned.startswith("/"):
        return PurePath(cleaned).name[:180]
    return cleaned[:180]


def extract_command_configuration(service: dict[str, Any]) -> dict[str, Any]:
    command = str(service.get("process", {}).get("command") or "")
    # The scanner has already redacted secrets.  This parser still records only
    # key presence for auth-related flags and never returns their values.
    tokens = re.findall(r'"[^"]*"|\'[^\']*\'|\S+', command)
    configuration: dict[str, Any] = {
        "model": None,
        "quantization": None,
        "context_tokens": None,
        "configured_concurrency": None,
        "bind_host": None,
        "auth_flag_present": any(marker in command.lower() for marker in AUTH_MARKERS),
        "backend": service.get("runtime") or "unknown",
        "accelerator": "unknown",
        "capabilities": {"tools": "unknown", "vision": "unknown", "audio": "unknown"},
        "source": "redacted process command",
    }
    for index, raw in enumerate(tokens):
        token = raw.strip('"\'')
        key, equals, inline = token.partition("=")
        key_lower = key.lower()
        next_value = tokens[index + 1].strip('"\'') if index + 1 < len(tokens) else ""
        value = inline if equals else next_value
        if key_lower in MODEL_FLAG_NAMES and value and value != "[REDACTED]":
            configuration["model"] = _basename(value)
        elif key_lower in CONTEXT_FLAG_NAMES and value.isdigit():
            configuration["context_tokens"] = int(value)
        elif key_lower in CONCURRENCY_FLAG_NAMES and value.isdigit():
            configuration["configured_concurrency"] = int(value)
        elif key_lower in HOST_FLAG_NAMES and value:
            configuration["bind_host"] = value[:80]
    quant = QUANT_RE.search(command)
    if quant:
        configuration["quantization"] = quant.group(0).upper()
    lowered = command.lower()
    runtime = str(service.get("runtime") or "")
    if "--mmproj" in lowered or "vision" in lowered or "multimodal" in lowered:
        configuration["capabilities"]["vision"] = "configured"
    if "whisper" in lowered or "audio" in lowered:
        configuration["capabilities"]["audio"] = "configured"
    if "tool-call" in lowered or "chat-template" in lowered:
        configuration["capabilities"]["tools"] = "configured_or_template_dependent"
    if "exllamav2" in lowered or "exllama_v2" in lowered:
        configuration["backend"] = "ExLlamaV2"
    elif "exllama" in lowered or runtime == "TabbyAPI":
        configuration["backend"] = "ExLlama"
    elif "tensorrt_llm" in lowered or "trtllm" in lowered or runtime == "TensorRT-LLM":
        configuration["backend"] = "TensorRT-LLM"
    if "cuda" in lowered or runtime in {"vLLM", "SGLang", "TensorRT-LLM", "TabbyAPI"}:
        configuration["accelerator"] = "CUDA"
    elif "vulkan" in lowered:
        configuration["accelerator"] = "Vulkan"
    elif "metal" in lowered or runtime == "MLX-LM":
        configuration["accelerator"] = "Metal"
    elif "rocm" in lowered or "hip" in lowered:
        configuration["accelerator"] = "ROCm/HIP"
    return configuration


def parse_prometheus(text: str) -> dict[str, float]:
    values: dict[str, float] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = PROMETHEUS_RE.match(line)
        if not match:
            continue
        name = match.group("name")
        try:
            value = float(match.group("value"))
        except ValueError:
            continue
        values[name] = values.get(name, 0.0) + value
    return values


class LocalHttpClient:
    """Small non-proxying HTTP client locked to a local TCP port."""

    def __init__(self, port: int, timeout: float = SAFE_TIMEOUT_SECONDS):
        if isinstance(port, bool) or not 1 <= int(port) <= 65535:
            raise ValueError("端口无效")
        self.port = int(port)
        self.timeout = min(max(float(timeout), 0.1), 2.0)

    def get(self, path: str) -> dict[str, Any]:
        if not path.startswith("/") or "\r" in path or "\n" in path:
            raise ValueError("探测路径无效")
        last_error = "unreachable"
        for host in ("127.0.0.1", "::1"):
            connection = http.client.HTTPConnection(host, self.port, timeout=self.timeout)
            try:
                connection.request(
                    "GET",
                    path,
                    headers={"Host": f"localhost:{self.port}", "Accept": "application/json,text/plain"},
                )
                response = connection.getresponse()
                body = response.read(MAX_RESPONSE_BYTES + 1)
                truncated = len(body) > MAX_RESPONSE_BYTES
                body = body[:MAX_RESPONSE_BYTES]
                content_type = str(response.getheader("Content-Type") or "")[:120]
                text = body.decode("utf-8", errors="replace")
                payload = None
                if "json" in content_type.lower() or text.lstrip().startswith(("{", "[")):
                    try:
                        payload = json.loads(text)
                    except json.JSONDecodeError:
                        payload = None
                return {
                    "status": int(response.status),
                    "content_type": content_type,
                    "text": text,
                    "json": payload,
                    "truncated": truncated,
                    "www_authenticate": bool(response.getheader("WWW-Authenticate")),
                }
            except (OSError, http.client.HTTPException) as exc:
                last_error = type(exc).__name__
            finally:
                connection.close()
        return {"status": None, "error": last_error, "text": "", "json": None, "truncated": False}


def _metric(metrics: dict[str, float], *suffixes: str) -> float | None:
    for name, value in metrics.items():
        if any(name.lower().endswith(suffix.lower()) for suffix in suffixes):
            return value
    return None


def _prometheus_performance(metrics: dict[str, float]) -> dict[str, Any]:
    predicted = _metric(metrics, "tokens_predicted_total", "generation_tokens_total")
    predicted_seconds = _metric(metrics, "seconds_predicted_total")
    evaluated = _metric(metrics, "tokens_evaluated_total", "prompt_tokens_total")
    evaluated_seconds = _metric(metrics, "seconds_evaluated_total")
    ttft_sum = _metric(metrics, "time_to_first_token_seconds_sum")
    ttft_count = _metric(metrics, "time_to_first_token_seconds_count")
    running = _metric(metrics, "num_requests_running", "requests_processing")
    waiting = _metric(metrics, "num_requests_waiting", "requests_deferred")
    cache = _metric(metrics, "gpu_cache_usage_perc", "kv_cache_usage_ratio")
    result: dict[str, Any] = {
        "source": "passive runtime metrics",
        "generation_tps": None,
        "prompt_tps": None,
        "ttft_seconds_average": None,
        "requests_running": int(running) if running is not None else None,
        "requests_waiting": int(waiting) if waiting is not None else None,
        "kv_cache_usage_percent": round(cache * 100, 2) if cache is not None and cache <= 1 else cache,
    }
    if predicted is not None and predicted_seconds and predicted_seconds > 0:
        result["generation_tps"] = round(predicted / predicted_seconds, 2)
    if evaluated is not None and evaluated_seconds and evaluated_seconds > 0:
        result["prompt_tps"] = round(evaluated / evaluated_seconds, 2)
    if ttft_sum is not None and ttft_count and ttft_count > 0:
        result["ttft_seconds_average"] = round(ttft_sum / ttft_count, 4)
    return result


def _model_entry(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    details = value.get("details") if isinstance(value.get("details"), dict) else {}
    model_name = value.get("name") or value.get("model") or value.get("id")
    if not model_name:
        return None
    return {
        "name": _basename(str(model_name)),
        "format": details.get("format"),
        "family": details.get("family"),
        "parameter_size": details.get("parameter_size"),
        "quantization": details.get("quantization_level") or value.get("quantization"),
        "size_bytes": value.get("size"),
        "size_vram_bytes": value.get("size_vram"),
        "context_length": value.get("context_length"),
        "expires_at": value.get("expires_at"),
    }


def _auth_posture(response: dict[str, Any] | None) -> str:
    if not response or response.get("status") is None:
        return "unknown"
    if response.get("status") in {401, 403}:
        return "required"
    if response.get("status") == 200:
        return "unauthenticated_read"
    return "unknown"


def _health_from_response(response: dict[str, Any] | None) -> str:
    if not response or response.get("status") is None:
        return "unreachable"
    payload = response.get("json")
    text = str(response.get("text") or "").lower()
    status = response.get("status")
    payload_status = str(payload.get("status") or "").lower() if isinstance(payload, dict) else ""
    if status == 503 or "loading model" in text or payload_status == "loading model":
        return "loading"
    if 200 <= int(status) < 300:
        return "ready"
    if status in {401, 403}:
        return "reachable_auth_required"
    return "unhealthy"


class RuntimeProbeCollector:
    """Read-only local runtime adapters.  Passive probes never send a prompt."""

    def __init__(self, timeout: float = SAFE_TIMEOUT_SECONDS, cache_seconds: float = 7.0):
        self.timeout = timeout
        self.cache_seconds = cache_seconds
        self._cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self._observed_max_concurrency: dict[str, int] = {}
        self._metric_history: dict[str, tuple[float, dict[str, float]]] = {}

    @staticmethod
    def _port(service: dict[str, Any]) -> int | None:
        for endpoint in service.get("endpoints", []):
            if endpoint.get("protocol") == "TCP" and endpoint.get("state") == "LISTEN":
                try:
                    port = int(endpoint.get("port"))
                except (TypeError, ValueError):
                    continue
                if 1 <= port <= 65535:
                    return port
        return None

    def _base_result(self, service: dict[str, Any], port: int) -> dict[str, Any]:
        return {
            "service_id": service.get("id"),
            "pid": service.get("process", {}).get("pid"),
            "port": port,
            "runtime": service.get("runtime"),
            "probe_mode": "passive_read_only",
            "reachable": False,
            "health": "unknown",
            "model_load": "unknown",
            "models": [],
            "performance": {
                "source": "unavailable",
                "generation_tps": None,
                "prompt_tps": None,
                "ttft_seconds_average": None,
                "requests_running": None,
                "requests_waiting": None,
                "observed_max_concurrency": 0,
                "kv_cache_usage_percent": None,
            },
            "capacity": {"context_tokens": None, "slots": None, "oom_evidence": False},
            "security": {"auth_posture": "unknown", "auth_flag_present": False},
            "configuration": extract_command_configuration(service),
            "probes": [],
            "limitations": [],
            "captured_at": time.time(),
        }

    @staticmethod
    def _record_probe(result: dict[str, Any], name: str, response: dict[str, Any]) -> None:
        result["probes"].append(
            {
                "name": name,
                "status": response.get("status"),
                "reachable": response.get("status") is not None,
                "truncated": bool(response.get("truncated")),
            }
        )
        if response.get("status") is not None:
            result["reachable"] = True

    def _ollama(self, client: LocalHttpClient, result: dict[str, Any]) -> None:
        running = client.get("/api/ps")
        self._record_probe(result, "/api/ps", running)
        result["health"] = _health_from_response(running)
        result["security"]["auth_posture"] = _auth_posture(running)
        payload = running.get("json")
        if isinstance(payload, dict) and isinstance(payload.get("models"), list):
            result["models"] = [item for item in (_model_entry(value) for value in payload["models"]) if item]
            result["model_load"] = "loaded" if result["models"] else "idle"
            contexts = [item.get("context_length") for item in result["models"] if item.get("context_length")]
            if contexts:
                result["capacity"]["context_tokens"] = max(int(value) for value in contexts)
        version = client.get("/api/version")
        self._record_probe(result, "/api/version", version)
        if isinstance(version.get("json"), dict):
            result["version"] = str(version["json"].get("version") or "")[:80] or None

    def _llama_cpp(self, client: LocalHttpClient, result: dict[str, Any]) -> None:
        health = client.get("/health")
        self._record_probe(result, "/health", health)
        result["health"] = _health_from_response(health)
        result["model_load"] = "loading" if result["health"] == "loading" else "loaded" if result["health"] == "ready" else "unknown"
        props = client.get("/props")
        self._record_probe(result, "/props", props)
        payload = props.get("json")
        if isinstance(payload, dict):
            context = payload.get("n_ctx")
            generation_settings = payload.get("default_generation_settings")
            if context is None and isinstance(generation_settings, dict):
                context = generation_settings.get("n_ctx")
            if isinstance(context, (int, float)):
                result["capacity"]["context_tokens"] = int(context)
            model_path = payload.get("model_path")
            if model_path:
                result["models"] = [{"name": _basename(str(model_path))}]
        slots = client.get("/slots")
        self._record_probe(result, "/slots", slots)
        if isinstance(slots.get("json"), list):
            result["capacity"]["slots"] = len(slots["json"])
            running = sum(bool(item.get("is_processing")) for item in slots["json"] if isinstance(item, dict))
            result["performance"]["requests_running"] = running
        metrics = client.get("/metrics")
        self._record_probe(result, "/metrics", metrics)
        if metrics.get("status") == 200:
            parsed_metrics = parse_prometheus(metrics.get("text") or "")
            result["performance"].update(_prometheus_performance(parsed_metrics))
            result["_metrics"] = parsed_metrics
        models = client.get("/v1/models")
        self._record_probe(result, "/v1/models", models)
        result["security"]["auth_posture"] = _auth_posture(models)
        result["limitations"].append("llama.cpp /health 按官方设计可公开访问，认证判断改用 /v1/models")

    def _comfyui(self, client: LocalHttpClient, result: dict[str, Any]) -> None:
        system_stats = client.get("/system_stats")
        self._record_probe(result, "/system_stats", system_stats)
        result["health"] = _health_from_response(system_stats)
        result["security"]["auth_posture"] = _auth_posture(system_stats)
        queue = client.get("/queue")
        self._record_probe(result, "/queue", queue)
        payload = queue.get("json")
        if isinstance(payload, dict):
            running = payload.get("queue_running")
            pending = payload.get("queue_pending")
            result["performance"]["requests_running"] = len(running) if isinstance(running, list) else None
            result["performance"]["requests_waiting"] = len(pending) if isinstance(pending, list) else None
        result["model_load"] = "unknown"

    def _openai_compatible(self, client: LocalHttpClient, result: dict[str, Any]) -> None:
        health = client.get("/health")
        self._record_probe(result, "/health", health)
        models = client.get("/v1/models")
        self._record_probe(result, "/v1/models", models)
        result["health"] = _health_from_response(health if health.get("status") is not None else models)
        result["security"]["auth_posture"] = _auth_posture(models)
        payload = models.get("json")
        if isinstance(payload, dict) and isinstance(payload.get("data"), list):
            result["models"] = [item for item in (_model_entry(value) for value in payload["data"]) if item]
            result["model_load"] = "loaded" if result["models"] else "idle"
        metrics = client.get("/metrics")
        self._record_probe(result, "/metrics", metrics)
        if metrics.get("status") == 200:
            parsed_metrics = parse_prometheus(metrics.get("text") or "")
            result["performance"].update(_prometheus_performance(parsed_metrics))
            result["_metrics"] = parsed_metrics

    def _apply_metric_delta(self, key: str, result: dict[str, Any]) -> None:
        metrics = result.pop("_metrics", None)
        if not isinstance(metrics, dict):
            return
        now = time.time()
        previous = self._metric_history.get(key)
        self._metric_history[key] = (now, metrics)
        if not previous or not 0 < now - previous[0] <= 120:
            return
        elapsed = now - previous[0]
        for target, suffixes in (
            ("generation_tps", ("generation_tokens_total", "tokens_predicted_total")),
            ("prompt_tps", ("prompt_tokens_total", "tokens_evaluated_total")),
        ):
            current = _metric(metrics, *suffixes)
            old = _metric(previous[1], *suffixes)
            if current is None or old is None or current < old:
                continue
            result["performance"][target] = round((current - old) / elapsed, 3)
            result["performance"]["source"] = "passive runtime metrics delta"

    def _probe(self, service: dict[str, Any]) -> dict[str, Any] | None:
        port = self._port(service)
        if port is None:
            return None
        result = self._base_result(service, port)
        result["security"]["auth_flag_present"] = bool(result["configuration"].get("auth_flag_present"))
        client = LocalHttpClient(port, self.timeout)
        runtime = str(service.get("runtime") or "")
        if runtime == "Ollama":
            self._ollama(client, result)
        elif runtime == "llama.cpp":
            self._llama_cpp(client, result)
        elif runtime == "ComfyUI":
            self._comfyui(client, result)
        else:
            self._openai_compatible(client, result)
        running = result["performance"].get("requests_running")
        key = str(service.get("id"))
        self._apply_metric_delta(key, result)
        if isinstance(running, (int, float)):
            self._observed_max_concurrency[key] = max(self._observed_max_concurrency.get(key, 0), int(running))
        result["performance"]["observed_max_concurrency"] = self._observed_max_concurrency.get(key, 0)
        result["captured_at"] = time.time()
        return result

    def collect(self, services: list[dict[str, Any]]) -> list[dict[str, Any]]:
        now = time.time()
        targets = [
            service
            for service in services
            if service.get("metadata", {}).get("model_runtime") and self._port(service) is not None
        ][:16]
        results: list[dict[str, Any]] = []
        pending: list[dict[str, Any]] = []
        for service in targets:
            key = f"{service.get('id')}:{self._port(service)}"
            cached = self._cache.get(key)
            if cached and now - cached[0] < self.cache_seconds:
                results.append(json.loads(json.dumps(cached[1], ensure_ascii=False)))
            else:
                pending.append(service)
        if pending:
            with ThreadPoolExecutor(max_workers=min(4, len(pending)), thread_name_prefix="vsg-probe") as pool:
                futures = {pool.submit(self._probe, service): service for service in pending}
                for future in as_completed(futures):
                    try:
                        value = future.result()
                    except Exception as exc:
                        service = futures[future]
                        port = self._port(service) or 0
                        value = self._base_result(service, port)
                        value["health"] = "probe_error"
                        value["limitations"].append(f"只读探测失败：{type(exc).__name__}")
                    if value:
                        key = f"{value.get('service_id')}:{value.get('port')}"
                        self._cache[key] = (time.time(), value)
                        results.append(value)
        live_keys = {f"{service.get('id')}:{self._port(service)}" for service in targets}
        self._cache = {key: value for key, value in self._cache.items() if key in live_keys}
        live_service_ids = {str(service.get("id")) for service in targets}
        self._metric_history = {key: value for key, value in self._metric_history.items() if key in live_service_ids}
        return sorted(results, key=lambda item: (str(item.get("runtime")), int(item.get("port") or 0)))
