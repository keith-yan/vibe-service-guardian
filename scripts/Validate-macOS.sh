#!/bin/bash
set -euo pipefail

if [ "$(uname -s)" != "Darwin" ]; then
  printf '%s\n' "此验收脚本必须在真实 macOS 上运行。"
  exit 2
fi

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd -P)"
cd "$PROJECT_ROOT"
TEMP_BASE="${TMPDIR:-/tmp}"
TEMP_BASE="${TEMP_BASE%/}"
TEMP_ROOT="$(mktemp -d "$TEMP_BASE/vsg-macos-validation.XXXXXX")"
DATA_DIR="$TEMP_ROOT/data"

cleanup() {
  if [ -n "${APP_PID:-}" ]; then
    kill "$APP_PID" >/dev/null 2>&1 || true
  fi
  case "$TEMP_ROOT" in
    "$TEMP_BASE"/vsg-macos-validation.*) rm -rf "$TEMP_ROOT" ;;
  esac
}
trap cleanup EXIT INT TERM

if [ -x "./VibeServiceGuardian" ]; then
  CURRENT_ARCH="$(uname -m)"
  BINARY_ARCHES="$(lipo -archs ./VibeServiceGuardian)"
  case " $BINARY_ARCHES " in
    *" $CURRENT_ARCH "*) ;;
    *) printf 'Mach-O 架构 %s 与当前 Mac %s 不匹配。\n' "$BINARY_ARCHES" "$CURRENT_ARCH"; exit 3 ;;
  esac
  codesign --verify --verbose=2 ./VibeServiceGuardian
  RUNNER=("./VibeServiceGuardian")
elif [ -x "./.venv/bin/python3" ]; then
  RUNNER=("./.venv/bin/python3" "-m" "vsg")
else
  printf '%s\n' "未找到原生程序或 .venv。"
  exit 2
fi

"${RUNNER[@]}" --port 0 --data-dir "$DATA_DIR" &
APP_PID=$!

for ((attempt=0; attempt<240; attempt++)); do
  if [ -f "$DATA_DIR/runtime.json" ]; then
    if python3 - "$DATA_DIR" >/dev/null 2>&1 <<'PY'
import json
import sys
import urllib.request
from pathlib import Path

runtime = json.loads((Path(sys.argv[1]) / "runtime.json").read_text(encoding="utf-8"))
with urllib.request.urlopen(f"http://127.0.0.1:{runtime['port']}/healthz", timeout=0.5) as response:
    payload = json.loads(response.read().decode("utf-8"))
    raise SystemExit(
        0
        if response.status == 200
        and payload.get("ok") is True
        and payload.get("version")
        and payload.get("instance_id")
        else 1
    )
PY
    then
      break
    fi
  fi
  sleep 0.25
done

python3 - "$DATA_DIR" <<'PY'
import json
import sys
import time
import urllib.request
from pathlib import Path

data_dir = Path(sys.argv[1])
runtime = json.loads((data_dir / "runtime.json").read_text(encoding="utf-8"))
base = f"http://127.0.0.1:{runtime['port']}"
last_error = None
for _ in range(120):
    try:
        with urllib.request.urlopen(base + "/healthz", timeout=1) as response:
            health = json.loads(response.read().decode("utf-8"))
        with urllib.request.urlopen(base + "/api/bootstrap", timeout=1) as response:
            bootstrap = json.loads(response.read().decode("utf-8"))
        with urllib.request.urlopen(base + "/api/status", timeout=1) as response:
            payload = json.loads(response.read().decode("utf-8"))
        snapshot = payload.get("snapshot") or {}
        if snapshot.get("generated_at") is None or snapshot.get("schema_version") != "2.0":
            raise RuntimeError(
                "first collector snapshot is not ready: "
                f"schema={snapshot.get('schema_version')}; errors={snapshot.get('errors') or []}"
            )
        with urllib.request.urlopen(base + "/api/model-planner/status", timeout=15) as response:
            planner = json.loads(response.read().decode("utf-8"))
        estimate_request = urllib.request.Request(
            base + "/api/model-planner/estimate",
            data=json.dumps({
                "total_users": 25,
                "concurrency": 4,
                "prompt_tokens": 1024,
                "context_tokens": 8192,
                "output_tokens": 512,
                "target_tps_per_user": 8,
                "target_ttft_seconds": 5,
                "preference": "balanced",
                "runtime": "auto",
                "kv_cache_bits": 16,
            }).encode("utf-8"),
            method="POST",
            headers={
                "Content-Type": "application/json",
                "X-VSG-Token": bootstrap["token"],
            },
        )
        with urllib.request.urlopen(estimate_request, timeout=15) as response:
            estimate = json.loads(response.read().decode("utf-8"))
        break
    except Exception as exc:
        last_error = exc
        time.sleep(0.125)
else:
    log_path = data_dir / "vsg.log"
    if log_path.is_file():
        log_tail = log_path.read_text(encoding="utf-8", errors="replace")[-12000:]
        print("--- vsg.log tail ---", file=sys.stderr)
        print(log_tail, file=sys.stderr)
        print("--- end vsg.log tail ---", file=sys.stderr)
    raise SystemExit(f"status API unavailable: {last_error}")

assert health["ok"] is True and health["version"], health
assert bootstrap["version"] == health["version"], bootstrap
assert bootstrap["instance_id"] == health["instance_id"], bootstrap
assert payload["platform"]["key"] == "macos", payload["platform"]
assert payload["platform"]["architecture"] in {"arm64", "x86_64"}, payload["platform"]
snapshot = payload["snapshot"]
assert snapshot["schema_version"] == "2.0", snapshot["schema_version"]
assert snapshot["collectors"]["host"]["method"] == "lsof", snapshot["collectors"]["host"]
assert snapshot["telemetry"]["cpu"]["percent"] is not None, snapshot["telemetry"]["cpu"]
assert snapshot["telemetry"]["memory"]["used_percent"] is not None, snapshot["telemetry"]["memory"]
assert len(snapshot["telemetry"]["disks"]) >= 1, snapshot["telemetry"]["disks"]
assert snapshot["posture"]["overall"]["state"], snapshot["posture"]["overall"]
assert "unknown_domain_count" in snapshot["posture"]["overall"], snapshot["posture"]["overall"]
assert isinstance(snapshot["runtime_probes"], list), snapshot["runtime_probes"]
assert isinstance(snapshot["trusted_nodes"], dict), snapshot["trusted_nodes"]
assert planner["ok"] is True, planner
assert planner["hardware"]["platform"]["key"] == "macos", planner["hardware"]["platform"]
assert planner["catalog"]["model_count"] >= 10, planner["catalog"]
assert planner["catalog"]["offline"] is True, planner["catalog"]
assert planner["privacy"]["telemetry"] is False, planner["privacy"]
assert len(estimate["estimate"]["candidates"]) == planner["catalog"]["model_count"], estimate["estimate"]
assert set(estimate["estimate"]["ceilings"]) == {"physical", "usable", "sla"}, estimate["estimate"]["ceilings"]
assert estimate["estimate"]["runtime_plan"]["binding"].startswith("127.0.0.1:"), estimate["estimate"]["runtime_plan"]
assert estimate["estimate"]["runtime_plan"]["will_execute"] is False, estimate["estimate"]["runtime_plan"]
print(json.dumps({
    "platform": payload["platform"],
    "summary": snapshot["summary"],
    "host_collector": snapshot["collectors"]["host"],
    "model_catalog_count": planner["catalog"]["model_count"],
    "model_estimate_candidates": len(estimate["estimate"]["candidates"]),
    "model_planner_offline": True,
    "telemetry_gpu_count": len(snapshot["telemetry"]["gpus"]),
    "health_overall": snapshot["posture"]["overall"],
}, ensure_ascii=False, indent=2))
PY

for private_path in "$DATA_DIR" "$DATA_DIR/runtime.json" "$DATA_DIR/history.sqlite3" "$DATA_DIR/vsg.log"; do
  mode="$(stat -f '%Lp' "$private_path")"
  case "$private_path" in
    "$DATA_DIR") expected="700" ;;
    *) expected="600" ;;
  esac
  if [ "$mode" != "$expected" ]; then
    printf '隐私权限不匹配：%s 为 %s，预期 %s。\n' "$private_path" "$mode" "$expected"
    exit 3
  fi
done

"${RUNNER[@]}" --data-dir "$DATA_DIR" --stop
wait "$APP_PID"
APP_PID=""
printf '%s\n' "MACOS_NATIVE_VALIDATION_OK"
