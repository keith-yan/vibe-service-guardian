import importlib.util
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "Audit-Public-Tree.py"
SPEC = importlib.util.spec_from_file_location("vsg_public_audit", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class PublicAuditTests(unittest.TestCase):
    def test_clean_tree_passes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "README.md").write_text("safe\n", encoding="utf-8")
            self.assertEqual(MODULE.audit(root), [])

    def test_local_data_and_literal_token_are_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "history.sqlite3").write_bytes(b"sqlite")
            (root / "settings.txt").write_text(
                "access_token=" + "abcdefghijkl" + "mnopqrstuvwxyz\n",
                encoding="utf-8",
            )
            kinds = {item["kind"] for item in MODULE.audit(root)}
            self.assertIn("forbidden-file", kinds)
            self.assertIn("secret-assignment", kinds)

    def test_generated_and_runtime_directories_are_excluded(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = root / "data"
            data.mkdir()
            (data / "history.sqlite3").write_bytes(b"sqlite")
            release = root / "release"
            release.mkdir()
            (release / "runtime.json").write_text("{}", encoding="utf-8")
            self.assertEqual(MODULE.audit(root), [])


if __name__ == "__main__":
    unittest.main()
