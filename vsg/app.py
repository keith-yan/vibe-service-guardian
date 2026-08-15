from __future__ import annotations

import argparse
import json
import logging
import logging.handlers
import os
import secrets
import signal
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlsplit

from . import __version__
from .actions import (
    ActionError,
    open_local_url,
    open_project_path,
    terminate_process_tree,
    verify_post_stop,
)
from .advisor import generate_hardware_advice
from .config import AppConfig, default_data_dir, load_config, save_config, validate_config
from .diagnostics import (
    DiagnosticError,
    create_snapshot_manifest,
    inspect_config,
    inspect_log,
    list_snapshot_manifests,
    restore_config_snapshot,
)
from .model_planner import ModelPlanner
from .engine_advisor import compare_service_benchmarks, recommend_engines
from .impact import build_export_envelope, build_impact_report
from .log_monitor import LogMonitor, LogMonitorError
from .model_inventory import ModelInventoryError, add_capacity_hints, scan_model_directory
from .capacity import QUANTIZATIONS
from .network_topology import build_network_topology
from .platforms import platform_info
from .posture import PostureEvaluator
from .privacy import atomic_write_private_text, ensure_private_directory, harden_private_file
from .runtime_probe import RuntimeProbeCollector
from .scanner import Scanner
from .project_rules import AttributionRuleError, validate_rule_payload
from .rule_packs import (
    RulePackError,
    build_rule_pack,
    preview_rule_pack,
    rebind_imported_rule,
    validate_rule_pack,
)
from .service_benchmark import ServiceBenchmarkError, run_service_benchmark
from .service_relationships import build_service_relationships
from .storage import Storage
from .stop_observation import (
    OBSERVATION_MINUTES,
    StopObservationError,
    StopObservationManager,
)
from .telemetry import TelemetryCollector
from .timeline import TimelineTracker, build_incident_view
from .trusted_nodes import TrustedNodeCollector
from .workload_matrix import WorkloadMatrixError, WorkloadMatrixManager


LOGGER = logging.getLogger("vsg")
MAX_BODY = 384 * 1024
MAX_CONTROL_RESPONSE = 1024 * 1024


def _attribution_summary(service: dict[str, Any]) -> dict[str, Any]:
    """Return only the local, redacted fields needed for correction audit."""

    project = service.get("project") or {}
    agent = service.get("agent") or {}
    metadata = service.get("metadata") or {}
    risk = service.get("risk") or {}
    return {
        "project_name": project.get("name"),
        "agent_provider": agent.get("provider"),
        "expected": bool(service.get("expected")),
        "protected": bool(service.get("protected")),
        "lifecycle_label": metadata.get("historical_lifecycle_label"),
        "risk_level": risk.get("level"),
        "attribution_source": metadata.get("attribution_source") or "scanner",
    }


class PrivateRotatingFileHandler(logging.handlers.RotatingFileHandler):
    def _open(self):  # type: ignore[no-untyped-def]
        stream = super()._open()
        harden_private_file(Path(self.baseFilename))
        return stream


def configure_logging(data_dir: Path, verbose: bool = False) -> None:
    ensure_private_directory(data_dir)
    LOGGER.setLevel(logging.DEBUG if verbose else logging.INFO)
    log_path = data_dir / "vsg.log"
    handler = PrivateRotatingFileHandler(
        log_path, maxBytes=2_000_000, backupCount=3, encoding="utf-8"
    )
    harden_private_file(log_path)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    LOGGER.handlers.clear()
    LOGGER.addHandler(handler)
    if verbose and getattr(sys, "stderr", None):
        stream = logging.StreamHandler()
        stream.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
        LOGGER.addHandler(stream)


class Collector:
    def __init__(
        self,
        config: AppConfig,
        storage: Storage,
        log_monitor: LogMonitor,
        calibration_profile_provider: Callable[[], list[dict[str, Any]]] | None = None,
    ):
        self.config = config
        self.storage = storage
        self.scanner = Scanner(config, storage)
        self.telemetry = TelemetryCollector(config)
        self.runtime_probes = RuntimeProbeCollector()
        self.posture = PostureEvaluator()
        self.trusted_nodes = TrustedNodeCollector()
        self.log_monitor = log_monitor
        self.calibration_profile_provider = calibration_profile_provider
        self.timeline = TimelineTracker(storage)
        self.snapshot: dict[str, Any] = {
            "schema_version": "1.1",
            "generated_at": None,
            "summary": {},
            "collectors": {},
            "errors": [],
            "services": [],
            "telemetry": {},
            "runtime_probes": [],
            "posture": {},
            "trusted_nodes": {},
            "log_monitor": {},
            "network_topology": {},
            "service_relationships": {},
            "storage": self.storage.status(),
            "loading": True,
        }
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._refresh = threading.Event()
        self._thread = threading.Thread(target=self._run, name="vsg-collector", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> bool:
        self._stop.set()
        self._refresh.set()
        # A Docker/WSL subprocess may already be inside its bounded timeout.
        # Give that read-only scan time to return, then exit before starting
        # any later collector stage.
        self._thread.join(timeout=30)
        return not self._thread.is_alive()

    def request_refresh(self) -> None:
        self._refresh.set()

    def update_config(self, config: AppConfig) -> None:
        with self._lock:
            self.config = config
            self.scanner.update_config(config)
            self.telemetry.update_config(config)
        self.request_refresh()

    def get_snapshot(self) -> dict[str, Any]:
        with self._lock:
            return json.loads(json.dumps(self.snapshot, ensure_ascii=False))

    def find_service(self, service_id: str) -> dict[str, Any] | None:
        with self._lock:
            for service in self.snapshot.get("services", []):
                if service.get("id") == service_id:
                    return json.loads(json.dumps(service, ensure_ascii=False))
        return None

    def record_impact_feedback(self, service_id: str, feedback: dict[str, Any]) -> None:
        with self._lock:
            for service in self.snapshot.get("services", []):
                if service.get("id") == service_id:
                    service["impact_feedback"] = json.loads(
                        json.dumps(feedback, ensure_ascii=False)
                    )
                    return

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                snapshot = self.scanner.scan()
                if self._stop.is_set():
                    break
                services = snapshot.get("services", [])
                telemetry = self.telemetry.collect(services)
                if self._stop.is_set():
                    break
                snapshot.setdefault("summary", {})["cpu_percent"] = telemetry["cpu"]["percent"]
                probes = self.runtime_probes.collect(services) if self.config.enable_runtime_probes else []
                if self._stop.is_set():
                    break
                probes_by_id = {item.get("service_id"): item for item in probes}
                for service in services:
                    if service.get("id") in probes_by_id:
                        service["runtime_probe"] = probes_by_id[service.get("id")]
                snapshot["schema_version"] = "2.0"
                snapshot["telemetry"] = telemetry
                snapshot["runtime_probes"] = probes
                snapshot["log_monitor"] = self.log_monitor.poll(services)
                snapshot["posture"] = self.posture.evaluate(telemetry, services, probes)
                snapshot["trusted_nodes"] = self.trusted_nodes.collect(self.config.trusted_nodes)
                self.timeline.observe(snapshot, telemetry)
                snapshot["network_topology"] = build_network_topology(services, telemetry)
                if self.calibration_profile_provider:
                    profiles = self.calibration_profile_provider()
                else:
                    profiles = self.storage.calibration_profiles(100)
                    for profile in profiles:
                        profile.setdefault(
                            "validity",
                            "valid"
                            if profile.get("status") == "active"
                            else profile.get("status"),
                        )
                relationships = build_service_relationships(
                    services,
                    runtime_probes=probes,
                    telemetry=telemetry,
                    service_benchmarks=self.storage.recent_service_benchmarks(100),
                    calibration_profiles=profiles,
                )
                for service in services:
                    service["stop_assessment"] = (relationships.get("assessments") or {}).get(
                        service.get("id"), {}
                    )
                feedbacks = self.storage.impact_feedbacks(
                    str(service.get("fingerprint") or "") for service in services
                )
                for service in services:
                    feedback = feedbacks.get(str(service.get("fingerprint") or ""))
                    if feedback:
                        service["impact_feedback"] = feedback
                snapshot["service_relationships"] = relationships
                snapshot["incident_summary"] = build_incident_view(self.storage, 24)
                snapshot["storage"] = self.storage.status()
                with self._lock:
                    self.snapshot = snapshot
                self.storage.cleanup(self.config.history_days, self.config.log_retention_days)
            except Exception as exc:  # collector must survive a partial platform failure
                LOGGER.exception("collector scan failed")
                with self._lock:
                    self.snapshot = {
                        **self.snapshot,
                        "generated_at": time.time(),
                        "loading": False,
                        "errors": [f"采集失败：{type(exc).__name__}"],
                    }
            self._refresh.wait(timeout=self.config.refresh_seconds)
            self._refresh.clear()


class AppState:
    def __init__(self, data_dir: Path, config: AppConfig):
        self.data_dir = data_dir
        self.config = config
        self.storage = Storage(data_dir)
        self.log_monitor = LogMonitor(self.storage)
        self.model_planner = ModelPlanner(self.storage)
        self.collector = Collector(
            config,
            self.storage,
            self.log_monitor,
            lambda: self.model_planner.measured_profiles()["items"],
        )
        self.workload_matrix = WorkloadMatrixManager(
            self.storage,
            self.collector.get_snapshot,
            self.model_planner.hardware,
            self.model_planner.predict_workload,
            lambda: float(self.config.low_disk_free_gib),
        )
        self.stop_observations = StopObservationManager(
            self.storage,
            notifications_enabled=lambda: bool(self.config.enable_system_notifications),
        )
        self.token = secrets.token_urlsafe(32)
        self.instance_id = secrets.token_urlsafe(18)
        self.server: ThreadingHTTPServer | None = None
        self.started_at = time.time()

    def update_config(self, raw: dict[str, Any]) -> AppConfig:
        unknown = sorted(set(raw) - set(self.config.public_dict()))
        if unknown:
            raise ValueError(f"设置包含未知字段：{', '.join(unknown[:10])}")
        config = validate_config(raw, self.config)
        save_config(config, self.data_dir)
        self.config = config
        self.collector.update_config(config)
        self.storage.add_audit("settings.update", "configuration", "success", {"keys": sorted(raw)})
        return config

    def advisor_payload(self, raw: dict[str, Any] | None = None) -> dict[str, Any]:
        request = raw or {}
        snapshot = self.collector.get_snapshot()
        hardware = self.model_planner.hardware()
        runtimes = self.model_planner.runtimes()
        events = self.storage.recent_log_events(100)
        engine = recommend_engines(hardware, runtimes, request)
        advice = generate_hardware_advice(
            hardware,
            snapshot.get("telemetry") or {},
            engine["request"],
            snapshot.get("runtime_probes") or [],
            events,
            self.config.low_disk_free_gib,
        )
        benchmarks = compare_service_benchmarks(self.storage.recent_service_benchmarks(200))
        return {
            "engine": engine,
            "advice": advice,
            "log_monitor": self.log_monitor.status(),
            "benchmarks": benchmarks,
            "workflow": {
                "stages": ["detect", "diagnose", "recommend", "benchmark", "monitor", "rollback"],
                "automatic_changes": False,
                "network_required": False,
            },
        }

    def model_planner_payload(self) -> dict[str, Any]:
        payload = self.model_planner.status()
        telemetry = self.collector.get_snapshot().get("telemetry") or {}
        memory = telemetry.get("memory") or {}
        gpus = telemetry.get("gpus") or []
        payload["current_resource_margin"] = {
            "captured_at": telemetry.get("captured_at"),
            "ram": {
                "available_gib": memory.get("available_gib"),
                "available_percent": (
                    round(100 - float(memory["used_percent"]), 1)
                    if memory.get("used_percent") is not None
                    else None
                ),
                "used_percent": memory.get("used_percent"),
            },
            "gpus": [
                {
                    "name": item.get("name"),
                    "memory_free_gib": item.get("memory_free_gib"),
                    "memory_total_gib": item.get("memory_total_gib"),
                    "available_percent": (
                        round(100 - float(item["memory_util_percent"]), 1)
                        if item.get("memory_util_percent") is not None
                        else None
                    ),
                    "used_percent": item.get("memory_util_percent"),
                }
                for item in gpus
            ],
            "guard_percent": 85,
            "source": "current passive collector snapshot",
        }
        return payload

    def impact_report(self) -> dict[str, Any]:
        return build_impact_report(
            self.storage,
            self.collector.get_snapshot(),
            platform_info(),
            __version__,
        )

    def close(self) -> None:
        observations_stopped = self.stop_observations.close()
        matrix_stopped = self.workload_matrix.close()
        collector_stopped = self.collector.stop()
        if observations_stopped and matrix_stopped and collector_stopped:
            self.storage.close()
        else:
            LOGGER.warning(
                "a background collector or workload request is still exiting; "
                "leaving SQLite open for process exit"
            )


class VSGServer(ThreadingHTTPServer):
    daemon_threads = True
    # A local control plane must never share a listening socket with an older
    # VSG process.  SO_REUSEADDR on Windows can otherwise let two packaged
    # versions bind the same host/port and serve a random mix of old/new pages.
    allow_reuse_address = False

    def __init__(self, address: tuple[str, int], state: AppState):
        self.state = state
        super().__init__(address, VSGHandler)

    def server_bind(self) -> None:
        if os.name == "nt" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


class VSGHandler(BaseHTTPRequestHandler):
    server: VSGServer
    server_version = "VSG"
    sys_version = ""

    def setup(self) -> None:
        super().setup()
        try:
            # A local peer must not retain an unbounded request thread by
            # trickling headers or a request body indefinitely.
            self.connection.settimeout(10.0)
        except OSError:
            pass

    def log_message(self, format_string: str, *args: Any) -> None:
        LOGGER.debug("http " + format_string, *args)

    @property
    def state(self) -> AppState:
        return self.server.state

    def _allowed_hosts(self) -> set[str]:
        port = self.server.server_address[1]
        return {f"127.0.0.1:{port}", f"localhost:{port}"}

    def _request_is_local(self) -> bool:
        host = self.headers.get("Host", "").lower()
        if host not in self._allowed_hosts():
            return False
        origin = self.headers.get("Origin")
        if origin:
            allowed_origins = {f"http://{item}" for item in self._allowed_hosts()}
            if origin.lower() not in allowed_origins:
                return False
        return self.client_address[0] in {"127.0.0.1", "::1"}

    def _security_headers(self, content_type: str) -> None:
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header(
            "Permissions-Policy",
            "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
        )
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
            "connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'",
        )

    def _json(self, status: int, payload: Any) -> None:
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self._security_headers("application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _error(self, status: int, message: str) -> None:
        self._json(status, {"ok": False, "error": message})

    def _body(self) -> dict[str, Any]:
        if self.headers.get("Transfer-Encoding"):
            raise ValueError("不支持 Transfer-Encoding 请求体")
        raw_length = self.headers.get("Content-Length", "0")
        try:
            length = int(raw_length)
        except ValueError as exc:
            raise ValueError("Content-Length 无效") from exc
        if length < 0 or length > MAX_BODY:
            raise ValueError("请求体过大")
        body = self.rfile.read(length)
        try:
            value = json.loads(body or b"{}")
        except json.JSONDecodeError as exc:
            raise ValueError("JSON 请求体无效") from exc
        if not isinstance(value, dict):
            raise ValueError("JSON 根节点必须是对象")
        return value

    def _authorized_post(self) -> bool:
        if not self._request_is_local():
            self._error(HTTPStatus.FORBIDDEN, "拒绝非本机或跨来源请求")
            return False
        token = self.headers.get("X-VSG-Token", "")
        if not secrets.compare_digest(token, self.state.token):
            self._error(HTTPStatus.FORBIDDEN, "控制令牌无效")
            return False
        return True

    def do_GET(self) -> None:
        if not self._request_is_local():
            self._error(HTTPStatus.MISDIRECTED_REQUEST, "Host 或来源无效")
            return
        parsed = urlsplit(self.path)
        if parsed.path == "/healthz":
            self._json(
                HTTPStatus.OK,
                {"ok": True, "version": __version__, "instance_id": self.state.instance_id},
            )
            return
        if parsed.path == "/api/bootstrap":
            self._json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "version": __version__,
                    "instance_id": self.state.instance_id,
                    "token": self.state.token,
                    "started_at": self.state.started_at,
                    "platform": platform_info(),
                },
            )
            return
        if parsed.path == "/api/status":
            self._json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "snapshot": self.state.collector.get_snapshot(),
                    "config": self.state.config.public_dict(),
                    "platform": platform_info(),
                },
            )
            return
        if parsed.path == "/api/audit":
            query = parse_qs(parsed.query)
            try:
                limit = int(query.get("limit", ["100"])[0])
            except ValueError:
                limit = 100
            self._json(HTTPStatus.OK, {"ok": True, "items": self.state.storage.recent_audit(limit)})
            return
        if parsed.path == "/api/impact":
            self._json(HTTPStatus.OK, {"ok": True, "report": self.state.impact_report()})
            return
        if parsed.path == "/api/timeline":
            query = parse_qs(parsed.query)
            try:
                limit = int(query.get("limit", ["200"])[0])
                hours = max(1, min(int(query.get("hours", ["24"])[0]), 24 * 30))
            except ValueError:
                limit, hours = 200, 24
            category = query.get("category", [None])[0]
            severity = query.get("severity", [None])[0]
            self._json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "items": self.state.storage.recent_timeline_events(
                        limit,
                        category=category if isinstance(category, str) and category else None,
                        severity=severity if isinstance(severity, str) and severity else None,
                        since=time.time() - hours * 3600,
                    ),
                },
            )
            return
        if parsed.path == "/api/incidents":
            query = parse_qs(parsed.query)
            try:
                hours = int(query.get("hours", ["24"])[0])
            except ValueError:
                hours = 24
            self._json(HTTPStatus.OK, {"ok": True, **build_incident_view(self.state.storage, hours)})
            return
        if parsed.path == "/api/log-events":
            query = parse_qs(parsed.query)
            try:
                limit = int(query.get("limit", ["200"])[0])
                hours = max(1, min(int(query.get("hours", ["24"])[0]), 24 * 30))
            except ValueError:
                limit, hours = 200, 24
            watch_id = query.get("watch_id", [None])[0]
            severity = query.get("severity", [None])[0]
            code = query.get("code", [None])[0]
            self._json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "items": self.state.storage.recent_log_events(
                        limit,
                        watch_id if isinstance(watch_id, str) and watch_id else None,
                        severity=severity if isinstance(severity, str) and severity else None,
                        code=code if isinstance(code, str) and code else None,
                        since=time.time() - hours * 3600,
                    ),
                },
            )
            return
        if parsed.path == "/api/attribution/rules":
            query = parse_qs(parsed.query)
            search = query.get("search", [None])[0]
            self._json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "items": self.state.storage.attribution_rules(
                        search=search if isinstance(search, str) else None
                    ),
                    "metrics": self.state.storage.attribution_metrics(30),
                },
            )
            return
        if parsed.path == "/api/attribution/rules/versions":
            query = parse_qs(parsed.query)
            try:
                rule_id = int(query.get("rule_id", ["0"])[0])
            except ValueError:
                rule_id = 0
            if rule_id <= 0:
                self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "rule_id 无效"})
                return
            self._json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "rule_id": rule_id,
                    "items": self.state.storage.attribution_rule_versions(rule_id),
                },
            )
            return
        if parsed.path == "/api/model-inventory":
            query = parse_qs(parsed.query)
            try:
                limit = int(query.get("limit", ["10"])[0])
            except ValueError:
                limit = 10
            self._json(
                HTTPStatus.OK,
                {"ok": True, "items": self.state.storage.recent_model_inventory_scans(limit)},
            )
            return
        if parsed.path == "/api/network-topology":
            snapshot = self.state.collector.get_snapshot()
            self._json(
                HTTPStatus.OK,
                {"ok": True, "topology": snapshot.get("network_topology") or {}},
            )
            return
        if parsed.path == "/api/service-relationships":
            snapshot = self.state.collector.get_snapshot()
            self._json(
                HTTPStatus.OK,
                {"ok": True, "relationships": snapshot.get("service_relationships") or {}},
            )
            return
        if parsed.path == "/api/model-planner/status":
            self._json(HTTPStatus.OK, {"ok": True, **self.state.model_planner_payload()})
            return
        if parsed.path == "/api/advisor/status":
            self._json(HTTPStatus.OK, {"ok": True, **self.state.advisor_payload()})
            return
        if parsed.path == "/api/service-benchmarks":
            query = parse_qs(parsed.query)
            fingerprint = query.get("fingerprint", [None])[0]
            self._json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "items": self.state.storage.recent_service_benchmarks(
                        50, fingerprint if isinstance(fingerprint, str) and fingerprint else None
                    ),
                },
            )
            return
        if parsed.path == "/api/stop-verifications":
            query = parse_qs(parsed.query)
            fingerprint = query.get("fingerprint", [None])[0]
            self._json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "items": self.state.storage.recent_stop_verifications(
                        50, fingerprint if isinstance(fingerprint, str) and fingerprint else None
                    ),
                },
            )
            return
        if parsed.path == "/api/stop-observations":
            query = parse_qs(parsed.query)
            job_id = query.get("job_id", [None])[0]
            self._json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    **self.state.stop_observations.status(
                        job_id if isinstance(job_id, str) and job_id else None
                    ),
                },
            )
            return
        if parsed.path == "/api/benchmark-matrix/status":
            query = parse_qs(parsed.query)
            job_id = query.get("job_id", [None])[0]
            self._json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    **self.state.workload_matrix.status(
                        job_id if isinstance(job_id, str) and job_id else None
                    ),
                },
            )
            return
        if parsed.path == "/api/snapshots":
            self._json(
                HTTPStatus.OK,
                {"ok": True, "items": list_snapshot_manifests(self.state.data_dir)},
            )
            return
        if parsed.path in {"/", "/index.html"}:
            self._static("index.html", "text/html; charset=utf-8")
            return
        if parsed.path == "/assets/styles.css":
            self._static("styles.css", "text/css; charset=utf-8")
            return
        if parsed.path == "/assets/app.js":
            self._static("app.js", "application/javascript; charset=utf-8")
            return
        if parsed.path == "/assets/i18n.js":
            self._static("i18n.js", "application/javascript; charset=utf-8")
            return
        self._error(HTTPStatus.NOT_FOUND, "资源不存在")

    def _static(self, filename: str, content_type: str) -> None:
        try:
            content = resources.files("vsg").joinpath("web", filename).read_bytes()
        except (FileNotFoundError, OSError):
            self._error(HTTPStatus.NOT_FOUND, "静态资源缺失")
            return
        self.send_response(HTTPStatus.OK)
        self._security_headers(content_type)
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def do_POST(self) -> None:
        if not self._authorized_post():
            return
        try:
            body = self._body()
        except ValueError as exc:
            self._error(HTTPStatus.BAD_REQUEST, str(exc))
            return
        path = urlsplit(self.path).path
        try:
            if path == "/api/refresh":
                self.state.collector.request_refresh()
                self._json(HTTPStatus.ACCEPTED, {"ok": True})
                return
            if path == "/api/settings":
                config = self.state.update_config(body)
                self._json(HTTPStatus.OK, {"ok": True, "config": config.public_dict()})
                return
            if path == "/api/impact/feedback":
                service = self._require_service(body)
                risk = service.get("risk") or {}
                feedback = self.state.storage.set_impact_feedback(
                    str(service.get("fingerprint") or ""),
                    str(body.get("outcome") or ""),
                    str(risk.get("level") or "unknown"),
                    risk.get("score"),
                    str(service.get("source") or "unknown"),
                )
                self.state.storage.add_audit(
                    "impact.feedback",
                    service["id"],
                    "success",
                    {"outcome": feedback.get("outcome")},
                )
                self.state.collector.record_impact_feedback(service["id"], feedback)
                self._json(HTTPStatus.OK, {"ok": True, "feedback": feedback})
                return
            if path == "/api/impact/export":
                if str(body.get("confirmation") or "") != "EXPORT REPORT":
                    raise ValueError("确认短语必须精确输入 EXPORT REPORT")
                envelope = build_export_envelope(self.state.impact_report())
                digest = envelope["integrity"]["canonical_report_sha256"]
                self.state.storage.add_audit(
                    "impact.export",
                    "aggregate_report",
                    "success",
                    {"schema_version": envelope["report"]["schema_version"], "sha256": digest},
                )
                filename = time.strftime("vsg-impact-report-%Y%m%d-%H%M%SZ.json", time.gmtime())
                self._json(
                    HTTPStatus.OK,
                    {"ok": True, "filename": filename, "export": envelope},
                )
                return
            if path == "/api/attribution/rules":
                rule = validate_rule_payload(body, self.state.config.project_roots)
                rule_id = self.state.storage.add_attribution_rule(rule)
                self.state.storage.add_audit(
                    "attribution_rule.create", str(rule_id), "success", {"name": rule["name"]}
                )
                self.state.collector.request_refresh()
                self._json(HTTPStatus.OK, {"ok": True, "id": rule_id, "rule": rule})
                return
            if path == "/api/attribution/rules/update":
                rule_id = int(body.get("rule_id") or 0)
                if rule_id <= 0 or str(body.get("confirmation") or "") != f"UPDATE RULE {rule_id}":
                    raise AttributionRuleError(f"确认短语必须是 UPDATE RULE {rule_id}")
                raw_rule = body.get("rule")
                if not isinstance(raw_rule, dict):
                    raise AttributionRuleError("rule 必须是对象")
                raw_rule = {**raw_rule, "source": "user"}
                rule = validate_rule_payload(raw_rule, self.state.config.project_roots)
                updated = self.state.storage.update_attribution_rule(rule_id, rule)
                if updated is None:
                    raise AttributionRuleError("归属规则不存在")
                self.state.storage.add_audit(
                    "attribution_rule.update",
                    str(rule_id),
                    "success",
                    {"revision": updated.get("revision"), "scope": updated.get("scope")},
                )
                self.state.collector.request_refresh()
                self._json(HTTPStatus.OK, {"ok": True, "rule": updated})
                return
            if path == "/api/attribution/rules/status":
                rule_id = int(body.get("rule_id") or 0)
                enabled = body.get("enabled")
                if not isinstance(enabled, bool):
                    raise AttributionRuleError("enabled 必须是布尔值")
                verb = "ENABLE" if enabled else "DISABLE"
                if rule_id <= 0 or str(body.get("confirmation") or "") != f"{verb} RULE {rule_id}":
                    raise AttributionRuleError(f"确认短语必须是 {verb} RULE {rule_id}")
                existing = next(
                    (
                        item
                        for item in self.state.storage.attribution_rules()
                        if int(item.get("id") or 0) == rule_id
                    ),
                    None,
                )
                if existing is None:
                    raise AttributionRuleError("归属规则不存在")
                existing["enabled"] = enabled
                existing["source"] = "user"
                rule = validate_rule_payload(existing, self.state.config.project_roots)
                updated = self.state.storage.update_attribution_rule(
                    rule_id, rule, action="enable" if enabled else "disable"
                )
                self.state.storage.add_audit(
                    "attribution_rule.status", str(rule_id), "success", {"enabled": enabled}
                )
                self.state.collector.request_refresh()
                self._json(HTTPStatus.OK, {"ok": True, "rule": updated})
                return
            if path == "/api/attribution/rules/restore":
                rule_id = int(body.get("rule_id") or 0)
                version = int(body.get("version") or 0)
                phrase = f"RESTORE RULE {rule_id} VERSION {version}"
                if rule_id <= 0 or version <= 0 or str(body.get("confirmation") or "") != phrase:
                    raise AttributionRuleError(f"确认短语必须是 {phrase}")
                restored = self.state.storage.restore_attribution_rule(rule_id, version)
                if restored is None:
                    raise AttributionRuleError("归属规则或版本不存在")
                self.state.storage.add_audit(
                    "attribution_rule.restore",
                    str(rule_id),
                    "success",
                    {"source_version": version, "revision": restored.get("revision")},
                )
                self.state.collector.request_refresh()
                self._json(HTTPStatus.OK, {"ok": True, "rule": restored})
                return
            if path == "/api/attribution/rules/export":
                if str(body.get("confirmation") or "") != "EXPORT RULES":
                    raise AttributionRuleError("确认短语必须精确输入 EXPORT RULES")
                envelope = build_rule_pack(self.state.storage.attribution_rules(), __version__)
                digest = envelope["integrity"]["canonical_payload_sha256"]
                self.state.storage.add_audit(
                    "attribution_rule.export",
                    "rule_pack",
                    "success",
                    {"rules": len(envelope["rules"]), "sha256": digest},
                )
                filename = time.strftime("vsg-attribution-rules-%Y%m%d-%H%M%SZ.json", time.gmtime())
                self._json(
                    HTTPStatus.OK,
                    {"ok": True, "filename": filename, "export": envelope},
                )
                return
            if path == "/api/attribution/rules/import/preview":
                pack = body.get("pack")
                snapshot = self.state.collector.get_snapshot()
                preview = preview_rule_pack(
                    pack,
                    snapshot.get("services") or [],
                    self.state.storage.attribution_rules(),
                )
                self._json(HTTPStatus.OK, {"ok": True, "preview": preview})
                return
            if path == "/api/attribution/rules/import":
                pack = body.get("pack")
                validated_pack = validate_rule_pack(pack)
                bindings = body.get("bindings")
                if not isinstance(bindings, list) or not bindings:
                    raise RulePackError("导入至少需要一条明确的服务重绑定")
                if len(bindings) > len(validated_pack["rules"]):
                    raise RulePackError("导入重绑定数量超过规则包条目数")
                phrase = f"IMPORT RULES {len(bindings)} {validated_pack['digest'][:12]}"
                if str(body.get("confirmation") or "") != phrase:
                    raise RulePackError(f"确认短语必须是 {phrase}")
                services = {
                    str(item.get("id") or ""): item
                    for item in self.state.collector.get_snapshot().get("services") or []
                }
                selected: set[int] = set()
                imported: list[dict[str, Any]] = []
                for binding in bindings:
                    if not isinstance(binding, dict):
                        raise RulePackError("导入重绑定条目必须是对象")
                    index = int(binding.get("index") if binding.get("index") is not None else -1)
                    if index < 0 or index >= len(validated_pack["rules"]) or index in selected:
                        raise RulePackError("导入规则索引无效或重复")
                    selected.add(index)
                    service_id = str(binding.get("service_id") or "")
                    service = services.get(service_id)
                    if service is None:
                        raise RulePackError(f"重绑定目标服务不存在：{service_id[:120]}")
                    imported.append(
                        rebind_imported_rule(
                            validated_pack["rules"][index],
                            service,
                            str(binding.get("scope") or "standard"),
                            self.state.config.project_roots,
                        )
                    )
                rule_ids = self.state.storage.add_attribution_rules(imported)
                self.state.storage.add_audit(
                    "attribution_rule.import",
                    "rule_pack",
                    "success",
                    {
                        "rules": len(rule_ids),
                        "rule_ids": rule_ids,
                        "sha256": validated_pack["digest"],
                    },
                )
                self.state.collector.request_refresh()
                self._json(HTTPStatus.OK, {"ok": True, "rule_ids": rule_ids})
                return
            if path == "/api/attribution/rules/delete":
                rule_id = int(body.get("rule_id") or 0)
                if rule_id <= 0 or str(body.get("confirmation") or "") != f"DELETE RULE {rule_id}":
                    raise AttributionRuleError(f"确认短语必须是 DELETE RULE {rule_id}")
                deleted = self.state.storage.delete_attribution_rule(rule_id)
                if not deleted:
                    raise AttributionRuleError("归属规则不存在")
                self.state.storage.add_audit("attribution_rule.delete", str(rule_id), "success")
                self.state.collector.request_refresh()
                self._json(HTTPStatus.OK, {"ok": True, "deleted": rule_id})
                return
            if path == "/api/service/attribute":
                service = self._require_service(body)
                before_summary = _attribution_summary(service)
                override = body.get("override") or {}
                if not isinstance(override, dict):
                    raise AttributionRuleError("override 必须是对象")
                inherit_raw = body.get("inherit_similar", False)
                if not isinstance(inherit_raw, bool):
                    raise AttributionRuleError("inherit_similar 必须是布尔值")
                inherit_similar = inherit_raw
                reuse_scope_raw = body.get("reuse_scope")
                reuse_scope = str(reuse_scope_raw or "").strip().lower()
                if reuse_scope and reuse_scope not in {"instance", "standard", "strict"}:
                    raise AttributionRuleError(
                        "reuse_scope 必须是 instance、standard 或 strict"
                    )
                lifecycle_label = str(override.get("lifecycle_label") or "")
                if inherit_similar and not lifecycle_label and not reuse_scope:
                    raise AttributionRuleError("只有历史生命周期标签可以继承到同类进程")
                signature = str(
                    (service.get("metadata") or {}).get("ownership_signature") or ""
                )
                if inherit_similar and not signature:
                    raise AttributionRuleError(
                        "当前进程缺少可执行路径或工作目录，不能创建可继承历史标签"
                    )
                command_hash = str(
                    (service.get("metadata") or {}).get("command_hash") or ""
                )
                base_name = body.get("name") or (
                    f"Attribution · {service.get('display_name') or service.get('fingerprint')}"
                )
                if reuse_scope:
                    if reuse_scope == "instance":
                        reusable_match = {"fingerprint": service.get("fingerprint")}
                    elif reuse_scope == "standard":
                        reusable_match = {"ownership_signature": signature}
                    else:
                        reusable_match = {
                            "ownership_signature": signature,
                            "redacted_command_hash": command_hash,
                        }
                    if any(value in (None, "") for value in reusable_match.values()):
                        raise AttributionRuleError(
                            f"当前服务缺少创建 {reuse_scope} 规则所需的脱敏证据"
                        )
                    rules = [
                        validate_rule_payload(
                            {
                                "name": base_name,
                                "priority": body.get("priority") or 500,
                                "source": "user",
                                "scope": reuse_scope,
                                "match": reusable_match,
                                "override": override,
                            },
                            self.state.config.project_roots,
                        )
                    ]
                else:
                    current_rule = validate_rule_payload(
                        {
                            "name": base_name,
                            "priority": 1000 if lifecycle_label else body.get("priority") or 500,
                            "source": "user",
                            "scope": "instance",
                            "match": {"fingerprint": service.get("fingerprint")},
                            "override": override,
                        },
                        self.state.config.project_roots,
                    )
                    rules = [current_rule]
                if inherit_similar and not reuse_scope:
                    rules.append(
                        validate_rule_payload(
                            {
                                "name": f"Historical lifecycle inheritance · {service.get('display_name') or service.get('fingerprint')}",
                                "priority": 900,
                                "source": "user",
                                "scope": "standard",
                                "match": {"ownership_signature": signature},
                                "override": {"lifecycle_label": lifecycle_label},
                            },
                            self.state.config.project_roots,
                        )
                    )
                rule_ids = self.state.storage.add_attribution_rules(rules)
                metadata = service.get("metadata") or {}
                matched_rule_ids: list[int] = []
                for value in metadata.get("attribution_rule_ids") or []:
                    try:
                        rule_id_value = int(value)
                    except (TypeError, ValueError):
                        continue
                    if rule_id_value > 0:
                        matched_rule_ids.append(rule_id_value)
                after_summary = {**before_summary, "attribution_source": "local_rule"}
                if override.get("project_name"):
                    after_summary["project_name"] = str(override["project_name"])
                if override.get("agent_provider"):
                    after_summary["agent_provider"] = str(override["agent_provider"])
                if "expected" in override:
                    after_summary["expected"] = bool(override["expected"])
                if "protected" in override:
                    after_summary["protected"] = bool(before_summary["protected"]) or bool(
                        override["protected"]
                    )
                if lifecycle_label:
                    after_summary["lifecycle_label"] = lifecycle_label
                process = service.get("process") or {}
                episode_key = str(
                    metadata.get("attribution_episode_key")
                    or f"legacy:{service.get('fingerprint')}:{process.get('create_time') or 0}"
                )
                correction = self.state.storage.record_attribution_correction(
                    episode_key=episode_key,
                    service_fingerprint=str(service.get("fingerprint") or ""),
                    before=before_summary,
                    after=after_summary,
                    matched_rule_ids=matched_rule_ids,
                )
                self.state.storage.add_audit(
                    "service.attribution_corrected",
                    service["id"],
                    "success",
                    {
                        "rule_ids": rule_ids,
                        "inherit_similar": inherit_similar,
                        "reuse_scope": reuse_scope or None,
                        "lifecycle_label": lifecycle_label or None,
                        "overridden_rule_ids": correction["newly_overridden_rule_ids"],
                    },
                )
                self.state.collector.request_refresh()
                self._json(
                    HTTPStatus.OK,
                    {
                        "ok": True,
                        "id": rule_ids[0],
                        "rule_ids": rule_ids,
                        "rule": rules[0],
                        "inherited_rule": rules[1] if len(rules) > 1 else None,
                    },
                )
                return
            if path == "/api/service/lifecycle-label/clear":
                service = self._require_service(body)
                before_summary = _attribution_summary(service)
                signature = str(
                    (service.get("metadata") or {}).get("ownership_signature") or ""
                )
                fingerprint = str(service.get("fingerprint") or "")
                matching_rule_ids: list[int] = []
                for rule in self.state.storage.attribution_rules():
                    match = rule.get("match") or {}
                    override = rule.get("override") or {}
                    if not override.get("lifecycle_label"):
                        continue
                    if match.get("ownership_signature") == signature or match.get("fingerprint") == fingerprint:
                        rule_id = int(rule.get("id") or 0)
                        if rule_id > 0:
                            matching_rule_ids.append(rule_id)
                if not matching_rule_ids:
                    raise AttributionRuleError("当前服务没有可撤销的历史生命周期标签")
                rewrite = self.state.storage.remove_attribution_override(
                    matching_rule_ids, "lifecycle_label"
                )
                metadata = service.get("metadata") or {}
                process = service.get("process") or {}
                after_summary = {**before_summary, "lifecycle_label": None}
                self.state.storage.record_attribution_correction(
                    episode_key=str(
                        metadata.get("attribution_episode_key")
                        or f"legacy:{service.get('fingerprint')}:{process.get('create_time') or 0}"
                    ),
                    service_fingerprint=str(service.get("fingerprint") or ""),
                    before=before_summary,
                    after=after_summary,
                    matched_rule_ids=matching_rule_ids,
                )
                deleted = rewrite["deleted"]
                self.state.storage.add_audit(
                    "service.lifecycle_label_clear",
                    service["id"],
                    "success",
                    {
                        "rule_ids": deleted,
                        "preserved_rule_ids": rewrite["recreated"],
                    },
                )
                self.state.collector.request_refresh()
                self._json(
                    HTTPStatus.OK,
                    {
                        "ok": True,
                        "deleted_rule_ids": deleted,
                        "preserved_rule_ids": rewrite["recreated"],
                    },
                )
                return
            if path == "/api/model-inventory/scan":
                result = scan_model_directory(
                    str(body.get("root") or ""), str(body.get("confirmation") or "")
                )
                add_capacity_hints(result, self.state.model_planner.hardware())
                scan_id = self.state.storage.add_model_inventory_scan(result)
                result["id"] = scan_id
                self.state.storage.add_audit(
                    "model_inventory.scan",
                    str(result.get("root_hash") or "models"),
                    "success",
                    {
                        "root_name": result.get("root_name"),
                        "assets": (result.get("summary") or {}).get("assets"),
                        "truncated": result.get("truncated"),
                    },
                )
                self._json(HTTPStatus.OK, {"ok": True, "scan": result})
                return
            if path == "/api/history/clear":
                if str(body.get("confirmation") or "") != "CLEAR HISTORY":
                    raise ValueError("确认短语必须精确输入 CLEAR HISTORY")
                categories = body.get("categories")
                if not isinstance(categories, list):
                    raise ValueError("categories 必须是数组")
                result = self.state.storage.clear_history(categories)
                self.state.storage.add_audit(
                    "history.clear", "local_history", "success", {"removed": result}
                )
                self._json(HTTPStatus.OK, {"ok": True, "removed": result})
                return
            if path == "/api/model-planner/refresh":
                self.state.model_planner.refresh()
                result = self.state.model_planner_payload()
                self.state.storage.add_audit(
                    "model_planner.hardware_refresh",
                    str(result.get("hardware", {}).get("hardware_fingerprint") or "hardware"),
                    "success",
                )
                self._json(HTTPStatus.OK, {"ok": True, **result})
                return
            if path == "/api/model-planner/estimate":
                result = self.state.model_planner.estimate(body)
                workload = result.get("workload", {})
                self.state.storage.add_audit(
                    "model_planner.estimate",
                    str(result.get("selected_model_id") or "no_match"),
                    "success",
                    {
                        "concurrency": workload.get("concurrency"),
                        "context_tokens": workload.get("context_tokens"),
                        "target_tps_per_user": workload.get("target_tps_per_user"),
                    },
                )
                self._json(HTTPStatus.OK, {"ok": True, "estimate": result})
                return
            if path == "/api/advisor/evaluate":
                result = self.state.advisor_payload(body)
                request = result.get("engine", {}).get("request", {})
                top_engines = result.get("engine", {}).get("top3", [])
                self.state.storage.add_audit(
                    "advisor.evaluate",
                    str(top_engines[0].get("id") if top_engines else "no_match"),
                    "success",
                    {
                        "model_format": request.get("model_format"),
                        "priority": request.get("priority"),
                        "concurrency": request.get("concurrency"),
                        "context_tokens": request.get("context_tokens"),
                    },
                )
                self._json(HTTPStatus.OK, {"ok": True, **result})
                return
            if path == "/api/log-monitor/watch":
                service = self._require_service(body)
                result = self.state.log_monitor.start_watch(
                    service,
                    str(body.get("path") or ""),
                    str(body.get("confirmation") or ""),
                )
                self.state.log_monitor.poll(self.state.collector.get_snapshot().get("services", []))
                self.state.storage.add_audit(
                    "log_monitor.watch",
                    service["id"],
                    "success",
                    {"watch_id": result.get("id"), "file_name": result.get("file_name")},
                )
                self.state.collector.request_refresh()
                self._json(
                    HTTPStatus.OK,
                    {"ok": True, "watch": result, "log_monitor": self.state.log_monitor.status()},
                )
                return
            if path == "/api/log-monitor/unwatch":
                watch_id = str(body.get("watch_id") or "")
                if not watch_id:
                    raise LogMonitorError("缺少 watch_id")
                result = self.state.log_monitor.stop_watch(
                    watch_id, str(body.get("confirmation") or "")
                )
                self.state.storage.add_audit(
                    "log_monitor.unwatch",
                    watch_id,
                    "success",
                    {"file_name": result.get("file_name")},
                )
                self.state.collector.request_refresh()
                self._json(
                    HTTPStatus.OK,
                    {"ok": True, "watch": result, "log_monitor": self.state.log_monitor.status()},
                )
                return
            if path == "/api/model-planner/benchmark":
                result = self.state.model_planner.benchmark(body)
                self.state.storage.add_audit(
                    "model_planner.benchmark",
                    str(result.get("model_id") or "model"),
                    "success",
                    {
                        "benchmark_id": result.get("id"),
                        "quantization": result.get("quantization"),
                        "model_file_name": result.get("model_file_name"),
                    },
                )
                self._json(HTTPStatus.OK, {"ok": True, "benchmark": result})
                return
            if path == "/api/service/stop-assessment":
                service = self._require_service(body)
                snapshot = self.state.collector.get_snapshot()
                assessment = (
                    (snapshot.get("service_relationships") or {}).get("assessments") or {}
                ).get(service.get("id"))
                if not assessment:
                    assessment = service.get("stop_assessment") or {}
                self.state.storage.add_audit(
                    "process.stop_assessment",
                    service["id"],
                    "success",
                    {
                        "decision": assessment.get("decision"),
                        "client_count": (assessment.get("impact") or {}).get("client_count"),
                    },
                )
                self._json(HTTPStatus.OK, {"ok": True, "assessment": assessment})
                return
            if path == "/api/benchmark-matrix/preview":
                service = self._require_service(body)
                snapshot = self.state.collector.get_snapshot()
                probe = next(
                    (
                        item
                        for item in snapshot.get("runtime_probes", [])
                        if item.get("service_id") == service.get("id")
                    ),
                    None,
                )
                if not probe:
                    raise WorkloadMatrixError("该服务没有可用的只读运行时探测结果")
                catalog_model_id = str(body.get("catalog_model_id") or "").strip() or None
                quantization = str(body.get("quantization") or "").strip() or None
                if catalog_model_id and not self.state.model_planner.catalog_model(catalog_model_id):
                    raise WorkloadMatrixError("容量目录模型映射无效")
                if catalog_model_id and quantization not in QUANTIZATIONS:
                    raise WorkloadMatrixError("进入容量校准时必须选择有效量化版本")
                if not catalog_model_id:
                    quantization = None
                plan = self.state.workload_matrix.preview(
                    service,
                    probe,
                    catalog_model_id=catalog_model_id,
                    quantization=quantization,
                    mode=str(body.get("mode") or "matrix"),
                    model_name=str(body.get("model_name") or "").strip() or None,
                    concurrency=(
                        int(body.get("concurrency"))
                        if body.get("concurrency") is not None
                        else None
                    ),
                    duration_seconds=(
                        int(body.get("duration_seconds"))
                        if body.get("duration_seconds") is not None
                        else None
                    ),
                )
                self.state.storage.add_audit(
                    "benchmark_matrix.preview",
                    service["id"],
                    "success",
                    {
                        "plan_id": plan.get("plan_id"),
                        "steps": len(plan.get("steps") or []),
                        "capacity_calibration": plan.get("capacity_calibration"),
                        "mode": plan.get("mode"),
                    },
                )
                self._json(HTTPStatus.OK, {"ok": True, "plan": plan})
                return
            if path == "/api/benchmark-matrix/start":
                job = self.state.workload_matrix.start(
                    str(body.get("plan_id") or ""),
                    str(body.get("confirmation") or ""),
                )
                self._json(HTTPStatus.ACCEPTED, {"ok": True, "job": job})
                return
            if path == "/api/calibration-profiles/status":
                profile_id = str(body.get("profile_id") or "")
                if not 1 <= len(profile_id) <= 100 or any(
                    character in profile_id for character in "\r\n\0"
                ):
                    raise ValueError("本机实测档案 ID 无效")
                status = str(body.get("status") or "")
                if status not in {"active", "expired"}:
                    raise ValueError("档案状态只允许 active 或 expired")
                updated = self.state.storage.set_calibration_profile_status(
                    profile_id, status
                )
                if not updated:
                    raise ValueError("本机实测档案不存在")
                self.state.storage.add_audit(
                    "calibration_profile.status",
                    profile_id,
                    "success",
                    {"status": status},
                )
                self._json(HTTPStatus.OK, {"ok": True, "profile_id": profile_id, "status": status})
                return
            if path == "/api/calibration-profiles/delete":
                profile_id = str(body.get("profile_id") or "")
                if not 1 <= len(profile_id) <= 100 or any(
                    character in profile_id for character in "\r\n\0"
                ):
                    raise ValueError("本机实测档案 ID 无效")
                if str(body.get("confirmation") or "") != f"DELETE PROFILE {profile_id}":
                    raise ValueError(f"确认短语必须是 DELETE PROFILE {profile_id}")
                deleted = self.state.storage.delete_calibration_profile(profile_id)
                if not deleted:
                    raise ValueError("本机实测档案不存在")
                self.state.storage.add_audit(
                    "calibration_profile.delete", profile_id, "success"
                )
                self._json(HTTPStatus.OK, {"ok": True, "deleted": profile_id})
                return
            if path == "/api/benchmark-matrix/cancel":
                job = self.state.workload_matrix.cancel(str(body.get("job_id") or ""))
                self._json(HTTPStatus.ACCEPTED, {"ok": True, "job": job})
                return
            if path == "/api/service/benchmark":
                service = self._require_service(body)
                snapshot = self.state.collector.get_snapshot()
                probe = next(
                    (
                        item
                        for item in snapshot.get("runtime_probes", [])
                        if item.get("service_id") == service.get("id")
                    ),
                    None,
                )
                if not probe:
                    raise ServiceBenchmarkError("该服务没有可用的只读运行时探测结果")
                result = run_service_benchmark(
                    service,
                    probe,
                    body,
                    float(snapshot.get("telemetry", {}).get("memory", {}).get("used_percent") or 0),
                )
                result["id"] = self.state.storage.add_service_benchmark(result)
                self.state.storage.add_audit(
                    "service.benchmark",
                    service["id"],
                    "success" if result["successful_requests"] else "failed",
                    {
                        "benchmark_id": result["id"],
                        "port": result["port"],
                        "concurrency": result["concurrency"],
                        "requested_context_tokens": result["requested_context_tokens"],
                    },
                )
                self.state.collector.request_refresh()
                self._json(HTTPStatus.OK, {"ok": True, "benchmark": result})
                return
            if path == "/api/diagnostics/log":
                service = self._require_service(body)
                pid = int(service.get("process", {}).get("pid") or 0)
                result = inspect_log(
                    str(body.get("path") or ""), str(body.get("confirmation") or ""), pid
                )
                self.state.storage.add_audit(
                    "diagnostics.log_inspect",
                    service["id"],
                    "success",
                    {"file_name": result["file_name"], "lines_examined": result["lines_examined"]},
                )
                self._json(HTTPStatus.OK, {"ok": True, "result": result})
                return
            if path == "/api/diagnostics/config":
                service = self._require_service(body)
                pid = int(service.get("process", {}).get("pid") or 0)
                result = inspect_config(
                    str(body.get("path") or ""), str(body.get("confirmation") or ""), pid
                )
                self.state.storage.add_audit(
                    "diagnostics.config_inspect",
                    service["id"],
                    "success",
                    {"file_name": result["file_name"], "syntax": result["syntax"]},
                )
                self._json(HTTPStatus.OK, {"ok": True, "result": result})
                return
            if path == "/api/snapshots/create":
                raw_paths = body.get("paths")
                result = create_snapshot_manifest(
                    raw_paths if isinstance(raw_paths, list) else [],
                    self.state.data_dir,
                    str(body.get("confirmation") or ""),
                )
                self.state.storage.add_audit(
                    "snapshot.create",
                    str(result["snapshot_id"]),
                    "success",
                    {"files": len(result["items"]), "rollback_items": sum(bool(item.get("rollback_available")) for item in result["items"])},
                )
                public_result = {
                    **result,
                    "items": [
                        {key: value for key, value in item.items() if key != "original_path"}
                        for item in result["items"]
                    ],
                }
                self._json(HTTPStatus.OK, {"ok": True, "snapshot": public_result})
                return
            if path == "/api/snapshots/restore":
                result = restore_config_snapshot(
                    self.state.data_dir,
                    str(body.get("snapshot_id") or ""),
                    int(body.get("item_index", -1)),
                    str(body.get("confirmation") or ""),
                )
                self.state.storage.add_audit(
                    "snapshot.restore", str(result["snapshot_id"]), "success", {"file_name": result["file_name"]}
                )
                self._json(HTTPStatus.OK, {"ok": True, "result": result})
                return
            if path == "/api/process/mark":
                service = self._require_service(body)
                expected = bool(body.get("expected", False))
                protected = bool(body.get("protected", False))
                self.state.storage.set_mark(
                    service["fingerprint"], expected, protected, str(body.get("note") or "")
                )
                self.state.storage.add_audit(
                    "service.mark",
                    service["id"],
                    "success",
                    {"expected": expected, "protected": protected},
                )
                self.state.collector.request_refresh()
                self._json(HTTPStatus.OK, {"ok": True})
                return
            if path == "/api/process/stop":
                service = self._require_service(body)
                if service.get("source") != "host":
                    raise ActionError("只允许停止普通宿主机开发/推理进程；Agent 本体、系统服务、Docker 和 WSL 仅展示")
                if not service.get("metadata", {}).get("stoppable_candidate"):
                    raise ActionError("只允许停止已识别的开发或模型推理运行时；普通应用、LM Studio 主程序和系统进程仅展示")
                try:
                    observation_minutes = int(body.get("observation_minutes") or 15)
                except (TypeError, ValueError) as exc:
                    raise ActionError("观察时长必须是 5、15 或 30 分钟") from exc
                if observation_minutes not in OBSERVATION_MINUTES:
                    raise ActionError("观察时长必须是 5、15 或 30 分钟")
                process = service["process"]
                result = terminate_process_tree(
                    int(process["pid"]),
                    process.get("create_time"),
                    str(body.get("confirmation") or ""),
                    self.state.config.protected_names,
                    already_protected=bool(service.get("protected")),
                )
                affected_pids = [
                    *result.get("terminated", []),
                    *result.get("forced", []),
                ]
                try:
                    verification = verify_post_stop(service, affected_pids)
                except Exception as exc:
                    verification = {
                        "schema_version": "1.1",
                        "service_id": service.get("id"),
                        "service_fingerprint": service.get("fingerprint"),
                        "original_pid": int(process.get("pid") or 0),
                        "checked_at": time.time(),
                        "observation_window_seconds": 0,
                        "checks": 0,
                        "outcome": "verification_partial",
                        "restart_detected": False,
                        "replacement_pids": [],
                        "endpoint_verification": [],
                        "second_stop_attempted": False,
                        "limitations": [
                            f"停止动作已经完成，但即时验证失败：{type(exc).__name__}"
                        ],
                    }
                if result.get("errors"):
                    verification.setdefault("limitations", []).extend(result["errors"])
                try:
                    verification["id"] = self.state.storage.add_stop_verification(
                        verification
                    )
                except Exception as exc:
                    verification["id"] = None
                    verification.setdefault("limitations", []).append(
                        f"停止验证未能写入本机历史：{type(exc).__name__}"
                    )
                result["verification"] = verification
                try:
                    observation = self.state.stop_observations.start(
                        service,
                        affected_pids,
                        observation_minutes,
                    )
                except Exception as exc:
                    # The destructive action has already completed.  Never turn
                    # an observation start failure into a misleading "stop
                    # failed" response; return the partial result explicitly.
                    observation = {
                        "job_id": f"failed-to-start-{secrets.token_urlsafe(8)}",
                        "status": "failed_to_start",
                        "observation_minutes": observation_minutes,
                        "original_pid": int(process.get("pid") or 0),
                        "display_name": service.get("display_name"),
                        "project_name": (service.get("project") or {}).get("name"),
                        "port_state": "unknown",
                        "error": type(exc).__name__,
                        "limitations": [
                            "进程停止动作已经完成，但持续观察任务未能启动；请立即刷新服务状态并人工复核端口。"
                        ],
                    }
                result["observation"] = observation
                try:
                    self.state.storage.add_audit(
                        "process.stop",
                        service["id"],
                        "success" if verification.get("outcome") == "stopped" else str(verification.get("outcome") or "unknown"),
                        {
                            "pid": process["pid"],
                            "terminated": result.get("terminated"),
                            "forced": result.get("forced"),
                            "action_completed": result.get("completed"),
                            "action_errors": result.get("errors"),
                            "verification_id": verification.get("id"),
                            "verification_outcome": verification.get("outcome"),
                            "replacement_pids": verification.get("replacement_pids"),
                            "observation_job_id": observation.get("job_id"),
                            "observation_status": observation.get("status"),
                            "observation_minutes": observation.get("observation_minutes"),
                        },
                    )
                except Exception:
                    LOGGER.warning("process stop audit write failed", exc_info=True)
                self.state.collector.request_refresh()
                self._json(HTTPStatus.OK, {"ok": True, "result": result})
                return
            if path == "/api/stop-observations/cancel":
                job = self.state.stop_observations.cancel(str(body.get("job_id") or ""))
                self.state.storage.add_audit(
                    "stop_observation.cancel",
                    str(job.get("service_id") or job.get("service_fingerprint")),
                    "requested",
                    {"job_id": job.get("job_id")},
                )
                self._json(HTTPStatus.ACCEPTED, {"ok": True, "job": job})
                return
            if path == "/api/open/path":
                service = self._require_service(body)
                project_path = service.get("project", {}).get("path")
                if not project_path:
                    raise ActionError("该服务没有已归属的项目目录")
                open_project_path(project_path, self.state.config.project_roots)
                self.state.storage.add_audit("project.open", service["id"], "success")
                self._json(HTTPStatus.OK, {"ok": True})
                return
            if path == "/api/open/url":
                service = self._require_service(body)
                port = int(body.get("port") or 0)
                if service.get("source") in {"host", "agent", "windows_service"} and not service.get("metadata", {}).get("openable_candidate"):
                    raise ActionError("该端点未识别为开发 Web 服务，不自动用浏览器打开")
                allowed_ports = {
                    int(item["port"])
                    for item in service.get("endpoints", [])
                    if item.get("protocol") == "TCP"
                }
                if port not in allowed_ports:
                    raise ActionError("端口不属于该服务的 TCP 监听端点")
                url = open_local_url(port)
                self.state.storage.add_audit("service.open_url", service["id"], "success", {"port": port})
                self._json(HTTPStatus.OK, {"ok": True, "url": url})
                return
            if path == "/api/shutdown":
                self.state.storage.add_audit("application.shutdown", "vsg", "success")
                self._json(HTTPStatus.OK, {"ok": True})
                threading.Thread(target=self.server.shutdown, name="vsg-shutdown", daemon=True).start()
                return
        except (
            ActionError,
            AttributionRuleError,
            DiagnosticError,
            LogMonitorError,
            ModelInventoryError,
            ServiceBenchmarkError,
            WorkloadMatrixError,
            StopObservationError,
            TypeError,
            ValueError,
        ) as exc:
            target = str(body.get("service_id") or "request")
            self.state.storage.add_audit("request.rejected", target, "rejected", {"reason": str(exc)})
            self._error(HTTPStatus.CONFLICT, str(exc))
            return
        except Exception as exc:
            LOGGER.exception("request failed: %s", path)
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, f"操作失败：{type(exc).__name__}")
            return
        self._error(HTTPStatus.NOT_FOUND, "接口不存在")

    def _require_service(self, body: dict[str, Any]) -> dict[str, Any]:
        service_id = body.get("service_id")
        if not isinstance(service_id, str) or not service_id:
            raise ValueError("缺少 service_id")
        service = self.state.collector.find_service(service_id)
        if not service:
            raise ActionError("目标服务已变化，请刷新后重试")
        return service


def _runtime_file(data_dir: Path) -> Path:
    return data_dir / "runtime.json"


def _write_runtime(data_dir: Path, port: int, instance_id: str) -> None:
    path = _runtime_file(data_dir)
    atomic_write_private_text(
        path,
        json.dumps(
            {
                "pid": os.getpid(),
                "port": port,
                "instance_id": instance_id,
                "started_at": time.time(),
            },
            indent=2,
        )
        + "\n",
    )


def _read_runtime(data_dir: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(_runtime_file(data_dir).read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def _validated_control_url(url: str) -> Any:
    parsed = urlsplit(url)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "localhost"}
        or parsed.port is None
        or not 1 <= parsed.port <= 65535
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or any(character in parsed.path for character in "\r\n")
    ):
        raise ValueError("控制地址必须是无凭据的本机 HTTP URL")
    return parsed


def _read_control_json(response: Any) -> dict[str, Any]:
    body = response.read(MAX_CONTROL_RESPONSE + 1)
    if len(body) > MAX_CONTROL_RESPONSE:
        raise ValueError("控制接口响应超过 1 MiB")
    value = json.loads(body.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("控制接口响应根节点无效")
    return value


def _get_json(url: str, timeout: float = 2.0) -> dict[str, Any]:
    parsed = _validated_control_url(url)
    request = urllib.request.Request(url, headers={"Host": parsed.netloc})
    # The validator above permits loopback HTTP only.
    with urllib.request.urlopen(  # nosec B310
        request, timeout=timeout
    ) as response:
        return _read_control_json(response)


def _health_is_vsg(payload: Any, expected_instance_id: str | None = None) -> bool:
    valid = (
        isinstance(payload, dict)
        and payload.get("ok") is True
        and payload.get("version") == __version__
        and isinstance(payload.get("instance_id"), str)
        and bool(payload.get("instance_id"))
    )
    if not valid:
        return False
    return expected_instance_id is None or payload.get("instance_id") == expected_instance_id


def _post_json(url: str, token: str, body: dict[str, Any] | None = None, timeout: float = 3.0) -> dict[str, Any]:
    parsed = _validated_control_url(url)
    encoded = json.dumps(body or {}).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=encoded,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-VSG-Token": token,
            "Host": parsed.netloc,
        },
    )
    # The validator above permits loopback HTTP only.
    with urllib.request.urlopen(  # nosec B310
        request, timeout=timeout
    ) as response:
        return _read_control_json(response)


def control_existing(data_dir: Path, action: str) -> int:
    runtime = _read_runtime(data_dir)
    if not runtime:
        return 2
    port = runtime.get("port")
    instance_id = runtime.get("instance_id")
    if (
        isinstance(port, bool)
        or not isinstance(port, int)
        or not 1 <= port <= 65535
        or not isinstance(instance_id, str)
        or not instance_id
    ):
        return 2
    base = f"http://127.0.0.1:{port}"
    try:
        if action == "open":
            if not _health_is_vsg(_get_json(base + "/healthz"), instance_id):
                return 3
            webbrowser.open(base + "/", new=2)
            return 0
        if not _health_is_vsg(_get_json(base + "/healthz"), instance_id):
            return 3
        bootstrap = _get_json(base + "/api/bootstrap")
        if (
            not isinstance(bootstrap, dict)
            or bootstrap.get("version") != __version__
            or bootstrap.get("instance_id") != instance_id
            or not isinstance(bootstrap.get("token"), str)
        ):
            return 3
        _post_json(base + "/api/shutdown", bootstrap["token"])
        return 0
    except (OSError, urllib.error.URLError, KeyError, json.JSONDecodeError):
        return 3


def _create_server(port: int, state: AppState) -> VSGServer:
    candidates = [port] if port == 0 else list(range(port, min(port + 20, 65536)))
    last_error: OSError | None = None
    for candidate in candidates:
        try:
            return VSGServer(("127.0.0.1", candidate), state)
        except OSError as exc:
            last_error = exc
    raise last_error or OSError("无法分配本地端口")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Vibe Service Guardian")
    parser.add_argument("--port", type=int, default=None, help="本地控制台端口；0 表示自动分配")
    parser.add_argument("--data-dir", type=Path, default=None)
    parser.add_argument("--open", action="store_true", help="启动后打开浏览器")
    parser.add_argument("--open-existing", action="store_true", help="打开已运行的控制台")
    parser.add_argument("--stop", action="store_true", help="停止已运行的控制台")
    parser.add_argument("--once", action="store_true", help="执行一次只读扫描并输出摘要")
    parser.add_argument("--json", action="store_true", help="与 --once 一起输出完整 JSON")
    parser.add_argument("--verbose", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    data_dir = (args.data_dir or default_data_dir()).expanduser().resolve()
    configure_logging(data_dir, args.verbose)
    if args.open_existing:
        return control_existing(data_dir, "open")
    if args.stop:
        return control_existing(data_dir, "stop")

    config = load_config(data_dir)
    if args.once:
        storage = Storage(data_dir)
        try:
            snapshot = Scanner(config, storage).scan()
        finally:
            storage.close()
        output = snapshot if args.json else {
            "generated_at": snapshot["generated_at"],
            "duration_ms": snapshot["duration_ms"],
            "summary": snapshot["summary"],
            "collectors": snapshot["collectors"],
            "errors": snapshot["errors"],
        }
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return 0

    existing = _read_runtime(data_dir)
    if existing:
        existing_port = existing.get("port")
        existing_instance = existing.get("instance_id")
    else:
        existing_port = None
        existing_instance = None
    if (
        isinstance(existing_port, int)
        and not isinstance(existing_port, bool)
        and 1 <= existing_port <= 65535
        and isinstance(existing_instance, str)
        and existing_instance
    ):
        try:
            health = _get_json(
                f"http://127.0.0.1:{existing_port}/healthz",
                timeout=0.7,
            )
            if _health_is_vsg(health, existing_instance):
                if args.open:
                    webbrowser.open(f"http://127.0.0.1:{existing_port}/", new=2)
                return 0
        except (
            OSError,
            ValueError,
            urllib.error.URLError,
            json.JSONDecodeError,
        ):
            pass

    state = AppState(data_dir, config)
    preferred_port = args.port if args.port is not None else config.preferred_port
    try:
        server = _create_server(preferred_port, state)
    except OSError:
        state.storage.close()
        LOGGER.exception("unable to bind local server")
        return 4
    state.server = server
    actual_port = server.server_address[1]
    _write_runtime(data_dir, actual_port, state.instance_id)
    state.storage.add_audit("application.start", "vsg", "success", {"port": actual_port, "version": __version__})
    state.collector.start()

    if args.open:
        threading.Timer(0.8, lambda: webbrowser.open(f"http://127.0.0.1:{actual_port}/", new=2)).start()

    stopping = threading.Event()

    def handle_signal(_signum: int, _frame: Any) -> None:
        if not stopping.is_set():
            stopping.set()
            threading.Thread(target=server.shutdown, daemon=True).start()

    for signal_name in ("SIGINT", "SIGTERM"):
        if hasattr(signal, signal_name):
            try:
                signal.signal(getattr(signal, signal_name), handle_signal)
            except (OSError, ValueError):
                pass

    LOGGER.info("VSG %s listening on 127.0.0.1:%s", __version__, actual_port)
    try:
        server.serve_forever(poll_interval=0.4)
    finally:
        server.server_close()
        state.close()
        runtime = _read_runtime(data_dir)
        if runtime and runtime.get("pid") == os.getpid():
            try:
                _runtime_file(data_dir).unlink()
            except OSError:
                pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
