import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

from vsg.sessions import (
    SessionHint,
    load_gemini_session_hints,
    load_hermes_session_hints,
    load_opencode_session_hints,
    match_session_hint,
)


class SessionTests(unittest.TestCase):
    def test_hermes_reads_only_session_metadata_table(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "state.db"
            connection = sqlite3.connect(database)
            connection.execute("CREATE TABLE sessions (id TEXT, cwd TEXT, created_at REAL)")
            connection.execute("CREATE TABLE messages (content TEXT)")
            connection.execute(
                "INSERT INTO sessions VALUES (?, ?, ?)",
                ("hermes-1", str(Path(directory) / "project"), time.time()),
            )
            connection.execute("INSERT INTO messages VALUES ('must-not-be-returned')")
            connection.commit()
            connection.close()
            hints = load_hermes_session_hints(database)
            self.assertEqual(len(hints), 1)
            self.assertEqual(hints[0].session_id, "hermes-1")
            self.assertEqual(hints[0].provider, "Hermes Agent")
            self.assertNotIn("must-not-be-returned", repr(hints))

    def test_opencode_supports_official_directory_and_time_columns(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "opencode.db"
            connection = sqlite3.connect(database)
            connection.execute('CREATE TABLE session (id TEXT, directory TEXT, time_created INTEGER)')
            connection.execute(
                "INSERT INTO session VALUES (?, ?, ?)",
                ("ses_123", str(Path(directory) / "app"), int(time.time() * 1000)),
            )
            connection.commit()
            connection.close()
            hints = load_opencode_session_hints(database)
            self.assertEqual(hints[0].session_id, "ses_123")
            self.assertEqual(hints[0].cwd, str(Path(directory) / "app"))
            self.assertGreater(hints[0].started_at, 1_000_000_000)

    def test_sqlite_session_hints_enforce_age_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "state.db"
            connection = sqlite3.connect(database)
            connection.execute("CREATE TABLE sessions (id TEXT, cwd TEXT, created_at REAL)")
            connection.execute(
                "INSERT INTO sessions VALUES (?, ?, ?)",
                ("old-session", directory, time.time() - 72 * 3600),
            )
            connection.commit()
            connection.close()
            self.assertEqual(load_hermes_session_hints(database, max_age_hours=48), [])

    def test_hermes_schema_without_cwd_cannot_claim_project_session(self):
        process_started = time.time()
        hint = SessionHint(
            provider="Hermes Agent",
            session_id="hermes-no-project",
            cwd=None,
            started_at=process_started,
            source="Hermes 只读 sessions 表",
        )
        matched, score = match_session_hint(
            "Hermes Agent",
            str(Path.cwd()),
            process_started,
            [hint],
        )
        self.assertIs(matched, hint)
        self.assertLess(score, 60)

    def test_gemini_uses_filename_without_parsing_chat_content(self):
        with tempfile.TemporaryDirectory() as directory:
            chats = Path(directory) / "project-hash" / "chats"
            chats.mkdir(parents=True)
            session = chats / "session-abc-123.json"
            session.write_text("this is deliberately not valid JSON and may contain chat text", encoding="utf-8")
            hints = load_gemini_session_hints(Path(directory))
            self.assertEqual(hints[0].session_id, "abc-123")
            self.assertIsNone(hints[0].cwd)


if __name__ == "__main__":
    unittest.main()
