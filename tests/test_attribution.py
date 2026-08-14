import tempfile
import time
import unittest
from pathlib import Path

from vsg.attribution import attribute_agent, attribute_project, detect_runtime, identify_agent_process
from vsg.models import ProcessSnapshot
from vsg.sessions import SessionHint


class AttributionTests(unittest.TestCase):
    def test_project_from_nested_git_working_directory(self):
        with tempfile.TemporaryDirectory() as root_value:
            root = Path(root_value)
            project = root / "sample"
            nested = project / "frontend" / "src"
            nested.mkdir(parents=True)
            (project / ".git").mkdir()
            process = ProcessSnapshot(pid=10, name="node.exe", cwd=str(nested), cmdline=["node", "vite"])
            result = attribute_project(process, [], [str(root)])
            self.assertIsNotNone(result.path)
            self.assertEqual(Path(result.path).resolve(), project.resolve())
            self.assertGreaterEqual(result.confidence, 90)

    def test_codex_ancestor_and_session_metadata(self):
        now = time.time()
        process = ProcessSnapshot(pid=20, ppid=19, name="node.exe", cwd=r"E:\vibe coding\demo", create_time=now)
        ancestor = ProcessSnapshot(pid=19, name="codex.exe", exe=r"C:\tools\codex.exe")
        hints = [SessionHint("Codex CLI", "session-123", r"E:\vibe coding\demo", now - 30, "test metadata")]
        result = attribute_agent(process, [ancestor], r"E:\vibe coding\demo", hints)
        self.assertEqual(result.provider, "Codex CLI")
        self.assertEqual(result.session_id, "session-123")
        self.assertTrue(result.active)
        self.assertGreaterEqual(result.confidence, 90)

    def test_workbuddy_signature(self):
        process = ProcessSnapshot(pid=30, name="python.exe")
        ancestor = ProcessSnapshot(pid=29, name="WorkBuddy.exe")
        result = attribute_agent(process, [ancestor], None, [])
        self.assertEqual(result.provider, "WorkBuddy")
        self.assertEqual(result.kind, "agent")

    def test_workbuddy_listener_attributes_itself(self):
        process = ProcessSnapshot(pid=31, name="WorkBuddy.exe")
        result = attribute_agent(process, [ProcessSnapshot(pid=30, name="WorkBuddy.exe")], None, [])
        self.assertEqual(result.provider, "WorkBuddy")
        self.assertTrue(any("服务进程本身" in item for item in result.evidence))

    def test_vscode_fallback(self):
        process = ProcessSnapshot(pid=40, name="node.exe")
        ancestors = [
            ProcessSnapshot(pid=39, name="cmd.exe"),
            ProcessSnapshot(pid=38, name="Code.exe"),
        ]
        result = attribute_agent(process, ancestors, None, [])
        self.assertEqual(result.provider, "VS Code")

    def test_supported_agent_and_terminal_signatures(self):
        signatures = {
            "codex.exe": "Codex CLI",
            "claude.exe": "Claude Code",
            "cursor.exe": "Cursor",
            "windsurf.exe": "Windsurf",
            "code.exe": "VS Code",
            "workbuddy.exe": "WorkBuddy",
            "powershell.exe": "PowerShell",
            "pwsh.exe": "PowerShell",
            "cmd.exe": "CMD",
            "windowsterminal.exe": "Windows Terminal",
            "gnome-terminal-server": "GNOME Terminal",
            "konsole": "Konsole",
        }
        for executable, provider in signatures.items():
            with self.subTest(executable=executable):
                result = attribute_agent(
                    ProcessSnapshot(pid=50, name="node.exe"),
                    [ProcessSnapshot(pid=49, name=executable)],
                    None,
                    [],
                )
                self.assertEqual(result.provider, provider)

    def test_runtime_detection(self):
        self.assertEqual(detect_runtime(ProcessSnapshot(pid=1, name="node.exe", cmdline=["node", "vite"])), "Node.js")
        self.assertEqual(detect_runtime(ProcessSnapshot(pid=2, name="python.exe")), "Python")

    def test_model_runtime_detection(self):
        cases = {
            "Ollama": ProcessSnapshot(pid=3, name="ollama.exe", cmdline=["ollama", "serve"]),
            "llama.cpp": ProcessSnapshot(pid=4, name="llama-server.exe"),
            "vLLM": ProcessSnapshot(
                pid=5,
                name="python",
                cmdline=["python", "-m", "vllm.entrypoints.openai.api_server"],
            ),
            "SGLang": ProcessSnapshot(
                pid=6,
                name="python",
                cmdline=["python", "-m", "sglang.launch_server"],
            ),
            "MLX-LM": ProcessSnapshot(
                pid=7,
                name="python3",
                cmdline=["python3", "-m", "mlx_lm.server"],
            ),
            "LM Studio": ProcessSnapshot(pid=8, name="LM Studio.exe"),
            "TensorRT-LLM": ProcessSnapshot(pid=9, name="python", cmdline=["python", "-m", "tensorrt_llm.serve"]),
            "Text Generation WebUI": ProcessSnapshot(pid=10, name="python", cmdline=["python", "C:\\apps\\text-generation-webui\\server.py", "--loader", "ExLlamav2"]),
            "TabbyAPI": ProcessSnapshot(pid=11, name="tabbyapi"),
        }
        for runtime, snapshot in cases.items():
            with self.subTest(runtime=runtime):
                self.assertEqual(detect_runtime(snapshot), runtime)

    def test_first_batch_agent_signatures(self):
        snapshots = {
            "Hermes Agent": ProcessSnapshot(pid=60, name="python3", cmdline=["python3", "-m", "hermes_cli"]),
            "OpenCode": ProcessSnapshot(pid=61, name="opencode", cmdline=["opencode"]),
            "Aider": ProcessSnapshot(pid=62, name="python3", cmdline=["python3", "-m", "aider"]),
            "Gemini CLI": ProcessSnapshot(
                pid=63,
                name="node",
                cmdline=["node", "/usr/local/lib/node_modules/@google/gemini-cli/dist/index.js"],
            ),
            "Goose": ProcessSnapshot(pid=64, name="goose", cmdline=["goose", "session"]),
        }
        for provider, snapshot in snapshots.items():
            with self.subTest(provider=provider):
                signature = identify_agent_process(snapshot)
                self.assertIsNotNone(signature)
                self.assertEqual(signature.provider, provider)
                self.assertEqual(signature.kind, "agent")

    def test_macos_codex_desktop_and_terminal(self):
        desktop = ProcessSnapshot(
            pid=70,
            name="codex",
            exe="/Applications/Codex.app/Contents/MacOS/Codex",
        )
        self.assertEqual(identify_agent_process(desktop).provider, "Codex Desktop")
        result = attribute_agent(
            ProcessSnapshot(pid=72, name="node"),
            [ProcessSnapshot(pid=71, name="zsh")],
            None,
            [],
        )
        self.assertEqual(result.provider, "Zsh")

    def test_explicit_hermes_resume_id_has_strong_evidence(self):
        result = attribute_agent(
            ProcessSnapshot(pid=80, name="hermes", cmdline=["hermes", "--resume", "session-xyz"]),
            [],
            None,
            [],
        )
        self.assertEqual(result.session_id, "session-xyz")
        self.assertGreaterEqual(result.confidence, 98)


if __name__ == "__main__":
    unittest.main()
