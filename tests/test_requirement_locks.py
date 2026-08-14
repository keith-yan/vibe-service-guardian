import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "Requirement-Locks.py"


def verify(output_dir: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--root",
            str(ROOT),
            "--output-dir",
            str(output_dir),
            "--verify",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=20,
        check=False,
    )


class RequirementLockTests(unittest.TestCase):
    def test_checked_in_lock_set_is_complete_and_fresh(self):
        result = verify(ROOT / "requirements-lock")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertIn("verified 20", result.stdout)

    def test_lock_tampering_is_detected_offline(self):
        with tempfile.TemporaryDirectory() as directory:
            copied = Path(directory) / "requirements-lock"
            shutil.copytree(ROOT / "requirements-lock", copied)
            target = copied / "runtime-windows-py312.txt"
            target.write_text(
                target.read_text(encoding="utf-8") + "# unexpected edit\n",
                encoding="utf-8",
            )
            result = verify(copied)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("lock digest mismatch", result.stderr)


if __name__ == "__main__":
    unittest.main()
