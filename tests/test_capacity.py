import unittest
from copy import deepcopy

from vsg.capacity import CapacityError, estimate_capacity, memory_breakdown, validate_workload
from vsg.model_catalog import load_catalog


def hardware_fixture():
    return {
        "hardware_fingerprint": "fixture-24gb",
        "platform": {"key": "windows", "architecture": "AMD64"},
        "cpu": {"name": "Test CPU", "memory_bandwidth_gbps_estimate": 75},
        "memory": {"total_gib": 64, "available_gib": 58, "unified": False},
        "gpus": [
            {
                "vendor": "NVIDIA",
                "name": "NVIDIA GeForce RTX 4090",
                "memory_total_gib": 24,
                "memory_free_gib": 22,
                "bandwidth_gbps": 1008,
                "backend": "cuda",
                "support_tier": "supported",
                "confidence": "high",
                "integrated": False,
            }
        ],
    }


class CapacityTests(unittest.TestCase):
    def test_workload_rejects_impossible_concurrency(self):
        with self.assertRaises(CapacityError):
            validate_workload({"total_users": 2, "concurrency": 3})
        with self.assertRaises(CapacityError):
            validate_workload({"prompt_tokens": 8000, "output_tokens": 1000, "context_tokens": 8192})

    def test_moe_weights_use_total_not_active_parameters(self):
        moe = {
            "total_params_b": 36,
            "active_params_b": 3,
            "kv_cache_kib_per_token_fp16": 96,
        }
        dense = {**moe, "active_params_b": 36}
        moe_memory = memory_breakdown(moe, "Q4_K_M", 8192, 2, 16)
        dense_memory = memory_breakdown(dense, "Q4_K_M", 8192, 2, 16)
        self.assertEqual(moe_memory["weights_gib"], dense_memory["weights_gib"])
        self.assertLess(moe_memory["workspace_gib"], dense_memory["workspace_gib"])

    def test_kv_memory_scales_with_concurrency(self):
        model = load_catalog()["models"][0]
        one = memory_breakdown(model, "Q4_K_M", 8192, 1, 16)
        four = memory_breakdown(model, "Q4_K_M", 8192, 4, 16)
        self.assertAlmostEqual(four["kv_cache_gib"], one["kv_cache_gib"] * 4, places=2)

    def test_estimate_returns_three_distinct_decision_levels(self):
        estimate = estimate_capacity(hardware_fixture(), load_catalog(), {})
        self.assertEqual(set(estimate["ceilings"]), {"physical", "usable", "sla"})
        self.assertEqual(len(estimate["candidates"]), len(load_catalog()["models"]))
        self.assertIn("127.0.0.1", estimate["runtime_plan"]["display"])
        self.assertFalse(estimate["runtime_plan"]["will_execute"])
        self.assertGreaterEqual(
            estimate["ceilings"]["physical"].get("total_params_b", 0),
            estimate["ceilings"]["sla"].get("total_params_b", 0),
        )

    def test_unavailable_plan_keeps_complete_non_executing_contract(self):
        constrained = deepcopy(hardware_fixture())
        constrained["memory"] = {
            "total_gib": 1.0,
            "available_gib": 0.5,
            "unified": False,
        }
        constrained["gpus"] = []
        estimate = estimate_capacity(
            constrained,
            load_catalog(),
            {
                "total_users": 25,
                "concurrency": 4,
                "context_tokens": 8192,
                "target_tps_per_user": 8,
                "target_ttft_seconds": 5,
            },
        )
        plan = estimate["runtime_plan"]
        self.assertIsNone(estimate["selected_model_id"])
        self.assertFalse(plan["available"])
        self.assertFalse(plan["will_execute"])
        self.assertEqual(plan["command"], [])
        self.assertEqual(plan["display"], "")
        self.assertEqual(plan["binding"], "127.0.0.1:8080")

    def test_total_users_does_not_change_memory_at_fixed_concurrency(self):
        first = estimate_capacity(hardware_fixture(), load_catalog(), {"total_users": 10, "concurrency": 2})
        second = estimate_capacity(hardware_fixture(), load_catalog(), {"total_users": 100, "concurrency": 2})
        by_id_first = {item["model_id"]: item for item in first["candidates"]}
        by_id_second = {item["model_id"]: item for item in second["candidates"]}
        model_id = next(iter(by_id_first))
        self.assertEqual(by_id_first[model_id]["memory"], by_id_second[model_id]["memory"])


if __name__ == "__main__":
    unittest.main()
