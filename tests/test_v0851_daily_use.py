import tempfile
import unittest
from pathlib import Path

from vsg.attention import build_attention_summary
from vsg.models import ProcessSnapshot
from vsg.scanner import _is_vsg_process
from vsg.service_relationships import recommended_operations
from vsg.single_instance import SingleInstanceGuard


def service(
    service_id: str,
    *,
    source: str = "host",
    project: str | None = None,
    model_runtime: bool = False,
    risk_level: str = "normal",
    exposure: str = "loopback",
) -> dict:
    return {
        "id": service_id,
        "source": source,
        "display_name": service_id,
        "runtime": "Ollama" if model_runtime else "Node.js",
        "process": {"pid": 100, "name": "node", "create_time": 1},
        "project": {"name": project, "path": project, "confidence": 90},
        "agent": {},
        "risk": {
            "level": risk_level,
            "score": 60 if risk_level == "likely_stale" else 0,
            "scored": True,
            "reasons": ["test evidence"],
        },
        "endpoints": [
            {
                "protocol": "TCP",
                "state": "LISTEN",
                "port": 11434,
                "address": "0.0.0.0" if exposure == "all_interfaces" else "127.0.0.1",
                "exposure": exposure,
            }
        ],
        "metadata": {
            "model_runtime": model_runtime,
            "stoppable_candidate": source == "host",
        },
        "stop_assessment": {
            "decision": "allowed" if source == "host" else "blocked",
            "can_request_stop": source == "host",
            "recommended_operations": [] if source == "host" else [{"title": "managed path"}],
        },
    }


class DailyUseConvergenceTests(unittest.TestCase):
    def test_attention_focus_keeps_project_work_and_removes_windows_noise(self):
        project_service = service("project", project="E:/vibe coding/sample")
        windows_noise = service("windows", source="windows_service")
        result = build_attention_summary(
            [project_service, windows_noise],
            {
                "assessments": {
                    "project": project_service["stop_assessment"],
                    "windows": windows_noise["stop_assessment"],
                }
            },
        )
        self.assertIn("project", result["focus_service_ids"])
        self.assertNotIn("windows", result["focus_service_ids"])
        self.assertEqual(result["summary"]["system_noise"], 1)

    def test_attention_surfaces_stale_exposure_and_runtime_headline(self):
        item = service(
            "model",
            project="E:/vibe coding/model",
            model_runtime=True,
            risk_level="likely_stale",
            exposure="all_interfaces",
        )
        item["runtime_probe"] = {"health": "loading", "model_load": "loading"}
        result = build_attention_summary(
            [item],
            {"assessments": {"model": item["stop_assessment"]}},
        )
        kinds = {value["kind"] for value in result["items"]}
        self.assertEqual(
            kinds,
            {"stale_candidate", "network_exposure", "runtime_health"},
        )
        self.assertEqual(result["summary"]["model_runtimes"], 1)

    def test_duplicate_vsg_instances_get_one_high_priority_item(self):
        first = service("vsg-current")
        first["metadata"].update({"vsg_instance": True, "vsg_current_instance": True})
        second = service("vsg-old")
        second["process"]["pid"] = 200
        second["metadata"].update({"vsg_instance": True, "vsg_current_instance": False})
        result = build_attention_summary([first, second])
        duplicates = [item for item in result["items"] if item["kind"] == "duplicate_instance"]
        self.assertEqual(len(duplicates), 1)
        self.assertEqual(set(duplicates[0]["service_ids"]), {"vsg-current", "vsg-old"})

    def test_vsg_process_detection_supports_packaged_and_module_launches(self):
        packaged = ProcessSnapshot(
            pid=1,
            name="VibeServiceGuardian.exe",
            exe="C:/portable/VibeServiceGuardian.exe",
        )
        module = ProcessSnapshot(
            pid=2,
            name="python.exe",
            exe="C:/Python/python.exe",
            cmdline=["python", "-m", "vsg"],
        )
        unrelated = ProcessSnapshot(pid=3, name="python.exe", cmdline=["python", "server.py"])
        self.assertTrue(_is_vsg_process(packaged))
        self.assertTrue(_is_vsg_process(module))
        self.assertFalse(_is_vsg_process(unrelated))

    def test_other_vsg_instance_guidance_is_display_only(self):
        item = service("vsg-old")
        item["metadata"].update(
            {"vsg_instance": True, "vsg_current_instance": False}
        )
        operations = recommended_operations(item)
        self.assertEqual([operation["kind"] for operation in operations], ["vsg_previous_instance"])
        self.assertTrue(operations[0]["requires_manual_review"])
        self.assertFalse(operations[0]["will_execute"])
        self.assertIsNone(operations[0]["argv"])
        self.assertIsNone(operations[0]["copy_text"])

    def test_single_instance_guard_is_exclusive_and_crash_file_is_not_a_blocker(self):
        with tempfile.TemporaryDirectory() as directory:
            lock_path = Path(directory) / "data" / "instance.lock"
            lock_path.parent.mkdir()
            lock_path.write_bytes(b"stale")
            first = SingleInstanceGuard(lock_path)
            second = SingleInstanceGuard(lock_path)
            self.assertTrue(first.acquire())
            self.assertFalse(second.acquire())
            first.release()
            self.assertTrue(second.acquire())
            second.release()


if __name__ == "__main__":
    unittest.main()
