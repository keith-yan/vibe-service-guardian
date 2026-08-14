import unittest

from vsg.advisor import generate_hardware_advice


class HardwareAdvisorTests(unittest.TestCase):
    def test_uses_measured_pressure_and_log_evidence(self):
        result = generate_hardware_advice(
            {"platform": {"key": "linux"}, "gpus": [{"vendor": "NVIDIA"}]},
            {
                "memory": {"used_percent": 94},
                "disks": [{"root": "/models", "free_gib": 10}],
                "gpus": [{"vendor": "NVIDIA", "memory_util_percent": 97, "temperature_c": 88}],
                "sensors": {"temperatures": [], "fans": []},
            },
            {"concurrency": 8, "context_tokens": 32768},
            [],
            [{"code": "CUDA_OOM"}],
            50,
        )
        ids = {item["id"] for item in result["recommendations"]}
        self.assertTrue({"ram-pressure", "disk-headroom", "vram-pressure", "thermal-headroom", "log-capacity-failure"} <= ids)
        self.assertGreaterEqual(result["summary"]["critical"], 3)

    def test_missing_sensor_data_is_unknown_not_estimated(self):
        result = generate_hardware_advice(
            {"platform": {"key": "windows"}, "gpus": [{"vendor": "AMD"}]},
            {"memory": {"used_percent": 20}, "disks": [], "gpus": [{"vendor": "AMD"}], "sensors": {}},
        )
        self.assertTrue(any("显存" in item for item in result["unknowns"]))
        self.assertTrue(any("温度" in item for item in result["unknowns"]))
        self.assertTrue(all(item["automatic"] is False for item in result["recommendations"]))


if __name__ == "__main__":
    unittest.main()
