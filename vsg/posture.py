from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import time
from typing import Any, Callable, Sequence

from .hardware import CommandResult


CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0
Runner = Callable[[Sequence[str], float], CommandResult]
REVERSE_PROXY_NAMES = {"nginx", "nginx.exe", "caddy", "caddy.exe", "traefik", "traefik.exe", "httpd", "httpd.exe", "apache2", "cloudflared", "cloudflared.exe"}


def _run_command(args: Sequence[str], timeout: float = 12.0) -> CommandResult:
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
            creationflags=CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return CommandResult(127, "", type(exc).__name__)
    return CommandResult(completed.returncode, completed.stdout, completed.stderr)


def _as_list(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return []


def _port_matches(specification: str, port: int) -> bool:
    value = specification.strip().lower()
    if value in {"any", "*"}:
        return True
    if value.isdigit():
        return int(value) == port
    match = re.fullmatch(r"(\d+)\s*-\s*(\d+)", value)
    return bool(match and int(match.group(1)) <= port <= int(match.group(2)))


def parse_windows_firewall(profiles_raw: str, rules_raw: str, ports: set[int]) -> dict[str, Any]:
    try:
        profiles_value = json.loads(profiles_raw or "[]")
    except json.JSONDecodeError:
        profiles_value = []
    try:
        rules_value = json.loads(rules_raw or "[]")
    except json.JSONDecodeError:
        rules_value = []
    profiles = []
    for item in _as_list(profiles_value):
        profiles.append(
            {
                "name": str(item.get("Name") or "unknown"),
                "enabled": bool(item.get("Enabled")),
                "default_inbound": str(item.get("DefaultInboundAction") or "Unknown"),
                "default_outbound": str(item.get("DefaultOutboundAction") or "Unknown"),
            }
        )
    matches: dict[int, list[dict[str, Any]]] = {port: [] for port in ports}
    broad_allow_count = 0
    for item in _as_list(rules_value):
        protocol = str(item.get("Protocol") or "").upper()
        local_port = str(item.get("LocalPort") or "")
        if protocol not in {"TCP", "6", "ANY", "256"}:
            continue
        if local_port.lower() in {"any", "*"}:
            broad_allow_count += 1
        for port in ports:
            if _port_matches(local_port, port):
                matches[port].append(
                    {
                        "name": str(item.get("DisplayName") or item.get("Name") or "允许规则")[:160],
                        "profile": str(item.get("Profile") or "Any")[:80],
                        "local_port": local_port[:80],
                    }
                )
    return {
        "platform": "windows",
        "status": "measured" if profiles else "partial",
        "profiles": profiles,
        "all_profiles_enabled": bool(profiles) and all(item["enabled"] for item in profiles),
        "inbound_allow_matches": {str(port): value[:20] for port, value in matches.items()},
        "broad_allow_rule_count": broad_allow_count,
        "limitations": ["规则匹配只覆盖 Windows 当前活动策略中的启用入站允许规则；仍不能判断路由器/NAT 的公网可达性"],
    }


def parse_linux_firewall(raw: str, ports: set[int], backend: str, enabled: bool | None) -> dict[str, Any]:
    """Extract only explicit TCP allow evidence from common Linux firewall output."""

    matches: dict[int, list[dict[str, Any]]] = {port: [] for port in ports}
    broad_allow_count = 0
    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    for line in lines:
        lower = line.lower()
        allowed = "allow" in lower or "accept" in lower or backend == "firewalld" and "ports:" in lower
        if not allowed:
            continue
        if re.search(r"\b(?:anywhere|0\.0\.0\.0/0|::/0)\b", lower) and not re.search(r"\b\d{1,5}(?:/tcp|\s+tcp)\b", lower):
            broad_allow_count += 1
        for port in ports:
            patterns = (
                rf"(?<!\d){port}/tcp\b",
                rf"\btcp\s+dport\s+(?:\{{[^}}]*\b)?{port}\b",
                rf"\bdport\s+{port}\b",
            )
            if any(re.search(pattern, lower) for pattern in patterns):
                matches[port].append({"name": line[:160], "profile": backend, "local_port": str(port)})
    return {
        "platform": "linux",
        "backend": backend,
        "status": "measured" if enabled is not None else "partial",
        "profiles": [{"name": backend, "enabled": enabled}],
        "all_profiles_enabled": enabled,
        "inbound_allow_matches": {str(port): value[:20] for port, value in matches.items()},
        "broad_allow_rule_count": broad_allow_count,
        "limitations": ["Read-only matching covers visible host firewall rules only; it cannot prove router, NAT, cloud-security-group, or container-network reachability"],
    }


class FirewallCollector:
    def __init__(self, runner: Runner = _run_command, cache_seconds: float = 60.0):
        self.runner = runner
        self.cache_seconds = cache_seconds
        self._cache_at = 0.0
        self._cache_ports: set[int] = set()
        self._cache: dict[str, Any] | None = None

    def _windows(self, ports: set[int]) -> dict[str, Any]:
        powershell = shutil.which("powershell.exe") or shutil.which("powershell")
        if not powershell:
            return {"platform": "windows", "status": "unavailable", "profiles": [], "limitations": ["未找到 PowerShell，无法读取 Windows 防火墙"]}
        profile_script = (
            "Get-NetFirewallProfile | ForEach-Object { [pscustomobject]@{"
            "Name=$_.Name.ToString();Enabled=[bool]$_.Enabled;"
            "DefaultInboundAction=$_.DefaultInboundAction.ToString();"
            "DefaultOutboundAction=$_.DefaultOutboundAction.ToString()} } | ConvertTo-Json -Compress"
        )
        profiles = self.runner([powershell, "-NoProfile", "-NonInteractive", "-Command", profile_script], 8)
        if profiles.returncode != 0:
            return {
                "platform": "windows",
                "status": "unavailable",
                "profiles": [],
                "limitations": [f"Windows 防火墙读取失败：{profiles.stderr.strip()[:160] or '权限或组件不可用'}"],
            }
        rules_raw = "[]"
        if ports:
            rules_script = (
                "$rows=@(); Get-NetFirewallRule -PolicyStore ActiveStore -Enabled True -Direction Inbound -Action Allow | "
                "ForEach-Object { $rule=$_; Get-NetFirewallPortFilter -AssociatedNetFirewallRule $rule | "
                "ForEach-Object { $rows += [pscustomobject]@{Name=$rule.Name;DisplayName=$rule.DisplayName;"
                "Profile=$rule.Profile.ToString();Protocol=$_.Protocol.ToString();LocalPort=$_.LocalPort.ToString()} } }; "
                "$rows | ConvertTo-Json -Compress"
            )
            rules = self.runner([powershell, "-NoProfile", "-NonInteractive", "-Command", rules_script], 15)
            if rules.returncode == 0:
                rules_raw = rules.stdout
        value = parse_windows_firewall(profiles.stdout, rules_raw, ports)
        if not ports:
            value["rules_status"] = "not_requested_no_exposed_model_port"
            value["broad_allow_rule_count"] = None
        elif rules_raw == "[]":
            value["rules_status"] = "unavailable_or_no_matching_rules"
        else:
            value["rules_status"] = "measured"
        return value

    def _macos(self) -> dict[str, Any]:
        executable = "/usr/libexec/ApplicationFirewall/socketfilterfw"
        if not os.path.isfile(executable):
            return {"platform": "macos", "status": "unavailable", "profiles": [], "limitations": ["macOS Application Firewall 工具不可用"]}
        global_state = self.runner([executable, "--getglobalstate"], 5)
        stealth = self.runner([executable, "--getstealthmode"], 5)
        text = f"{global_state.stdout} {global_state.stderr}".lower()
        enabled = "enabled" in text and "disabled" not in text
        return {
            "platform": "macos",
            "status": "measured" if global_state.returncode == 0 else "unavailable",
            "profiles": [{"name": "Application Firewall", "enabled": enabled}],
            "all_profiles_enabled": enabled,
            "stealth_mode": (stealth.stdout or stealth.stderr).strip()[:160] if stealth.returncode == 0 else None,
            "inbound_allow_matches": {},
            "limitations": ["非提权模式不解析 pf 规则，也不能判断路由器/NAT 的公网可达性"],
        }

    def _linux(self, ports: set[int]) -> dict[str, Any]:
        ufw = shutil.which("ufw")
        if ufw:
            status = self.runner([ufw, "status"], 7)
            if status.returncode == 0:
                enabled = "status: active" in status.stdout.lower()
                return parse_linux_firewall(status.stdout, ports, "ufw", enabled)
        firewall_cmd = shutil.which("firewall-cmd")
        if firewall_cmd:
            state = self.runner([firewall_cmd, "--state"], 5)
            rules = self.runner([firewall_cmd, "--list-all"], 7)
            if rules.returncode == 0:
                enabled = state.returncode == 0 and "running" in state.stdout.lower()
                return parse_linux_firewall(rules.stdout, ports, "firewalld", enabled)
        nft = shutil.which("nft")
        if nft:
            rules = self.runner([nft, "list", "ruleset"], 8)
            if rules.returncode == 0:
                return parse_linux_firewall(rules.stdout, ports, "nftables", bool(rules.stdout.strip()))
        return {
            "platform": "linux",
            "status": "unavailable",
            "profiles": [],
            "inbound_allow_matches": {str(port): [] for port in ports},
            "limitations": ["No readable ufw, firewalld, or nftables status was available to the current unprivileged process"],
        }

    def collect(self, services: list[dict[str, Any]]) -> dict[str, Any]:
        ports = {
            int(endpoint.get("port"))
            for service in services
            if service.get("metadata", {}).get("model_runtime")
            for endpoint in service.get("endpoints", [])
            if endpoint.get("protocol") == "TCP" and endpoint.get("exposure") != "loopback"
        }
        now = time.time()
        if self._cache is not None and ports == self._cache_ports and now - self._cache_at < self.cache_seconds:
            return json.loads(json.dumps(self._cache, ensure_ascii=False))
        system = platform.system().lower()
        if system == "windows":
            value = self._windows(ports)
        elif system == "darwin":
            value = self._macos()
        elif system == "linux":
            value = self._linux(ports)
        else:
            value = {
                "platform": system,
                "status": "unavailable",
                "profiles": [],
                "limitations": ["当前平台未实现只读防火墙适配器"],
            }
        value["captured_at"] = now
        self._cache = value
        self._cache_at = now
        self._cache_ports = ports
        return json.loads(json.dumps(value, ensure_ascii=False))


def _reverse_proxies(services: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for service in services:
        name = str(service.get("process", {}).get("name") or "").lower()
        runtime = str(service.get("runtime") or "").lower()
        if name not in REVERSE_PROXY_NAMES and runtime not in REVERSE_PROXY_NAMES:
            continue
        result.append(
            {
                "service_id": service.get("id"),
                "name": service.get("display_name") or name,
                "pid": service.get("process", {}).get("pid"),
                "endpoints": service.get("endpoints", []),
                "configuration_status": "process_detected_only",
                "note": "默认不读取代理配置文件；可在明确选择文件后执行脱敏检查",
            }
        )
    return result


def _domain(label: str, state: str, score: int, evidence: list[str], unknowns: list[str]) -> dict[str, Any]:
    return {
        "label": label,
        "state": state,
        "score": max(0, min(int(score), 100)),
        "evidence": evidence,
        "unknowns": unknowns,
    }


class PostureEvaluator:
    def __init__(self, firewall: FirewallCollector | None = None):
        self.firewall = firewall or FirewallCollector()

    def evaluate(
        self,
        telemetry: dict[str, Any],
        services: list[dict[str, Any]],
        runtime_probes: list[dict[str, Any]],
    ) -> dict[str, Any]:
        firewall = self.firewall.collect(services)
        findings: list[dict[str, Any]] = []
        memory_percent = float(telemetry.get("memory", {}).get("used_percent") or 0)
        low_disks = [item for item in telemetry.get("disks", []) if item.get("low_space")]
        temperatures = telemetry.get("sensors", {}).get("temperatures", [])
        hottest = max((float(item.get("current_c") or 0) for item in temperatures), default=0)
        machine_score = 100
        machine_state = "healthy"
        if memory_percent >= 95 or hottest >= 95:
            machine_score -= 50
            machine_state = "critical"
        elif memory_percent >= 85 or hottest >= 85:
            machine_score -= 25
            machine_state = "warning"
        if low_disks:
            machine_score -= 25
            machine_state = "warning" if machine_state == "healthy" else machine_state
            findings.append(
                {
                    "severity": "warning",
                    "domain": "machine",
                    "title": "模型磁盘剩余空间低于阈值",
                    "evidence": "、".join(f"{item.get('root')} 剩余 {item.get('free_gib')} GiB" for item in low_disks),
                    "action": "清理缓存或把新模型放到空间更充足的卷；VSG 不自动删除文件",
                }
            )
        machine_unknowns = []
        if telemetry.get("sensors", {}).get("temperature_status") != "measured":
            machine_unknowns.append("温度不可见")
        if telemetry.get("sensors", {}).get("fan_status") != "measured":
            machine_unknowns.append("风扇转速不可见")
        if telemetry.get("power", {}).get("measurement_scope") == "unavailable":
            machine_unknowns.append("功耗不可见")

        performance_score = 100
        performance_state = "healthy" if runtime_probes else "unknown"
        performance_evidence: list[str] = []
        loaded = 0
        for probe in runtime_probes:
            health = probe.get("health")
            if probe.get("model_load") == "loaded":
                loaded += 1
            if health in {"unhealthy", "unreachable", "probe_error"}:
                performance_score -= 35
                performance_state = "warning"
                findings.append(
                    {
                        "severity": "warning",
                        "domain": "performance",
                        "title": f"{probe.get('runtime')}:{probe.get('port')} 运行时未就绪",
                        "evidence": f"只读健康探测状态：{health}",
                        "action": "查看服务日志和模型加载状态；不要仅凭进程仍存在判断服务可用",
                    }
                )
            elif health == "loading":
                performance_score -= 15
                performance_state = "warning"
            perf = probe.get("performance", {})
            if (perf.get("requests_waiting") or 0) > 0:
                performance_score -= 10
                performance_state = "warning"
            if perf.get("generation_tps") is not None:
                performance_evidence.append(f"{probe.get('runtime')} 实测生成 {perf.get('generation_tps')} tok/s")
        performance_unknowns = [] if runtime_probes else ["未发现可探测的本地模型服务"]
        if runtime_probes and not any(item.get("performance", {}).get("generation_tps") is not None for item in runtime_probes):
            performance_unknowns.append("运行时未暴露被动 TPS 指标；需显式短基准")
        if runtime_probes and not any(item.get("performance", {}).get("ttft_seconds_average") is not None for item in runtime_probes):
            performance_unknowns.append("运行时未暴露被动 TTFT 指标；需显式短基准")

        security_score = 100
        security_state = "healthy"
        security_evidence: list[str] = []
        probe_by_service = {item.get("service_id"): item for item in runtime_probes}
        for service in services:
            if not service.get("metadata", {}).get("model_runtime"):
                continue
            exposures = {item.get("exposure") for item in service.get("endpoints", [])}
            probe = probe_by_service.get(service.get("id"), {})
            auth = probe.get("security", {}).get("auth_posture", "unknown")
            if "all_interfaces" in exposures and auth == "unauthenticated_read":
                security_score -= 45
                security_state = "critical"
                findings.append(
                    {
                        "severity": "critical",
                        "domain": "security",
                        "title": f"{service.get('display_name')} 监听所有网卡且只读 API 未要求认证",
                        "evidence": f"端口 {', '.join(str(item.get('port')) for item in service.get('endpoints', []))}；认证探测={auth}",
                        "action": "优先改为 127.0.0.1；如必须局域网访问，增加认证与最小化防火墙规则",
                    }
                )
            elif exposures & {"all_interfaces", "lan"}:
                security_score -= 15
                security_state = "warning" if security_state == "healthy" else security_state
                security_evidence.append(f"{service.get('display_name')} 存在非回环监听，认证={auth}")
        if firewall.get("status") == "measured" and not firewall.get("all_profiles_enabled", True):
            security_score -= 15
            security_state = "warning" if security_state == "healthy" else security_state
        public_connections = int(telemetry.get("network", {}).get("public_remote_connections") or 0)
        if public_connections:
            findings.append(
                {
                    "severity": "info",
                    "domain": "security",
                    "title": "模型服务当前存在公网远端连接",
                    "evidence": f"去重连接数：{public_connections}；只证明当前 TCP/UDP 对端，不证明上传了内容",
                    "action": "核对是否为预期下载、更新或 API 调用；需要内容级判断时应使用独立抓包工具",
                }
            )
        security_unknowns = list(firewall.get("limitations", []))
        if any(item.get("security", {}).get("auth_posture") == "unknown" for item in runtime_probes):
            security_unknowns.append("部分运行时认证状态未知")

        restarts_by_fingerprint: dict[str, int] = {}
        for service in services:
            fingerprint = str(service.get("fingerprint") or service.get("id") or "")
            restarts_by_fingerprint[fingerprint] = max(
                restarts_by_fingerprint.get(fingerprint, 0),
                int(service.get("metadata", {}).get("restart_count") or 0),
            )
        restart_count = sum(restarts_by_fingerprint.values())
        stability_score = max(0, 100 - min(restart_count, 5) * 10)
        stability_state = "warning" if restart_count >= 3 else "healthy"
        if any(probe.get("health") in {"unhealthy", "probe_error"} for probe in runtime_probes):
            stability_state = "warning"
            stability_score -= 20
        stability_unknowns = ["日志错误/OOM/CUDA 检查需由用户明确选择日志文件"]

        capacity_score = 100
        capacity_state = "healthy" if runtime_probes else "unknown"
        capacity_evidence = [f"已检测 {len(telemetry.get('gpus', []))} 个 GPU，{loaded} 个运行时报告模型已加载"]
        if memory_percent >= 90 or low_disks:
            capacity_score -= 25
            capacity_state = "warning"
        for probe in runtime_probes:
            cache = probe.get("performance", {}).get("kv_cache_usage_percent")
            if isinstance(cache, (int, float)) and cache >= 90:
                capacity_score -= 25
                capacity_state = "warning"
        capacity_unknowns = [] if runtime_probes else ["没有运行中的模型可核实实际上下文和并发"]

        domains = {
            "machine": _domain("机器健康", machine_state, machine_score, [f"内存 {memory_percent}%", f"最高可见温度 {hottest or '不可见'}"], machine_unknowns),
            "performance": _domain("模型性能", performance_state, performance_score, performance_evidence, performance_unknowns),
            "security": _domain("服务安全", security_state, security_score, security_evidence, security_unknowns),
            "stability": _domain("服务稳定", stability_state, stability_score, [f"历史重启计数 {restart_count}"], stability_unknowns),
            "capacity": _domain("资源容量", capacity_state, capacity_score, capacity_evidence, capacity_unknowns),
        }
        known = [value for value in domains.values() if value["state"] != "unknown"]
        unknown_domain_count = len(domains) - len(known)
        overall_score = round(sum(value["score"] for value in known) / len(known)) if known else 0
        states = {value["state"] for value in known}
        overall_state = "critical" if "critical" in states else "warning" if "warning" in states else "healthy" if known else "unknown"
        if overall_state == "healthy" and unknown_domain_count:
            overall_summary = f"已知项未发现高优先级异常；仍有 {unknown_domain_count} 个领域证据不足"
        else:
            overall_summary = {
                "healthy": "机器与模型服务的已知项未发现高优先级异常",
                "warning": "机器或模型服务存在需要处理的告警，请按证据逐项复核",
                "critical": "发现高风险暴露或严重资源/健康问题，应优先处置",
                "unknown": "当前证据不足，尚不能判断机器与模型服务状态",
            }[overall_state]
        return {
            "schema_version": "1.0",
            "captured_at": time.time(),
            "overall": {
                "state": overall_state,
                "score": overall_score,
                "known_domain_count": len(known),
                "unknown_domain_count": unknown_domain_count,
                "summary": overall_summary,
            },
            "domains": domains,
            "findings": sorted(findings, key=lambda item: {"critical": 0, "warning": 1, "info": 2}.get(item["severity"], 3)),
            "firewall": firewall,
            "reverse_proxies": _reverse_proxies(services),
            "limitations": ["监听 0.0.0.0/:: 只表示潜在远程可达，不等于已暴露到公网；VSG 不进行外网回连测试或 LAN 扫描"],
        }
