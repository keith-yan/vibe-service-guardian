import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from vsg.storage import (
    CURRENT_SCHEMA_VERSION,
    SCHEMA,
    Storage,
    StorageVersionError,
)


def create_legacy_database(data_dir: Path) -> Path:
    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / "history.sqlite3"
    connection = sqlite3.connect(path)
    try:
        connection.executescript(SCHEMA)
        connection.execute(
            """
            INSERT INTO observations(
                fingerprint, first_seen, last_seen, last_pid, command_hash
            ) VALUES(?, ?, ?, ?, ?)
            """,
            ("legacy-fixture", 10.0, 20.0, 4242, "fixture-hash"),
        )
        connection.execute("PRAGMA user_version = 0")
        connection.commit()
    finally:
        connection.close()
    return path


class StorageMigrationTests(unittest.TestCase):
    def test_legacy_database_is_backed_up_and_data_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            create_legacy_database(data_dir)

            storage = Storage(data_dir)
            try:
                status = storage.status()
                self.assertEqual(status["schema_version"], CURRENT_SCHEMA_VERSION)
                self.assertEqual(status["integrity"], "ok")
                self.assertEqual(status["migration"]["from_version"], 0)
                backup_name = status["migration"]["backup_file"]
                self.assertTrue(backup_name)
                self.assertFalse(Path(backup_name).is_absolute())
                self.assertTrue((data_dir / backup_name).is_file())
                self.assertEqual(
                    storage.histories(["legacy-fixture"])["legacy-fixture"]["last_pid"],
                    4242,
                )
            finally:
                storage.close()

            connection = sqlite3.connect(data_dir / "history.sqlite3")
            try:
                version = connection.execute("PRAGMA user_version").fetchone()[0]
            finally:
                connection.close()
            self.assertEqual(version, CURRENT_SCHEMA_VERSION)

    def test_newer_database_is_refused_without_mutation_or_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            path = data_dir / "history.sqlite3"
            connection = sqlite3.connect(path)
            try:
                connection.execute("CREATE TABLE sentinel(value TEXT NOT NULL)")
                connection.execute("INSERT INTO sentinel(value) VALUES('keep')")
                connection.execute("PRAGMA user_version = 99")
                connection.commit()
            finally:
                connection.close()

            with self.assertRaises(StorageVersionError):
                Storage(data_dir)

            self.assertEqual(list(data_dir.glob("history.pre-migration-*.sqlite3")), [])
            connection = sqlite3.connect(path)
            try:
                self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 99)
                self.assertEqual(
                    connection.execute("SELECT value FROM sentinel").fetchone()[0],
                    "keep",
                )
            finally:
                connection.close()

    def test_corrupt_database_is_quarantined_and_recreated(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            path = data_dir / "history.sqlite3"
            path.write_bytes(b"not-a-sqlite-database\x00fixture")

            storage = Storage(data_dir)
            try:
                status = storage.status()
                self.assertEqual(status["integrity"], "ok")
                self.assertEqual(
                    status["recovery"]["action"], "quarantined_and_recreated"
                )
                quarantined = status["recovery"]["quarantined_files"]
                self.assertEqual(len(quarantined), 1)
                self.assertFalse(Path(quarantined[0]).is_absolute())
                self.assertEqual(
                    (data_dir / quarantined[0]).read_bytes(),
                    b"not-a-sqlite-database\x00fixture",
                )
            finally:
                storage.close()

            connection = sqlite3.connect(path)
            try:
                self.assertEqual(
                    connection.execute("PRAGMA quick_check").fetchone()[0], "ok"
                )
                self.assertEqual(
                    connection.execute("PRAGMA user_version").fetchone()[0],
                    CURRENT_SCHEMA_VERSION,
                )
            finally:
                connection.close()

    def test_failed_migration_rolls_back_and_retains_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            path = create_legacy_database(data_dir)
            original = Storage._apply_migration

            def fail_after_second_migration(instance: Storage, target_version: int):
                original(instance, target_version)
                if target_version == 2:
                    raise RuntimeError("fixture migration failure")

            with patch.object(Storage, "_apply_migration", fail_after_second_migration):
                with self.assertRaisesRegex(RuntimeError, "fixture migration failure"):
                    Storage(data_dir)

            backups = list(data_dir.glob("history.pre-migration-v0-to-v*.sqlite3"))
            self.assertEqual(len(backups), 1)
            connection = sqlite3.connect(path)
            try:
                self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 0)
                self.assertEqual(
                    connection.execute(
                        "SELECT last_pid FROM observations WHERE fingerprint = ?",
                        ("legacy-fixture",),
                    ).fetchone()[0],
                    4242,
                )
            finally:
                connection.close()

    def test_new_database_has_current_version_without_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            storage = Storage(data_dir)
            try:
                status = storage.status()
                self.assertEqual(status["schema_version"], CURRENT_SCHEMA_VERSION)
                self.assertEqual(status["migration"]["backup_file"], None)
                self.assertEqual(status["recovery"], None)
            finally:
                storage.close()
            self.assertEqual(list(data_dir.glob("history.pre-migration-*.sqlite3")), [])

    def test_version_three_database_migrates_through_current_schema_with_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            data_dir.mkdir(parents=True, exist_ok=True)
            path = data_dir / "history.sqlite3"
            connection = sqlite3.connect(path)
            try:
                connection.executescript(SCHEMA)
                connection.execute("DROP TABLE impact_feedback")
                connection.execute(
                    """
                    INSERT INTO observations(
                        fingerprint, first_seen, last_seen, last_pid, command_hash
                    ) VALUES(?, ?, ?, ?, ?)
                    """,
                    ("v3-fixture", 10.0, 20.0, 99, "hash"),
                )
                connection.execute("PRAGMA user_version = 3")
                connection.commit()
            finally:
                connection.close()

            storage = Storage(data_dir)
            try:
                self.assertEqual(storage.status()["migration"]["from_version"], 3)
                self.assertEqual(
                    storage.histories(["v3-fixture"])["v3-fixture"]["last_pid"], 99
                )
                stored = storage.set_impact_feedback(
                    "v3-fixture", "uncertain", "review", 70, "host"
                )
                self.assertEqual(stored["outcome"], "uncertain")
                backups = list(
                    data_dir.glob(
                        f"history.pre-migration-v3-to-v{CURRENT_SCHEMA_VERSION}-*.sqlite3"
                    )
                )
                self.assertEqual(len(backups), 1)
            finally:
                storage.close()


if __name__ == "__main__":
    unittest.main()
