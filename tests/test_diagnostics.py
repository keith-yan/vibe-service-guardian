import json
import tempfile
import unittest
from pathlib import Path

from vsg.diagnostics import create_snapshot_manifest, inspect_config, inspect_log, restore_config_snapshot


class DiagnosticTests(unittest.TestCase):
    def test_log_and_config_results_are_redacted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            log = root / "server.log"
            log.write_text('CUDA out of memory {"token":"super-secret-value"}\n', encoding="utf-8")
            result = inspect_log(str(log), "INSPECT 42", 42)
            self.assertEqual(result["counts"]["oom"], 1)
            self.assertNotIn("super-secret-value", json.dumps(result))
            config = root / "config.json"
            config.write_text('{"api_key":"secret-value","model":"demo","context":4096}', encoding="utf-8")
            audited = inspect_config(str(config), "INSPECT 42", 42)
            self.assertEqual(audited["sanitized_content"]["api_key"], "[REDACTED]")
            self.assertNotIn("secret-value", json.dumps(audited))

    def test_small_config_snapshot_can_restore_after_change(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = root / "data"
            config = root / "config.json"
            config.write_text('{"value":1}', encoding="utf-8")
            snapshot = create_snapshot_manifest([str(config)], data, "SNAPSHOT")
            config.write_text('{"value":2}', encoding="utf-8")
            result = restore_config_snapshot(data, snapshot["snapshot_id"], 0, "RESTORE config.json")
            self.assertTrue(result["restored"])
            self.assertEqual(config.read_text(encoding="utf-8"), '{"value":1}')


if __name__ == "__main__":
    unittest.main()
