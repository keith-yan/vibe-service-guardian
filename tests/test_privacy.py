import os
import tempfile
import unittest
from pathlib import Path

from vsg.privacy import atomic_write_private_text, ensure_private_directory


class PrivacyTests(unittest.TestCase):
    def test_atomic_private_write_replaces_content(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "private" / "runtime.json"
            atomic_write_private_text(path, "first\n")
            atomic_write_private_text(path, "second\n")
            self.assertEqual(path.read_text(encoding="utf-8"), "second\n")

    @unittest.skipIf(os.name == "nt", "POSIX mode bits are not Windows ACLs")
    def test_posix_permissions_are_owner_only(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory) / "data"
            ensure_private_directory(data_dir)
            path = data_dir / "config.json"
            atomic_write_private_text(path, "{}\n")
            self.assertEqual(data_dir.stat().st_mode & 0o777, 0o700)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
