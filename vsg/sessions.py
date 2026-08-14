from __future__ import annotations

import json
import os
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Sequence


@dataclass(slots=True)
class SessionHint:
    provider: str
    session_id: str
    cwd: str | None
    started_at: float
    source: str


def _timestamp(value: object, fallback: float) -> float:
    if isinstance(value, (int, float)):
        numeric = float(value)
        if numeric > 100_000_000_000_000:
            return numeric / 1_000_000
        if numeric > 100_000_000_000:
            return numeric / 1_000
        return numeric
    if isinstance(value, str):
        try:
            return _timestamp(float(value), fallback)
        except ValueError:
            pass
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return fallback
    return fallback


def _recent_files(root: Path, pattern: str, max_age_hours: int, limit: int) -> list[Path]:
    if not root.exists():
        return []
    cutoff = time.time() - max_age_hours * 3600
    candidates: list[tuple[float, Path]] = []
    try:
        for path in root.rglob(pattern):
            try:
                modified = path.stat().st_mtime
            except OSError:
                continue
            if modified >= cutoff:
                candidates.append((modified, path))
    except OSError:
        return []
    candidates.sort(key=lambda item: item[0], reverse=True)
    return [item[1] for item in candidates[:limit]]


def _codex_hint(path: Path) -> SessionHint | None:
    fallback = path.stat().st_mtime
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for _ in range(20):
                line = handle.readline(131072)
                if not line:
                    break
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if record.get("type") != "session_meta":
                    continue
                payload = record.get("payload") or {}
                session_id = payload.get("id")
                if not isinstance(session_id, str):
                    continue
                cwd = payload.get("cwd") if isinstance(payload.get("cwd"), str) else None
                origin = str(payload.get("originator") or "").lower()
                provider = "Codex CLI" if "cli" in origin else "Codex Desktop"
                return SessionHint(
                    provider=provider,
                    session_id=session_id,
                    cwd=cwd,
                    started_at=_timestamp(payload.get("timestamp") or record.get("timestamp"), fallback),
                    source="Codex 本地 session_meta",
                )
    except OSError:
        return None
    return None


def _claude_hint(path: Path) -> SessionHint | None:
    fallback = path.stat().st_mtime
    session_id = path.stem
    cwd: str | None = None
    started = fallback
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for _ in range(12):
                line = handle.readline(131072)
                if not line:
                    break
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                candidate_id = record.get("sessionId") or record.get("session_id")
                if isinstance(candidate_id, str):
                    session_id = candidate_id
                candidate_cwd = record.get("cwd")
                if isinstance(candidate_cwd, str):
                    cwd = candidate_cwd
                started = _timestamp(record.get("timestamp"), started)
                if cwd:
                    break
    except OSError:
        return None
    return SessionHint(
        provider="Claude Code",
        session_id=session_id,
        cwd=cwd,
        started_at=started,
        source="Claude Code 本地 JSONL 元数据",
    )


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _sqlite_session_hints(
    database: Path,
    provider: str,
    source: str,
    table_candidates: Sequence[str],
    id_candidates: Sequence[str],
    cwd_candidates: Sequence[str],
    time_candidates: Sequence[str],
    limit: int,
    max_age_hours: int | None,
) -> list[SessionHint]:
    if not database.is_file():
        return []
    try:
        fallback = database.stat().st_mtime
        uri = database.resolve().as_uri() + "?mode=ro"
        connection = sqlite3.connect(uri, uri=True, timeout=0.25)
    except (OSError, sqlite3.Error, ValueError):
        return []
    try:
        connection.execute("PRAGMA query_only=ON")
        tables = {
            str(row[0])
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        table = next((candidate for candidate in table_candidates if candidate in tables), None)
        if not table:
            return []
        columns = {
            str(row[1])
            for row in connection.execute(f"PRAGMA table_info({_quote_identifier(table)})")
        }
        id_column = next((candidate for candidate in id_candidates if candidate in columns), None)
        if not id_column:
            return []
        cwd_column = next((candidate for candidate in cwd_candidates if candidate in columns), None)
        time_column = next((candidate for candidate in time_candidates if candidate in columns), None)
        select = [f"{_quote_identifier(id_column)} AS session_id"]
        select.append(
            f"{_quote_identifier(cwd_column)} AS cwd" if cwd_column else "NULL AS cwd"
        )
        select.append(
            f"{_quote_identifier(time_column)} AS started_at" if time_column else "NULL AS started_at"
        )
        order = f" ORDER BY {_quote_identifier(time_column)} DESC" if time_column else ""
        fetch_limit = max(1, min(int(limit) * 4, 1000))
        # Identifiers come only from the opened database schema and are quoted
        # by _quote_identifier; the row limit remains a bound parameter.
        query = f"SELECT {', '.join(select)} FROM {_quote_identifier(table)}{order} LIMIT ?"  # nosec B608
        hints: list[SessionHint] = []
        now = time.time()
        cutoff = now - max_age_hours * 3600 if max_age_hours is not None else None
        for session_id, cwd, started_at in connection.execute(query, (fetch_limit,)):
            if not isinstance(session_id, (str, int)):
                continue
            normalized_started = _timestamp(started_at, fallback)
            if cutoff is not None and normalized_started < cutoff:
                continue
            if normalized_started > now + 24 * 3600:
                continue
            normalized_cwd = cwd if isinstance(cwd, str) and cwd.strip() else None
            hints.append(
                SessionHint(
                    provider=provider,
                    session_id=str(session_id),
                    cwd=normalized_cwd,
                    started_at=normalized_started,
                    source=source,
                )
            )
            if len(hints) >= limit:
                break
        return hints
    except sqlite3.Error:
        return []
    finally:
        connection.close()


def load_hermes_session_hints(
    database: Path,
    limit: int = 80,
    max_age_hours: int = 48,
) -> list[SessionHint]:
    return _sqlite_session_hints(
        database,
        provider="Hermes Agent",
        source="Hermes 只读 sessions 表",
        table_candidates=("sessions", "session"),
        id_candidates=("id", "session_id"),
        cwd_candidates=("cwd", "directory", "project_path", "working_directory"),
        time_candidates=("started_at", "created_at", "time_created", "updated_at"),
        limit=limit,
        max_age_hours=max_age_hours,
    )


def load_opencode_session_hints(
    database: Path,
    limit: int = 80,
    max_age_hours: int = 48,
) -> list[SessionHint]:
    return _sqlite_session_hints(
        database,
        provider="OpenCode",
        source="OpenCode 只读 session 表",
        table_candidates=("session", "sessions"),
        id_candidates=("id", "session_id"),
        cwd_candidates=("directory", "cwd", "project_path", "working_directory"),
        time_candidates=("time_created", "created_at", "started_at", "updated_at"),
        limit=limit,
        max_age_hours=max_age_hours,
    )


def load_gemini_session_hints(root: Path, max_age_hours: int = 48, limit: int = 80) -> list[SessionHint]:
    """Use only filenames and mtimes; chat JSON content is deliberately not opened."""
    paths = _recent_files(root, "session-*.json", max_age_hours, limit)
    paths.extend(_recent_files(root, "session-*.jsonl", max_age_hours, limit))
    unique = sorted(set(paths), key=lambda path: path.stat().st_mtime, reverse=True)[:limit]
    hints: list[SessionHint] = []
    for path in unique:
        session_id = path.stem.removeprefix("session-")
        if not session_id:
            continue
        try:
            modified = path.stat().st_mtime
        except OSError:
            continue
        hints.append(
            SessionHint(
                provider="Gemini CLI",
                session_id=session_id,
                cwd=None,
                started_at=modified,
                source="Gemini CLI 会话文件名（未读取聊天正文）",
            )
        )
    return hints


def _opencode_databases(home: Path) -> list[Path]:
    candidates: list[Path] = []
    xdg_data = Path(os.environ.get("XDG_DATA_HOME", str(home / ".local" / "share"))).expanduser()
    roots = [xdg_data / "opencode"]
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        roots.append(Path(local_app_data) / "opencode")
    seen: set[Path] = set()
    for root in roots:
        if not root.exists():
            continue
        try:
            for path in root.rglob("opencode*.db"):
                resolved = path.resolve(strict=False)
                if resolved not in seen:
                    seen.add(resolved)
                    candidates.append(path)
        except OSError:
            continue
    candidates.sort(key=lambda path: path.stat().st_mtime if path.exists() else 0, reverse=True)
    return candidates[:4]


def load_recent_session_hints(max_age_hours: int = 48, limit_per_provider: int = 80) -> list[SessionHint]:
    home = Path.home()
    codex_home = Path(os.environ.get("CODEX_HOME", str(home / ".codex"))).expanduser()
    hints: list[SessionHint] = []
    for path in _recent_files(codex_home / "sessions", "rollout-*.jsonl", max_age_hours, limit_per_provider):
        hint = _codex_hint(path)
        if hint:
            hints.append(hint)
    for path in _recent_files(home / ".claude" / "projects", "*.jsonl", max_age_hours, limit_per_provider):
        hint = _claude_hint(path)
        if hint:
            hints.append(hint)

    hints.extend(
        load_hermes_session_hints(
            home / ".hermes" / "state.db",
            limit_per_provider,
            max_age_hours=max_age_hours,
        )
    )
    for database in _opencode_databases(home):
        hints.extend(
            load_opencode_session_hints(
                database,
                limit_per_provider,
                max_age_hours=max_age_hours,
            )
        )
    hints.extend(
        load_gemini_session_hints(home / ".gemini" / "tmp", max_age_hours, limit_per_provider)
    )
    return hints


def _normalized(path: str | None) -> str | None:
    if not path:
        return None
    try:
        return os.path.normcase(os.path.abspath(os.path.expanduser(path)))
    except (OSError, ValueError):
        return None


def match_session_hint(
    provider: str,
    project_path: str | None,
    process_started_at: float | None,
    hints: Iterable[SessionHint],
) -> tuple[SessionHint | None, int]:
    provider_family = "Codex" if provider.startswith("Codex") else provider
    project = _normalized(project_path)
    best: SessionHint | None = None
    best_score = 0
    for hint in hints:
        hint_family = "Codex" if hint.provider.startswith("Codex") else hint.provider
        if hint_family != provider_family:
            continue
        score = 20
        hint_cwd = _normalized(hint.cwd)
        if project and hint_cwd:
            if project == hint_cwd:
                score += 55
            elif hint_cwd.startswith(project + os.sep) or project.startswith(hint_cwd + os.sep):
                score += 40
            else:
                continue
        elif project or hint_cwd:
            # A one-sided path cannot establish project identity.
            score -= 5
        if process_started_at:
            delta = process_started_at - hint.started_at
            if delta < -3600 or delta > 48 * 3600:
                continue
            if -900 <= delta <= 4 * 3600:
                score += 20
            elif -3600 <= delta <= 24 * 3600:
                score += 8
        if score > best_score:
            best = hint
            best_score = score
    return best, min(best_score, 95)
