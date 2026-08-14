#!/bin/sh
set -eu

if [ "$(uname -s)" != "Linux" ]; then
  printf '%s\n' "This validation script must run on Linux."
  exit 2
fi

PACKAGE_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)
cd "$PACKAGE_ROOT"
RUNNER=./VibeServiceGuardian
if [ ! -x "$RUNNER" ]; then
  if [ -x ./.venv/bin/python3 ]; then
    RUNNER="./.venv/bin/python3 -m vsg"
  else
    printf '%s\n' "No executable or source virtual environment was found."
    exit 2
  fi
fi

VALIDATION_DIR=$(mktemp -d "${TMPDIR:-/tmp}/vsg-linux-validation.XXXXXX")
cleanup() {
  # shellcheck disable=SC2086
  $RUNNER --data-dir "$VALIDATION_DIR/data" --stop >/dev/null 2>&1 || true
  case "$VALIDATION_DIR" in "${TMPDIR:-/tmp}"/vsg-linux-validation.*) rm -rf "$VALIDATION_DIR" ;; esac
}
trap cleanup EXIT INT TERM

# shellcheck disable=SC2086
$RUNNER --data-dir "$VALIDATION_DIR/data" --port 0 >"$VALIDATION_DIR/stdout.log" 2>"$VALIDATION_DIR/stderr.log" &
APP_PID=$!
RUNTIME="$VALIDATION_DIR/data/runtime.json"
attempt=0
while [ ! -s "$RUNTIME" ] && [ "$attempt" -lt 100 ]; do
  kill -0 "$APP_PID" 2>/dev/null || { printf '%s\n' "VSG exited before writing runtime.json"; exit 3; }
  attempt=$((attempt + 1))
  sleep 0.1
done
[ -s "$RUNTIME" ] || { printf '%s\n' "runtime.json was not created"; exit 3; }

PYTHON_BIN=${PYTHON_BIN:-python3}
PORT=$("$PYTHON_BIN" -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["port"])' "$RUNTIME")
"$PYTHON_BIN" - "$PORT" <<'PY'
import json, sys, urllib.request
port = int(sys.argv[1]); base = f"http://127.0.0.1:{port}"
def get(path):
    with urllib.request.urlopen(base + path, timeout=5) as response:
        return response.status, response.headers, response.read()
status, _, raw = get('/healthz')
health = json.loads(raw); assert status == 200 and health['ok'] is True
status, _, html = get('/')
assert status == 200 and b'id="language-toggle"' in html and b'/assets/i18n.js' in html
status, _, i18n = get('/assets/i18n.js')
assert status == 200 and b'Service Monitor' in i18n
status, _, raw = get('/api/bootstrap')
bootstrap = json.loads(raw); assert bootstrap['platform']['key'] == 'linux' and bootstrap['platform']['supported'] is True
status, _, raw = get('/api/status')
snapshot = json.loads(raw)['snapshot']; assert snapshot['collectors']['host']['method'] == 'psutil'
assert snapshot['telemetry']['hardware']['platform'] == 'Linux'
print(json.dumps({'ok': True, 'port': port, 'platform': bootstrap['platform'], 'services': snapshot['summary']['services']}, ensure_ascii=False))
PY

# shellcheck disable=SC2086
$RUNNER --data-dir "$VALIDATION_DIR/data" --stop
wait "$APP_PID"
printf '%s\n' "Linux native validation passed."
