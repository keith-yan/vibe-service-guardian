from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .models import AgentAttribution, ProcessSnapshot, ProjectAttribution
from .sessions import SessionHint, match_session_hint


PROJECT_MARKERS = (
    ".git",
    "package.json",
    "pyproject.toml",
    "Cargo.toml",
    "go.mod",
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
    "*.sln",
)

AGENT_EXACT_NAMES: dict[str, tuple[str, int, str]] = {
    "codex.exe": ("Codex CLI", 96, "agent"),
    "codex": ("Codex CLI", 96, "agent"),
    "claude.exe": ("Claude Code", 96, "agent"),
    "claude": ("Claude Code", 96, "agent"),
    "workbuddy.exe": ("WorkBuddy", 98, "agent"),
    "workbuddy": ("WorkBuddy", 98, "agent"),
    "codebuddy.exe": ("WorkBuddy", 96, "agent"),
    "codebuddy": ("WorkBuddy", 96, "agent"),
    "cursor.exe": ("Cursor", 90, "ide"),
    "windsurf.exe": ("Windsurf", 90, "ide"),
    "code.exe": ("VS Code", 84, "ide"),
    "cursor": ("Cursor", 90, "ide"),
    "windsurf": ("Windsurf", 90, "ide"),
    "code": ("VS Code", 84, "ide"),
    "hermes.exe": ("Hermes Agent", 98, "agent"),
    "hermes": ("Hermes Agent", 98, "agent"),
    "hermes-agent": ("Hermes Agent", 98, "agent"),
    "opencode.exe": ("OpenCode", 98, "agent"),
    "opencode": ("OpenCode", 98, "agent"),
    "aider.exe": ("Aider", 98, "agent"),
    "aider": ("Aider", 98, "agent"),
    "aider-chat": ("Aider", 98, "agent"),
    "gemini.exe": ("Gemini CLI", 98, "agent"),
    "gemini": ("Gemini CLI", 98, "agent"),
    "goose.exe": ("Goose", 98, "agent"),
    "goose": ("Goose", 98, "agent"),
    "goosed.exe": ("Goose", 98, "agent"),
    "goosed": ("Goose", 98, "agent"),
}

TERMINAL_EXACT_NAMES: dict[str, tuple[str, int]] = {
    "powershell.exe": ("PowerShell", 75),
    "pwsh.exe": ("PowerShell", 78),
    "cmd.exe": ("CMD", 72),
    "windowsterminal.exe": ("Windows Terminal", 72),
    "gnome-terminal-server": ("GNOME Terminal", 76),
    "gnome-terminal": ("GNOME Terminal", 76),
    "konsole": ("Konsole", 76),
    "kitty": ("Kitty", 76),
    "alacritty": ("Alacritty", 76),
    "tilix": ("Tilix", 76),
    "xterm": ("XTerm", 70),
    "terminal": ("macOS Terminal", 76),
    "iterm2": ("iTerm2", 78),
    "warp": ("Warp", 78),
    "ghostty": ("Ghostty", 78),
    "wezterm": ("WezTerm", 76),
    "wezterm-gui": ("WezTerm", 76),
    "zsh": ("Zsh", 70),
    "bash": ("Bash", 70),
    "fish": ("Fish", 70),
    "sh": ("Shell", 66),
}


@dataclass(frozen=True, slots=True)
class AgentProcessSignature:
    provider: str
    confidence: int
    kind: str
    evidence: str


WRAPPER_NAMES = {
    "node",
    "node.exe",
    "python",
    "python.exe",
    "python3",
    "python3.exe",
    "pythonw.exe",
    "bun",
    "bun.exe",
    "deno",
    "deno.exe",
    "uv",
    "uv.exe",
    "uvx",
    "uvx.exe",
    "npx",
    "npx.cmd",
    "npm",
    "npm.cmd",
}

COMMAND_AGENT_TOKENS: dict[str, tuple[str, int]] = {
    "hermes": ("Hermes Agent", 92),
    "hermes-agent": ("Hermes Agent", 92),
    "hermes_cli": ("Hermes Agent", 92),
    "opencode": ("OpenCode", 92),
    "opencode-ai": ("OpenCode", 92),
    "aider": ("Aider", 92),
    "aider-chat": ("Aider", 92),
    "gemini": ("Gemini CLI", 92),
    "goose": ("Goose", 92),
    "goosed": ("Goose", 92),
}


def _norm(path: str) -> str:
    return os.path.normcase(os.path.abspath(os.path.expanduser(path)))


def _is_under(path: str, root: str) -> bool:
    try:
        return os.path.commonpath([_norm(path), _norm(root)]) == _norm(root)
    except (ValueError, OSError):
        return False


def _has_marker(directory: Path) -> bool:
    for marker in PROJECT_MARKERS:
        if "*" in marker:
            try:
                if next(directory.glob(marker), None):
                    return True
            except OSError:
                continue
        elif (directory / marker).exists():
            return True
    return False


def _project_from_candidate(candidate: str, roots: list[str]) -> tuple[str | None, int, str | None]:
    try:
        path = Path(candidate).expanduser()
        if path.suffix and not path.is_dir():
            path = path.parent
        resolved = path.resolve(strict=False)
    except (OSError, ValueError):
        return None, 0, None

    for root_value in sorted(roots, key=len, reverse=True):
        try:
            root = Path(root_value).expanduser().resolve(strict=False)
        except (OSError, ValueError):
            continue
        if not _is_under(str(resolved), str(root)):
            continue
        current = resolved
        while _is_under(str(current), str(root)):
            if _has_marker(current):
                return str(current), 94, "检测到项目标记文件"
            if current == root:
                break
            current = current.parent
        try:
            relative = resolved.relative_to(root)
        except ValueError:
            continue
        if relative.parts:
            return str(root / relative.parts[0]), 76, "命中已配置项目根目录的一级子目录"
        return str(root), 60, "命中已配置项目根目录"
    return None, 0, None


WINDOWS_PATH_RE = re.compile(r"(?i)([a-z]:\\[^\r\n\t\"']+)")


def _command_paths(command: str) -> list[str]:
    paths: list[str] = []
    for match in WINDOWS_PATH_RE.finditer(command):
        value = match.group(1).strip().rstrip(",;)")
        if value:
            paths.append(value)
    return paths


def _argument_paths(arguments: list[str]) -> list[str]:
    paths: list[str] = []
    for raw in arguments:
        value = raw.strip().strip("\"'")
        if "=" in value and value.startswith("-"):
            value = value.split("=", 1)[1]
        if value.startswith("/") and not value.startswith("//"):
            paths.append(value)
        elif re.match(r"(?i)^[a-z]:[\\/]", value):
            paths.append(value)
    return paths


def attribute_project(
    process: ProcessSnapshot,
    ancestors: list[ProcessSnapshot],
    project_roots: list[str],
) -> ProjectAttribution:
    candidates: list[tuple[str, str, int]] = []
    if process.cwd:
        candidates.append((process.cwd, "服务进程工作目录", 5))
    command_paths = [*_command_paths(process.command), *_argument_paths(process.cmdline)]
    for path in dict.fromkeys(command_paths):
        candidates.append((path, "服务命令中的本地路径", 0))
    for index, ancestor in enumerate(ancestors[:8]):
        if ancestor.cwd:
            candidates.append((ancestor.cwd, f"第 {index + 1} 级父进程工作目录", max(0, 2 - index)))

    best_path: str | None = None
    best_score = 0
    best_evidence: list[str] = []
    for candidate, source, bonus in candidates:
        project_path, score, marker = _project_from_candidate(candidate, project_roots)
        score += bonus
        if project_path and score > best_score:
            best_path = project_path
            best_score = min(score, 99)
            best_evidence = [source]
            if marker:
                best_evidence.append(marker)
    if not best_path:
        return ProjectAttribution(evidence=["未从工作目录、命令和父进程链命中已配置项目根目录"])
    return ProjectAttribution(
        name=Path(best_path).name or best_path,
        path=best_path,
        confidence=best_score,
        evidence=best_evidence,
    )


def _desktop_codex(snapshot: ProcessSnapshot) -> bool:
    executable = (snapshot.exe or "").lower()
    command = snapshot.command.lower()
    is_windows_package = "openai.codex_" in executable or "\\openai.codex" in executable
    windows_main = (
        snapshot.name.lower() == "chatgpt.exe"
        and is_windows_package
        and "--type=" not in command
        and "crashpad-handler" not in command
    )
    windows_internal = snapshot.name.lower() == "codex.exe" and is_windows_package
    macos_desktop = "/codex.app/contents/macos/" in executable.replace("\\", "/")
    return windows_main or windows_internal or macos_desktop


def _basename(value: str) -> str:
    normalized = value.replace("\\", "/").rstrip("/")
    return normalized.rsplit("/", 1)[-1].lower()


def _is_wrapper_name(value: str) -> bool:
    lowered = value.lower()
    return lowered in WRAPPER_NAMES or bool(re.fullmatch(r"python(?:3(?:\.\d+)*)?(?:\.exe)?", lowered))


def identify_agent_process(snapshot: ProcessSnapshot) -> AgentProcessSignature | None:
    if _desktop_codex(snapshot):
        return AgentProcessSignature("Codex Desktop", 100, "agent", "桌面应用可执行路径")

    lowered = snapshot.name.lower()
    exact = AGENT_EXACT_NAMES.get(lowered)
    if exact:
        provider, confidence, kind = exact
        return AgentProcessSignature(provider, confidence, kind, "进程名精确匹配")

    executable_name = _basename(snapshot.exe or "")
    exact = AGENT_EXACT_NAMES.get(executable_name)
    if exact:
        provider, confidence, kind = exact
        return AgentProcessSignature(provider, confidence - 1, kind, "可执行文件名精确匹配")

    if not _is_wrapper_name(lowered) and not _is_wrapper_name(executable_name):
        return None

    arguments = snapshot.cmdline[:10]
    for index, raw in enumerate(arguments):
        token = _basename(raw).removesuffix(".exe").removesuffix(".cmd")
        module = raw.lower().replace("-", "_") if index and arguments[index - 1] == "-m" else ""
        candidate = module or token
        if candidate in COMMAND_AGENT_TOKENS:
            provider, confidence = COMMAND_AGENT_TOKENS[candidate]
            return AgentProcessSignature(provider, confidence, "agent", "包装运行时命令令牌")

    command_head = " ".join(arguments).lower().replace("\\", "/")
    package_patterns = (
        ("Hermes Agent", ("/hermes_cli/", "-m hermes_cli", "/hermes-agent/")),
        ("OpenCode", ("/opencode-ai/", "/packages/opencode/", "/opencode/bin/")),
        ("Aider", ("-m aider", "/aider/", "/aider-chat/")),
        ("Gemini CLI", ("@google/gemini-cli", "/gemini-cli/")),
        ("Goose", ("/aaif-goose/", "/block/goose/")),
        ("Codex CLI", ("@openai/codex", "/codex/bin/")),
        ("Claude Code", ("@anthropic-ai/claude-code",)),
    )
    for provider, patterns in package_patterns:
        if any(pattern in command_head for pattern in patterns):
            return AgentProcessSignature(provider, 90, "agent", "包装运行时包路径")
    return None


def _command_session_id(provider: str, snapshot: ProcessSnapshot) -> str | None:
    arguments = snapshot.cmdline[:20]
    flag_map = {
        "Hermes Agent": {"--resume", "-r"},
        "OpenCode": {"--session", "-s"},
        "Gemini CLI": {"--resume", "-r"},
        "Claude Code": {"--resume", "-r"},
    }
    flags = flag_map.get(provider, set())
    for index, argument in enumerate(arguments[:-1]):
        if argument.lower() in flags:
            value = arguments[index + 1].strip()
            if value and not value.startswith("-") and value != "[REDACTED]":
                return value[:160]
    if provider == "Codex CLI":
        for index, argument in enumerate(arguments[:-1]):
            if argument.lower() == "resume":
                value = arguments[index + 1].strip()
                if value and not value.startswith("-"):
                    return value[:160]
    return None


def attribute_agent(
    process: ProcessSnapshot,
    ancestors: list[ProcessSnapshot],
    project_path: str | None,
    session_hints: Iterable[SessionHint] = (),
) -> AgentAttribution:
    chain = [process, *ancestors[:11]]
    agent_matches: list[tuple[int, int, str, str]] = []
    terminal_matches: list[tuple[int, int, str, str]] = []

    for index, snapshot in enumerate(chain):
        lowered = snapshot.name.lower()
        distance_penalty = max(0, index - 1)
        signature = identify_agent_process(snapshot)
        if signature:
            agent_matches.append(
                (signature.confidence - distance_penalty, index, signature.provider, signature.kind)
            )
            continue
        terminal = TERMINAL_EXACT_NAMES.get(lowered)
        if terminal:
            provider, confidence = terminal
            terminal_matches.append((confidence - distance_penalty, index, provider, "terminal"))

    selected = max(agent_matches, key=lambda item: (item[0], -item[1]), default=None)
    if selected is None:
        selected = max(terminal_matches, key=lambda item: (item[0], -item[1]), default=None)
    if selected is None:
        return AgentAttribution(
            evidence=["当前进程链中未发现受支持的 Agent、IDE 或终端签名"],
        )

    confidence, index, provider, kind = selected
    location = "服务进程本身" if index == 0 else f"第 {index} 级父进程"
    evidence = [f"{location}命中 {provider} 签名"]
    matched_snapshot = chain[index]
    session_id = _command_session_id(provider, matched_snapshot)
    if session_id:
        confidence = max(confidence, 98)
        evidence.append("运行命令显式携带会话恢复标识")
    hint, hint_score = match_session_hint(provider, project_path, process.create_time, session_hints)
    if not session_id and hint and hint_score >= 60:
        session_id = hint.session_id
        confidence = max(confidence, hint_score)
        evidence.append(f"同项目和时间窗口匹配 {hint.source}")
    elif not session_id and provider in {
        "Cursor",
        "Windsurf",
        "VS Code",
        "WorkBuddy",
        "Aider",
        "Gemini CLI",
        "Goose",
    }:
        evidence.append("该产品未提供已核验的稳定本地会话标识接口，仅确认进程归属")

    return AgentAttribution(
        provider=provider,
        kind=kind,
        session_id=session_id,
        confidence=max(1, min(confidence, 100)),
        active=True,
        evidence=evidence,
    )


def detect_runtime(process: ProcessSnapshot) -> str:
    name = process.name.lower()
    command = process.command.lower()
    model_names = {
        "ollama": "Ollama",
        "ollama.exe": "Ollama",
        "llama-server": "llama.cpp",
        "llama-server.exe": "llama.cpp",
        "llama_server": "llama.cpp",
        "llamafile": "llama.cpp",
        "llamafile.exe": "llama.cpp",
        "koboldcpp": "KoboldCpp",
        "koboldcpp.exe": "KoboldCpp",
        "lm studio.exe": "LM Studio",
        "lmstudio.exe": "LM Studio",
        "lms": "LM Studio",
        "lms.exe": "LM Studio",
        "text-generation-launcher": "Hugging Face TGI",
        "text-generation-launcher.exe": "Hugging Face TGI",
        "comfyui": "ComfyUI",
        "comfyui.exe": "ComfyUI",
        "trtllm-serve": "TensorRT-LLM",
        "trtllm-serve.exe": "TensorRT-LLM",
        "tabbyapi": "TabbyAPI",
        "tabbyapi.exe": "TabbyAPI",
    }
    if name in model_names:
        return model_names[name]
    command_checks = (
        (r"(?:^|\s)(?:-m\s+)?vllm(?:\.|\s+serve\b)", "vLLM"),
        (r"(?:^|\s)(?:-m\s+)?sglang(?:\.|\s+launch-server\b)", "SGLang"),
        (r"(?:^|\s)(?:-m\s+)?mlx_lm\.server\b", "MLX-LM"),
        (r"(?:^|\s)(?:-m\s+)?ktransformers(?:\.|\s)", "KTransformers"),
        (r"(?:^|\s)(?:-m\s+)?(?:tensorrt_llm|trtllm-serve)(?:\.|\s)", "TensorRT-LLM"),
        (r"(?:^|[\\/])text-generation-webui[\\/].*server\.py(?:\s|$)", "Text Generation WebUI"),
        (r"(?:^|\s)(?:-m\s+)?tabbyapi(?:\.|\s|$)", "TabbyAPI"),
        (r"(?:^|\s)(?:python(?:3|\.exe)?\s+)?(?:main\.py\s+.*)?comfyui(?:\s|$)", "ComfyUI"),
        (r"(?:^|[\\/])comfyui[\\/].*main\.py(?:\s|$)", "ComfyUI"),
        (r"(?:^|\s)llama-server(?:\.exe)?(?:\s|$)", "llama.cpp"),
        (r"(?:^|\s)ollama(?:\.exe)?\s+serve(?:\s|$)", "Ollama"),
    )
    for pattern, label in command_checks:
        if re.search(pattern, command):
            return label
    checks = (
        (("node.exe", "node", "npm.exe", "npm", "pnpm.exe", "pnpm", "bun.exe", "bun", "deno.exe", "deno"), "Node.js"),
        (("python.exe", "python", "pythonw.exe", "python3.exe", "python3", "uv.exe", "uv"), "Python"),
        (("java.exe", "javaw.exe", "java"), "Java"),
        (("dotnet.exe", "dotnet"), ".NET"),
        (("php.exe", "php"), "PHP"),
        (("ruby.exe", "ruby"), "Ruby"),
        (("go.exe", "go"), "Go"),
        (("cargo.exe", "cargo"), "Rust"),
        (("powershell.exe", "pwsh.exe", "pwsh"), "PowerShell"),
    )
    for names, label in checks:
        if name in names:
            if name == "node.exe" and "electron" in command:
                return "Electron"
            return label
    if name.endswith(".exe"):
        return name[:-4] or "Windows"
    return name or "unknown"
