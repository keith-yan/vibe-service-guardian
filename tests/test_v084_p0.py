import copy
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.test_capacity import hardware_fixture
from tests.test_v081_features import service_fixture, snapshot_fixture
from vsg.actions import verify_post_stop
from vsg.config import AppConfig, validate_config
from vsg.model_planner import ModelPlanner
from vsg.models import Endpoint, ProcessSnapshot, ProjectAttribution, ServiceRecord
from vsg.project_rules import apply_rules
from vsg.scanner import ownership_signature, redacted_command_hash
from vsg.service_relationships import build_service_relationships, recommended_operations
from vsg.stale import assess_service
from vsg.stop_observation import StopObservationError, StopObservationManager
from vsg.storage import Storage
from vsg.workload_matrix import WorkloadMatrixError, WorkloadMatrixManager


def record_fixture(root: Path, *, protected: bool = False) -> ServiceRecord:
    process = ProcessSnapshot(
        pid=321,
        ppid=44,
        name="python.exe",
        exe=str(root / "python.exe"),
        cmdline=["python", "-m", "demo"],
        cwd=str(root),
        create_time=time.time(),
    )
    record = ServiceRecord(
        id="host:321",
        fingerprint="fp-321",
        source="host",
        display_name="demo",
        runtime="Python",
        process=process,
        endpoints=[Endpoint("TCP", "127.0.0.1", 8123)],
        project=ProjectAttribution(name="demo", path=str(root), confidence=90),
        protected=protected,
        metadata={"stoppable_candidate": True},
    )
    record.metadata["ownership_signature"] = ownership_signature(process)
    return record


def wait_for_job(manager, job_id: str, timeout: float = 3.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = manager.status(job_id).get("job")
        if job and job.get("status") not in {
            "queued",
            "running",
            "cancelling",
            "observing",
            "cancel_requested",
        }:
            return job
        time.sleep(0.01)
    raise AssertionError("background job did not reach a terminal state")


class HistoricalLifecycleLabelTests(unittest.TestCase):
    def test_signature_is_one_way_stable_and_exact(self):
        first = ProcessSnapshot(pid=1, exe=r"C:\Tools\python.exe", cwd=r"E:\vibe coding\demo")
        same = ProcessSnapshot(pid=2, exe=r"c:\tools\PYTHON.EXE", cwd=r"e:\VIBE CODING\DEMO")
        other = ProcessSnapshot(pid=3, exe=r"C:\Tools\python.exe", cwd=r"E:\vibe coding\other")
        self.assertEqual(ownership_signature(first), ownership_signature(same))
        self.assertNotEqual(ownership_signature(first), ownership_signature(other))
        self.assertNotIn("python", ownership_signature(first))
        self.assertIsNone(ownership_signature(ProcessSnapshot(pid=4, exe=None, cwd="E:\\demo")))

    def test_missing_command_line_never_becomes_shared_restart_identity(self):
        self.assertEqual(redacted_command_hash(None), "")
        self.assertEqual(redacted_command_hash([]), "")

    def test_safe_cleanup_inherits_but_never_downgrades_protection(self):
        with tempfile.TemporaryDirectory() as directory:
            record = record_fixture(Path(directory), protected=True)
            signature = ownership_signature(record.process)
            matched = apply_rules(
                record,
                [
                    {
                        "id": "history-1",
                        "name": "user lifecycle history",
                        "priority": 900,
                        "enabled": True,
                        "match": {"ownership_signature": signature},
                        "override": {"lifecycle_label": "safe_cleanup"},
                    }
                ],
            )
            self.assertEqual(matched, ["history-1"])
            self.assertTrue(record.protected)
            self.assertFalse(record.expected)
            self.assertTrue(record.metadata["historical_label_inherited"])
            risk = assess_service(record, AppConfig(project_roots=[directory]))
            self.assertEqual(risk.level, "review")
            self.assertTrue(any("仍需重新确认" in reason for reason in risk.reasons))

    def test_expected_history_sets_expected_without_unprotecting(self):
        with tempfile.TemporaryDirectory() as directory:
            record = record_fixture(Path(directory), protected=True)
            apply_rules(
                record,
                [
                    {
                        "id": "history-expected",
                        "priority": 900,
                        "enabled": True,
                        "match": {"ownership_signature": ownership_signature(record.process)},
                        "override": {"lifecycle_label": "expected"},
                    }
                ],
            )
            self.assertTrue(record.expected)
            self.assertTrue(record.protected)

    def test_only_lifecycle_label_propagates_to_similar_process(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            current = record_fixture(root)
            current_rule = {
                "id": "current",
                "priority": 1000,
                "enabled": True,
                "match": {"fingerprint": current.fingerprint},
                "override": {
                    "project_name": "Corrected project",
                    "lifecycle_label": "safe_cleanup",
                },
            }
            inherited_rule = {
                "id": "history",
                "priority": 900,
                "enabled": True,
                "match": {"ownership_signature": ownership_signature(current.process)},
                "override": {"lifecycle_label": "safe_cleanup"},
            }
            apply_rules(current, [current_rule, inherited_rule])
            self.assertEqual(current.project.name, "Corrected project")
            self.assertEqual(current.metadata["historical_lifecycle_label"], "safe_cleanup")

            future = record_fixture(root)
            future.id = "host:654"
            future.fingerprint = "fp-654"
            future.project.name = "Original future project"
            apply_rules(future, [current_rule, inherited_rule])
            self.assertEqual(future.project.name, "Original future project")
            self.assertEqual(future.metadata["historical_lifecycle_label"], "safe_cleanup")
            self.assertTrue(future.metadata["historical_label_inherited"])

    def test_clearing_label_preserves_other_fields_in_current_rule(self):
        with tempfile.TemporaryDirectory() as directory:
            storage = Storage(Path(directory))
            try:
                identifiers = storage.add_attribution_rules(
                    [
                        {
                            "name": "current",
                            "priority": 1000,
                            "enabled": True,
                            "match": {"fingerprint": "fp"},
                            "override": {
                                "project_name": "Keep me",
                                "lifecycle_label": "expected",
                            },
                        },
                        {
                            "name": "history",
                            "priority": 900,
                            "enabled": True,
                            "match": {"ownership_signature": "a" * 64},
                            "override": {"lifecycle_label": "expected"},
                        },
                    ]
                )
                result = storage.remove_attribution_override(
                    identifiers, "lifecycle_label"
                )
                self.assertEqual(result["deleted"], identifiers)
                rules = storage.attribution_rules()
                self.assertEqual(len(rules), 1)
                self.assertEqual(rules[0]["override"], {"project_name": "Keep me"})
            finally:
                storage.close()


class RelaunchEvidenceTests(unittest.TestCase):
    def test_relaunch_requires_original_pid_to_disappear_first(self):
        service = service_fixture(pid=200, port=18080)
        service["process"]["ppid"] = 50
        service["metadata"]["command_hash"] = redacted_command_hash(
            ["python", "serve.py", "--token", "secret-value"]
        )
        while_alive = verify_post_stop(
            service,
            [200],
            observation_seconds=0,
            process_probe=lambda pid: 100.0 if pid == 200 else None,
            listener_provider=lambda: [
                {"protocol": "TCP", "address": "127.0.0.1", "port": 18080, "pid": 201}
            ],
            command_match_provider=lambda _hash: [
                {"pid": 202, "ppid": 99, "create_time": 101.0}
            ],
        )
        self.assertEqual(while_alive["outcome"], "stop_incomplete")
        self.assertFalse(while_alive["restart_detected"])

        after_exit = verify_post_stop(
            service,
            [200],
            observation_seconds=0,
            process_probe=lambda _pid: None,
            listener_provider=lambda: [],
            command_match_provider=lambda _hash: [
                {"pid": 202, "ppid": 99, "create_time": 101.0}
            ],
        )
        self.assertEqual(after_exit["outcome"], "relaunched")
        self.assertEqual(after_exit["replacement_pids"], [202])
        self.assertTrue(after_exit["parent_process_changed"])
        self.assertEqual(after_exit["restart_evidence"][0]["type"], "command_hash_reappeared")
        self.assertFalse(after_exit["second_stop_attempted"])

    def test_final_listener_snapshot_can_prove_port_rebound(self):
        service = service_fixture(pid=200, port=18080)
        listeners = iter(
            [
                [],
                [
                    {
                        "protocol": "TCP",
                        "address": "127.0.0.1",
                        "port": 18080,
                        "pid": 201,
                    }
                ],
            ]
        )
        result = verify_post_stop(
            service,
            [200],
            observation_seconds=0,
            process_probe=lambda _pid: None,
            listener_provider=lambda: next(listeners),
            command_match_provider=lambda _hash: [],
        )
        self.assertEqual(result["outcome"], "relaunched")
        self.assertTrue(result["restart_detected"])
        self.assertEqual(result["replacement_pids"], [201])


class StopObservationManagerTests(unittest.TestCase):
    def test_relaunch_is_persisted_and_notified_without_second_stop(self):
        notifications = []

        def verifier(_service, _pids, **kwargs):
            kwargs["progress_callback"](
                {
                    "checks": 2,
                    "remaining_seconds": 250,
                    "original_pid_disappeared": True,
                    "restart_detected": True,
                }
            )
            return {
                "service_id": "service:demo",
                "service_fingerprint": "fp-200",
                "original_pid": 200,
                "outcome": "relaunched",
                "restart_detected": True,
                "replacement_pids": [201],
                "restart_evidence": [
                    {"type": "port_rebound", "replacement_pid": 201, "parent_changed": True}
                ],
                "parent_process_changed": True,
                "endpoint_verification": [],
                "second_stop_attempted": False,
            }

        with tempfile.TemporaryDirectory() as directory:
            storage = Storage(Path(directory))
            manager = StopObservationManager(
                storage,
                notifications_enabled=lambda: True,
                verifier=verifier,
                notifier=lambda title, message: notifications.append((title, message))
                or {"sent": True, "platform": "test"},
            )
            try:
                service = service_fixture(pid=200)
                job_id = manager.start(service, [200], 5)["job_id"]
                job = wait_for_job(manager, job_id)
                self.assertEqual(job["status"], "relaunched")
                self.assertEqual(job["conclusion"], "higher_level_relaunch")
                self.assertTrue(job["attention_required"])
                self.assertEqual(len(notifications), 1)
                self.assertFalse(job["report"]["second_stop_attempted"])
                self.assertEqual(storage.recent_stop_observations()[0]["status"], "relaunched")
                self.assertEqual(storage.recent_stop_verifications()[0]["outcome"], "relaunched")
            finally:
                manager.close()
                storage.close()

    def test_notification_failure_does_not_replace_relaunch_conclusion(self):
        def verifier(_service, _pids, **_kwargs):
            return {
                "service_id": "service:demo",
                "service_fingerprint": "fp-200",
                "original_pid": 200,
                "outcome": "relaunched",
                "restart_detected": True,
                "replacement_pids": [201],
                "restart_evidence": [
                    {"type": "command_hash_reappeared", "replacement_pid": 201}
                ],
                "parent_process_changed": False,
                "endpoint_verification": [],
                "second_stop_attempted": False,
            }

        with tempfile.TemporaryDirectory() as directory:
            storage = Storage(Path(directory))
            manager = StopObservationManager(
                storage,
                notifications_enabled=lambda: True,
                verifier=verifier,
                notifier=lambda *_args: (_ for _ in ()).throw(OSError("disabled")),
            )
            try:
                job_id = manager.start(service_fixture(pid=200), [200], 5)["job_id"]
                job = wait_for_job(manager, job_id)
                self.assertEqual(job["status"], "relaunched")
                self.assertFalse(job["system_notification"]["sent"])
                self.assertEqual(job["system_notification"]["reason"], "OSError")
                self.assertEqual(job["port_state"], "unknown")
            finally:
                manager.close()
                storage.close()

    def test_history_write_failure_does_not_replace_relaunch_conclusion(self):
        def verifier(_service, _pids, **_kwargs):
            return {
                "service_id": "service:demo",
                "service_fingerprint": "fp-200",
                "original_pid": 200,
                "outcome": "relaunched",
                "restart_detected": True,
                "replacement_pids": [201],
                "restart_evidence": [
                    {"type": "command_hash_reappeared", "replacement_pid": 201}
                ],
                "parent_process_changed": False,
                "endpoint_verification": [],
                "second_stop_attempted": False,
            }

        with tempfile.TemporaryDirectory() as directory:
            storage = Storage(Path(directory))
            manager = StopObservationManager(
                storage,
                notifications_enabled=lambda: False,
                verifier=verifier,
            )
            try:
                with patch.object(
                    storage,
                    "add_stop_verification",
                    side_effect=OSError("disk full"),
                ):
                    with self.assertLogs("vsg", level="WARNING"):
                        job_id = manager.start(service_fixture(pid=200), [200], 5)["job_id"]
                        job = wait_for_job(manager, job_id)
                self.assertEqual(job["status"], "relaunched")
                self.assertIsNone(job["report"]["id"])
                self.assertTrue(
                    any("未能写入本机历史" in item for item in job["report"]["limitations"])
                )
            finally:
                manager.close()
                storage.close()

    def test_only_supported_bounded_windows_are_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            storage = Storage(Path(directory))
            manager = StopObservationManager(
                storage,
                notifications_enabled=lambda: False,
                verifier=lambda *_args, **_kwargs: {},
            )
            try:
                with self.assertRaises(StopObservationError):
                    manager.start(service_fixture(), [200], 10)
            finally:
                manager.close()
                storage.close()

    def test_unavailable_listener_evidence_never_displays_port_as_closed(self):
        def verifier(_service, _pids, **kwargs):
            kwargs["progress_callback"](
                {
                    "checks": 1,
                    "remaining_seconds": 250,
                    "original_pid_disappeared": True,
                    "listener_status": "unavailable",
                    "restart_detected": False,
                    "restart_evidence": [],
                }
            )
            return {
                "service_id": "service:demo",
                "service_fingerprint": "fp-200",
                "original_pid": 200,
                "outcome": "verification_partial",
                "restart_detected": False,
                "replacement_pids": [],
                "restart_evidence": [],
                "parent_process_changed": False,
                "endpoint_verification": [],
                "second_stop_attempted": False,
            }

        with tempfile.TemporaryDirectory() as directory:
            storage = Storage(Path(directory))
            manager = StopObservationManager(
                storage,
                notifications_enabled=lambda: False,
                verifier=verifier,
            )
            try:
                job_id = manager.start(service_fixture(pid=200), [200], 5)["job_id"]
                job = wait_for_job(manager, job_id)
                self.assertEqual(job["status"], "evidence_insufficient")
                self.assertEqual(job["port_state"], "unknown")
            finally:
                manager.close()
                storage.close()

    def test_unfinished_persisted_job_becomes_interrupted_on_restart(self):
        now = time.time()
        stored_job = {
            "job_id": "old-job",
            "created_at": now - 60,
            "updated_at": now - 30,
            "deadline_at": now + 300,
            "status": "cancel_requested",
            "service_fingerprint": "fp-old",
            "service_id": "service-old",
            "display_name": "old",
            "project_name": "demo",
            "original_pid": 99,
            "observation_minutes": 5,
            "poll_seconds": 10,
            "limitations": [],
        }
        with tempfile.TemporaryDirectory() as directory:
            storage = Storage(Path(directory))
            storage.upsert_stop_observation(stored_job)
            manager = StopObservationManager(storage, notifications_enabled=lambda: False)
            try:
                restored = storage.recent_stop_observations()[0]
                self.assertEqual(restored["status"], "interrupted")
                self.assertEqual(restored["conclusion"], "evidence_insufficient")
            finally:
                manager.close()
                storage.close()


class CapacityCalibrationTests(unittest.TestCase):
    @staticmethod
    def prediction(_model, _quantization, workload):
        return {
            "max_concurrency": {"memory": 4, "performance": 3, "effective": 3},
            "per_user_generation_tps": {"expected": 10},
            "aggregate_generation_tps": 10 * workload["concurrency"],
            "ttft_seconds": {"expected": 1},
        }

    def test_calibration_preview_is_fixed_to_loaded_model_c1_or_c2_and_60_seconds(self):
        target = service_fixture()
        snapshot = snapshot_fixture(target)
        with tempfile.TemporaryDirectory() as directory:
            storage = Storage(Path(directory))
            manager = WorkloadMatrixManager(
                storage,
                snapshot_provider=lambda: snapshot,
                hardware_provider=hardware_fixture,
                prediction_provider=self.prediction,
                low_disk_provider=lambda: 20,
            )
            try:
                plan = manager.preview(
                    target,
                    snapshot["runtime_probes"][0],
                    catalog_model_id="catalog-demo",
                    quantization="Q4_K_M",
                    mode="calibration",
                    model_name="demo-model",
                    concurrency=2,
                    duration_seconds=60,
                )
                self.assertEqual(plan["confirmation"], "BENCHMARK 11434")
                self.assertEqual(len(plan["steps"]), 1)
                self.assertEqual(plan["steps"][0]["concurrency"], 2)
                self.assertEqual(plan["steps"][0]["duration_seconds"], 60)
                self.assertEqual(plan["fixed_policy"]["resource_utilization_stop_percent"], 85)
                with self.assertRaises(WorkloadMatrixError):
                    manager.preview(
                        target,
                        snapshot["runtime_probes"][0],
                        catalog_model_id=None,
                        quantization=None,
                        mode="calibration",
                        model_name="not-loaded",
                        concurrency=1,
                        duration_seconds=60,
                    )
                with self.assertRaises(WorkloadMatrixError):
                    manager.preview(
                        target,
                        snapshot["runtime_probes"][0],
                        catalog_model_id=None,
                        quantization=None,
                        mode="calibration",
                        model_name="demo-model",
                        concurrency=4,
                        duration_seconds=60,
                    )
                unknown_memory = copy.deepcopy(snapshot)
                unknown_memory["telemetry"]["memory"].pop("used_percent")
                unknown_manager = WorkloadMatrixManager(
                    storage,
                    snapshot_provider=lambda: unknown_memory,
                    hardware_provider=hardware_fixture,
                    prediction_provider=self.prediction,
                    low_disk_provider=lambda: 20,
                )
                unknown_plan = unknown_manager.preview(
                    target,
                    unknown_memory["runtime_probes"][0],
                    catalog_model_id=None,
                    quantization=None,
                    mode="calibration",
                    model_name="demo-model",
                    concurrency=1,
                    duration_seconds=60,
                )
                self.assertFalse(unknown_plan["guard"]["allowed"])
                self.assertTrue(
                    any(
                        "未获得系统内存" in blocker
                        for blocker in unknown_plan["guard"]["blockers"]
                    )
                )
                unknown_manager.close()
            finally:
                manager.close()
                storage.close()

    def test_completed_calibration_persists_measured_profile_and_prediction_error(self):
        target = service_fixture()
        snapshot = snapshot_fixture(target)
        result = {
            "service_fingerprint": target["fingerprint"],
            "runtime": target["runtime"],
            "port": 11434,
            "model_name": "demo-model",
            "concurrency": 2,
            "requested_context_tokens": 1024,
            "requested_output_tokens": 32,
            "successful_requests": 6,
            "failed_requests": 0,
            "request_count": 6,
            "sample_count": 6,
            "ttft_seconds": 0.8,
            "ttft_p95_seconds": 0.9,
            "generation_tps": 12.0,
            "aggregate_generation_tps": 24.0,
            "prompt_tps": 120.0,
            "wall_seconds": 60.0,
            "oom_observed": False,
            "cancelled": False,
            "details": {"response_content_persisted": False},
        }
        with tempfile.TemporaryDirectory() as directory:
            storage = Storage(Path(directory))
            manager = WorkloadMatrixManager(
                storage,
                snapshot_provider=lambda: snapshot,
                hardware_provider=hardware_fixture,
                prediction_provider=self.prediction,
                low_disk_provider=lambda: 20,
            )
            try:
                plan = manager.preview(
                    target,
                    snapshot["runtime_probes"][0],
                    catalog_model_id="catalog-demo",
                    quantization="Q4_K_M",
                    mode="calibration",
                    model_name="demo-model",
                    concurrency=2,
                    duration_seconds=60,
                )
                with patch("vsg.workload_matrix.run_service_benchmark", return_value=result):
                    job_id = manager.start(plan["plan_id"], "BENCHMARK 11434")["job_id"]
                    job = wait_for_job(manager, job_id)
                self.assertEqual(job["status"], "completed")
                self.assertEqual(job["results"][0]["prediction_error"]["per_user_generation_tps"]["signed_percent"], 20.0)
                profile = storage.calibration_profiles()[0]
                self.assertTrue(profile["measured_safe"])
                self.assertEqual(profile["recommended_safe_concurrency"], 2)
                self.assertEqual(profile["theoretical_capacity"]["max_concurrency"]["effective"], 3)
                planner = ModelPlanner.__new__(ModelPlanner)
                planner.storage = storage
                measured = planner.measured_profiles(hardware_fixture())
                self.assertEqual(measured["summary"]["valid"], 1)
                changed = copy.deepcopy(hardware_fixture())
                changed["hardware_fingerprint"] = "different-hardware"
                invalidated = planner.measured_profiles(changed)
                self.assertEqual(invalidated["items"][0]["validity"], "possibly_invalid")
            finally:
                manager.close()
                storage.close()

    def test_live_85_percent_guard_cancels_new_requests(self):
        target = service_fixture()
        low = snapshot_fixture(target)
        high = copy.deepcopy(low)
        high["telemetry"]["gpus"][0]["memory_util_percent"] = 85.0
        benchmark_started = threading.Event()

        def snapshots():
            return high if benchmark_started.is_set() else low

        def benchmark(_service, _probe, _request, _memory, **kwargs):
            benchmark_started.set()
            self.assertTrue(kwargs["cancel_event"].wait(2))
            return {
                "service_fingerprint": target["fingerprint"],
                "runtime": target["runtime"],
                "port": 11434,
                "model_name": "demo-model",
                "concurrency": 1,
                "requested_context_tokens": 1024,
                "requested_output_tokens": 32,
                "successful_requests": 1,
                "failed_requests": 0,
                "request_count": 1,
                "sample_count": 1,
                "ttft_seconds": 1.0,
                "generation_tps": 9.0,
                "aggregate_generation_tps": 9.0,
                "wall_seconds": 1.0,
                "oom_observed": False,
                "cancelled": True,
                "details": {},
            }

        with tempfile.TemporaryDirectory() as directory:
            storage = Storage(Path(directory))
            manager = WorkloadMatrixManager(
                storage,
                snapshot_provider=snapshots,
                hardware_provider=hardware_fixture,
                prediction_provider=self.prediction,
                low_disk_provider=lambda: 20,
            )
            try:
                plan = manager.preview(
                    target,
                    low["runtime_probes"][0],
                    catalog_model_id="catalog-demo",
                    quantization="Q4_K_M",
                    mode="calibration",
                    model_name="demo-model",
                    concurrency=1,
                    duration_seconds=60,
                )
                with patch("vsg.workload_matrix.run_service_benchmark", side_effect=benchmark):
                    job_id = manager.start(plan["plan_id"], "BENCHMARK 11434")["job_id"]
                    job = wait_for_job(manager, job_id)
                self.assertEqual(job["status"], "guard_stopped")
                self.assertIn("85%", job["error"])
                profile = storage.calibration_profiles()[0]
                self.assertFalse(profile["measured_safe"])
                self.assertEqual(profile["recommended_safe_concurrency"], 0)
            finally:
                manager.close()
                storage.close()

    def test_user_cancelled_calibration_is_never_marked_safe(self):
        target = service_fixture()
        snapshot = snapshot_fixture(target)
        cancelled = {
            "service_fingerprint": target["fingerprint"],
            "runtime": target["runtime"],
            "port": 11434,
            "model_name": "demo-model",
            "concurrency": 1,
            "requested_context_tokens": 1024,
            "requested_output_tokens": 32,
            "successful_requests": 1,
            "failed_requests": 0,
            "request_count": 1,
            "sample_count": 1,
            "ttft_seconds": 1.0,
            "generation_tps": 9.0,
            "aggregate_generation_tps": 9.0,
            "wall_seconds": 2.0,
            "oom_observed": False,
            "cancelled": True,
            "details": {},
        }
        with tempfile.TemporaryDirectory() as directory:
            storage = Storage(Path(directory))
            manager = WorkloadMatrixManager(
                storage,
                snapshot_provider=lambda: snapshot,
                hardware_provider=hardware_fixture,
                prediction_provider=self.prediction,
                low_disk_provider=lambda: 20,
            )
            try:
                plan = manager.preview(
                    target,
                    snapshot["runtime_probes"][0],
                    catalog_model_id="catalog-demo",
                    quantization="Q4_K_M",
                    mode="calibration",
                    model_name="demo-model",
                    concurrency=1,
                    duration_seconds=60,
                )
                with patch(
                    "vsg.workload_matrix.run_service_benchmark",
                    return_value=cancelled,
                ):
                    job_id = manager.start(
                        plan["plan_id"], "BENCHMARK 11434"
                    )["job_id"]
                    job = wait_for_job(manager, job_id)
                self.assertEqual(job["status"], "cancelled")
                profile = storage.calibration_profiles()[0]
                self.assertFalse(profile["measured_safe"])
                self.assertEqual(profile["recommended_safe_concurrency"], 0)
            finally:
                manager.close()
                storage.close()


class RelationshipAndConfigurationTests(unittest.TestCase):
    def test_protected_service_recommendations_are_display_only(self):
        docker = service_fixture(source="docker", stoppable=False, protected=True)
        docker["metadata"].update(
            {
                "compose_project": "demo",
                "compose_service": "api",
                "container_id": "a" * 12,
            }
        )
        items = recommended_operations(docker)
        self.assertEqual({item["kind"] for item in items}, {"docker", "docker_compose"})
        self.assertTrue(all(item["will_execute"] is False for item in items))
        self.assertTrue(all(item["requires_manual_review"] for item in items))

    def test_project_runtime_view_uses_only_valid_local_profile(self):
        service = service_fixture()
        probe = snapshot_fixture(service)["runtime_probes"][0]
        probe["performance"] = {"requests_running": 1, "requests_waiting": 0}
        graph = build_service_relationships(
            [service],
            connection_provider=lambda: [],
            runtime_probes=[probe],
            calibration_profiles=[
                {
                    "service_fingerprint": service["fingerprint"],
                    "validity": "valid",
                    "measured_safe": True,
                    "model_name": "different-model",
                    "recommended_safe_concurrency": 8,
                },
                {
                    "service_fingerprint": service["fingerprint"],
                    "validity": "valid",
                    "measured_safe": True,
                    "model_name": "demo-model",
                    "recommended_safe_concurrency": 2,
                }
            ],
        )
        view = graph["project_runtime_views"][0]
        self.assertEqual(view["capacity"]["measured_safe_concurrency"], 2)
        self.assertEqual(view["capacity"]["available_concurrency"], 1)
        self.assertEqual(view["capacity"]["source"], "measured_local")

    def test_project_runtime_view_blocks_new_concurrency_at_resource_guard(self):
        service = service_fixture()
        snapshot = snapshot_fixture(service)
        probe = snapshot["runtime_probes"][0]
        probe["performance"] = {"requests_running": 0, "requests_waiting": 0}
        snapshot["telemetry"]["memory"]["used_percent"] = 86.0
        graph = build_service_relationships(
            [service],
            connection_provider=lambda: [],
            runtime_probes=[probe],
            telemetry=snapshot["telemetry"],
            calibration_profiles=[
                {
                    "service_fingerprint": service["fingerprint"],
                    "validity": "valid",
                    "measured_safe": True,
                    "model_name": "demo-model",
                    "recommended_safe_concurrency": 2,
                }
            ],
        )
        capacity = graph["project_runtime_views"][0]["capacity"]
        self.assertTrue(capacity["resource_guard_triggered"])
        self.assertEqual(capacity["available_concurrency"], 0)

    def test_project_runtime_view_does_not_reuse_profile_with_multiple_loaded_models(self):
        service = service_fixture()
        probe = snapshot_fixture(service)["runtime_probes"][0]
        probe["models"].append({"name": "second-model"})
        probe["performance"] = {"requests_running": 0, "requests_waiting": 0}
        graph = build_service_relationships(
            [service],
            connection_provider=lambda: [],
            runtime_probes=[probe],
            calibration_profiles=[
                {
                    "service_fingerprint": service["fingerprint"],
                    "validity": "valid",
                    "measured_safe": True,
                    "model_name": "demo-model",
                    "recommended_safe_concurrency": 2,
                }
            ],
        )
        capacity = graph["project_runtime_views"][0]["capacity"]
        self.assertEqual(capacity["source"], "unavailable")
        self.assertIsNone(capacity["available_concurrency"])
        self.assertIn("同时加载多个模型", capacity["evidence_label"])

    def test_system_notification_setting_is_strict_boolean(self):
        root = str(Path.cwd())
        config = validate_config({"project_roots": [root], "enable_system_notifications": True})
        self.assertTrue(config.enable_system_notifications)
        with self.assertRaises(ValueError):
            validate_config({"project_roots": [root], "enable_system_notifications": "yes"})


if __name__ == "__main__":
    unittest.main()
