import json
import tempfile
import threading
import unittest
import urllib.error
from pathlib import Path
from types import SimpleNamespace

from vsg import __version__
from vsg.app import VSGServer, _get_json, _post_json
from vsg.impact import build_export_envelope, build_impact_report
from vsg.storage import Storage


def service_fixture() -> dict:
    return {
        "id": "host:987654321:1",
        "fingerprint": "impact-fixture",
        "source": "host",
        "display_name": "private-project",
        "runtime": "Python",
        "process": {
            "pid": 987654321,
            "command": "python serve.py --token fixture-token-sentinel",
            "cwd": r"C:\FixtureUsers\private-user\secret-project",
        },
        "project": {
            "name": "secret-project",
            "path": r"C:\FixtureUsers\private-user\secret-project",
        },
        "agent": {"provider": "Codex CLI", "session_id": "private-session-id"},
        "risk": {"level": "likely_stale", "score": 91, "scored": True},
        "metadata": {"model_runtime": False, "stoppable_candidate": True},
        "endpoints": [
            {
                "protocol": "TCP",
                "address": "10.20.30.40",
                "port": 45678,
                "exposure": "lan",
            }
        ],
    }


class CollectorStub:
    def __init__(self, snapshot: dict):
        self.snapshot = snapshot

    def get_snapshot(self) -> dict:
        return self.snapshot

    def find_service(self, service_id: str) -> dict | None:
        return next(
            (item for item in self.snapshot.get("services", []) if item.get("id") == service_id),
            None,
        )

    def record_impact_feedback(self, service_id: str, feedback: dict) -> None:
        service = self.find_service(service_id)
        if service is not None:
            service["impact_feedback"] = dict(feedback)


class ImpactReportTests(unittest.TestCase):
    def test_feedback_upsert_is_deduplicated_and_aggregate_only(self):
        with tempfile.TemporaryDirectory() as directory:
            storage = Storage(Path(directory))
            try:
                first = storage.set_impact_feedback(
                    "impact-fixture", "confirmed_stale", "likely_stale", 91, "host"
                )
                second = storage.set_impact_feedback(
                    "impact-fixture", "not_stale", "review", 72, "host"
                )
                self.assertEqual(first["outcome"], "confirmed_stale")
                self.assertEqual(second["outcome"], "not_stale")
                stats = storage.impact_statistics()
                self.assertEqual(stats["feedback"]["total"], 1)
                self.assertEqual(stats["feedback"]["outcomes"]["not_stale"], 1)
                feedback = storage.impact_feedbacks(["impact-fixture"])["impact-fixture"]
                self.assertNotIn("service_fingerprint", feedback)
                with self.assertRaises(ValueError):
                    storage.set_impact_feedback(
                        "impact-fixture", "invented", "review", 72, "host"
                    )
                unscored = storage.set_impact_feedback(
                    "agent-fixture", "uncertain", "not_scored", 0, "agent"
                )
                self.assertEqual(unscored["assessed_risk_level"], "not_scored")
                removed = storage.clear_history(["impact_feedback"])
                self.assertEqual(removed["impact_feedback"], 2)
                self.assertEqual(storage.impact_statistics()["feedback"]["total"], 0)
            finally:
                storage.close()

    def test_report_excludes_live_identifiers_and_summarizes_prediction_error(self):
        with tempfile.TemporaryDirectory() as directory:
            storage = Storage(Path(directory))
            try:
                storage.set_impact_feedback(
                    "impact-fixture", "confirmed_stale", "likely_stale", 91, "host"
                )
                storage.add_service_benchmark(
                    {
                        "service_fingerprint": "impact-fixture",
                        "runtime": "Ollama",
                        "port": 45678,
                        "concurrency": 1,
                        "requested_context_tokens": 512,
                        "requested_output_tokens": 16,
                        "successful_requests": 1,
                        "failed_requests": 0,
                        "generation_tps": 10.0,
                        "details": {
                            "matrix": {
                                "prediction_error": {
                                    "per_user_generation_tps": {
                                        "absolute_percent": 12.5
                                    },
                                    "aggregate_generation_tps": None,
                                    "ttft_seconds": {"absolute_percent": 8.0},
                                }
                            }
                        },
                    }
                )
                snapshot = {"services": [service_fixture()]}
                report = build_impact_report(
                    storage,
                    snapshot,
                    {"key": "windows", "architecture": "x86_64", "hostname": "private-host"},
                    __version__,
                    generated_at=1000.0,
                )
                serialized = json.dumps(report, ensure_ascii=False)
                for secret in (
                    "987654321",
                    "fixture-token-sentinel",
                    "private-user",
                    "secret-project",
                    "private-session-id",
                    "10.20.30.40",
                    "private-host",
                ):
                    self.assertNotIn(secret, serialized)
                self.assertEqual(report["current_snapshot"]["services"], 1)
                self.assertEqual(report["current_snapshot"]["non_loopback_endpoints"], 1)
                prediction = report["retained_local_evidence"]["prediction_error"]
                self.assertEqual(prediction["runs_with_prediction"], 1)
                self.assertEqual(
                    prediction["metrics"]["per_user_generation_tps"][
                        "mean_absolute_error_percent"
                    ],
                    12.5,
                )
                self.assertFalse(report["scope"]["external_adoption_verified"])
                self.assertEqual(
                    build_export_envelope(report), build_export_envelope(report)
                )
            finally:
                storage.close()

    def test_loopback_feedback_and_explicit_export_api(self):
        with tempfile.TemporaryDirectory() as directory:
            storage = Storage(Path(directory))
            snapshot = {"services": [service_fixture()]}
            collector = CollectorStub(snapshot)
            state = SimpleNamespace(
                token="impact-control-token",
                instance_id="impact-instance",
                started_at=1.0,
                storage=storage,
                collector=collector,
            )
            state.impact_report = lambda: build_impact_report(
                storage,
                snapshot,
                {"key": "windows", "architecture": "x86_64"},
                __version__,
                generated_at=1000.0,
            )
            server = VSGServer(("127.0.0.1", 0), state)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_address[1]}"
            try:
                preview = _get_json(base + "/api/impact")
                self.assertEqual(preview["report"]["scope"]["kind"], "single_local_instance")
                feedback = _post_json(
                    base + "/api/impact/feedback",
                    state.token,
                    {"service_id": "host:987654321:1", "outcome": "confirmed_stale"},
                )
                self.assertEqual(feedback["feedback"]["outcome"], "confirmed_stale")
                with self.assertRaises(urllib.error.HTTPError):
                    _post_json(
                        base + "/api/impact/export",
                        state.token,
                        {"confirmation": "export report"},
                    )
                exported = _post_json(
                    base + "/api/impact/export",
                    state.token,
                    {"confirmation": "EXPORT REPORT"},
                )
                self.assertTrue(exported["filename"].endswith("Z.json"))
                self.assertEqual(
                    exported["export"]["integrity"]["algorithm"], "sha256"
                )
                self.assertNotIn("fixture-token-sentinel", json.dumps(exported))
            finally:
                server.shutdown()
                thread.join(timeout=5)
                server.server_close()
                storage.close()


if __name__ == "__main__":
    unittest.main()
