import os
import re
import shutil
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
START_SCRIPT = ROOT / "Start-macOS-Manual-Test.command"
FINISH_SCRIPT = ROOT / "Finish-macOS-Manual-Test.command"
AUTO_SCRIPT = ROOT / "Run-macOS-VM-Auto-Test.command"
KIT_BUILDER = ROOT / "scripts" / "Build-macOS-Build-Kit.ps1"
QUICKSTART = ROOT / "MACOS-VM-QUICKSTART.md"
UNBRACED_BEFORE_NON_ASCII = re.compile(r"\$[A-Za-z_][A-Za-z0-9_]*(?=[^\x00-\x7f])")


def _git_bash() -> str | None:
    candidates = [
        shutil.which("bash"),
        r"C:\Program Files\Git\bin\bash.exe",
        r"C:\Program Files\Git\usr\bin\bash.exe",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    return None


class MacOSValidationScriptTests(unittest.TestCase):
    def test_shell_variables_next_to_unicode_are_always_braced(self):
        shell_files = [
            *ROOT.glob("*.command"),
            *ROOT.glob("*.sh"),
            *(ROOT / "scripts").glob("*.sh"),
        ]
        findings = []
        for path in shell_files:
            for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if UNBRACED_BEFORE_NON_ASCII.search(line):
                    findings.append(f"{path.relative_to(ROOT)}:{line_number}:{line.strip()}")
        self.assertEqual(findings, [])

    def test_manual_checklist_is_braced_dynamic_atomic_and_precedes_vsg_start(self):
        content = START_SCRIPT.read_text(encoding="utf-8")
        self.assertIn('CHECKLIST_TMP="$RESULT_DIR/.MANUAL-TEST-CHECKLIST.$$.tmp"', content)
        self.assertIn('cat > "$CHECKLIST_TMP" <<EOF', content)
        self.assertIn('mv -f "$CHECKLIST_TMP" "$CHECKLIST"', content)
        for marker in (
            "${VERSION}",
            "${ARCH}",
            "${TEST_PROJECT}",
            "${TEST_PID}",
            "${TEST_PORT}",
        ):
            self.assertIn(marker, content)
        self.assertLess(
            content.index('mv -f "$CHECKLIST_TMP" "$CHECKLIST"'),
            content.index("./Start-VSG.command"),
        )

    def test_manual_service_uses_dynamic_loopback_port_and_atomic_state(self):
        start = START_SCRIPT.read_text(encoding="utf-8")
        finish = FINISH_SCRIPT.read_text(encoding="utf-8")
        self.assertNotIn("8765", start)
        self.assertNotIn("8765", finish)
        self.assertIn('-m http.server 0 --bind 127.0.0.1', start)
        self.assertIn('PORT_FILE="$RESULT_DIR/test-http.port"', start)
        self.assertIn('PORT_FILE="$RESULT_DIR/test-http.port"', finish)
        self.assertIn('TEST_STATE_FILE="$RESULT_DIR/test-http.state"', start)
        self.assertIn('TEST_STATE_FILE="$RESULT_DIR/test-http.state"', finish)
        self.assertIn('grep -Fx "n127.0.0.1:$port"', start)
        self.assertIn('grep -Fx "n127.0.0.1:$port"', finish)
        self.assertIn('mv -f "$PORT_TMP" "$PORT_FILE"', start)
        self.assertIn('mv -f "$PID_TMP" "$PID_FILE"', start)
        self.assertLess(
            start.index('mv -f "$PORT_TMP" "$PORT_FILE"'),
            start.index('mv -f "$PID_TMP" "$PID_FILE"'),
        )

    def test_manual_start_is_idempotent_and_failure_cleanup_is_owned(self):
        content = START_SCRIPT.read_text(encoding="utf-8")
        self.assertIn('if safe_test_pid "$OLD_PID" "$OLD_PORT"; then', content)
        self.assertIn("复用当前构建包已启动的测试服务", content)
        self.assertIn('TEST_CREATED=1', content)
        rollback_start = content.index("rollback_current_test_service()")
        rollback_end = content.index("\n}\n\nfinish()", rollback_start)
        rollback = content[rollback_start:rollback_end]
        self.assertIn('if safe_test_pid "${TEST_PID:-}" "${TEST_PORT:-}"; then', rollback)
        self.assertIn('kill "$TEST_PID"', rollback)
        self.assertNotIn("kill -9", rollback)
        self.assertNotIn("pkill", content)

    def test_manual_start_verifies_vsg_identity_and_health_before_success(self):
        content = START_SCRIPT.read_text(encoding="utf-8")
        for marker in (
            'runtime.get("instance_id")',
            'health.get("instance_id") != instance_id',
            'health.get("version") != expected_version',
            'http://127.0.0.1:{port}/healthz',
            'headers={"Host": f"127.0.0.1:{port}"}',
            "urllib.request.ProxyHandler({})",
        ):
            self.assertIn(marker, content)
        self.assertLess(content.index("./Start-VSG.command"), content.index("wait_for_vsg" , content.index("./Start-VSG.command")))
        self.assertLess(content.index("VSG_URL="), content.index("START_COMPLETED=1"))
        self.assertIn('exec > >(tee "$LOG_PATH") 2>&1', content)
        self.assertNotIn('tee -a "$LOG_PATH"', content)

    def test_finish_requires_dynamic_pid_command_and_port_evidence(self):
        content = FINISH_SCRIPT.read_text(encoding="utf-8")
        self.assertIn('if safe_test_pid "$TEST_PID" "$TEST_PORT"; then', content)
        self.assertIn('输入 CLEANUP $TEST_PID', content)
        self.assertIn('test_service_port=%s', content)
        self.assertIn('vsg_shutdown_status=%s', content)
        self.assertIn('close_log_stream()', content)
        self.assertIn('wait "$TEE_PID"', content)
        self.assertIn('ditto --norsrc', content)
        self.assertIn('EVIDENCE_STAGE="$STATE_ROOT/.evidence-stage-$STAMP"', content)
        self.assertLess(content.index("close_log_stream\n"), content.index("ditto --norsrc"))
        self.assertNotIn("kill -9", content)
        self.assertNotIn("pkill", content)

    def test_quickstart_explains_dynamic_port_and_cross_revision_boundary(self):
        content = QUICKSTART.read_text(encoding="utf-8")
        self.assertIn("端口由 macOS 自动分配", content)
        self.assertIn("不会自动清理旧构建包留下的测试进程", content)

    @unittest.skipUnless(os.name == "nt" and _git_bash(), "requires Windows Git Bash")
    def test_manual_workflow_mocked_end_to_end_reuses_finishes_and_rolls_back(self):
        bash = _git_bash()
        self.assertIsNotNone(bash)
        with tempfile.TemporaryDirectory() as temporary:
            kit_root = Path(temporary) / "Vibe-Service-Guardian-macOS-build-kit-0.8.5.2-r9"
            kit_root.mkdir()
            shutil.copy2(START_SCRIPT, kit_root / START_SCRIPT.name)
            shutil.copy2(FINISH_SCRIPT, kit_root / FINISH_SCRIPT.name)
            fake_bin = kit_root / "fake-bin"
            fake_bin.mkdir()
            portable = (
                kit_root
                / "release"
                / "Vibe-Service-Guardian-macOS-x86_64-0.8.5.2"
            )
            portable.mkdir(parents=True)

            files = {
                kit_root / "pyproject.toml": """
                    [project]
                    version = "0.8.5.2"
                """,
                fake_bin / "uname": """
                    #!/bin/bash
                    case "${1:-}" in
                      -s) printf '%s\\n' Darwin ;;
                      -m) printf '%s\\n' x86_64 ;;
                      *) printf '%s\\n' Darwin ;;
                    esac
                """,
                fake_bin / "sw_vers": """
                    #!/bin/bash
                    printf '%s\\n' 13.7.8
                """,
                fake_bin / "lsof": """
                    #!/bin/bash
                    printf '%s\\n' 'p999' 'f7' 'n127.0.0.1:54321'
                """,
                fake_bin / "ps": """
                    #!/bin/bash
                    printf '%s\\n' '/mock/Python -u -m http.server 0 --bind 127.0.0.1'
                """,
                fake_bin / "open": """
                    #!/bin/bash
                    exit 0
                """,
                fake_bin / "ditto": """
                    #!/bin/bash
                    set -e
                    arguments=("$@")
                    count="${#arguments[@]}"
                    source_dir="${arguments[$((count - 2))]}"
                    output="${arguments[$((count - 1))]}"
                    [ ! -e "$source_dir/._MANUAL-TEST-CHECKLIST.txt" ] || exit 42
                    latest_log="$(ls -1 "$source_dir"/03-manual-test-finish-*.log | tail -n 1)"
                    cp "$latest_log" "$MOCK_CAPTURED_LOG"
                    printf '%s\n' fake-evidence-zip > "$output"
                """,
                fake_bin / "shasum": """
                    #!/bin/bash
                    printf '%064d  %s\n' 0 "${3:-unknown}"
                """,
                fake_bin / "mock-python": """
                    #!/bin/bash
                    if [ "${1:-}" = "-c" ]; then
                      case "${2:-}" in
                        *sys.version_info*) printf '%s\\n' yes ;;
                        *platform.machine*) printf '%s\\n' x86_64 ;;
                        *'from vsg'*) printf '%s\\n' 0.8.5.2 ;;
                        *) exit 2 ;;
                      esac
                      exit 0
                    fi
                    if [ "${1:-}" = "-u" ]; then
                      trap 'exit 0' TERM INT
                      while :; do sleep 1; done
                    fi
                    if [ "${1:-}" = "-" ]; then
                      [ "${MOCK_VSG_FAIL:-0}" = "1" ] && exit 1
                      printf '%s\\n' 'http://127.0.0.1:43921'
                      exit 0
                    fi
                    exit 2
                """,
                portable / "VibeServiceGuardian": """
                    #!/bin/bash
                    exit 0
                """,
                portable / "Start-VSG.command": """
                    #!/bin/bash
                    mkdir -p ./data
                    printf '%s\n' '{}' > ./data/runtime.json
                    exit 0
                """,
                portable / "Stop-VSG.command": """
                    #!/bin/bash
                    rm -f ./data/runtime.json
                    exit 0
                """,
            }
            for path, content in files.items():
                path.write_text(textwrap.dedent(content).lstrip(), encoding="utf-8", newline="\n")
                path.chmod(0o755)

            harness = kit_root / "run-harness.sh"
            harness.write_text(
                textwrap.dedent(
                    """
                    #!/bin/bash
                    set -u
                    export PATH="$PWD/fake-bin:$PATH"
                    export PYTHON_BIN="$PWD/fake-bin/mock-python"
                    state="${PWD}.local-state/acceptance-results"
                    export MOCK_CAPTURED_LOG="$PWD/captured-finish.log"

                    bash ./Start-macOS-Manual-Test.command <<< '' > first.out 2>&1
                    first_status=$?
                    [ "$first_status" -eq 0 ] || exit 21
                    grep -F '地址=http://127.0.0.1:54321' first.out >/dev/null || exit 22
                    [ "$(cat "$state/test-http.port")" = "54321" ] || exit 23

                    bash ./Start-macOS-Manual-Test.command <<< '' > second.out 2>&1
                    second_status=$?
                    [ "$second_status" -eq 0 ] || exit 24
                    grep -F '复用当前构建包已启动的测试服务' second.out >/dev/null || exit 25

                    test_pid="$(cat "$state/test-http.pid")"
                    touch "$state/._MANUAL-TEST-CHECKLIST.txt"
                    printf 'CLEANUP %s\nEXIT VSG\n\n' "$test_pid" \
                      | bash ./Finish-macOS-Manual-Test.command > finish.out 2>&1
                    finish_status=$?
                    [ "$finish_status" -eq 0 ] || exit 26
                    [ ! -e "$state/test-http.pid" ] || exit 27
                    [ ! -e "$state/test-http.port" ] || exit 28
                    [ ! -e "release/Vibe-Service-Guardian-macOS-x86_64-0.8.5.2/data/runtime.json" ] || exit 29
                    summary="$(ls -1 "$state"/FINAL-SUMMARY-*.txt | tail -n 1)"
                    finish_log="$(ls -1 "$state"/03-manual-test-finish-*.log | tail -n 1)"
                    grep -Fx 'test_service_status=STOPPED' "$summary" >/dev/null || exit 30
                    grep -Fx "test_service_pid=$test_pid" "$summary" >/dev/null || exit 31
                    grep -Fx 'test_service_port=54321' "$summary" >/dev/null || exit 32
                    grep -Fx 'vsg_shutdown_status=CONFIRMED' "$summary" >/dev/null || exit 33
                    grep -Fx 'status=STOPPED' "$state/test-http.state" >/dev/null || exit 34
                    cmp "$finish_log" "$MOCK_CAPTURED_LOG" >/dev/null || exit 35
                    evidence_zip="$(ls -1 "${PWD}.local-state"/VSG-macOS-x86_64-0.8.5.2-VM-evidence-*.zip | tail -n 1)"
                    [ -s "$evidence_zip" ] || exit 36
                    [ -s "$evidence_zip.sha256" ] || exit 37

                    export MOCK_VSG_FAIL=1
                    bash ./Start-macOS-Manual-Test.command <<< '' > failure.out 2>&1
                    failure_status=$?
                    [ "$failure_status" -eq 2 ] || exit 38
                    grep -F '本次脚本创建的测试服务已回收' failure.out >/dev/null || exit 39
                    [ ! -e "$state/test-http.pid" ] || exit 40
                    [ ! -e "$state/test-http.port" ] || exit 41
                    rm -f "release/Vibe-Service-Guardian-macOS-x86_64-0.8.5.2/data/runtime.json"
                    exit 0
                    """
                ).lstrip(),
                encoding="utf-8",
                newline="\n",
            )
            harness.chmod(0o755)
            result = subprocess.run(
                [str(bash), "./run-harness.sh"],
                cwd=kit_root,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=40,
                check=False,
            )
            details = "\n".join(
                [
                    f"returncode={result.returncode}",
                    f"stdout={result.stdout}",
                    f"stderr={result.stderr}",
                    (kit_root / "first.out").read_text(encoding="utf-8", errors="replace")
                    if (kit_root / "first.out").exists()
                    else "first.out missing",
                    (kit_root / "second.out").read_text(encoding="utf-8", errors="replace")
                    if (kit_root / "second.out").exists()
                    else "second.out missing",
                    (kit_root / "finish.out").read_text(encoding="utf-8", errors="replace")
                    if (kit_root / "finish.out").exists()
                    else "finish.out missing",
                    (kit_root / "failure.out").read_text(encoding="utf-8", errors="replace")
                    if (kit_root / "failure.out").exists()
                    else "failure.out missing",
                ]
            )
            self.assertEqual(result.returncode, 0, details)

    def test_r9_revision_is_consistent_between_runner_and_builder(self):
        runner = AUTO_SCRIPT.read_text(encoding="utf-8")
        self.assertIn('BUILD_KIT_REVISION="r9"', runner)
        if KIT_BUILDER.exists():
            builder = KIT_BUILDER.read_text(encoding="utf-8")
            self.assertIn("$KitRevision = 'r9'", builder)
        else:
            self.assertTrue(ROOT.name.endswith("-r9"), ROOT.name)


if __name__ == "__main__":
    unittest.main()
