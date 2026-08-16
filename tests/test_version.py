import re
import unittest
from pathlib import Path

from vsg import __version__


class VersionTests(unittest.TestCase):
    def test_package_and_project_versions_match(self):
        pyproject = (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(
            encoding="utf-8"
        )
        match = re.search(r'^version = "([^"]+)"$', pyproject, re.MULTILINE)
        self.assertIsNotNone(match)
        assert match is not None
        self.assertEqual(match.group(1), __version__)

    def test_release_version_is_supported_by_all_packaging_scripts(self):
        root = Path(__file__).resolve().parents[1]
        self.assertRegex(__version__, r"^\d+\.\d+\.\d+(?:\.\d+)?$")
        four_part_markers = {
            "scripts/Build-Portable.ps1": r"(?:\.\d+)?",
            "scripts/Validate-Windows.ps1": r"(?:\.\d+)?",
            "scripts/Build-Portable-Linux.sh": r"(\.[0-9]+)?",
            "scripts/Build-Portable-macOS.sh": r"(\.[0-9]+)?",
        }
        for relative_path, marker in four_part_markers.items():
            with self.subTest(script=relative_path):
                content = (root / relative_path).read_text(encoding="utf-8")
                self.assertIn(marker, content)


if __name__ == "__main__":
    unittest.main()
