import tempfile
import unittest
from pathlib import Path

from vsg.config import AppConfig, load_config, save_config, validate_config


class ConfigTests(unittest.TestCase):
    def test_save_and_load(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "projects"
            root.mkdir()
            config = validate_config({"project_roots": [str(root)], "refresh_seconds": 5})
            save_config(config, Path(directory))
            loaded = load_config(Path(directory))
            self.assertEqual(loaded.refresh_seconds, 5)
            self.assertEqual(loaded.project_roots, [str(root.resolve())])

    def test_invalid_threshold_order(self):
        with self.assertRaises(ValueError):
            validate_config({"review_score": 70, "likely_stale_score": 60}, AppConfig())

    def test_relative_root_is_rejected(self):
        with self.assertRaises(ValueError):
            validate_config({"project_roots": ["relative"]}, AppConfig())

    def test_default_agent_protection_survives_old_config(self):
        config = validate_config({"protected_names": ["custom.exe"]}, AppConfig())
        self.assertIn("hermes", config.protected_names)
        self.assertIn("opencode", config.protected_names)
        self.assertIn("goose", config.protected_names)
        self.assertIn("custom.exe", config.protected_names)


if __name__ == "__main__":
    unittest.main()
