from __future__ import annotations

import json
import hashlib
import secrets
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterable

from .models import ServiceRecord
from .privacy import ensure_private_directory, harden_private_file
from .project_rules import infer_rule_scope, rule_specificity


MAX_BENCHMARK_DETAILS_CHARS = 30_000
CURRENT_SCHEMA_VERSION = 6
MAX_ATTRIBUTION_RULE_VERSIONS = 5


class StorageError(RuntimeError):
    """Base class for local history database initialization failures."""


class StorageVersionError(StorageError):
    """Raised when a newer database is opened by an older VSG build."""


class StorageRecoveryError(StorageError):
    """Raised when integrity validation or safe recovery cannot complete."""


def _bounded_benchmark_details(
    value: Any, limit: int = MAX_BENCHMARK_DETAILS_CHARS
) -> str:
    """Serialize benchmark detail without ever storing truncated JSON text.

    Per-request samples are useful for a short benchmark but can grow beyond
    the local history budget.  When compaction is needed, retain calibration
    evidence and policies field-by-field and explicitly record omissions.
    """

    details = value if isinstance(value, dict) else {}
    encoded = json.dumps(details, ensure_ascii=False, separators=(",", ":"))
    if len(encoded) <= limit:
        return encoded

    compact: dict[str, Any] = {
        "_truncated": True,
        "_original_characters": len(encoded),
        "requests_omitted": len(details.get("requests") or [])
        if isinstance(details.get("requests"), list)
        else 0,
    }
    for key in (
        "matrix",
        "prompt_policy",
        "limits",
        "p95_policy",
        "cancellation_policy",
        "response_content_persisted",
    ):
        if key not in details:
            continue
        candidate = {**compact, key: details[key]}
        candidate_encoded = json.dumps(
            candidate, ensure_ascii=False, separators=(",", ":")
        )
        if len(candidate_encoded) <= limit:
            compact = candidate
        else:
            compact[f"{key}_omitted"] = True
    return json.dumps(compact, ensure_ascii=False, separators=(",", ":"))


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _snapshot_sha256(value: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


SCHEMA = """
CREATE TABLE IF NOT EXISTS observations (
    fingerprint TEXT PRIMARY KEY,
    first_seen REAL NOT NULL,
    last_seen REAL NOT NULL,
    last_agent_provider TEXT,
    last_session_id TEXT,
    last_project_path TEXT,
    last_pid INTEGER,
    command_hash TEXT,
    last_create_time REAL,
    restart_count INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS user_marks (
    fingerprint TEXT PRIMARY KEY,
    expected INTEGER NOT NULL DEFAULT 0,
    protected INTEGER NOT NULL DEFAULT 0,
    note TEXT,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS impact_feedback (
    service_fingerprint TEXT PRIMARY KEY,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    outcome TEXT NOT NULL,
    assessed_risk_level TEXT NOT NULL,
    assessed_risk_score INTEGER,
    source TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS impact_feedback_updated
    ON impact_feedback(updated_at DESC);
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at REAL NOT NULL,
    action TEXT NOT NULL,
    target TEXT NOT NULL,
    result TEXT NOT NULL,
    details TEXT
);
CREATE INDEX IF NOT EXISTS audit_created_at ON audit_log(created_at DESC);
CREATE TABLE IF NOT EXISTS model_benchmarks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at REAL NOT NULL,
    hardware_fingerprint TEXT NOT NULL,
    model_id TEXT NOT NULL,
    model_file_name TEXT NOT NULL,
    model_file_size_bytes INTEGER NOT NULL,
    quantization TEXT NOT NULL,
    runtime TEXT NOT NULL,
    prompt_tps REAL,
    generation_tps REAL NOT NULL,
    result_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS model_benchmark_lookup
    ON model_benchmarks(hardware_fingerprint, model_id, quantization, created_at DESC);
CREATE TABLE IF NOT EXISTS service_benchmarks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at REAL NOT NULL,
    service_fingerprint TEXT NOT NULL,
    runtime TEXT NOT NULL,
    port INTEGER NOT NULL,
    model_name TEXT,
    concurrency INTEGER NOT NULL,
    requested_context_tokens INTEGER NOT NULL,
    requested_output_tokens INTEGER NOT NULL,
    successful_requests INTEGER NOT NULL,
    failed_requests INTEGER NOT NULL,
    ttft_seconds REAL,
    generation_tps REAL,
    result_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS service_benchmark_lookup
    ON service_benchmarks(service_fingerprint, created_at DESC);
CREATE TABLE IF NOT EXISTS stop_verifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at REAL NOT NULL,
    service_fingerprint TEXT NOT NULL,
    service_id TEXT,
    original_pid INTEGER NOT NULL,
    outcome TEXT NOT NULL,
    restart_detected INTEGER NOT NULL DEFAULT 0,
    result_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS stop_verification_recent
    ON stop_verifications(service_fingerprint, created_at DESC);
CREATE TABLE IF NOT EXISTS stop_observations (
    job_id TEXT PRIMARY KEY,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    deadline_at REAL NOT NULL,
    status TEXT NOT NULL,
    service_fingerprint TEXT NOT NULL,
    service_id TEXT,
    display_name TEXT,
    project_name TEXT,
    original_pid INTEGER NOT NULL,
    observation_minutes INTEGER NOT NULL,
    poll_seconds INTEGER NOT NULL,
    result_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS stop_observation_recent
    ON stop_observations(updated_at DESC);
CREATE INDEX IF NOT EXISTS stop_observation_status
    ON stop_observations(status, updated_at DESC);
CREATE TABLE IF NOT EXISTS calibration_profiles (
    profile_id TEXT PRIMARY KEY,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    hardware_fingerprint TEXT NOT NULL,
    vram_total_gib REAL,
    service_fingerprint TEXT NOT NULL,
    runtime TEXT NOT NULL,
    port INTEGER NOT NULL,
    model_name TEXT NOT NULL,
    catalog_model_id TEXT,
    quantization TEXT,
    concurrency INTEGER NOT NULL,
    duration_seconds INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    result_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS calibration_profile_recent
    ON calibration_profiles(updated_at DESC);
CREATE INDEX IF NOT EXISTS calibration_profile_hardware
    ON calibration_profiles(hardware_fingerprint, model_name, concurrency, updated_at DESC);
CREATE TABLE IF NOT EXISTS log_watches (
    id TEXT PRIMARY KEY,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    service_id TEXT NOT NULL,
    service_fingerprint TEXT NOT NULL,
    pid INTEGER NOT NULL,
    process_create_time REAL,
    runtime TEXT NOT NULL,
    path TEXT NOT NULL,
    file_name TEXT NOT NULL,
    file_identity TEXT,
    byte_offset INTEGER NOT NULL DEFAULT 0,
    last_size INTEGER NOT NULL DEFAULT 0,
    enabled INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'watching',
    last_error TEXT
);
CREATE INDEX IF NOT EXISTS log_watch_enabled ON log_watches(enabled, updated_at DESC);
CREATE TABLE IF NOT EXISTS log_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    watch_id TEXT NOT NULL,
    service_fingerprint TEXT NOT NULL,
    runtime TEXT NOT NULL,
    first_seen REAL NOT NULL,
    last_seen REAL NOT NULL,
    severity TEXT NOT NULL,
    category TEXT NOT NULL,
    code TEXT NOT NULL,
    message TEXT NOT NULL,
    message_hash TEXT NOT NULL,
    occurrences INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY(watch_id) REFERENCES log_watches(id)
);
CREATE UNIQUE INDEX IF NOT EXISTS log_event_dedup
    ON log_events(watch_id, code, message_hash);
CREATE INDEX IF NOT EXISTS log_event_recent ON log_events(last_seen DESC);
CREATE TABLE IF NOT EXISTS attribution_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    name TEXT NOT NULL,
    priority INTEGER NOT NULL DEFAULT 100,
    enabled INTEGER NOT NULL DEFAULT 1,
    source TEXT NOT NULL DEFAULT 'user',
    scope TEXT NOT NULL DEFAULT 'legacy',
    specificity INTEGER NOT NULL DEFAULT 0,
    revision INTEGER NOT NULL DEFAULT 1,
    hit_count INTEGER NOT NULL DEFAULT 0,
    last_hit_at REAL,
    override_count INTEGER NOT NULL DEFAULT 0,
    last_override_at REAL,
    needs_review INTEGER NOT NULL DEFAULT 0,
    match_json TEXT NOT NULL,
    override_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS attribution_rule_priority
    ON attribution_rules(enabled, priority DESC, id DESC);
CREATE TABLE IF NOT EXISTS attribution_rule_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_id INTEGER NOT NULL,
    version INTEGER NOT NULL,
    created_at REAL NOT NULL,
    action TEXT NOT NULL,
    snapshot_json TEXT NOT NULL,
    snapshot_sha256 TEXT NOT NULL,
    UNIQUE(rule_id, version)
);
CREATE INDEX IF NOT EXISTS attribution_rule_version_recent
    ON attribution_rule_versions(rule_id, version DESC);
CREATE TABLE IF NOT EXISTS attribution_episodes (
    episode_key TEXT PRIMARY KEY,
    first_seen REAL NOT NULL,
    last_seen REAL NOT NULL,
    source TEXT NOT NULL,
    service_fingerprint TEXT NOT NULL,
    initial_json TEXT NOT NULL,
    winner_rule_id INTEGER,
    corrected INTEGER NOT NULL DEFAULT 0,
    correction_count INTEGER NOT NULL DEFAULT 0,
    last_correction_at REAL
);
CREATE INDEX IF NOT EXISTS attribution_episode_recent
    ON attribution_episodes(first_seen DESC);
CREATE TABLE IF NOT EXISTS attribution_rule_hits (
    rule_id INTEGER NOT NULL,
    episode_key TEXT NOT NULL,
    first_hit_at REAL NOT NULL,
    last_hit_at REAL NOT NULL,
    overridden_at REAL,
    PRIMARY KEY(rule_id, episode_key)
);
CREATE INDEX IF NOT EXISTS attribution_rule_hit_recent
    ON attribution_rule_hits(rule_id, last_hit_at DESC);
CREATE TABLE IF NOT EXISTS attribution_corrections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    corrected_at REAL NOT NULL,
    episode_key TEXT NOT NULL,
    service_fingerprint TEXT NOT NULL,
    before_json TEXT NOT NULL,
    after_json TEXT NOT NULL,
    rule_ids_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS attribution_correction_recent
    ON attribution_corrections(corrected_at DESC);
CREATE TABLE IF NOT EXISTS timeline_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    first_seen REAL NOT NULL,
    last_seen REAL NOT NULL,
    category TEXT NOT NULL,
    code TEXT NOT NULL,
    severity TEXT NOT NULL,
    service_fingerprint TEXT,
    service_id TEXT,
    project_name TEXT,
    agent_provider TEXT,
    title_zh TEXT NOT NULL,
    title_en TEXT NOT NULL,
    details_json TEXT NOT NULL,
    dedup_key TEXT NOT NULL,
    occurrences INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS timeline_recent ON timeline_events(last_seen DESC);
CREATE INDEX IF NOT EXISTS timeline_service ON timeline_events(service_fingerprint, last_seen DESC);
CREATE TABLE IF NOT EXISTS telemetry_samples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    observed_at REAL NOT NULL,
    cpu_percent REAL,
    memory_percent REAL,
    gpu_memory_percent REAL,
    gpu_temperature_c REAL,
    disk_free_gib REAL,
    public_connections INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS telemetry_sample_recent ON telemetry_samples(observed_at DESC);
CREATE TABLE IF NOT EXISTS model_inventory_scans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at REAL NOT NULL,
    root_name TEXT NOT NULL,
    root_hash TEXT NOT NULL,
    result_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS model_inventory_recent ON model_inventory_scans(created_at DESC);
"""


class Storage:
    def __init__(self, data_dir: Path):
        ensure_private_directory(data_dir)
        self.path = data_dir / "history.sqlite3"
        self._lock = threading.RLock()
        self._connection: sqlite3.Connection
        self._closed = False
        self._status: dict[str, Any] = {
            "database_file": self.path.name,
            "schema_version": 0,
            "target_schema_version": CURRENT_SCHEMA_VERSION,
            "integrity": "unknown",
            "migration": None,
            "recovery": None,
        }
        self._initialize()

    @staticmethod
    def _timestamp_token() -> str:
        return f"{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(3)}"

    @staticmethod
    def _is_corruption_error(exc: sqlite3.DatabaseError) -> bool:
        message = str(exc).casefold()
        return any(
            marker in message
            for marker in (
                "file is not a database",
                "database disk image is malformed",
                "database malformed",
                "malformed database schema",
            )
        )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def _quick_check(connection: sqlite3.Connection) -> tuple[bool, str]:
        row = connection.execute("PRAGMA quick_check").fetchone()
        detail = str(row[0]) if row else "no result"
        return detail.casefold() == "ok", detail[:300]

    def _quarantine_corrupt_database(self, detail: str) -> dict[str, Any]:
        token = self._timestamp_token()
        quarantined: list[str] = []
        for suffix in ("", "-wal", "-shm"):
            source = Path(f"{self.path}{suffix}")
            if not source.exists():
                continue
            target = self.path.with_name(f"history.corrupt-{token}.sqlite3{suffix}")
            try:
                source.replace(target)
                harden_private_file(target)
            except OSError as exc:
                raise StorageRecoveryError(
                    f"无法隔离损坏的本地历史数据库：{source.name}"
                ) from exc
            quarantined.append(target.name)
        if not quarantined:
            raise StorageRecoveryError("历史数据库完整性异常，但未找到可隔离的数据库文件")
        return {
            "action": "quarantined_and_recreated",
            "reason": detail[:300],
            "quarantined_files": quarantined,
        }

    def _backup_database(self, from_version: int) -> Path:
        token = self._timestamp_token()
        final_path = self.path.with_name(
            f"history.pre-migration-v{from_version}-to-v{CURRENT_SCHEMA_VERSION}-{token}.sqlite3"
        )
        temporary_path = final_path.with_name(f"{final_path.name}.tmp-{secrets.token_hex(3)}")
        backup_connection: sqlite3.Connection | None = None
        try:
            backup_connection = sqlite3.connect(temporary_path)
            self._connection.backup(backup_connection)
            backup_connection.close()
            backup_connection = None
            harden_private_file(temporary_path)
            temporary_path.replace(final_path)
            harden_private_file(final_path)
            return final_path
        except (OSError, sqlite3.DatabaseError) as exc:
            if backup_connection is not None:
                backup_connection.close()
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise StorageRecoveryError("升级前数据库备份失败，已拒绝迁移") from exc

    def _execute_schema(self) -> None:
        for statement in SCHEMA.split(";"):
            sql = statement.strip()
            if sql:
                self._connection.execute(sql)

    def _ensure_additive_columns(self) -> None:
        for table, column, definition in (
            ("observations", "last_create_time", "REAL"),
            ("observations", "restart_count", "INTEGER NOT NULL DEFAULT 0"),
            ("log_watches", "file_identity", "TEXT"),
            ("service_benchmarks", "matrix_id", "TEXT"),
            ("service_benchmarks", "matrix_step_id", "TEXT"),
            ("service_benchmarks", "request_count", "INTEGER"),
            ("service_benchmarks", "hardware_fingerprint", "TEXT"),
            ("service_benchmarks", "catalog_model_id", "TEXT"),
            ("service_benchmarks", "quantization", "TEXT"),
            ("service_benchmarks", "prompt_tps", "REAL"),
            ("service_benchmarks", "aggregate_generation_tps", "REAL"),
            ("service_benchmarks", "ttft_p95_seconds", "REAL"),
            ("service_benchmarks", "sample_count", "INTEGER"),
            ("attribution_rules", "source", "TEXT NOT NULL DEFAULT 'user'"),
            ("attribution_rules", "scope", "TEXT NOT NULL DEFAULT 'legacy'"),
            ("attribution_rules", "specificity", "INTEGER NOT NULL DEFAULT 0"),
            ("attribution_rules", "revision", "INTEGER NOT NULL DEFAULT 1"),
            ("attribution_rules", "hit_count", "INTEGER NOT NULL DEFAULT 0"),
            ("attribution_rules", "last_hit_at", "REAL"),
            ("attribution_rules", "override_count", "INTEGER NOT NULL DEFAULT 0"),
            ("attribution_rules", "last_override_at", "REAL"),
            ("attribution_rules", "needs_review", "INTEGER NOT NULL DEFAULT 0"),
        ):
            self._ensure_column(table, column, definition)

    def _migrate_attribution_rules_v6(self) -> None:
        self._execute_schema()
        self._ensure_additive_columns()
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                applied_at REAL NOT NULL
            )
            """
        )
        rows = self._connection.execute("SELECT * FROM attribution_rules").fetchall()
        for row in rows:
            try:
                match = json.loads(row["match_json"] or "{}")
            except (json.JSONDecodeError, TypeError):
                match = {}
            scope = infer_rule_scope(match)
            specificity = rule_specificity(match, scope)
            self._connection.execute(
                """
                UPDATE attribution_rules
                SET source = CASE WHEN source = 'user' THEN 'migration' ELSE source END,
                    scope = ?, specificity = ?
                WHERE id = ?
                """,
                (scope, specificity, int(row["id"])),
            )
        self._connection.execute("DROP INDEX IF EXISTS attribution_rule_priority")
        self._connection.execute(
            """
            CREATE INDEX attribution_rule_priority
            ON attribution_rules(
                enabled, specificity DESC, updated_at DESC, priority DESC, id DESC
            )
            """
        )
        for row in self._connection.execute("SELECT * FROM attribution_rules").fetchall():
            snapshot = self._rule_snapshot(dict(row))
            self._connection.execute(
                """
                INSERT OR IGNORE INTO attribution_rule_versions(
                    rule_id, version, created_at, action, snapshot_json, snapshot_sha256
                ) VALUES(?, ?, ?, 'migration', ?, ?)
                """,
                (
                    int(row["id"]),
                    int(row["revision"] or 1),
                    time.time(),
                    _canonical_json(snapshot),
                    _snapshot_sha256(snapshot),
                ),
            )

    def _apply_migration(self, target_version: int) -> None:
        if target_version == 1:
            self._execute_schema()
            return
        if target_version == 2:
            self._execute_schema()
            self._ensure_additive_columns()
            return
        if target_version == 3:
            self._execute_schema()
            self._ensure_additive_columns()
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at REAL NOT NULL
                )
                """
            )
            return
        if target_version == 4:
            self._execute_schema()
            self._ensure_additive_columns()
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at REAL NOT NULL
                )
                """
            )
            return
        if target_version == 5:
            self._execute_schema()
            self._ensure_additive_columns()
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at REAL NOT NULL
                )
                """
            )
            return
        if target_version == 6:
            self._migrate_attribution_rules_v6()
            return
        raise StorageVersionError(f"没有数据库版本 {target_version} 的迁移程序")

    def _migrate(self, from_version: int) -> None:
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            for target_version in range(from_version + 1, CURRENT_SCHEMA_VERSION + 1):
                self._apply_migration(target_version)
                self._connection.execute(f"PRAGMA user_version = {target_version}")
                if target_version >= 3:
                    self._connection.execute(
                        "INSERT OR REPLACE INTO schema_migrations(version, applied_at) VALUES(?, ?)",
                        (target_version, time.time()),
                    )
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise

    def _initialize(self) -> None:
        existed_with_content = self.path.exists() and self.path.stat().st_size > 0
        connection: sqlite3.Connection | None = None
        try:
            connection = self._connect()
            try:
                integrity_ok, integrity_detail = self._quick_check(connection)
            except sqlite3.DatabaseError as exc:
                if not self._is_corruption_error(exc):
                    raise StorageRecoveryError(
                        "无法验证本地历史数据库完整性，未对数据库做任何修改"
                    ) from exc
                connection.close()
                connection = None
                self._status["recovery"] = self._quarantine_corrupt_database(str(exc))
                existed_with_content = False
                connection = self._connect()
                integrity_ok, integrity_detail = self._quick_check(connection)
            if not integrity_ok:
                connection.close()
                connection = None
                self._status["recovery"] = self._quarantine_corrupt_database(
                    integrity_detail
                )
                existed_with_content = False
                connection = self._connect()
                integrity_ok, integrity_detail = self._quick_check(connection)
            if not integrity_ok:
                raise StorageRecoveryError("重新创建的历史数据库仍未通过完整性检查")

            self._connection = connection
            connection = None
            harden_private_file(self.path)
            from_version = int(self._connection.execute("PRAGMA user_version").fetchone()[0])
            if from_version > CURRENT_SCHEMA_VERSION:
                raise StorageVersionError(
                    f"历史数据库版本 {from_version} 高于当前程序支持的 "
                    f"{CURRENT_SCHEMA_VERSION}，已拒绝降级打开"
                )

            backup_path: Path | None = None
            if existed_with_content and from_version < CURRENT_SCHEMA_VERSION:
                backup_path = self._backup_database(from_version)
            if from_version < CURRENT_SCHEMA_VERSION:
                self._migrate(from_version)

            final_ok, final_detail = self._quick_check(self._connection)
            if not final_ok:
                raise StorageRecoveryError(
                    f"数据库迁移后的完整性检查失败：{final_detail}"
                )
            self._status.update(
                {
                    "schema_version": CURRENT_SCHEMA_VERSION,
                    "integrity": "ok",
                    "migration": (
                        {
                            "from_version": from_version,
                            "to_version": CURRENT_SCHEMA_VERSION,
                            "backup_file": backup_path.name if backup_path else None,
                        }
                        if from_version < CURRENT_SCHEMA_VERSION
                        else None
                    ),
                }
            )
        except Exception:
            if connection is not None:
                connection.close()
            active = getattr(self, "_connection", None)
            if active is not None:
                active.close()
            self._closed = True
            raise

    def _ensure_column(self, table: str, column: str, definition: str) -> None:
        columns = {
            str(row["name"])
            for row in self._connection.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if column not in columns:
            self._connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def close(self) -> None:
        with self._lock:
            if not self._closed:
                self._connection.close()
                self._closed = True

    def status(self) -> dict[str, Any]:
        """Return a path-safe copy of migration and recovery state for diagnostics."""

        with self._lock:
            return json.loads(json.dumps(self._status, ensure_ascii=False))

    def marks(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute("SELECT * FROM user_marks").fetchall()
        return {
            row["fingerprint"]: {
                "expected": bool(row["expected"]),
                "protected": bool(row["protected"]),
                "note": row["note"],
                "updated_at": row["updated_at"],
            }
            for row in rows
        }

    def set_mark(self, fingerprint: str, expected: bool, protected: bool, note: str | None = None) -> None:
        if len(fingerprint) > 128:
            raise ValueError("fingerprint 无效")
        clean_note = (note or "").strip()[:300] or None
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT INTO user_marks(fingerprint, expected, protected, note, updated_at)
                VALUES(?, ?, ?, ?, ?)
                ON CONFLICT(fingerprint) DO UPDATE SET
                    expected=excluded.expected,
                    protected=excluded.protected,
                    note=excluded.note,
                    updated_at=excluded.updated_at
                """,
                (fingerprint, int(expected), int(protected), clean_note, time.time()),
            )

    def set_impact_feedback(
        self,
        fingerprint: str,
        outcome: str,
        assessed_risk_level: str,
        assessed_risk_score: int | None,
        source: str,
    ) -> dict[str, Any]:
        """Upsert one privacy-minimized human outcome per service fingerprint."""

        clean_fingerprint = str(fingerprint).strip()
        allowed_outcomes = {"confirmed_stale", "not_stale", "uncertain"}
        allowed_risk_levels = {
            "normal",
            "expected",
            "review",
            "likely_stale",
            "not_scored",
            "unknown",
        }
        allowed_sources = {"host", "agent", "windows_service", "docker", "wsl", "unknown"}
        if not clean_fingerprint or len(clean_fingerprint) > 128:
            raise ValueError("service fingerprint 无效")
        if outcome not in allowed_outcomes:
            raise ValueError("成效反馈 outcome 无效")
        if assessed_risk_level not in allowed_risk_levels:
            raise ValueError("成效反馈风险等级无效")
        if source not in allowed_sources:
            raise ValueError("成效反馈来源无效")
        score: int | None = None
        if assessed_risk_score is not None:
            if isinstance(assessed_risk_score, bool):
                raise ValueError("成效反馈风险分数无效")
            score = int(assessed_risk_score)
            if not 0 <= score <= 100:
                raise ValueError("成效反馈风险分数无效")
        now = time.time()
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT INTO impact_feedback(
                    service_fingerprint, created_at, updated_at, outcome,
                    assessed_risk_level, assessed_risk_score, source
                ) VALUES(?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(service_fingerprint) DO UPDATE SET
                    updated_at=excluded.updated_at,
                    outcome=excluded.outcome,
                    assessed_risk_level=excluded.assessed_risk_level,
                    assessed_risk_score=excluded.assessed_risk_score,
                    source=excluded.source
                """,
                (
                    clean_fingerprint,
                    now,
                    now,
                    outcome,
                    assessed_risk_level,
                    score,
                    source,
                ),
            )
            row = self._connection.execute(
                """
                SELECT created_at, updated_at, outcome, assessed_risk_level,
                       assessed_risk_score, source
                FROM impact_feedback WHERE service_fingerprint = ?
                """,
                (clean_fingerprint,),
            ).fetchone()
        return dict(row) if row else {}

    def impact_feedbacks(self, fingerprints: Iterable[str]) -> dict[str, dict[str, Any]]:
        values = list(
            dict.fromkeys(
                value[:128]
                for item in fingerprints
                if (value := str(item or "").strip())
            )
        )
        if not values:
            return {}
        rows: list[sqlite3.Row] = []
        with self._lock:
            for offset in range(0, len(values), 500):
                batch = values[offset : offset + 500]
                placeholders = ",".join("?" for _ in batch)
                rows.extend(
                    self._connection.execute(
                        # placeholders is generated only from the bounded batch size.
                        f"SELECT * FROM impact_feedback WHERE service_fingerprint IN ({placeholders})",  # nosec B608
                        batch,
                    ).fetchall()
                )
        result: dict[str, dict[str, Any]] = {}
        for row in rows:
            item = dict(row)
            fingerprint = str(item.pop("service_fingerprint"))
            result[fingerprint] = item
        return result

    def impact_statistics(self) -> dict[str, Any]:
        """Return aggregate-only evidence suitable for a redacted local report."""

        feedback_outcomes = {"confirmed_stale": 0, "not_stale": 0, "uncertain": 0}
        feedback_sources = {
            "host": 0,
            "agent": 0,
            "windows_service": 0,
            "docker": 0,
            "wsl": 0,
            "unknown": 0,
        }
        stop_outcomes = {
            "stopped": 0,
            "relaunched": 0,
            "stop_incomplete": 0,
            "verification_partial": 0,
            "other": 0,
        }
        with self._lock:
            observation_row = self._connection.execute(
                "SELECT COUNT(*) AS total, MIN(first_seen) AS first_seen, MAX(last_seen) AS last_seen FROM observations"
            ).fetchone()
            correction_count = int(
                self._connection.execute(
                    "SELECT COUNT(*) FROM audit_log WHERE action = 'service.attribution_corrected' AND result = 'success'"
                ).fetchone()[0]
            )
            feedback_rows = self._connection.execute(
                "SELECT outcome, assessed_risk_level, source FROM impact_feedback"
            ).fetchall()
            model_benchmarks = int(
                self._connection.execute("SELECT COUNT(*) FROM model_benchmarks").fetchone()[0]
            )
            service_benchmarks = int(
                self._connection.execute("SELECT COUNT(*) FROM service_benchmarks").fetchone()[0]
            )
            successful_service_benchmarks = int(
                self._connection.execute(
                    "SELECT COUNT(*) FROM service_benchmarks WHERE successful_requests > 0"
                ).fetchone()[0]
            )
            benchmarked_services = int(
                self._connection.execute(
                    "SELECT COUNT(DISTINCT service_fingerprint) FROM service_benchmarks"
                ).fetchone()[0]
            )
            stop_rows = self._connection.execute(
                "SELECT outcome, COUNT(*) AS total FROM stop_verifications GROUP BY outcome"
            ).fetchall()
            restart_detected = int(
                self._connection.execute(
                    "SELECT COUNT(*) FROM stop_verifications WHERE restart_detected = 1"
                ).fetchone()[0]
            )

        decisive = 0
        agreement = 0
        for row in feedback_rows:
            outcome = str(row["outcome"])
            risk_level = str(row["assessed_risk_level"])
            source = str(row["source"])
            if outcome in feedback_outcomes:
                feedback_outcomes[outcome] += 1
            if source in feedback_sources:
                feedback_sources[source] += 1
            else:
                feedback_sources["unknown"] += 1
            if outcome in {"confirmed_stale", "not_stale"}:
                decisive += 1
                was_candidate = risk_level in {"review", "likely_stale"}
                if (outcome == "confirmed_stale" and was_candidate) or (
                    outcome == "not_stale" and not was_candidate
                ):
                    agreement += 1
        for row in stop_rows:
            outcome = str(row["outcome"])
            key = outcome if outcome in stop_outcomes else "other"
            stop_outcomes[key] += int(row["total"])
        observed_total = int(observation_row["total"] if observation_row else 0)
        return {
            "retained_observations": {
                "unique_services": observed_total,
                "first_seen": float(observation_row["first_seen"])
                if observation_row and observation_row["first_seen"] is not None
                else None,
                "last_seen": float(observation_row["last_seen"])
                if observation_row and observation_row["last_seen"] is not None
                else None,
            },
            "attribution_corrections": correction_count,
            "feedback": {
                "total": len(feedback_rows),
                "outcomes": feedback_outcomes,
                "sources": feedback_sources,
                "decisive": decisive,
                "agreement_with_original_assessment": agreement,
                "agreement_rate_percent": round(agreement / decisive * 100, 2)
                if decisive
                else None,
            },
            "benchmarks": {
                "model_file_runs": model_benchmarks,
                "service_runs": service_benchmarks,
                "successful_service_runs": successful_service_benchmarks,
                "unique_services": benchmarked_services,
            },
            "stop_verifications": {
                "total": sum(stop_outcomes.values()),
                "outcomes": stop_outcomes,
                "restart_detected": restart_detected,
            },
        }

    def histories(self, fingerprints: Iterable[str]) -> dict[str, dict[str, Any]]:
        values = list(dict.fromkeys(fingerprints))
        if not values:
            return {}
        placeholders = ",".join("?" for _ in values)
        with self._lock:
            rows = self._connection.execute(
                # placeholders contains only a generated comma-separated list of '?'.
                f"SELECT * FROM observations WHERE fingerprint IN ({placeholders})",  # nosec B608
                values,
            ).fetchall()
        return {row["fingerprint"]: dict(row) for row in rows}

    def observe(self, services: Iterable[ServiceRecord], now: float | None = None) -> None:
        observed_at = now or time.time()
        rows: list[tuple[Any, ...]] = []
        grouped: dict[str, list[ServiceRecord]] = {}
        for service in services:
            grouped.setdefault(service.fingerprint, []).append(service)
        for fingerprint, group in grouped.items():
            service = group[0]
            command_hash = service.metadata.get("command_hash")
            rows.append(
                (
                    fingerprint,
                    observed_at,
                    observed_at,
                    service.agent.provider,
                    service.agent.session_id,
                    service.project.path,
                    service.process.pid,
                    command_hash,
                    service.process.create_time if len(group) == 1 else None,
                )
            )
        with self._lock, self._connection:
            self._connection.executemany(
                """
                INSERT INTO observations(
                    fingerprint, first_seen, last_seen, last_agent_provider,
                    last_session_id, last_project_path, last_pid, command_hash,
                    last_create_time
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(fingerprint) DO UPDATE SET
                    last_seen=excluded.last_seen,
                    last_agent_provider=COALESCE(excluded.last_agent_provider, observations.last_agent_provider),
                    last_session_id=COALESCE(excluded.last_session_id, observations.last_session_id),
                    last_project_path=COALESCE(excluded.last_project_path, observations.last_project_path),
                    last_pid=excluded.last_pid,
                    command_hash=excluded.command_hash,
                    restart_count=observations.restart_count + CASE
                        WHEN observations.last_create_time IS NOT NULL
                         AND excluded.last_create_time IS NOT NULL
                         AND ABS(observations.last_create_time - excluded.last_create_time) > 0.5
                        THEN 1 ELSE 0 END,
                    last_create_time=COALESCE(excluded.last_create_time, observations.last_create_time)
                """,
                rows,
            )

    def add_audit(self, action: str, target: str, result: str, details: dict[str, Any] | None = None) -> None:
        safe_details = json.dumps(details or {}, ensure_ascii=False)
        if len(safe_details.encode("utf-8")) > 4000:
            safe_details = json.dumps({"truncated": True}, ensure_ascii=False)
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT INTO audit_log(created_at, action, target, result, details) VALUES(?, ?, ?, ?, ?)",
                (time.time(), action[:80], target[:200], result[:80], safe_details),
            )

    def recent_audit(self, limit: int = 100) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 500))
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            try:
                item["details"] = json.loads(item["details"] or "{}")
            except json.JSONDecodeError:
                item["details"] = {}
            result.append(item)
        return result

    @staticmethod
    def _decode_rule_row(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        item = dict(row)
        for source, target in (("match_json", "match"), ("override_json", "override")):
            if source not in item:
                item.setdefault(target, {})
                continue
            try:
                item[target] = json.loads(item.pop(source))
            except (json.JSONDecodeError, TypeError, KeyError):
                item[target] = {}
        item["enabled"] = bool(item.get("enabled"))
        item["needs_review"] = bool(item.get("needs_review"))
        return item

    @classmethod
    def _rule_snapshot(cls, row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        item = cls._decode_rule_row(row)
        return {
            "name": str(item.get("name") or "本地归属规则")[:160],
            "priority": int(item.get("priority") or 100),
            "enabled": bool(item.get("enabled", True)),
            "source": str(item.get("source") or "user")[:20],
            "scope": str(item.get("scope") or "legacy")[:20],
            "specificity": int(item.get("specificity") or 0),
            "revision": int(item.get("revision") or 1),
            "match": item.get("match") or {},
            "override": item.get("override") or {},
        }

    def _insert_rule_version_locked(self, rule_id: int, action: str, created_at: float) -> None:
        row = self._connection.execute(
            "SELECT * FROM attribution_rules WHERE id = ?", (int(rule_id),)
        ).fetchone()
        if row is None:
            return
        snapshot = self._rule_snapshot(row)
        version = int(snapshot["revision"])
        self._connection.execute(
            """
            INSERT OR REPLACE INTO attribution_rule_versions(
                rule_id, version, created_at, action, snapshot_json, snapshot_sha256
            ) VALUES(?, ?, ?, ?, ?, ?)
            """,
            (
                int(rule_id),
                version,
                created_at,
                str(action or "update")[:40],
                _canonical_json(snapshot),
                _snapshot_sha256(snapshot),
            ),
        )
        self._connection.execute(
            """
            DELETE FROM attribution_rule_versions
            WHERE id IN (
                SELECT id FROM attribution_rule_versions
                WHERE rule_id = ? ORDER BY version DESC LIMIT -1 OFFSET ?
            )
            """,
            (int(rule_id), MAX_ATTRIBUTION_RULE_VERSIONS),
        )

    def attribution_rules(
        self, *, enabled_only: bool = False, search: str | None = None
    ) -> list[dict[str, Any]]:
        query = """
            SELECT r.*,
                   (SELECT COUNT(*) FROM attribution_rule_versions v WHERE v.rule_id = r.id)
                       AS version_count
            FROM attribution_rules r
        """
        if enabled_only:
            query += " WHERE r.enabled = 1"
        query += " ORDER BY r.specificity DESC, r.updated_at DESC, r.priority DESC, r.id DESC"
        with self._lock:
            rows = self._connection.execute(query).fetchall()
        result = [self._decode_rule_row(row) for row in rows]
        needle = str(search or "").strip().casefold()
        if needle:
            result = [
                item
                for item in result
                if needle
                in " ".join(
                    (
                        str(item.get("name") or ""),
                        str((item.get("override") or {}).get("project_name") or ""),
                        str((item.get("override") or {}).get("service_name") or ""),
                        str((item.get("override") or {}).get("agent_provider") or ""),
                        str((item.get("override") or {}).get("note") or ""),
                    )
                ).casefold()
            ]
        return result

    def attribution_rule_versions(self, rule_id: int) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT version, created_at, action, snapshot_json, snapshot_sha256
                FROM attribution_rule_versions
                WHERE rule_id = ? ORDER BY version DESC
                """,
                (int(rule_id),),
            ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            try:
                snapshot = json.loads(row["snapshot_json"] or "{}")
            except (json.JSONDecodeError, TypeError):
                snapshot = {}
            result.append(
                {
                    "version": int(row["version"]),
                    "created_at": float(row["created_at"]),
                    "action": str(row["action"]),
                    "snapshot": snapshot,
                    "snapshot_sha256": str(row["snapshot_sha256"]),
                }
            )
        return result

    def add_attribution_rule(self, rule: dict[str, Any]) -> int:
        return self.add_attribution_rules([rule])[0]

    def add_attribution_rules(self, rules: list[dict[str, Any]]) -> list[int]:
        if not rules:
            raise ValueError("至少需要一条归属规则")
        now = time.time()
        identifiers: list[int] = []
        with self._lock, self._connection:
            for rule in rules:
                cursor = self._connection.execute(
                    """
                    INSERT INTO attribution_rules(
                        created_at, updated_at, name, priority, enabled, source, scope,
                        specificity, revision, match_json, override_json
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                    """,
                    (
                        now,
                        now,
                        str(rule["name"])[:160],
                        int(rule.get("priority") or 100),
                        int(bool(rule.get("enabled", True))),
                        str(rule.get("source") or "user")[:20],
                        str(rule.get("scope") or infer_rule_scope(rule.get("match") or {}))[:20],
                        int(
                            rule.get("specificity")
                            if rule.get("specificity") is not None
                            else rule_specificity(
                                rule.get("match") or {}, str(rule.get("scope") or "") or None
                            )
                        ),
                        json.dumps(rule.get("match") or {}, ensure_ascii=False, sort_keys=True)[:4000],
                        json.dumps(rule.get("override") or {}, ensure_ascii=False, sort_keys=True)[:4000],
                    ),
                )
                rule_id = int(cursor.lastrowid)
                self._insert_rule_version_locked(rule_id, "create", now)
                identifiers.append(rule_id)
        return identifiers

    def update_attribution_rule(
        self, rule_id: int, rule: dict[str, Any], *, action: str = "update"
    ) -> dict[str, Any] | None:
        now = time.time()
        with self._lock, self._connection:
            current = self._connection.execute(
                "SELECT revision FROM attribution_rules WHERE id = ?", (int(rule_id),)
            ).fetchone()
            if current is None:
                return None
            revision = int(current["revision"] or 1) + 1
            match = rule.get("match") or {}
            scope = str(rule.get("scope") or infer_rule_scope(match))[:20]
            specificity = int(
                rule.get("specificity")
                if rule.get("specificity") is not None
                else rule_specificity(match, scope)
            )
            self._connection.execute(
                """
                UPDATE attribution_rules
                SET updated_at = ?, name = ?, priority = ?, enabled = ?, source = ?,
                    scope = ?, specificity = ?, revision = ?, needs_review = 0,
                    match_json = ?, override_json = ?
                WHERE id = ?
                """,
                (
                    now,
                    str(rule["name"])[:160],
                    int(rule.get("priority") or 100),
                    int(bool(rule.get("enabled", True))),
                    str(rule.get("source") or "user")[:20],
                    scope,
                    specificity,
                    revision,
                    _canonical_json(match)[:4000],
                    _canonical_json(rule.get("override") or {})[:4000],
                    int(rule_id),
                ),
            )
            self._insert_rule_version_locked(int(rule_id), action, now)
            row = self._connection.execute(
                "SELECT * FROM attribution_rules WHERE id = ?", (int(rule_id),)
            ).fetchone()
        return self._decode_rule_row(row) if row else None

    def restore_attribution_rule(self, rule_id: int, version: int) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT snapshot_json FROM attribution_rule_versions
                WHERE rule_id = ? AND version = ?
                """,
                (int(rule_id), int(version)),
            ).fetchone()
        if row is None:
            return None
        try:
            snapshot = json.loads(row["snapshot_json"] or "{}")
        except (json.JSONDecodeError, TypeError):
            return None
        return self.update_attribution_rule(
            int(rule_id), snapshot, action=f"restore:{int(version)}"
        )

    def delete_attribution_rule(self, rule_id: int) -> bool:
        with self._lock, self._connection:
            self._connection.execute(
                "DELETE FROM attribution_rule_versions WHERE rule_id = ?", (int(rule_id),)
            )
            self._connection.execute(
                "DELETE FROM attribution_rule_hits WHERE rule_id = ?", (int(rule_id),)
            )
            cursor = self._connection.execute(
                "DELETE FROM attribution_rules WHERE id = ?", (int(rule_id),)
            )
            return bool(cursor.rowcount)

    def remove_attribution_override(
        self, rule_ids: Iterable[int], override_key: str
    ) -> dict[str, list[int]]:
        """Remove one override while preserving the rest of each local rule."""

        if override_key not in {"lifecycle_label"}:
            raise ValueError("不允许移除该归属覆盖字段")
        normalized_identifiers: set[int] = set()
        for item in rule_ids:
            try:
                identifier = int(item)
            except (TypeError, ValueError):
                continue
            if identifier > 0:
                normalized_identifiers.add(identifier)
        identifiers = sorted(normalized_identifiers)
        if not identifiers:
            return {"deleted": [], "recreated": []}
        deleted: list[int] = []
        recreated: list[int] = []
        for rule_id in identifiers:
            with self._lock:
                row = self._connection.execute(
                    "SELECT * FROM attribution_rules WHERE id = ?", (rule_id,)
                ).fetchone()
            if not row:
                continue
            rule = self._decode_rule_row(row)
            override = rule.get("override") or {}
            if override_key not in override:
                continue
            override.pop(override_key, None)
            deleted.append(rule_id)
            if not override:
                self.delete_attribution_rule(rule_id)
                continue
            rule["override"] = override
            updated = self.update_attribution_rule(
                rule_id, rule, action=f"remove:{override_key}"
            )
            if updated:
                recreated.append(rule_id)
        return {"deleted": deleted, "recreated": recreated}

    def record_attribution_evaluations(
        self, evaluations: Iterable[dict[str, Any]], *, observed_at: float | None = None
    ) -> None:
        now = float(observed_at or time.time())
        with self._lock, self._connection:
            for evaluation in evaluations:
                episode_key = str(evaluation.get("episode_key") or "")[:96]
                fingerprint = str(evaluation.get("service_fingerprint") or "")[:96]
                source = str(evaluation.get("source") or "unknown")[:40]
                if not episode_key or not fingerprint:
                    continue
                initial = evaluation.get("initial") or {}
                initial_json = _canonical_json(initial)
                if len(initial_json.encode("utf-8")) > 4000:
                    initial_json = _canonical_json({"truncated": True})
                winner = evaluation.get("winner_rule_id")
                try:
                    winner_id = int(winner) if winner not in (None, "") else None
                except (TypeError, ValueError):
                    winner_id = None
                self._connection.execute(
                    """
                    INSERT INTO attribution_episodes(
                        episode_key, first_seen, last_seen, source, service_fingerprint,
                        initial_json, winner_rule_id
                    ) VALUES(?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(episode_key) DO UPDATE SET
                        last_seen = excluded.last_seen,
                        winner_rule_id = excluded.winner_rule_id
                    """,
                    (episode_key, now, now, source, fingerprint, initial_json, winner_id),
                )
                if winner_id is None:
                    continue
                cursor = self._connection.execute(
                    """
                    INSERT OR IGNORE INTO attribution_rule_hits(
                        rule_id, episode_key, first_hit_at, last_hit_at
                    ) VALUES(?, ?, ?, ?)
                    """,
                    (winner_id, episode_key, now, now),
                )
                if cursor.rowcount:
                    self._connection.execute(
                        """
                        UPDATE attribution_rules
                        SET hit_count = hit_count + 1, last_hit_at = ? WHERE id = ?
                        """,
                        (now, winner_id),
                    )
                else:
                    self._connection.execute(
                        """
                        UPDATE attribution_rule_hits SET last_hit_at = ?
                        WHERE rule_id = ? AND episode_key = ?
                        """,
                        (now, winner_id, episode_key),
                    )
                    self._connection.execute(
                        "UPDATE attribution_rules SET last_hit_at = ? WHERE id = ?",
                        (now, winner_id),
                    )

    def record_attribution_correction(
        self,
        *,
        episode_key: str,
        service_fingerprint: str,
        before: dict[str, Any],
        after: dict[str, Any],
        matched_rule_ids: Iterable[int] = (),
        corrected_at: float | None = None,
    ) -> dict[str, Any]:
        now = float(corrected_at or time.time())
        key = str(episode_key or "")[:96]
        fingerprint = str(service_fingerprint or "")[:96]
        if not key or not fingerprint:
            raise ValueError("归属纠正缺少有效 episode")
        normalized_rule_ids: set[int] = set()
        for item in matched_rule_ids:
            try:
                identifier = int(item)
            except (TypeError, ValueError):
                continue
            if identifier > 0:
                normalized_rule_ids.add(identifier)
        rule_ids = sorted(normalized_rule_ids)
        before_json = _canonical_json(before)
        after_json = _canonical_json(after)
        if len(before_json.encode("utf-8")) > 4000 or len(after_json.encode("utf-8")) > 4000:
            raise ValueError("归属纠正摘要超过本地审计上限")
        newly_overridden: list[int] = []
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT INTO attribution_episodes(
                    episode_key, first_seen, last_seen, source, service_fingerprint,
                    initial_json, corrected, correction_count, last_correction_at
                ) VALUES(?, ?, ?, 'unknown', ?, ?, 1, 1, ?)
                ON CONFLICT(episode_key) DO UPDATE SET
                    last_seen = excluded.last_seen,
                    corrected = 1,
                    correction_count = attribution_episodes.correction_count + 1,
                    last_correction_at = excluded.last_correction_at
                """,
                (key, now, now, fingerprint, before_json, now),
            )
            self._connection.execute(
                """
                INSERT INTO attribution_corrections(
                    corrected_at, episode_key, service_fingerprint,
                    before_json, after_json, rule_ids_json
                ) VALUES(?, ?, ?, ?, ?, ?)
                """,
                (now, key, fingerprint, before_json, after_json, _canonical_json(rule_ids)),
            )
            for rule_id in rule_ids:
                cursor = self._connection.execute(
                    """
                    UPDATE attribution_rule_hits SET overridden_at = ?
                    WHERE rule_id = ? AND episode_key = ? AND overridden_at IS NULL
                    """,
                    (now, rule_id, key),
                )
                if not cursor.rowcount:
                    continue
                newly_overridden.append(rule_id)
                self._connection.execute(
                    """
                    UPDATE attribution_rules
                    SET override_count = override_count + 1,
                        last_override_at = ?,
                        needs_review = CASE WHEN override_count + 1 >= 2 THEN 1 ELSE needs_review END
                    WHERE id = ?
                    """,
                    (now, rule_id),
                )
        return {
            "episode_key": key,
            "rule_ids": rule_ids,
            "newly_overridden_rule_ids": newly_overridden,
        }

    def attribution_metrics(self, days: int = 30) -> dict[str, Any]:
        window_days = max(1, min(int(days), 365))
        cutoff = time.time() - window_days * 86400
        with self._lock:
            episode = self._connection.execute(
                """
                SELECT COUNT(*) AS total,
                       SUM(CASE WHEN corrected = 1 THEN 1 ELSE 0 END) AS corrected
                FROM attribution_episodes WHERE first_seen >= ?
                """,
                (cutoff,),
            ).fetchone()
            correction_events = self._connection.execute(
                "SELECT COUNT(*) FROM attribution_corrections WHERE corrected_at >= ?",
                (cutoff,),
            ).fetchone()[0]
            needs_review = self._connection.execute(
                "SELECT COUNT(*) FROM attribution_rules WHERE needs_review = 1"
            ).fetchone()[0]
        total = int(episode["total"] or 0) if episode else 0
        corrected = int(episode["corrected"] or 0) if episode else 0
        return {
            "window_days": window_days,
            "episodes": total,
            "corrected_episodes": corrected,
            "correction_events": int(correction_events or 0),
            "correction_rate": round(corrected / total, 4) if total else None,
            "rules_needing_review": int(needs_review or 0),
            "denominator": "unique_service_episodes",
        }

    def add_timeline_event(self, event: dict[str, Any], dedup_seconds: float = 60.0) -> int:
        observed_at = float(event.get("observed_at") or time.time())
        dedup_key = str(event.get("dedup_key") or "")[:240]
        if not dedup_key:
            raise ValueError("timeline event requires dedup_key")
        details = json.dumps(event.get("details") or {}, ensure_ascii=False, sort_keys=True)
        if len(details.encode("utf-8")) > 4000:
            details = json.dumps({"truncated": True}, ensure_ascii=False)
        with self._lock, self._connection:
            existing = self._connection.execute(
                """
                SELECT id, last_seen FROM timeline_events
                WHERE dedup_key = ? ORDER BY id DESC LIMIT 1
                """,
                (dedup_key,),
            ).fetchone()
            if existing and observed_at - float(existing["last_seen"]) <= dedup_seconds:
                self._connection.execute(
                    """
                    UPDATE timeline_events SET last_seen = ?, occurrences = occurrences + 1,
                        severity = ?, details_json = ? WHERE id = ?
                    """,
                    (
                        observed_at,
                        str(event.get("severity") or "info")[:20],
                        details,
                        int(existing["id"]),
                    ),
                )
                return int(existing["id"])
            cursor = self._connection.execute(
                """
                INSERT INTO timeline_events(
                    first_seen, last_seen, category, code, severity,
                    service_fingerprint, service_id, project_name, agent_provider,
                    title_zh, title_en, details_json, dedup_key
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    observed_at,
                    observed_at,
                    str(event.get("category") or "system")[:60],
                    str(event.get("code") or "EVENT")[:80],
                    str(event.get("severity") or "info")[:20],
                    str(event.get("service_fingerprint") or "")[:128] or None,
                    str(event.get("service_id") or "")[:200] or None,
                    str(event.get("project_name") or "")[:160] or None,
                    str(event.get("agent_provider") or "")[:80] or None,
                    str(event.get("title_zh") or event.get("code") or "事件")[:300],
                    str(event.get("title_en") or event.get("code") or "Event")[:300],
                    details,
                    dedup_key,
                ),
            )
            return int(cursor.lastrowid)

    def recent_timeline_events(
        self,
        limit: int = 200,
        *,
        category: str | None = None,
        severity: str | None = None,
        since: float | None = None,
    ) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 1000))
        where: list[str] = []
        values: list[Any] = []
        if category:
            where.append("category = ?")
            values.append(str(category)[:60])
        if severity:
            where.append("severity = ?")
            values.append(str(severity)[:20])
        if since is not None:
            where.append("last_seen >= ?")
            values.append(float(since))
        query = "SELECT * FROM timeline_events"
        if where:
            query += " WHERE " + " AND ".join(where)
        query += " ORDER BY last_seen DESC LIMIT ?"
        values.append(limit)
        with self._lock:
            rows = self._connection.execute(query, values).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            try:
                item["details"] = json.loads(item.pop("details_json") or "{}")
            except (json.JSONDecodeError, TypeError):
                item["details"] = {}
            item.pop("dedup_key", None)
            result.append(item)
        return result

    def add_telemetry_sample(self, sample: dict[str, Any]) -> int:
        with self._lock, self._connection:
            cursor = self._connection.execute(
                """
                INSERT INTO telemetry_samples(
                    observed_at, cpu_percent, memory_percent, gpu_memory_percent,
                    gpu_temperature_c, disk_free_gib, public_connections
                ) VALUES(?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    float(sample.get("observed_at") or time.time()),
                    sample.get("cpu_percent"),
                    sample.get("memory_percent"),
                    sample.get("gpu_memory_percent"),
                    sample.get("gpu_temperature_c"),
                    sample.get("disk_free_gib"),
                    int(sample.get("public_connections") or 0),
                ),
            )
            return int(cursor.lastrowid)

    def telemetry_samples(self, since: float, limit: int = 1000) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 5000))
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT * FROM telemetry_samples WHERE observed_at >= ?
                ORDER BY observed_at ASC LIMIT ?
                """,
                (float(since), limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def add_model_inventory_scan(self, result: dict[str, Any]) -> int:
        public = {
            "summary": result.get("summary") or {},
            "assets": list(result.get("assets") or [])[:2000],
            "models": list(result.get("models") or [])[:1000],
            "duplicates": list(result.get("duplicates") or [])[:500],
            "warnings": result.get("warnings") or [],
            "truncated": bool(result.get("truncated")),
            "privacy": result.get("privacy"),
            "limitations": result.get("limitations") or [],
            "hardware_fingerprint": result.get("hardware_fingerprint"),
        }
        encoded = json.dumps(public, ensure_ascii=False)
        if len(encoded.encode("utf-8")) > 2_000_000:
            public["assets"] = public["assets"][:500]
            public["models"] = public["models"][:300]
            public["duplicates"] = public["duplicates"][:100]
            public["history_truncated"] = True
            encoded = json.dumps(public, ensure_ascii=False)
        if len(encoded.encode("utf-8")) > 2_000_000:
            public["assets"] = []
            public["duplicates"] = []
            public["models"] = public["models"][:100]
            public["warnings"] = [str(item)[:300] for item in public["warnings"][:20]]
            public["history_assets_omitted"] = True
            encoded = json.dumps(public, ensure_ascii=False)
        if len(encoded.encode("utf-8")) > 2_000_000:
            public["models"] = []
            public["history_models_omitted"] = True
            encoded = json.dumps(public, ensure_ascii=False)
        with self._lock, self._connection:
            cursor = self._connection.execute(
                """
                INSERT INTO model_inventory_scans(created_at, root_name, root_hash, result_json)
                VALUES(?, ?, ?, ?)
                """,
                (
                    float(result.get("created_at") or time.time()),
                    str(result.get("root_name") or "models")[:160],
                    str(result.get("root_hash") or "")[:80],
                    encoded,
                ),
            )
            return int(cursor.lastrowid)

    def recent_model_inventory_scans(self, limit: int = 10) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 50))
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM model_inventory_scans ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            item = {
                "id": int(row["id"]),
                "created_at": float(row["created_at"]),
                "root_name": row["root_name"],
                "root_hash": row["root_hash"],
            }
            try:
                item.update(json.loads(row["result_json"] or "{}"))
            except (json.JSONDecodeError, TypeError):
                pass
            result.append(item)
        return result

    def add_model_benchmark(self, result: dict[str, Any]) -> int:
        required = {
            "hardware_fingerprint",
            "model_id",
            "model_file_name",
            "model_file_size_bytes",
            "quantization",
            "runtime",
            "generation_tps",
        }
        if required - set(result):
            raise ValueError("基准结果字段不完整")
        safe_result = _bounded_benchmark_details(result.get("details") or {}, 20_000)
        with self._lock, self._connection:
            cursor = self._connection.execute(
                """
                INSERT INTO model_benchmarks(
                    created_at, hardware_fingerprint, model_id, model_file_name,
                    model_file_size_bytes, quantization, runtime, prompt_tps,
                    generation_tps, result_json
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    time.time(),
                    str(result["hardware_fingerprint"])[:80],
                    str(result["model_id"])[:120],
                    str(result["model_file_name"])[:255],
                    int(result["model_file_size_bytes"]),
                    str(result["quantization"])[:40],
                    str(result["runtime"])[:60],
                    float(result["prompt_tps"]) if result.get("prompt_tps") is not None else None,
                    float(result["generation_tps"]),
                    safe_result,
                ),
            )
            return int(cursor.lastrowid)

    def recent_model_benchmarks(
        self, limit: int = 50, hardware_fingerprint: str | None = None
    ) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 200))
        with self._lock:
            if hardware_fingerprint:
                rows = self._connection.execute(
                    """
                    SELECT * FROM model_benchmarks
                    WHERE hardware_fingerprint = ?
                    ORDER BY id DESC LIMIT ?
                    """,
                    (hardware_fingerprint, limit),
                ).fetchall()
            else:
                rows = self._connection.execute(
                    "SELECT * FROM model_benchmarks ORDER BY id DESC LIMIT ?", (limit,)
                ).fetchall()
        items: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            try:
                item["details"] = json.loads(item.pop("result_json") or "{}")
            except json.JSONDecodeError:
                item["details"] = {}
            items.append(item)
        return items

    def add_service_benchmark(self, result: dict[str, Any]) -> int:
        required = {
            "service_fingerprint",
            "runtime",
            "port",
            "concurrency",
            "requested_context_tokens",
            "requested_output_tokens",
            "successful_requests",
            "failed_requests",
        }
        if required - set(result):
            raise ValueError("服务基准结果字段不完整")
        details = _bounded_benchmark_details(result.get("details") or {})
        with self._lock, self._connection:
            cursor = self._connection.execute(
                """
                INSERT INTO service_benchmarks(
                    created_at, service_fingerprint, runtime, port, model_name,
                    concurrency, requested_context_tokens, requested_output_tokens,
                    successful_requests, failed_requests, ttft_seconds,
                    generation_tps, result_json, matrix_id, matrix_step_id,
                    request_count, hardware_fingerprint, catalog_model_id,
                    quantization, prompt_tps, aggregate_generation_tps,
                    ttft_p95_seconds, sample_count
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    time.time(),
                    str(result["service_fingerprint"])[:128],
                    str(result["runtime"])[:80],
                    int(result["port"]),
                    str(result.get("model_name") or "")[:180] or None,
                    int(result["concurrency"]),
                    int(result["requested_context_tokens"]),
                    int(result["requested_output_tokens"]),
                    int(result["successful_requests"]),
                    int(result["failed_requests"]),
                    float(result["ttft_seconds"]) if result.get("ttft_seconds") is not None else None,
                    float(result["generation_tps"]) if result.get("generation_tps") is not None else None,
                    details,
                    str(result.get("matrix_id") or "")[:80] or None,
                    str(result.get("matrix_step_id") or "")[:80] or None,
                    int(result.get("request_count") or result.get("concurrency") or 0) or None,
                    str(result.get("hardware_fingerprint") or "")[:128] or None,
                    str(result.get("catalog_model_id") or "")[:128] or None,
                    str(result.get("quantization") or "")[:40] or None,
                    float(result["prompt_tps"]) if result.get("prompt_tps") is not None else None,
                    float(result["aggregate_generation_tps"]) if result.get("aggregate_generation_tps") is not None else None,
                    float(result["ttft_p95_seconds"]) if result.get("ttft_p95_seconds") is not None else None,
                    int(result.get("sample_count") or result.get("successful_requests") or 0),
                ),
            )
            return int(cursor.lastrowid)

    def recent_service_benchmarks(
        self, limit: int = 50, service_fingerprint: str | None = None
    ) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 200))
        with self._lock:
            if service_fingerprint:
                rows = self._connection.execute(
                    """
                    SELECT * FROM service_benchmarks
                    WHERE service_fingerprint = ? ORDER BY id DESC LIMIT ?
                    """,
                    (service_fingerprint, limit),
                ).fetchall()
            else:
                rows = self._connection.execute(
                    "SELECT * FROM service_benchmarks ORDER BY id DESC LIMIT ?", (limit,)
                ).fetchall()
        items: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            try:
                item["details"] = json.loads(item.pop("result_json") or "{}")
            except json.JSONDecodeError:
                item["details"] = {}
            items.append(item)
        return items

    def recent_service_calibrations(
        self, hardware_fingerprint: str, limit: int = 100
    ) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 300))
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT * FROM service_benchmarks
                WHERE hardware_fingerprint = ?
                  AND catalog_model_id IS NOT NULL
                  AND quantization IS NOT NULL
                  AND generation_tps IS NOT NULL
                  AND successful_requests > 0
                ORDER BY id DESC LIMIT ?
                """,
                (hardware_fingerprint, limit),
            ).fetchall()
        items: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            try:
                item["details"] = json.loads(item.pop("result_json") or "{}")
            except json.JSONDecodeError:
                item["details"] = {}
            items.append(item)
        return items

    def add_stop_verification(self, result: dict[str, Any]) -> int:
        fingerprint = str(result.get("service_fingerprint") or "")
        original_pid = int(result.get("original_pid") or 0)
        outcome = str(result.get("outcome") or "")
        if not fingerprint or original_pid <= 0 or not outcome:
            raise ValueError("停止验证结果字段不完整")
        encoded = json.dumps(result, ensure_ascii=False)
        if len(encoded) > 30_000:
            raise ValueError("停止验证结果过大")
        with self._lock, self._connection:
            cursor = self._connection.execute(
                """
                INSERT INTO stop_verifications(
                    created_at, service_fingerprint, service_id, original_pid,
                    outcome, restart_detected, result_json
                ) VALUES(?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    time.time(),
                    fingerprint[:128],
                    str(result.get("service_id") or "")[:200] or None,
                    original_pid,
                    outcome[:40],
                    int(bool(result.get("restart_detected"))),
                    encoded,
                ),
            )
            return int(cursor.lastrowid)

    def recent_stop_verifications(
        self, limit: int = 50, service_fingerprint: str | None = None
    ) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 200))
        with self._lock:
            if service_fingerprint:
                rows = self._connection.execute(
                    """
                    SELECT id, created_at, result_json FROM stop_verifications
                    WHERE service_fingerprint = ? ORDER BY id DESC LIMIT ?
                    """,
                    (service_fingerprint, limit),
                ).fetchall()
            else:
                rows = self._connection.execute(
                    "SELECT id, created_at, result_json FROM stop_verifications ORDER BY id DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        items: list[dict[str, Any]] = []
        for row in rows:
            try:
                result = json.loads(row["result_json"] or "{}")
            except json.JSONDecodeError:
                result = {}
            result["id"] = int(row["id"])
            result["created_at"] = float(row["created_at"])
            items.append(result)
        return items

    def upsert_stop_observation(self, job: dict[str, Any]) -> str:
        required = {
            "job_id",
            "created_at",
            "updated_at",
            "deadline_at",
            "status",
            "service_fingerprint",
            "original_pid",
            "observation_minutes",
            "poll_seconds",
        }
        if required - set(job):
            raise ValueError("持续观察记录字段不完整")
        status = str(job.get("status") or "")
        if status not in {
            "observing",
            "cancel_requested",
            "cancelled",
            "completed",
            "relaunched",
            "evidence_insufficient",
            "interrupted",
            "failed",
        }:
            raise ValueError("持续观察状态无效")
        encoded = json.dumps(job, ensure_ascii=False, separators=(",", ":"))
        if len(encoded) > 30_000:
            raise ValueError("持续观察记录过大")
        job_id = str(job["job_id"])
        if not job_id or len(job_id) > 100:
            raise ValueError("持续观察 job_id 无效")
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT INTO stop_observations(
                    job_id, created_at, updated_at, deadline_at, status,
                    service_fingerprint, service_id, display_name, project_name,
                    original_pid, observation_minutes, poll_seconds, result_json
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(job_id) DO UPDATE SET
                    updated_at = excluded.updated_at,
                    deadline_at = excluded.deadline_at,
                    status = excluded.status,
                    result_json = excluded.result_json
                """,
                (
                    job_id,
                    float(job["created_at"]),
                    float(job["updated_at"]),
                    float(job["deadline_at"]),
                    status,
                    str(job["service_fingerprint"])[:128],
                    str(job.get("service_id") or "")[:200] or None,
                    str(job.get("display_name") or "")[:160] or None,
                    str(job.get("project_name") or "")[:160] or None,
                    int(job["original_pid"]),
                    int(job["observation_minutes"]),
                    int(job["poll_seconds"]),
                    encoded,
                ),
            )
        return job_id

    def recent_stop_observations(
        self, limit: int = 50, *, status: str | None = None
    ) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 200))
        with self._lock:
            if status:
                rows = self._connection.execute(
                    """
                    SELECT result_json FROM stop_observations
                    WHERE status = ? ORDER BY updated_at DESC LIMIT ?
                    """,
                    (str(status)[:40], limit),
                ).fetchall()
            else:
                rows = self._connection.execute(
                    "SELECT result_json FROM stop_observations ORDER BY updated_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        items: list[dict[str, Any]] = []
        for row in rows:
            try:
                value = json.loads(row["result_json"] or "{}")
            except json.JSONDecodeError:
                value = {}
            if isinstance(value, dict):
                items.append(value)
        return items

    def add_calibration_profile(self, profile: dict[str, Any]) -> str:
        required = {
            "profile_id",
            "hardware_fingerprint",
            "service_fingerprint",
            "runtime",
            "port",
            "model_name",
            "concurrency",
            "duration_seconds",
        }
        if required - set(profile):
            raise ValueError("本机实测档案字段不完整")
        now = float(profile.get("updated_at") or time.time())
        created_at = float(profile.get("created_at") or now)
        status = str(profile.get("status") or "active")
        if status not in {"active", "possibly_invalid", "expired"}:
            raise ValueError("本机实测档案状态无效")
        encoded = json.dumps(profile, ensure_ascii=False, separators=(",", ":"))
        if len(encoded) > 30_000:
            raise ValueError("本机实测档案过大")
        profile_id = str(profile["profile_id"])
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT INTO calibration_profiles(
                    profile_id, created_at, updated_at, hardware_fingerprint,
                    vram_total_gib, service_fingerprint, runtime, port,
                    model_name, catalog_model_id, quantization, concurrency,
                    duration_seconds, status, result_json
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    profile_id[:100],
                    created_at,
                    now,
                    str(profile["hardware_fingerprint"])[:128],
                    float(profile["vram_total_gib"])
                    if profile.get("vram_total_gib") is not None
                    else None,
                    str(profile["service_fingerprint"])[:128],
                    str(profile["runtime"])[:80],
                    int(profile["port"]),
                    str(profile["model_name"])[:180],
                    str(profile.get("catalog_model_id") or "")[:128] or None,
                    str(profile.get("quantization") or "")[:40] or None,
                    int(profile["concurrency"]),
                    int(profile["duration_seconds"]),
                    status,
                    encoded,
                ),
            )
        return profile_id

    def calibration_profiles(
        self,
        limit: int = 100,
        *,
        hardware_fingerprint: str | None = None,
    ) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 300))
        with self._lock:
            if hardware_fingerprint:
                rows = self._connection.execute(
                    """
                    SELECT result_json, status FROM calibration_profiles
                    WHERE hardware_fingerprint = ? ORDER BY updated_at DESC LIMIT ?
                    """,
                    (hardware_fingerprint, limit),
                ).fetchall()
            else:
                rows = self._connection.execute(
                    """
                    SELECT result_json, status FROM calibration_profiles
                    ORDER BY updated_at DESC LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
        items: list[dict[str, Any]] = []
        for row in rows:
            try:
                value = json.loads(row["result_json"] or "{}")
            except json.JSONDecodeError:
                value = {}
            if isinstance(value, dict):
                value["status"] = str(row["status"])
                items.append(value)
        return items

    def set_calibration_profile_status(self, profile_id: str, status: str) -> bool:
        if status not in {"active", "possibly_invalid", "expired"}:
            raise ValueError("本机实测档案状态无效")
        with self._lock, self._connection:
            row = self._connection.execute(
                "SELECT result_json FROM calibration_profiles WHERE profile_id = ?",
                (str(profile_id)[:100],),
            ).fetchone()
            if not row:
                return False
            try:
                value = json.loads(row["result_json"] or "{}")
            except json.JSONDecodeError:
                value = {}
            value["status"] = status
            value["updated_at"] = time.time()
            encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
            cursor = self._connection.execute(
                """
                UPDATE calibration_profiles
                SET status = ?, updated_at = ?, result_json = ? WHERE profile_id = ?
                """,
                (status, float(value["updated_at"]), encoded, str(profile_id)[:100]),
            )
            return bool(cursor.rowcount)

    def delete_calibration_profile(self, profile_id: str) -> bool:
        with self._lock, self._connection:
            cursor = self._connection.execute(
                "DELETE FROM calibration_profiles WHERE profile_id = ?",
                (str(profile_id)[:100],),
            )
            return bool(cursor.rowcount)

    def add_log_watch(self, watch: dict[str, Any]) -> str:
        required = {
            "id",
            "service_id",
            "service_fingerprint",
            "pid",
            "runtime",
            "path",
            "file_name",
            "file_identity",
            "byte_offset",
            "last_size",
        }
        if required - set(watch):
            raise ValueError("日志监控记录字段不完整")
        now = time.time()
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT INTO log_watches(
                    id, created_at, updated_at, service_id, service_fingerprint,
                    pid, process_create_time, runtime, path, file_name,
                    file_identity, byte_offset, last_size, enabled, status, last_error
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 'watching', NULL)
                """,
                (
                    str(watch["id"])[:80],
                    now,
                    now,
                    str(watch["service_id"])[:180],
                    str(watch["service_fingerprint"])[:128],
                    int(watch["pid"]),
                    float(watch["process_create_time"])
                    if watch.get("process_create_time") is not None
                    else None,
                    str(watch["runtime"])[:80],
                    str(watch["path"])[:2000],
                    str(watch["file_name"])[:255],
                    str(watch["file_identity"])[:120],
                    max(0, int(watch["byte_offset"])),
                    max(0, int(watch["last_size"])),
                ),
            )
        return str(watch["id"])

    @staticmethod
    def _public_log_watch(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        item = dict(row)
        public = {
            key: item.get(key)
            for key in (
                "id",
                "created_at",
                "updated_at",
                "service_id",
                "pid",
                "runtime",
                "file_name",
                "last_size",
                "enabled",
                "status",
                "last_error",
            )
        }
        public["enabled"] = bool(public.get("enabled"))
        public["confirmation_phrase"] = f"WATCH {public.get('pid')}"
        return public

    def log_watches(self, *, enabled_only: bool = False, public: bool = True) -> list[dict[str, Any]]:
        query = "SELECT * FROM log_watches"
        arguments: tuple[Any, ...] = ()
        if enabled_only:
            query += " WHERE enabled = 1"
        query += " ORDER BY updated_at DESC"
        with self._lock:
            rows = self._connection.execute(query, arguments).fetchall()
        if public:
            return [self._public_log_watch(row) for row in rows]
        return [dict(row) for row in rows]

    def log_watch(self, watch_id: str, *, public: bool = True) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM log_watches WHERE id = ?", (str(watch_id)[:80],)
            ).fetchone()
        if row is None:
            return None
        return self._public_log_watch(row) if public else dict(row)

    def update_log_watch(
        self,
        watch_id: str,
        *,
        byte_offset: int | None = None,
        last_size: int | None = None,
        file_identity: str | None = None,
        status: str | None = None,
        last_error: str | None = None,
        enabled: bool | None = None,
    ) -> None:
        assignments = ["updated_at = ?"]
        values: list[Any] = [time.time()]
        if byte_offset is not None:
            assignments.append("byte_offset = ?")
            values.append(max(0, int(byte_offset)))
        if last_size is not None:
            assignments.append("last_size = ?")
            values.append(max(0, int(last_size)))
        if file_identity is not None:
            assignments.append("file_identity = ?")
            values.append(str(file_identity)[:120])
        if status is not None:
            assignments.append("status = ?")
            values.append(str(status)[:40])
        if last_error is not None:
            assignments.append("last_error = ?")
            values.append(str(last_error)[:500] or None)
        if enabled is not None:
            assignments.append("enabled = ?")
            values.append(int(enabled))
        values.append(str(watch_id)[:80])
        with self._lock, self._connection:
            self._connection.execute(
                # assignments is assembled solely from the fixed field map above.
                f"UPDATE log_watches SET {', '.join(assignments)} WHERE id = ?",  # nosec B608
                values,
            )

    def add_log_event(self, event: dict[str, Any]) -> int:
        required = {
            "watch_id",
            "service_fingerprint",
            "runtime",
            "observed_at",
            "severity",
            "category",
            "code",
            "message",
            "message_hash",
        }
        if required - set(event):
            raise ValueError("日志事件字段不完整")
        observed_at = float(event["observed_at"])
        values = (
            str(event["watch_id"])[:80],
            str(event["service_fingerprint"])[:128],
            str(event["runtime"])[:80],
            observed_at,
            observed_at,
            str(event["severity"])[:20],
            str(event["category"])[:60],
            str(event["code"])[:80],
            str(event["message"])[:500],
            str(event["message_hash"])[:80],
        )
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT INTO log_events(
                    watch_id, service_fingerprint, runtime, first_seen, last_seen,
                    severity, category, code, message, message_hash
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(watch_id, code, message_hash) DO UPDATE SET
                    last_seen=excluded.last_seen,
                    severity=excluded.severity,
                    message=excluded.message,
                    occurrences=log_events.occurrences + 1
                """,
                values,
            )
            row = self._connection.execute(
                "SELECT id FROM log_events WHERE watch_id = ? AND code = ? AND message_hash = ?",
                (values[0], values[7], values[9]),
            ).fetchone()
        return int(row["id"]) if row else 0

    def recent_log_events(
        self,
        limit: int = 100,
        watch_id: str | None = None,
        *,
        severity: str | None = None,
        code: str | None = None,
        since: float | None = None,
    ) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 500))
        where: list[str] = []
        values: list[Any] = []
        if watch_id:
            where.append("watch_id = ?")
            values.append(str(watch_id)[:80])
        if severity:
            where.append("severity = ?")
            values.append(str(severity)[:20])
        if code:
            where.append("code = ?")
            values.append(str(code)[:80])
        if since is not None:
            where.append("last_seen >= ?")
            values.append(float(since))
        query = "SELECT * FROM log_events"
        if where:
            query += " WHERE " + " AND ".join(where)
        query += " ORDER BY last_seen DESC LIMIT ?"
        values.append(limit)
        with self._lock:
            rows = self._connection.execute(query, values).fetchall()
        return [dict(row) for row in rows]

    def clear_history(self, categories: Iterable[str]) -> dict[str, int]:
        allowed = {
            "observations": "observations",
            "audit": "audit_log",
            "benchmarks": "model_benchmarks",
            "service_benchmarks": "service_benchmarks",
            "stop_verifications": "stop_verifications",
            "stop_observations": "stop_observations",
            "calibration_profiles": "calibration_profiles",
            "log_events": "log_events",
            "timeline": "timeline_events",
            "telemetry": "telemetry_samples",
            "model_inventory": "model_inventory_scans",
            "impact_feedback": "impact_feedback",
        }
        selected = list(dict.fromkeys(str(item) for item in categories if str(item) in allowed))
        if not selected:
            raise ValueError("至少选择一类可清除历史")
        result: dict[str, int] = {}
        with self._lock, self._connection:
            for category in selected:
                # Table names come from the fixed allowlist, never request text.
                cursor = self._connection.execute(f"DELETE FROM {allowed[category]}")  # nosec B608
                result[category] = max(0, int(cursor.rowcount))
        return result

    def cleanup(self, history_days: int, log_retention_days: int | None = None) -> None:
        cutoff = time.time() - history_days * 86400
        log_cutoff = time.time() - (log_retention_days or history_days) * 86400
        with self._lock, self._connection:
            self._connection.execute("DELETE FROM observations WHERE last_seen < ?", (cutoff,))
            self._connection.execute("DELETE FROM audit_log WHERE created_at < ?", (cutoff,))
            self._connection.execute("DELETE FROM model_benchmarks WHERE created_at < ?", (cutoff,))
            self._connection.execute("DELETE FROM service_benchmarks WHERE created_at < ?", (cutoff,))
            self._connection.execute("DELETE FROM stop_verifications WHERE created_at < ?", (cutoff,))
            self._connection.execute("DELETE FROM stop_observations WHERE updated_at < ?", (cutoff,))
            self._connection.execute("DELETE FROM calibration_profiles WHERE updated_at < ?", (cutoff,))
            self._connection.execute("DELETE FROM log_events WHERE last_seen < ?", (log_cutoff,))
            self._connection.execute("DELETE FROM timeline_events WHERE last_seen < ?", (cutoff,))
            self._connection.execute("DELETE FROM telemetry_samples WHERE observed_at < ?", (cutoff,))
            self._connection.execute("DELETE FROM model_inventory_scans WHERE created_at < ?", (cutoff,))
            self._connection.execute("DELETE FROM impact_feedback WHERE updated_at < ?", (cutoff,))
            self._connection.execute(
                """
                DELETE FROM attribution_rule_hits
                WHERE episode_key IN (
                    SELECT episode_key FROM attribution_episodes WHERE last_seen < ?
                )
                """,
                (cutoff,),
            )
            self._connection.execute(
                "DELETE FROM attribution_episodes WHERE last_seen < ?", (cutoff,)
            )
            self._connection.execute(
                "DELETE FROM attribution_corrections WHERE corrected_at < ?", (cutoff,)
            )
