from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class Endpoint:
    protocol: str
    address: str
    port: int
    state: str = "LISTEN"
    exposure: str = "loopback"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ProcessSnapshot:
    pid: int
    ppid: int | None = None
    name: str = "unknown"
    exe: str | None = None
    cmdline: list[str] = field(default_factory=list)
    cwd: str | None = None
    create_time: float | None = None
    cpu_percent: float = 0.0
    memory_percent: float = 0.0
    status: str = "unknown"
    accessible: bool = True

    @property
    def command(self) -> str:
        return " ".join(self.cmdline)

    def public_dict(self) -> dict[str, Any]:
        return {
            "pid": self.pid,
            "ppid": self.ppid,
            "name": self.name,
            "exe": self.exe,
            "command": self.command,
            "cwd": self.cwd,
            "create_time": self.create_time,
            "cpu_percent": round(self.cpu_percent, 2),
            "memory_percent": round(self.memory_percent, 2),
            "status": self.status,
            "accessible": self.accessible,
        }


@dataclass(slots=True)
class ProjectAttribution:
    name: str | None = None
    path: str | None = None
    confidence: int = 0
    evidence: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class AgentAttribution:
    provider: str | None = None
    kind: str = "unknown"
    session_id: str | None = None
    confidence: int = 0
    active: bool = False
    evidence: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class RiskAssessment:
    score: int = 0
    level: str = "normal"
    reasons: list[str] = field(default_factory=list)
    scored: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ServiceRecord:
    id: str
    fingerprint: str
    source: str
    display_name: str
    runtime: str
    process: ProcessSnapshot
    endpoints: list[Endpoint] = field(default_factory=list)
    ancestor_chain: list[ProcessSnapshot] = field(default_factory=list)
    project: ProjectAttribution = field(default_factory=ProjectAttribution)
    agent: AgentAttribution = field(default_factory=AgentAttribution)
    risk: RiskAssessment = field(default_factory=RiskAssessment)
    windows_services: list[str] = field(default_factory=list)
    established_connections: int = 0
    first_seen: float | None = None
    last_seen: float | None = None
    expected: bool = False
    protected: bool = False
    tags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "fingerprint": self.fingerprint,
            "source": self.source,
            "display_name": self.display_name,
            "runtime": self.runtime,
            "process": self.process.public_dict(),
            "endpoints": [item.to_dict() for item in self.endpoints],
            "ancestor_chain": [item.public_dict() for item in self.ancestor_chain],
            "project": self.project.to_dict(),
            "agent": self.agent.to_dict(),
            "risk": self.risk.to_dict(),
            "windows_services": self.windows_services,
            "established_connections": self.established_connections,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
            "expected": self.expected,
            "protected": self.protected,
            "tags": self.tags,
            "metadata": self.metadata,
        }
