from __future__ import annotations

import json
import ipaddress
import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .platforms import DEFAULT_PROTECTED_NAMES, default_project_roots, default_windows_features
from .privacy import atomic_write_private_text


def application_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def default_data_dir() -> Path:
    override = os.environ.get("VSG_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()
    return application_root() / "data"


def _default_roots() -> list[str]:
    return default_project_roots()


@dataclass(slots=True)
class AppConfig:
    project_roots: list[str] = field(default_factory=_default_roots)
    refresh_seconds: int = 5
    review_score: int = 35
    likely_stale_score: int = 60
    stale_after_hours: int = 8
    history_days: int = 14
    include_udp: bool = True
    include_windows_services: bool = field(default_factory=default_windows_features)
    include_docker: bool = True
    include_wsl: bool = field(default_factory=default_windows_features)
    protected_names: list[str] = field(default_factory=lambda: list(DEFAULT_PROTECTED_NAMES))
    preferred_port: int = 43921
    electricity_price_per_kwh: float = 0.6
    low_disk_free_gib: int = 50
    log_retention_days: int = 7
    enable_runtime_probes: bool = True
    trusted_nodes: list[str] = field(default_factory=list)

    def public_dict(self) -> dict[str, Any]:
        return asdict(self)


def validate_config(data: dict[str, Any], base: AppConfig | None = None) -> AppConfig:
    source = base or AppConfig()
    merged = source.public_dict()
    allowed = set(merged)
    for key, value in data.items():
        if key in allowed:
            merged[key] = value

    roots: list[str] = []
    raw_roots = merged.get("project_roots", [])
    if not isinstance(raw_roots, list) or len(raw_roots) > 20:
        raise ValueError("project_roots 必须是最多 20 项的数组")
    for item in raw_roots:
        if not isinstance(item, str) or not item.strip():
            raise ValueError("项目根目录不能为空")
        path = Path(item).expanduser()
        if not path.is_absolute():
            raise ValueError(f"项目根目录必须是绝对路径：{item}")
        normalized = str(path.resolve(strict=False))
        if normalized.lower() not in {value.lower() for value in roots}:
            roots.append(normalized)
    if not roots:
        raise ValueError("至少需要一个项目根目录")
    merged["project_roots"] = roots

    numeric_ranges = {
        "refresh_seconds": (2, 30),
        "review_score": (1, 99),
        "likely_stale_score": (2, 100),
        "stale_after_hours": (1, 720),
        "history_days": (1, 365),
        "preferred_port": (1024, 65535),
        "low_disk_free_gib": (1, 4096),
        "log_retention_days": (1, 90),
    }
    for key, (minimum, maximum) in numeric_ranges.items():
        value = merged[key]
        if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
            raise ValueError(f"{key} 必须在 {minimum} 到 {maximum} 之间")
    if merged["review_score"] >= merged["likely_stale_score"]:
        raise ValueError("review_score 必须小于 likely_stale_score")

    rate = merged.get("electricity_price_per_kwh")
    if isinstance(rate, bool) or not isinstance(rate, (int, float)) or not 0 <= float(rate) <= 100:
        raise ValueError("electricity_price_per_kwh 必须在 0 到 100 之间")
    merged["electricity_price_per_kwh"] = round(float(rate), 4)

    for key in (
        "include_udp",
        "include_windows_services",
        "include_docker",
        "include_wsl",
        "enable_runtime_probes",
    ):
        if not isinstance(merged[key], bool):
            raise ValueError(f"{key} 必须是布尔值")

    nodes = merged.get("trusted_nodes", [])
    if not isinstance(nodes, list) or len(nodes) > 8:
        raise ValueError("trusted_nodes 必须是最多 8 项的数组")
    trusted_nodes: list[str] = []
    for raw in nodes:
        if not isinstance(raw, str) or not raw.strip():
            raise ValueError("受信节点地址不能为空")
        value = raw.strip().rstrip("/")
        parsed = urlsplit(value)
        if parsed.scheme != "http" or not parsed.hostname or parsed.port is None:
            raise ValueError("受信节点必须使用 http://主机:端口 格式")
        if parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
            raise ValueError("受信节点地址不能包含凭据、查询参数或额外路径")
        host = parsed.hostname.lower()
        allowed_host = host == "localhost" or host.endswith(".local")
        try:
            address = ipaddress.ip_address(host)
            allowed_host = (
                address.is_loopback
                or address.is_link_local
                or (address.is_private and not address.is_reserved)
            ) and not address.is_unspecified and not address.is_multicast
        except ValueError:
            pass
        if not allowed_host:
            raise ValueError("受信节点仅允许回环、私网 IP 或 .local 主机；VSG 不扫描公网节点")
        canonical = f"http://{parsed.netloc.lower()}"
        if canonical not in trusted_nodes:
            trusted_nodes.append(canonical)
    merged["trusted_nodes"] = trusted_nodes

    protected = merged.get("protected_names", [])
    if (
        not isinstance(protected, list)
        or len(protected) > 256
        or not all(isinstance(item, str) for item in protected)
    ):
        raise ValueError("protected_names 必须是最多 256 项的字符串数组")
    if any(len(item.strip()) > 128 or any(char in item for char in "\r\n\0") for item in protected):
        raise ValueError("protected_names 单项必须不超过 128 字符且不能包含控制字符")
    merged["protected_names"] = sorted(
        {item.strip().lower() for item in protected if item.strip()}
        | {item.lower() for item in DEFAULT_PROTECTED_NAMES}
    )
    return AppConfig(**merged)


def load_config(data_dir: Path | None = None) -> AppConfig:
    directory = data_dir or default_data_dir()
    path = directory / "config.json"
    if not path.exists():
        return AppConfig()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("配置文件根节点必须是对象")
        return validate_config(raw)
    except (OSError, json.JSONDecodeError, ValueError):
        return AppConfig()


def save_config(config: AppConfig, data_dir: Path | None = None) -> Path:
    directory = data_dir or default_data_dir()
    path = directory / "config.json"
    atomic_write_private_text(
        path,
        json.dumps(config.public_dict(), ensure_ascii=False, indent=2) + "\n",
    )
    return path
