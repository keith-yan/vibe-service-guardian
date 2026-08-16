import tempfile
import time
import unittest
from pathlib import Path

from vsg.config import AppConfig
from vsg.models import ProcessSnapshot, ProjectAttribution, ServiceRecord
from vsg.stale import assess_service


def service(source="host", started=None, runtime="Node.js"):
    return ServiceRecord(
        id="test",
        fingerprint="fingerprint",
        source=source,
        display_name="sample",
        runtime=runtime,
        process=ProcessSnapshot(
            pid=123,
            name="node.exe",
            cmdline=["node", "vite"],
            create_time=started or time.time(),
            cpu_percent=0.0,
        ),
    )


class StaleTests(unittest.TestCase):
    def test_missing_project_and_lost_agent_is_likely_stale(self):
        item = service(started=time.time() - 80 * 3600)
        item.project = ProjectAttribution(name="gone", path=str(Path(tempfile.gettempdir()) / "does-not-exist-vsg"))
        result = assess_service(
            item,
            AppConfig(),
            history={"last_agent_provider": "Codex CLI"},
        )
        self.assertEqual(result.level, "likely_stale")
        self.assertGreaterEqual(result.score, 60)
        self.assertTrue(any("目录已不存在" in reason for reason in result.reasons))

    def test_expected_override(self):
        item = service(started=time.time() - 100 * 3600)
        item.expected = True
        result = assess_service(item, AppConfig())
        self.assertEqual(result.level, "expected")
        self.assertEqual(result.score, 0)

    def test_docker_not_scored(self):
        result = assess_service(service(source="docker"), AppConfig())
        self.assertFalse(result.scored)
        self.assertEqual(result.level, "not_scored")

    def test_agent_process_is_not_scored_as_stale_service(self):
        result = assess_service(service(source="agent", started=time.time() - 100 * 3600), AppConfig())
        self.assertFalse(result.scored)
        self.assertEqual(result.level, "not_scored")

    def test_model_server_without_project_is_not_penalized_as_unclassified_dev_runtime(self):
        result = assess_service(service(runtime="Ollama"), AppConfig())
        self.assertFalse(any("未能归入" in reason for reason in result.reasons))

    def test_duplicate_vsg_instance_is_protected_review_evidence(self):
        item = service(started=time.time() - 100 * 3600)
        item.protected = True
        item.metadata["vsg_instance"] = True
        result = assess_service(item, AppConfig(), vsg_instance_count=2)
        self.assertEqual(result.level, "review")
        self.assertTrue(any("2 个 VSG 实例" in reason for reason in result.reasons))


if __name__ == "__main__":
    unittest.main()
