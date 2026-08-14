from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Any, Sequence

from .capacity import QUANTIZATIONS, estimate_capacity, predict_model_variant
from .hardware import CommandResult, collect_hardware, detect_runtimes
from .model_catalog import catalog_summary, load_catalog, model_by_id
from .storage import Storage


class BenchmarkError(ValueError):
    """A local benchmark request was unsafe, invalid, or failed."""


def _benchmark_command(args: Sequence[str], timeout: float) -> CommandResult:
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    try:
        completed = subprocess.run(
            list(args),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
            shell=False,
            creationflags=creationflags,
        )
    except subprocess.TimeoutExpired as exc:
        raise BenchmarkError("基准测试超过 300 秒，已终止") from exc
    except OSError as exc:
        raise BenchmarkError(f"无法启动 llama-bench：{type(exc).__name__}") from exc
    return CommandResult(completed.returncode, completed.stdout, completed.stderr)


def _json_payload(text: str) -> Any:
    stripped = text.strip()
    if not stripped:
        raise BenchmarkError("llama-bench 没有返回 JSON")
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        array_start, array_end = stripped.find("["), stripped.rfind("]")
        if 0 <= array_start < array_end:
            try:
                return json.loads(stripped[array_start : array_end + 1])
            except json.JSONDecodeError:
                pass
        records: list[Any] = []
        for line in stripped.splitlines():
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        if records:
            return records
    raise BenchmarkError("无法解析 llama-bench JSON 输出")


def parse_llama_bench_json(text: str) -> dict[str, Any]:
    payload = _json_payload(text)
    if isinstance(payload, dict):
        values = payload.get("results") if isinstance(payload.get("results"), list) else [payload]
    elif isinstance(payload, list):
        values = payload
    else:
        raise BenchmarkError("llama-bench JSON 结构无效")

    prompt_values: list[float] = []
    generation_values: list[float] = []
    safe_records: list[dict[str, Any]] = []
    for value in values:
        if not isinstance(value, dict):
            continue
        speed = next(
            (
                value.get(key)
                for key in ("avg_ts", "t_s", "tokens_per_second", "tokens_per_sec")
                if value.get(key) is not None
            ),
            None,
        )
        try:
            speed_value = float(speed)
        except (TypeError, ValueError):
            continue
        if speed_value <= 0:
            continue
        n_prompt = int(value.get("n_prompt") or 0)
        n_gen = int(value.get("n_gen") or 0)
        test_name = str(value.get("test") or "").lower()
        if n_gen > 0 or test_name.startswith(("tg", "gen")):
            generation_values.append(speed_value)
            kind = "generation"
        elif n_prompt > 0 or test_name.startswith(("pp", "prompt")):
            prompt_values.append(speed_value)
            kind = "prompt"
        else:
            continue
        safe_records.append(
            {
                "kind": kind,
                "n_prompt": n_prompt,
                "n_gen": n_gen,
                "tokens_per_second": round(speed_value, 3),
                "n_threads": value.get("n_threads"),
                "n_gpu_layers": value.get("n_gpu_layers"),
            }
        )
    if not generation_values:
        raise BenchmarkError("基准输出缺少生成速度记录")
    return {
        "prompt_tps": round(sum(prompt_values) / len(prompt_values), 3) if prompt_values else None,
        "generation_tps": round(sum(generation_values) / len(generation_values), 3),
        "records": safe_records,
    }


class ModelPlanner:
    def __init__(self, storage: Storage):
        self.storage = storage
        self.catalog = load_catalog()
        self._lock = threading.RLock()
        self._hardware = collect_hardware()
        self._runtimes = detect_runtimes()

    def refresh(self) -> dict[str, Any]:
        hardware = collect_hardware()
        runtimes = detect_runtimes()
        with self._lock:
            self._hardware = hardware
            self._runtimes = runtimes
        return self.status()

    def hardware(self) -> dict[str, Any]:
        with self._lock:
            return json.loads(json.dumps(self._hardware, ensure_ascii=False))

    def runtimes(self) -> list[dict[str, Any]]:
        with self._lock:
            return json.loads(json.dumps(self._runtimes, ensure_ascii=False))

    def _calibration_benchmarks(self, hardware_fingerprint: str, limit: int = 120) -> list[dict[str, Any]]:
        model_items = self.storage.recent_model_benchmarks(limit, hardware_fingerprint)
        normalized: list[dict[str, Any]] = []
        for item in model_items:
            details = item.get("details") or {}
            normalized.append(
                {
                    **item,
                    "calibration_source": "llama_bench",
                    "sample_count": max(1, len(details.get("records") or [])),
                }
            )
        for item in self.storage.recent_service_calibrations(hardware_fingerprint, limit):
            normalized.append(
                {
                    **item,
                    "model_id": item.get("catalog_model_id"),
                    "calibration_source": "service_matrix",
                    "sample_count": int(item.get("sample_count") or item.get("successful_requests") or 0),
                }
            )
        normalized.sort(
            key=lambda item: float(item.get("created_at") or item.get("id") or 0),
            reverse=True,
        )
        return normalized[: max(1, int(limit))]

    def catalog_model(self, model_id: str) -> dict[str, Any] | None:
        model = model_by_id(self.catalog, model_id)
        return json.loads(json.dumps(model, ensure_ascii=False)) if model else None

    def predict_workload(
        self, model_id: str, quantization: str, workload: dict[str, Any]
    ) -> dict[str, Any] | None:
        return predict_model_variant(
            self.hardware(), self.catalog, model_id, quantization, workload
        )

    def status(self) -> dict[str, Any]:
        hardware = self.hardware()
        runtimes = self.runtimes()
        benchmarks = self._calibration_benchmarks(
            str(hardware.get("hardware_fingerprint") or ""), 40
        )
        model_fields = {
            "id",
            "name",
            "publisher",
            "family",
            "architecture",
            "total_params_b",
            "active_params_b",
            "native_context_tokens",
            "license",
            "source_url",
            "model_url",
            "modalities",
            "strengths",
            "runtimes",
            "notes",
        }
        return {
            "hardware": hardware,
            "runtimes": runtimes,
            "catalog": catalog_summary(self.catalog),
            "models": [
                {key: value for key, value in model.items() if key in model_fields}
                for model in self.catalog.get("models", [])
            ],
            "quantizations": [
                {"id": key, **value} for key, value in QUANTIZATIONS.items()
            ],
            "benchmarks": benchmarks[:20],
            "calibration_sources": {
                "llama_bench": sum(item.get("calibration_source") == "llama_bench" for item in benchmarks),
                "service_matrix": sum(item.get("calibration_source") == "service_matrix" for item in benchmarks),
            },
            "benchmark": {
                "runtime": "llama-bench",
                "available": bool(shutil.which("llama-bench") or shutil.which("llama-bench.exe")),
                "requires_confirmation": "BENCHMARK",
                "network": False,
                "downloads": False,
            },
            "privacy": {
                "offline_by_default": True,
                "telemetry": False,
                "automatic_model_downloads": False,
                "stores_model_path": False,
            },
        }

    def estimate(self, raw: dict[str, Any]) -> dict[str, Any]:
        hardware = self.hardware()
        benchmarks = self._calibration_benchmarks(
            str(hardware.get("hardware_fingerprint") or ""), 160
        )
        return estimate_capacity(
            hardware,
            self.catalog,
            raw,
            self.runtimes(),
            benchmarks,
        )

    def benchmark(self, raw: dict[str, Any], runner=_benchmark_command) -> dict[str, Any]:
        if str(raw.get("confirmation") or "") != "BENCHMARK":
            raise BenchmarkError("必须输入确认短语 BENCHMARK")
        model_id = str(raw.get("model_id") or "")
        if not model_by_id(self.catalog, model_id):
            raise BenchmarkError("model_id 不在内置离线目录中")
        quantization = str(raw.get("quantization") or "")
        if quantization not in QUANTIZATIONS:
            raise BenchmarkError("quantization 无效")
        raw_path = raw.get("model_path")
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise BenchmarkError("缺少本地 GGUF 模型路径")
        path = Path(raw_path).expanduser()
        if not path.is_absolute():
            raise BenchmarkError("模型路径必须是绝对路径")
        try:
            path = path.resolve(strict=True)
        except OSError as exc:
            raise BenchmarkError("模型文件不存在或不可访问") from exc
        if not path.is_file() or path.suffix.lower() != ".gguf":
            raise BenchmarkError("只允许对本地 .gguf 普通文件运行基准")
        executable = shutil.which("llama-bench") or shutil.which("llama-bench.exe")
        if not executable:
            raise BenchmarkError("未检测到 llama-bench；请先安装与模型兼容的 llama.cpp 版本")
        args = [
            executable,
            "-m",
            str(path),
            "-p",
            "128",
            "-n",
            "64",
            "-r",
            "1",
            "-o",
            "json",
        ]
        completed = runner(args, 300)
        if completed.returncode != 0:
            raise BenchmarkError(
                f"llama-bench 失败（退出码 {completed.returncode}）；请在本机终端复核模型、量化和后端兼容性"
            )
        parsed = parse_llama_bench_json(completed.stdout)
        hardware = self.hardware()
        stored = {
            "hardware_fingerprint": str(hardware.get("hardware_fingerprint") or ""),
            "model_id": model_id,
            "model_file_name": path.name,
            "model_file_size_bytes": path.stat().st_size,
            "quantization": quantization,
            "runtime": "llama-bench",
            "prompt_tps": parsed["prompt_tps"],
            "generation_tps": parsed["generation_tps"],
            "details": {
                "arguments": {"prompt_tokens": 128, "generation_tokens": 64, "repetitions": 1},
                "records": parsed["records"],
                "privacy": "仅保存文件名和大小，不保存模型绝对路径",
            },
        }
        benchmark_id = self.storage.add_model_benchmark(stored)
        return {
            "id": benchmark_id,
            **{key: value for key, value in stored.items() if key != "details"},
            "records": parsed["records"],
        }
