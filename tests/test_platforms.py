import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from vsg.platforms import default_project_roots, platform_info, platform_key


class PlatformTests(unittest.TestCase):
    def test_supported_platform_metadata(self):
        mac = platform_info("Darwin", "arm64")
        self.assertEqual(mac["key"], "macos")
        self.assertEqual(mac["architecture"], "arm64")
        self.assertTrue(mac["supported"])
        self.assertTrue(mac["capabilities"]["macos_lsof"])
        self.assertFalse(mac["capabilities"]["wsl"])
        self.assertEqual(platform_key("Windows"), "windows")
        linux = platform_info("Linux", "x86_64")
        self.assertTrue(linux["supported"])
        self.assertTrue(linux["capabilities"]["linux_procfs"])
        self.assertTrue(linux["capabilities"]["desktop_launcher"])

    def test_macos_default_roots_prefer_developer_and_projects(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            developer = home / "Developer"
            projects = home / "Projects"
            developer.mkdir()
            projects.mkdir()
            roots = default_project_roots("Darwin", home=home, cwd=home)
            self.assertEqual(roots, [str(developer.resolve()), str(projects.resolve())])

    def test_macos_default_root_is_stable_when_folder_is_not_created(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            roots = default_project_roots("Darwin", home=home, cwd=home)
            self.assertEqual(roots, [str((home / "Projects").resolve())])

    def test_windows_defaults_include_common_existing_roots(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            projects = home / "Projects"
            projects.mkdir()
            roots = default_project_roots("Windows", home=home, cwd=home)
            self.assertIn(str(projects.resolve()), roots)
            preferred = Path(r"E:\vibe coding")
            if preferred.is_dir():
                self.assertEqual(roots[0], str(preferred.resolve(strict=False)))

    def test_windows_fallback_does_not_use_application_cwd(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            with patch("pathlib.Path.is_dir", return_value=False):
                roots = default_project_roots("Windows", home=home, cwd=Path.cwd())
            self.assertEqual(roots, [str((home / "Projects").resolve(strict=False))])


if __name__ == "__main__":
    unittest.main()
