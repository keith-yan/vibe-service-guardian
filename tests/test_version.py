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


if __name__ == "__main__":
    unittest.main()
